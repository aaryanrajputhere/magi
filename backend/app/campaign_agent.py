"""Bounded conversational orchestration over campaign services.

Approval remains exclusively in the HTTP approval/execution handlers.
"""
import json
import re
import time
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.brand.campaign_requirements import CAMPAIGN_CAPABILITIES
from app.brand.decisions import DECISION_GUIDANCE, current_decisions, record_answer
from app.llm.client import ModelRequest, ModelRunError, TrackedOpenAI
from app.llm.context import RunContext
from app.llm.prompt_cache import prompt_cache_key
from app.llm.prompt_payloads import prompt_decisions
from app.memory import context as memory_context
from app.models.agent import AgentTurn, CampaignLease


def run_agent_step(client, request, session, *, turn=None):
    """Retry one disconnected model selection; campaign tools execute only after return."""
    from app.models import LLMRun

    if turn is not None:
        return recover_preparation(lambda: client.run(request), client, session, turn, request.operation)
    try:
        return client.run(request)
    except ModelRunError as exc:
        record = session.get(LLMRun, exc.run_id)
        root = (record.error or {}).get('root_cause', {}) if record else {}
        if exc.status != 'provider_error' or root.get('type') != 'RemoteProtocolError':
            raise
        # Both attempts remain separate audited runs. Billing of the failed attempt is unknown.
        client.close()
        time.sleep(0.5)
        return client.run(request)


def recover_preparation(action, client, session, turn, operation):
    """Retry one failed preparation step, retaining its committed checkpoints.

    Call only for model selection or preparation, never saved copy edits, image
    changes, approvals, or generation. Logging failures are deliberately excluded.
    """
    from app.models import LLMRun

    for attempt in range(2):
        try:
            return action()
        except Exception as exc:
            cause = exc
            seen = set()
            while not isinstance(cause, ModelRunError) and cause.__cause__ is not None and id(cause) not in seen:
                seen.add(id(cause))
                cause = cause.__cause__
            if not isinstance(cause, ModelRunError) or attempt:
                raise
            record = session.get(LLMRun, cause.run_id)
            error = (record.error or {}) if record else {}
            status_code = error.get('status_code')
            retryable = cause.status in {'timeout', 'validation_error'} or (
                cause.status == 'provider_error' and (
                    error.get('root_cause', {}).get('type') in {'RemoteProtocolError', 'ConnectError', 'ReadError'}
                    or status_code == 429 or isinstance(status_code, int) and status_code >= 500))
            if not retryable:
                raise
            session.rollback()
            session.expire_all()
            turn.data = {**turn.data, 'recoveries': [*turn.data.get('recoveries', []),
                {'tool': operation, 'run_id': cause.run_id, 'status': cause.status, 'attempt': 1}]}
            session.commit()
            client.close()
            time.sleep(0.5)


def tool(name, description, properties):
    return {"type": "function", "name": name, "description": description,
            "strict": True, "parameters": {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}}


TOOLS = [
    tool("revise_plan", "Remove or edit specific outlines before draft generation without research. Resolve numbered references using latest plan order. Changes are explicit fields; timing may be null.", {
        "remove_asset_ids": {"type": "array", "items": {"type": "string"}},
        "patches": {"type": "array", "items": {"type": "object", "properties": {
            "asset_id": {"type": "string"}, "changes": {"type": "array", "items": {
                "type": "object", "properties": {"field": {"type": "string", "enum": ["audience", "purpose", "timing", "angle", "hook", "cta"]}, "value": {"type": ["string", "null"]}},
                "required": ["field", "value"], "additionalProperties": False}}}, "required": ["asset_id", "changes"], "additionalProperties": False}}}),
    tool("add_images", "Select library images for saved drafts using their content and brief rules. Empty asset_ids targets all drafts. Preserve attachments unless explicitly asked to replace them.", {
        "asset_ids": {"type": "array", "items": {"type": "string"}},
        "replace_existing": {"type": "boolean"}, "instruction": {"type": "string"}}),
    tool("retrieve_context", "Retrieve and compact brand evidence for saved requirements before planning", {"addition": {"type": "boolean"}}),
    tool("ask_question", "Ask one essential decision and wait. Never use for approval. Prefer 2-5 actionable choices that let planning continue, including omission or a pending-clearance placeholder only when consistent with the brief. Inspect retrieved originals before asking for quotes or clearance, and state precisely what remains missing. Never treat a placeholder as approved copy. Use text only for an indispensable value without valid alternatives.", {
        "text": {"type": "string"}, "kind": {"type": "string", "enum": ["choices", "text"]},
        "options": {"type": "array", "items": {"type": "string"}}}),
    tool("extract_requirements", "Save requirements and inspect unknowns before planning", {"addition": {"type": "boolean"}}),
    tool("list_brand_images", "List eligible image IDs and metadata before selecting an image", {}),
    tool("change_image", "Save or remove one draft image. Use an eligible ID from list_brand_images; null removes it. This changes no copy.",
         {"asset_id": {"type": "string"}, "image_id": {"type": ["string", "null"]}}),
    tool("read_campaign", "Read current plan and saved drafts", {}),
    tool("propose_plan", "Propose or adjust a plan; additions require approval before generation",
         {"instruction": {"type": "string"}, "addition": {"type": "boolean"}}),
    tool("revise_draft", "Immediately edit one existing saved draft. Pass the user edit faithfully. Shortening and wording edits may override length/style defaults; do not add minimum lengths or extra requirements.",
         {"instruction": {"type": "string"}, "asset_id": {"type": "string"}}),
    tool("answer_brand_question", "Answer using reviewed brand evidence through the campaign service",
         {"instruction": {"type": "string"}}),
    tool("finish_turn", "Finish with a concise confirmation or clarification; never write campaign copy",
         {"text": {"type": "string"}}),
]


class Instruction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    instruction: str = Field(min_length=1)

class AddImages(Instruction):
    asset_ids: list[str]
    replace_existing: bool

class PlanField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: Literal['audience', 'purpose', 'timing', 'angle', 'hook', 'cta']
    value: str | None

class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    asset_id: str
    changes: list[PlanField]

class PlanRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    remove_asset_ids: list[str]
    patches: list[PlanPatch]

class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    addition: bool

class Proposal(Instruction):
    addition: bool

class Revision(Instruction):
    asset_id: str = Field(min_length=1)

class ImageChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    asset_id: str = Field(min_length=1)
    image_id: str | None

class Finish(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1)

class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["choices", "text"]
    options: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def valid_options(self):
        if any(ord(c) < 32 and c not in "\n\t" for value in [self.text, *self.options] for c in value):
            raise ValueError("Question text and choices must not contain control characters")
        if self.kind == "text" and self.options:
            raise ValueError("Text questions must have no options")
        if self.kind == "choices" and (len(self.options) < 2 or any(not x.strip() for x in self.options)
                                       or len(set(self.options)) != len(self.options)):
            raise ValueError("Provide 2-5 distinct nonblank choices")
        return self

class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

ARGUMENTS = {"revise_plan": PlanRevision, "add_images": AddImages, "retrieve_context": Extraction, "ask_question": Question, "extract_requirements": Extraction, "list_brand_images": Empty, "change_image": ImageChange, "read_campaign": Empty, "propose_plan": Proposal,
             "revise_draft": Revision, "answer_brand_question": Instruction, "finish_turn": Finish}



def agent_state(campaign, full=False):
    """A routing view; original artifacts remain available through their tools."""
    data = campaign.data
    plans = data.get('plans', [])
    return {
        'id': campaign.id, 'state': campaign.state, 'version': campaign.version,
        'requirements': (data.get('requirements') if full else
            {k: v for k, v in (data.get('requirements') or {}).items() if k in {'topic', 'issues'}}),
        'pending_question': data.get('pending_question'),
        'current_plan': {k: v for k, v in plans[-1].items()
                         if k in ({'goal', 'constraints', 'assumptions', 'assets'} if full else {'goal'})} if plans else None,
        'retrieved_context': {k: v for k, v in data.get('retrieved_context', {}).items()
                              if k in {'artifact_id', 'requirements_revision', 'empty_tracks', 'status'}},
        'unresolved_conflicts': [{k: v for k, v in f.items()
                                 if k in {'id', 'issue_id', 'question', 'status'}}
                                for f in data.get('conflicts', []) if f.get('status') == 'unresolved'],
        'drafts': [dict(d) if full else {k: v for k, v in d.items() if k in {'asset_id', 'format', 'title', 'status'}}
                   for d in data.get('generation', {}).get('drafts', [])],
    }

def tool_receipt(campaign, name, args, output):
    """Return actionable outcomes; full saved copy stays behind read_campaign."""
    pending = campaign.data.get('pending_question')
    result = {'status': 'needs_clarification' if pending else output.get('status', 'saved'),
              'version': campaign.version, 'campaign_state': campaign.state,
              **{k: output[k] for k in ('image_id', 'empty_tracks', 'removed_asset_ids',
                   'edited_asset_ids', 'asset_count', 'results', 'issues', 'unknowns') if k in output}}
    if args.get('asset_id'):
        result['asset_id'] = args['asset_id']
    if pending:
        result.update({'question_id': pending['id'], 'question': pending['text'],
                       'options': pending.get('options', [])})
    if name == 'extract_requirements':
        result['requirements'] = {k: v for k, v in (output.get('requirements') or {}).items()
                                  if k in {'topic', 'issues', 'unknowns'}}
    return result


def run_turn(identifier, body, session, settings):
    from app.api.campaigns import Message, MessageRoute, load, process_message, snapshot
    if not body.text.strip():
        raise HTTPException(422, "Message cannot be blank")
    turn_id = identifier + ":" + (body.request_id or str(uuid4()))
    existing = session.get(AgentTurn, turn_id)
    if existing:
        if existing.data.get("request") != body.text:
            raise HTTPException(409, "Request ID already used")
        return snapshot(load(session, identifier))
    session.execute(delete(CampaignLease).where(CampaignLease.campaign_id == identifier, CampaignLease.expires_at < time.time()))
    session.add(CampaignLease(campaign_id=identifier, owner=turn_id, expires_at=time.time()+3600))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Campaign is processing") from exc
    c = load(session, identifier)
    if c.version != body.version or c.state in {"planning_busy", "generating"}:
        session.execute(delete(CampaignLease).where(CampaignLease.owner == turn_id))
        session.commit()
        raise HTTPException(409, "Campaign changed or is processing")
    if body.question_id:
        pending = c.data.get("pending_question")
        if not pending or pending["id"] != body.question_id:
            session.execute(delete(CampaignLease).where(CampaignLease.owner == turn_id))
            session.commit()
            raise HTTPException(409, "Question is no longer pending")
    pending = c.data.get("pending_question")
    if pending:
        record_answer(c, pending, body.text)
    turn = AgentTurn(id=turn_id, campaign_id=identifier, status="running", data={"request": body.text, "executions": []})
    session.add(turn)
    c.data = {**c.data, "messages": [*c.data.get("messages", []), {"id": body.request_id, "role": "user", "text": body.text}]}
    c.version += 1
    session.commit()
    inputs = [{"role": "user", "content": json.dumps({"conversation": [{"role": m['role'], "text": m.get('text', '')}
                         for m in c.data.get("messages", [])[-12:]],
        "state": agent_state(c), "brand_preferences": memory_context(session, c.brand_id),
        "current_decisions": prompt_decisions(current_decisions(c))})}]
    client = TrackedOpenAI(session.get_bind(), settings)
    completed = []
    needs_action = True
    try:
        def finish(text=None):
            if text:
                c.data = {**c.data, "messages": [*c.data["messages"],
                          {"role": "assistant", "text": text}]}
                c.version += 1
            c.data = {**c.data, "agent_last_turn": {"id": turn_id, "completed": completed}}
            turn.status = "completed"
            session.commit()
            return snapshot(c)

        if pending:
            from app.brand.conflicts import publish_conflict
            publish_conflict(c, [])
            if c.data.get('pending_question'):
                c.version += 1
                completed.append({'tool': 'ask_question', 'question_id': c.data['pending_question']['id']})
                return finish()
        plans = c.data.get("plans", [])
        pending_question = bool(plans and plans[-1].get("questions"))
        approval_reply = body.text.strip().lower().rstrip(".! ") in {
            "yes", "confirm", "confirmed", "approve", "approved", "proceed", "go ahead"}
        if approval_reply and c.state in {"awaiting_approval", "approved"} and not pending_question and not pending:
            return finish("Review the saved plan and choose Approve & generate to create the drafts.")
        for _ in range(12):
            # A clarification answer resumes the pipeline, not an arbitrary edit
            # to a plan that may not exist. Enforce prerequisites in code.
            available_tools = TOOLS
            if pending:
                from app.brand.campaign_requirements import requirement_revision
                continuation_mode = ('extend' if c.data.get('generation', {}).get('drafts')
                                     else 'campaign')
                requirements = c.data.get('requirements')
                current = c.data.get('requirements_revision') == requirement_revision(
                    c.data.get('messages', []), continuation_mode)
                if not requirements or not current:
                    allowed = {'extract_requirements'}
                elif requirements.get('issues'):
                    allowed = {'ask_question'}
                elif c.data.get('retrieved_context', {}).get('requirements_revision') != c.data.get('requirements_revision'):
                    allowed = {'retrieve_context'}
                else:
                    allowed = {'propose_plan', 'ask_question'}
                available_tools = [t for t in TOOLS if t['name'] in allowed]
            if (pending and len(available_tools) == 1
                    and available_tools[0]['name'] in {'extract_requirements', 'retrieve_context'}):
                from types import SimpleNamespace
                call = {'type': 'function_call', 'name': available_tools[0]['name'],
                        'call_id': 'dispatch_' + str(uuid4()),
                        'arguments': json.dumps({'addition': continuation_mode == 'extend'})}
                result = SimpleNamespace(response={'output': [call]})
            else:
                result = run_agent_step(client, ModelRequest(operation="campaign_agent_step", prompt_cache_key=prompt_cache_key(c.brand_id, "campaign_agent_step"),
                    model=settings.magi_agent_model,
                    instructions=CAMPAIGN_CAPABILITIES + DECISION_GUIDANCE + "You manage a brand campaign using tools. For removals or individual outline edits to an existing ungenerated plan, use revise_plan directly: do not extract, retrieve, or propose again. Preserve all unspecified fields. Resolve numbered assets by latest saved plan order; read_campaign if needed. Format changes and additions require planning. Before proposing a plan call extract_requirements. Inspect structured issues and ask one essential clarification using ask_question if required information is missing. Optional details need not block planning. After a saved conflict choice, reuse current requirements and retrieved evidence; apply current decisions when proposing the plan. Extract again only for new instructions or incomplete requirements. Once brief issues are resolved, call retrieve_context, inspect its topic and channel tracks and empty_tracks, then propose_plan. Compacted context preserves sources but does not establish claim approval. Ask if essential evidence is absent; do not invent facts. Perform requested actions, never merely describe them. Edits apply immediately; new assets (social posts, emails, blogs and landing pages) require a plan and explicit UI approval. Never approve or generate new drafts yourself. For compound requests execute edits first then propose additions. Resolve numbered posts by saved order. Ask when ambiguous. Preserve the full user request, especially publication dates and required statistics, in tool instructions. Use propose_plan for requests to create any campaign assets, including emails, not answer_brand_question. When adding assets to an existing generated campaign, set addition=true and preserve existing drafts. Resolve requests such as an email for it using the current campaign topic and audience. Resolve brief issues before planning. After a plan reports a question, use ask_question and wait; never answer it yourself. Never write unsaved campaign copy. Finish only after tool results confirm success. For automatic library image selection use add_images, preserving existing attachments unless replacement is requested. Inspect per-asset results and report gaps honestly. Never generate images. Exact image edits require list_brand_images then change_image; revise_draft edits text only and preserves the attachment. For compound requests perform both tools. A change_image result must confirm the saved image ID before you claim it changed. Never claim scheduling is available or offer to schedule; requested dates are draft planning dates only. When a plan needs clarification, relay its one essential question concisely without introducing alternative evidence or reports that the plan has not verified as eligible. Use finish_turn to finish.",
                    input=inputs, tools=available_tools, tool_choice="required", max_output_tokens=2000,
                    context=RunContext(campaign_id=identifier, brand_id=c.brand_id)), session, turn=turn)
            inputs.extend(result.response.get("output", []))
            calls = [x for x in result.response.get("output", []) if x.get("type") == "function_call"]
            if len(calls) != 1:
                raise ValueError("Agent must choose exactly one tool")
            call = calls[0]
            name = call["name"]
            if name not in {t['name'] for t in available_tools}:
                inputs.append({'type': 'function_call_output', 'call_id': call['call_id'],
                    'output': json.dumps({'status': 'blocked',
                        'reason': 'Continue processing the clarification answer before finishing or editing.',
                        'available_actions': [t['name'] for t in available_tools]})})
                needs_action = True
                continue
            if call["name"] not in ARGUMENTS:
                raise ValueError("Unknown tool")
            try:
                args = ARGUMENTS[name].model_validate_json(call["arguments"]).model_dump()
            except ValidationError as exc:
                inputs.append({"type": "function_call_output", "call_id": call["call_id"],
                    "output": json.dumps({"status": "invalid_arguments",
                        "errors": exc.errors(include_url=False, include_context=False, include_input=False),
                        "reason": "Correct the tool arguments and retry. For ask_question use 2-5 distinct choices, or kind=text with options=[] for an open answer. Ask one essential question only. Completed actions remain saved; do not repeat them."})})
                continue
            schema = next((t for t in TOOLS if t["name"] == name), None)
            if schema is None or set(args) != set(schema["parameters"]["properties"]):
                raise ValueError("Invalid agent tool arguments")
            if pending and name in {'extract_requirements', 'retrieve_context', 'propose_plan'}:
                args['addition'] = continuation_mode == 'extend'
            session.expire_all()
            c = load(session, identifier)
            if name in {"propose_plan", "retrieve_context"}:
                from app.brand.campaign_requirements import requirement_revision
                mode = "extend" if args["addition"] and c.data.get("generation", {}).get("drafts") else "campaign"
                requirements = c.data.get("requirements")
                current = c.data.get("requirements_revision") == requirement_revision(c.data.get("messages", []), mode)
                if not requirements or not current or requirements.get("issues"):
                    needs_action = True
                    output = {"status": "blocked", "reason": "Extract current requirements and resolve blocking brief issues before planning",
                              "issues": (requirements or {}).get("issues", []),
                              "available_actions": ["extract_requirements", "ask_question"]}
                    inputs.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(output)})
                    continue
            if name == "propose_plan":
                cached = c.data.get("retrieved_context", {})
                if cached.get("requirements_revision") != c.data.get("requirements_revision"):
                    needs_action = True
                    inputs.append({"type": "function_call_output", "call_id": call["call_id"],
                        "output": json.dumps({"status": "blocked", "reason": "Call retrieve_context before planning", "available_actions": ["retrieve_context"]})})
                    continue
            if name == "ask_question":
                if re.search(r"\b(videos?|animations?|audio|podcasts?)\b",
                             " ".join([args["text"], *args["options"]]), re.IGNORECASE):
                    inputs.append({"type": "function_call_output", "call_id": call["call_id"],
                        "output": json.dumps({"status": "blocked", "reason":
                            "Unsupported media in clarification. " + CAMPAIGN_CAPABILITIES})})
                    continue
                if current_decisions(c):
                    from app.brand.conflicts import reconcile_decisions
                    from app.brand.context import load_artifact
                    cached = c.data.get('retrieved_context', {})
                    evidence = (load_artifact(session.get_bind(), c.brand_id, cached['artifact_id'],
                                              'campaign_review')['evidence']
                                if cached.get('artifact_id') else [])
                    unanswered = reconcile_decisions(client, settings, c,
                        [{'explanation': args['text'], 'question': args['text'],
                          'options': args['options'], 'affected_asset_ids': []}], evidence,
                        c.data.get('messages', []))
                    if not unanswered:
                        inputs.append({'type': 'function_call_output', 'call_id': call['call_id'],
                            'output': json.dumps({'status': 'blocked',
                                'reason': 'This question has already been answered. Apply current_decisions and continue planning.',
                                'current_decisions': prompt_decisions(current_decisions(c))})})
                        needs_action = True
                        continue
                question = {"id": str(uuid4()), **args, "answer": None,
                            "source_snapshot": c.data.get('decision_evidence', {}),
                            "message_count": len(c.data.get('messages', []))}
                c.data = {**c.data, "pending_question": question,
                    "questions": [*c.data.get("questions", []), question],
                    "messages": [*c.data["messages"], {"role": "assistant", "text": args["text"], "question": question}]}
                c.version += 1
                c.state = 'clarifying'
                completed.append({"tool": name, "question_id": question["id"]})
                return finish()
            if name == "finish_turn":
                if needs_action:
                    inputs.append({'type': 'function_call_output', 'call_id': call['call_id'],
                        'output': json.dumps({'status': 'blocked',
                            'reason': 'No requested change has been saved. Complete the required action or ask a concrete clarification; do not promise future work.',
                            'available_actions': ['extract_requirements', 'read_campaign', 'ask_question']})})
                    continue
                reply = ("Updated the saved draft." if any(x['tool'] == 'revise_draft' for x in completed)
                         else args['text'])
                c.data = {**c.data, "agent_last_turn": {"id": turn_id, "completed": completed},
                    "messages": [*c.data["messages"], {"role": "assistant", "text": reply}]}
                c.version += 1
                turn.status = "completed"
                session.commit()
                return snapshot(c)
            if name == "revise_plan":
                from app.api.campaigns import revise_campaign_plan
                patches = []
                for patch in args['patches']:
                    fields = patch['changes']
                    if len({f['field'] for f in fields}) != len(fields):
                        raise ValueError('Duplicate edited fields')
                    patches.append({'asset_id': patch['asset_id'], 'changes': {f['field']: f['value'] for f in fields}})
                output = revise_campaign_plan(session, c, args['remove_asset_ids'], patches,
                                             settings=settings, client=client)
                if output['status'] == 'saved':
                    completed.append({'tool': name, 'version': output['version']})
                    return finish()
            elif name == "add_images":
                from app.api.campaigns import add_campaign_images
                output = add_campaign_images(session, c, settings, **args)
                completed.append({"tool": name, "version": output["version"]})
            elif name == "retrieve_context":
                from app.api.campaigns import retrieve_campaign_context
                output = recover_preparation(
                    lambda c=c, mode=mode: retrieve_campaign_context(session, c, settings, mode, client=client),
                    client, session, turn, name)
                completed.append({"tool": name, "version": c.version})
            elif name == "extract_requirements":
                needs_action = True
                from app.brand.campaign_requirements import (
                    extract_requirements,
                    requirement_revision,
                )
                route = "extend" if args["addition"] and c.data.get("generation", {}).get("drafts") else "campaign"
                requirements = recover_preparation(lambda c=c, route=route: extract_requirements(client, settings, c.brand_id, identifier, {
                    "conversation": c.data.get("messages", []), "latest_instruction": body.text,
                    "current_decisions": current_decisions(c),
                    "route": route, "previous_requirements": c.data.get("requirements"),
                    "current_plan": c.data.get("plans", [])[-1:],
                    "current_drafts": c.data.get("generation", {}).get("drafts", [])}), client, session, turn, name)
                c.data = {**c.data, "requirements": requirements,
                    "requirements_revision": requirement_revision(c.data.get("messages", []), route),
                    "requirements_history": [*c.data.get("requirements_history", []), requirements]}
                c.version += 1
                session.commit()
                output = {"version": c.version, "requirements": requirements, "unknowns": requirements["unknowns"], "issues": requirements.get("issues", [])}
                completed.append({"tool": name, "version": c.version})
            elif name == "list_brand_images":
                from app.api.campaigns import image_catalog
                _, catalog = image_catalog(session, c, settings)
                output = {"version": c.version, "images": catalog}
            elif name == "change_image":
                from app.api.campaigns import ImageAttachment, save_draft_image
                output = save_draft_image(identifier, args["asset_id"],
                    ImageAttachment(version=c.version, image_id=args["image_id"]), session, settings)
                completed.append({"tool": name, "version": output["version"], "image_id": args["image_id"]})
            elif name == "read_campaign":
                output = {'version': c.version, 'state': agent_state(c, full=True)}
            else:
                has_drafts = bool(c.data.get("generation", {}).get("drafts"))
                route = "revise" if name == "revise_draft" else "extend" if name == "propose_plan" and args["addition"] and has_drafts else "campaign"
                def process(c=c, args=args, route=route):
                    return process_message(identifier, Message(text=args["instruction"], version=c.version),
                        session, settings, forced_route=MessageRoute(route=route, target_asset_id=args.get("asset_id"), reply=""), internal=True)
                output = (recover_preparation(process, client, session, turn, name)
                          if name in {'propose_plan', 'answer_brand_question'} else process())
                completed.append({"tool": name, "version": output["version"]})
            if output.get('status') == 'blocked':
                needs_action = True
                inputs.append({'type': 'function_call_output', 'call_id': call['call_id'],
                               'output': json.dumps(output)})
                continue
            if name in {'change_image', 'revise_draft', 'add_images'}:
                needs_action = False
            turn.data = {**turn.data, "executions": [*turn.data["executions"], {"call_id": call["call_id"], "tool": name, "arguments": args, "version": output["version"]}]}
            session.commit()
            if name == 'retrieve_context' and (output.get('pending_question') or output.get('status') == 'conflicts_reviewed'):
                return finish()
            if name == "propose_plan":
                latest = output.get("plans", [])[-1:]
                questions = latest[0].get("questions", []) if latest else []
                if questions and not output.get('pending_question'):
                    from app.brand.clarifications import pending_plan_question
                    question = pending_plan_question(questions[0])
                    c = load(session, identifier)
                    c.data = {**c.data, "pending_question": question,
                        "questions": [*c.data.get("questions", []), question],
                        "messages": [*c.data.get("messages", []), {"role": "assistant", "text": question["text"], "question": question}]}
                    c.version += 1
                return finish()
            if name == "answer_brand_question" or (name == "revise_draft" and output.get("pending_question")):
                return finish()
            # Services persist complete evidence and drafts. Selection needs their
            # routing state and success receipts, never another full snapshot.
            if name not in {'list_brand_images', 'read_campaign'}:
                session.expire_all()
                output = tool_receipt(load(session, identifier), name, args, output)
            inputs.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(output)})
        raise ValueError("Agent tool limit reached")
    except Exception as exc:
        session.rollback()
        # Failed preparation must not fabricate a plan from unreviewed requirements.
        c = load(session, identifier)
        trace = list(c.data.get('trace', []))
        if trace and trace[-1].get('status') == 'running':
            trace[-1] = {**trace[-1], 'status': 'failed', 'error': str(exc)}
        c.data = {**c.data, 'planning_error': str(exc), 'trace': trace,
                  'agent_last_turn': {'id': turn_id, 'completed': completed}}
        if c.state not in {'awaiting_approval', 'approved', 'ready', 'generation_error'}:
            c.state = 'planning_error'
        turn.status = "failed"
        turn.data = {**turn.data, "error": str(exc)}
        session.commit()
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raise HTTPException(
            exc.status_code if isinstance(exc, HTTPException) else getattr(exc, "status", 502),
            f"Agent turn stopped: {detail}. Completed actions retained: {completed}",
        ) from exc
    finally:
        session.execute(delete(CampaignLease).where(CampaignLease.owner == turn_id))
        session.commit()
        client.close()
