"""Opt-in strategy plus channel outlines, merged before the ordinary verifier."""
import json
from copy import deepcopy
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from app.brand.campaign_requirements import require_deliverables
from app.brand.retrieval import RetrievalError
from app.llm.context import RunContext
from app.llm.parallel import run_jobs
from app.llm.prompt_cache import prompt_cache_key
from app.llm.prompt_payloads import lean_plan_context, planning_prompt


class StrategyAsset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    audience: str
    timing: str | None
    publish_after_asset_ids: list[str]


class CampaignStrategy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str
    assumptions: list[str]
    constraints: list[str]
    assets: list[StrategyAsset]


def task_from_request(request):
    content = request.input[-1]['content']
    payload = json.loads(content)
    return {k: v for k, v in payload.items() if k != 'brand_context'}


def channel_context(context, requirements, ids):
    selected = deepcopy(context)
    selected['requirements'] = {**requirements, 'assets': [a for a in requirements['assets'] if a['id'] in ids]}
    selected['asset_packages'] = {k: v for k, v in selected.get('asset_packages', {}).items() if k in ids}
    # Context-level evidence is shared; asset-specific originals come only from retained packages.
    return selected


def run_campaign_plan(client, settings, campaign, request, requirements, context, output_model):
    if (not settings.magi_parallel_planning or not requirements or
            len({a['format'] for a in requirements['assets']}) < 2):
        result = client.run(request, output_model=output_model)
        campaign.data = {**campaign.data, 'parallel_planning': {
            'version': 'parallel_plan_v1', 'mode': 'serial', 'run_ids': [result.run_id]}}
        return result
    task = task_from_request(request)
    strategy_context = deepcopy(context)
    strategy_context.pop('asset_packages', None)
    # Strategy uses original global/factual evidence, not channel production mechanics.
    pool = {p['chunk_id']: p for package in context.get('asset_packages', {}).values()
            for p in [*package.get('evidence', []), *package.get('rule_pack', {}).get('evidence', [])]}
    pool.update({p['chunk_id']: p for p in context.get('evidence', [])})
    from app.brand.evidence_scope import global_source_ids
    protected = {ref for f in campaign.data.get('conflicts', []) for ref in f.get('source_ids', [])}
    ids = global_source_ids(list(pool.values()), protected)
    strategy_context['evidence'] = [p for key, p in pool.items() if key in ids]
    strategy_request = request.model_copy(update={
        'operation': 'campaign_plan_strategy', 'max_output_tokens': 6000,
        'instructions': request.instructions +
            ' Establish only shared strategy and the complete asset calendar. Return each required ID '
            'exactly once. Preserve explicit audiences/dates; propose coherent timing only where allowed. '
            'Include cross-channel publication dependencies. No hooks or draft copy.',
        'input': planning_prompt(lean_plan_context(strategy_context), task),
        'prompt_cache_key': prompt_cache_key(campaign.brand_id, 'campaign_plan_strategy')})
    strategy_result = client.run(strategy_request, output_model=CampaignStrategy)
    strategy = strategy_result.parsed.model_dump()
    expected = [a['id'] for a in requirements['assets']]
    if len(strategy['assets']) != len(expected) or {a['id'] for a in strategy['assets']} != set(expected):
        raise RetrievalError('Parallel plan strategy changed required assets')
    calendar = {a['id']: a for a in strategy['assets']}
    for a in requirements['assets']:
        from app.brand.plan_checks import proposed
        for field in ('audience', 'timing'):
            if not proposed(a, field):
                calendar[a['id']][field] = a.get(field)
        if any(i not in expected or i == a['id'] for i in calendar[a['id']]['publish_after_asset_ids']):
            raise RetrievalError('Parallel plan strategy returned invalid dependencies')
    requests = []
    for channel in sorted({a['format'] for a in requirements['assets']}):
        ids = {a['id'] for a in requirements['assets'] if a['format'] == channel}
        scoped = channel_context(context, requirements, ids)
        messages = planning_prompt(lean_plan_context(scoped), task)
        messages.append({'role': 'developer', 'content': json.dumps({
            'shared_strategy': strategy, 'target_asset_ids': sorted(ids),
            'directive': 'Outline ONLY target_asset_ids, preserving the strategy calendar, audiences and dependencies. '
                         'Return no changes to shared strategy. All other assets are read-only dependency context.'}, sort_keys=True)})
        requests.append((channel, ids, request.model_copy(update={
            'operation': 'campaign_plan_channel', 'input': messages,
            'context': RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id),
            'prompt_cache_key': prompt_cache_key(campaign.brand_id, 'campaign_plan_channel_' + channel)})))
    outcomes = run_jobs(client, [lambda child, r=r: child.run(r, output_model=output_model)
                                 for _, _, r in requests])
    assets, assumptions, constraints, questions, runs = {}, list(strategy['assumptions']), list(strategy['constraints']), [], [strategy_result.run_id]
    for (channel, ids, _), (result, error) in zip(requests, outcomes):
        if error is not None:
            raise error
        value = result.parsed.model_dump()
        if value['questions'] and not value['assets']:
            questions.extend(value['questions'])
            runs.append(result.run_id)
            continue
        if len(value['assets']) != len(ids) or {a['id'] for a in value['assets']} != ids:
            raise RetrievalError('Parallel channel plan changed required assets')
        for asset in value['assets']:
            if asset['format'] != channel:
                raise RetrievalError('Parallel channel plan changed asset format')
            shared = calendar[asset['id']]
            asset.update(audience=shared['audience'], timing=shared['timing'],
                         publish_after_asset_ids=list(dict.fromkeys([
                             *shared['publish_after_asset_ids'], *asset.get('publish_after_asset_ids', [])])))
            assets[asset['id']] = asset
        assumptions.extend(value['assumptions'])
        constraints.extend(value['constraints'])
        questions.extend(value['questions'])
        runs.append(result.run_id)
    # Global dependency conflicts remain visible to the normal verifier/repair flow.
    merged = {'goal': strategy['goal'], 'assumptions': list(dict.fromkeys(assumptions)),
              'constraints': list(dict.fromkeys(constraints)), 'questions': questions[:1],
              'assets': [] if questions else [assets[key] for key in expected]}
    require_deliverables(merged, requirements)
    parsed = output_model.model_validate({k: v for k, v in merged.items() if k != 'deferred_candidate'})
    campaign.data = {**campaign.data, 'parallel_planning': {'version': 'parallel_plan_v1', 'mode': 'parallel', 'run_ids': runs}}
    return SimpleNamespace(run_id=strategy_result.run_id, parsed=parsed)
