"""Bounded conversational orchestration over campaign services.

Approval remains exclusively in the HTTP approval/execution handlers.
"""
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.llm.client import ModelRequest, TrackedOpenAI
from app.llm.context import RunContext
from app.memory import context as memory_context
from app.models.agent import AgentTurn, CampaignLease


def tool(name, description, properties):
    return {"type": "function", "name": name, "description": description,
            "strict": True, "parameters": {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}}


TOOLS = [
    tool("list_brand_images", "List eligible image IDs and metadata before selecting an image", {}),
    tool("change_image", "Save or remove one draft image. Use an eligible ID from list_brand_images; null removes it. This changes no copy.",
         {"asset_id": {"type": "string"}, "image_id": {"type": ["string", "null"]}}),
    tool("read_campaign", "Read current plan and saved drafts", {}),
    tool("propose_plan", "Propose or adjust a plan; additions require approval before generation",
         {"instruction": {"type": "string"}, "addition": {"type": "boolean"}}),
    tool("revise_draft", "Immediately edit one existing saved draft",
         {"instruction": {"type": "string"}, "asset_id": {"type": "string"}}),
    tool("answer_brand_question", "Answer using reviewed brand evidence through the campaign service",
         {"instruction": {"type": "string"}}),
    tool("finish_turn", "Finish with a concise confirmation or clarification; never write campaign copy",
         {"text": {"type": "string"}}),
]


class Instruction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    instruction: str = Field(min_length=1)

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

class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

ARGUMENTS = {"list_brand_images": Empty, "change_image": ImageChange, "read_campaign": Empty, "propose_plan": Proposal,
             "revise_draft": Revision, "answer_brand_question": Instruction, "finish_turn": Finish}


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
    turn = AgentTurn(id=turn_id, campaign_id=identifier, status="running", data={"request": body.text, "executions": []})
    session.add(turn)
    c.data = {**c.data, "messages": [*c.data.get("messages", []), {"id": body.request_id, "role": "user", "text": body.text}]}
    c.version += 1
    session.commit()
    inputs = [{"role": "user", "content": json.dumps({"request": body.text,
        "conversation": c.data.get("messages", [])[-12:], "state": snapshot(c), "brand_preferences": memory_context(session, c.brand_id)})}]
    client = TrackedOpenAI(session.get_bind(), settings)
    completed = []
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

        plans = c.data.get("plans", [])
        pending_question = bool(plans and plans[-1].get("questions"))
        approval_reply = body.text.strip().lower().rstrip(".! ") in {
            "yes", "confirm", "confirmed", "approve", "approved", "proceed", "go ahead"}
        if approval_reply and c.state in {"awaiting_approval", "approved"} and not pending_question:
            return finish("Review the saved plan and choose Approve & generate to create the drafts.")
        if pending_question:
            output = process_message(identifier, Message(text=body.text, version=c.version),
                session, settings, forced_route=MessageRoute(route="campaign", target_asset_id=None, reply=""), internal=True)
            completed.append({"tool": "propose_plan", "version": output["version"]})
            return finish()
        for _ in range(12):
            result = client.run(ModelRequest(operation="campaign_agent_step",
                model=settings.magi_retrieval_planner_model,
                instructions="You manage a brand campaign using tools. Perform requested actions, never merely describe them. Edits apply immediately; new assets (social posts, emails, blogs and landing pages) require a plan and explicit UI approval. Never approve or generate new drafts yourself. For compound requests execute edits first then propose additions. Resolve numbered posts by saved order. Ask when ambiguous. Preserve the full user request, especially publication dates and required statistics, in tool instructions. Use propose_plan for requests to create any campaign assets, including emails, not answer_brand_question. When adding assets to an existing generated campaign, set addition=true and preserve existing drafts. Resolve requests such as an email for it using the current campaign topic and audience. After a tool returns a plan with questions, finish the turn and wait for the user; do not propose another plan or answer those questions yourself. Never write unsaved campaign copy. Finish only after tool results confirm success. Image edits require list_brand_images then change_image; revise_draft edits text only and preserves the attachment. For compound requests perform both tools. A change_image result must confirm the saved image ID before you claim it changed. Never claim scheduling is available or offer to schedule; requested dates are draft planning dates only. When a plan needs clarification, relay its one essential question concisely without introducing alternative evidence or reports that the plan has not verified as eligible. Use finish_turn to finish.",
                input=inputs, tools=TOOLS, tool_choice="required", max_output_tokens=2000,
                context=RunContext(campaign_id=identifier, brand_id=c.brand_id)))
            inputs.extend(result.response.get("output", []))
            calls = [x for x in result.response.get("output", []) if x.get("type") == "function_call"]
            if len(calls) != 1:
                raise ValueError("Agent must choose exactly one tool")
            call = calls[0]
            name = call["name"]
            if call["name"] not in ARGUMENTS:
                raise ValueError("Unknown tool")
            args = ARGUMENTS[call["name"]].model_validate_json(call["arguments"]).model_dump()
            schema = next((t for t in TOOLS if t["name"] == name), None)
            if schema is None or set(args) != set(schema["parameters"]["properties"]):
                raise ValueError("Invalid agent tool arguments")
            session.expire_all()
            c = load(session, identifier)
            if name == "finish_turn":
                c.data = {**c.data, "agent_last_turn": {"id": turn_id, "completed": completed},
                    "messages": [*c.data["messages"], {"role": "assistant", "text": args["text"]}]}
                c.version += 1
                turn.status = "completed"
                session.commit()
                return snapshot(c)
            if name == "list_brand_images":
                from app.api.campaigns import image_catalog
                _, catalog = image_catalog(session, c, settings)
                output = {"version": c.version, "images": catalog}
            elif name == "change_image":
                from app.api.campaigns import ImageAttachment, save_draft_image
                output = save_draft_image(identifier, args["asset_id"],
                    ImageAttachment(version=c.version, image_id=args["image_id"]), session, settings)
                completed.append({"tool": name, "version": output["version"], "image_id": args["image_id"]})
            elif name == "read_campaign":
                output = snapshot(c)
            else:
                has_drafts = bool(c.data.get("generation", {}).get("drafts"))
                route = "revise" if name == "revise_draft" else "extend" if name == "propose_plan" and args["addition"] and has_drafts else "campaign"
                output = process_message(identifier, Message(text=args["instruction"], version=c.version),
                    session, settings, forced_route=MessageRoute(route=route, target_asset_id=args.get("asset_id"), reply=""), internal=True)
                completed.append({"tool": name, "version": output["version"]})
            turn.data = {**turn.data, "executions": [*turn.data["executions"], {"call_id": call["call_id"], "tool": name, "arguments": args, "version": output["version"]}]}
            session.commit()
            if name in {"propose_plan", "answer_brand_question"}:
                return finish()
            inputs.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(output)})
        raise ValueError("Agent tool limit reached")
    except Exception as exc:
        session.rollback()
        turn.status = "failed"
        turn.data = {**turn.data, "error": str(exc)}
        session.commit()
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raise HTTPException(
            exc.status_code if isinstance(exc, HTTPException) else 502,
            f"Agent turn stopped: {detail}. Completed actions retained: {completed}",
        ) from exc
    finally:
        session.execute(delete(CampaignLease).where(CampaignLease.owner == turn_id))
        session.commit()
        client.close()
