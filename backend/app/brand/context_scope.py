"""Select intact originals by scope; retain mandatory rules and dependencies."""
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from app.brand.decisions import current_decisions
from app.brand.retrieval import RetrievalError
from app.llm.client import ModelRequest
from app.llm.context import RunContext
from app.llm.prompt_cache import cached_prompt, prompt_cache_key
from app.llm.prompt_payloads import prompt_decisions


class SourceScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_ids: list[str]
    full_review_required: bool
    reason: str = Field(min_length=1)


class PlanningSourceScope(SourceScope):
    """Selection certainty is independent of invalidating a prior review."""
    source_selection_complete: bool


def intent(messages):
    """Status messages and stored UI/audit snapshots cannot change user intent."""
    return [m.get('text', '') for m in messages if m.get('role') == 'user']


def reviewed_choices_only(campaign, prior, messages):
    """An answer to an already-checked option supplies intent, not new facts."""
    previous = (prior or {}).get('user_messages', [])
    current = intent(messages)
    if not previous or current[:len(previous)] != previous or len(current) <= len(previous):
        return False
    approved = {q.get('answer') for q in campaign.data.get('questions', [])
                if q.get('conflict_id') and q.get('answer') in q.get('options', [])}
    return all(text in approved for text in current[len(previous):])


def mandatory_sources(pool, formats):
    from app.brand.evidence_scope import select_stage
    eligible, _ = select_stage(list(pool.values()), formats)
    factual = ('/products/', '/research/', '/case-studies/', '/archive/', '/examples/')
    return {key for key in eligible if not any(part in pool[key].get('relative_path', '').lower()
                                              for part in factual)}


def select_scope(client, settings, campaign, messages, evidence, *, operation, prior=None):
    pool = {e['chunk_id']: e for e in evidence}
    requirements = campaign.data.get('requirements') or {}
    decisions = prompt_decisions([d for d in current_decisions(campaign)
        if d.get('origin') != 'reviewed_rule'])
    source_hashes = {key: hashlib.sha256(p['content'].encode()).hexdigest() for key, p in pool.items()}
    task = {'user_messages': intent(messages), 'requirements': {k: v for k, v in requirements.items()
        if k not in {'run_id', 'extraction_run_id'}}, 'decisions': decisions,
        'previous_receipt': ({k: v for k, v in prior.items() if k != 'source_snapshot'} if prior else None),
        'changed_source_ids': [key for key, value in source_hashes.items()
            if prior and prior.get('source_snapshot', {}).get(key) != value],
        'removed_source_ids': sorted(set((prior or {}).get('source_snapshot', {})) - pool.keys())}
    scope_version = 'planning_scope_v3' if operation == 'scope_campaign_plan' else 'stage_scope_v1'
    signature = hashlib.sha256(json.dumps([scope_version, operation, source_hashes, task], sort_keys=True).encode()).hexdigest()
    cached = campaign.data.get('context_scopes', {}).get(signature)
    if cached is not None:
        return set(cached['source_ids']), cached['full_review_required']
    # The selector sees a source inventory, never another copy of the corpus.
    aliases = {f's{i}': key for i, key in enumerate(sorted(pool))}
    reverse = {key: ref for ref, key in aliases.items()}
    catalog = [{'ref': reverse[key], **{k: p[k] for k in ('relative_path', 'heading_path') if k in p}}
               for key, p in sorted(pool.items())]
    request = ModelRequest(operation=operation, model=settings.magi_retrieval_planner_model,
        instructions='Select ORIGINAL passages needed for the supplied task from the inventory. '
        'For scope_campaign_changes select evidence affected by new user answers, changed requirements '
        'or source versions, including counterpart restrictions and previous conflicts. For '
        'scope_campaign_plan select factual support and examples needed to outline the requested assets. '
        'Applicable planning rules are retained separately. Do not select detailed grammar or image-production instructions unless required to resolve a planning choice. '
        'Include definitions, qualifications and exceptions. The inventory is not factual proof. '
        'If scope cannot be established from this inventory, or changes could invalidate the whole '
        'prior review, set full_review_required=true. Never declare omitted sources compliant. '
        + ('For scope_campaign_plan, full_review_required describes prior review invalidation ONLY. '
           'Set source_selection_complete=true when the selected factual support and examples, '
           'together with separately retained planning rules, cover the outline task. '
           'Set it false if the inventory cannot establish which factual sources are needed; '
           'this retains the full pool. A central-message change alone does not make source selection incomplete. '
           if operation == 'scope_campaign_plan' else '') +
        'source_ids must contain only catalog ref values (such as s0), not UUIDs or IDs from task history. Do not rewrite source text.',
        input=cached_prompt({'catalog': catalog, 'task': task}, ('catalog',)), cache_prefix_messages=1,
        prompt_cache_key=prompt_cache_key(campaign.brand_id, operation), max_output_tokens=5000,
        context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id))
    for attempt in range(2):
        result = client.run(request, output_model=(PlanningSourceScope
            if operation == 'scope_campaign_plan' else SourceScope))
        selected = result.parsed
        unknown = sorted(set(selected.source_ids) - aliases.keys())
        if not unknown:
            selected = selected.model_copy(update={'source_ids': [aliases[ref] for ref in selected.source_ids]})
            break
        if attempt:
            raise RetrievalError('Context scope cites an unknown original passage after correction')
        request = request.model_copy(update={'input': cached_prompt({
            'catalog': catalog, 'task': task, 'correction': {
                'invalid_refs': unknown, 'instruction': 'Return the complete selection using only catalog ref values.'}}, ('catalog',))})
    formats = {a['format'] for a in requirements.get('assets', [])}
    expand = (not getattr(selected, 'source_selection_complete', True)
              if operation == 'scope_campaign_plan' else selected.full_review_required)
    refs = set(pool) if expand else set(selected.source_ids) | mandatory_sources(pool, formats)
    # Findings being reconsidered and their counterparts cannot disappear.
    for finding in campaign.data.get('conflicts', []):
        refs.update(key for key in finding.get('source_ids', []) if key in pool)
    while True:
        dependencies = {key for ref in refs for key in pool[ref].get('dependency_ids', [])}
        if not dependencies <= pool.keys():
            raise RetrievalError('Required source dependency unavailable during context selection')
        if dependencies <= refs:
            break
        refs.update(dependencies)
    campaign.data = {**campaign.data, 'context_scopes': {**campaign.data.get('context_scopes', {}),
        signature: {'version': scope_version, 'source_ids': sorted(refs),
                    'source_selection_complete': not expand,
                    'full_review_required': selected.full_review_required,
                    'reason': selected.reason, 'run_id': result.run_id}}}
    return refs, selected.full_review_required


def planning_context(client, settings, campaign, messages, context):
    """Narrow a large plan packet before assembly; sources remain verbatim."""
    from copy import deepcopy

    from app.llm.asset_context_router import source_pool
    pool = source_pool(context.get('asset_packages', {}))
    pool.update({p['chunk_id']: p for p in context.get('evidence', [])})
    from app.brand.evidence_scope import select_stage
    eligible, audit = select_stage(list(pool.values()), {a['format'] for a in (campaign.data.get('requirements') or {}).get('assets', [])})
    if sum(len(p.get('content', '')) for p in pool.values()) <= 30000:
        refs = eligible
    else:
        refs, _ = select_scope(client, settings, campaign, messages, list(pool.values()),
                               operation='scope_campaign_plan')
    # Explicit selector choices and dependencies can override normal deferral.
    audit['retained_ids'] = sorted(refs)
    audit['deferred_ids'] = sorted(pool.keys() - refs)
    for key in refs - eligible:
        audit['reasons'][key] = 'retained: explicit selector choice or dependency'
    campaign.data = {**campaign.data, 'planning_scope': audit}
    result = deepcopy(context)
    result['evidence'] = [p for p in result.get('evidence', []) if p['chunk_id'] in refs]
    for package in result.get('asset_packages', {}).values():
        package['evidence'] = [p for p in package.get('evidence', []) if p['chunk_id'] in refs]
        pack = package.get('rule_pack') or {}
        pack['evidence'] = [p for p in pack.get('evidence', []) if p['chunk_id'] in refs]
        pack.pop('writing_evidence', None)
    return result
