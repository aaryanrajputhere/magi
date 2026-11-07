"""Offline payload measurements, or isolated paid planning/verification comparisons.

Default makes no provider calls. --live runs three isolated cold/warm pairs per
configuration against an exported campaign, recording runs in a NEW SQLite DB.
Never reads or writes the application database or calls campaign mutation APIs.
"""
import argparse
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.llm.client import ModelRequest, TrackedOpenAI
from app.llm.context import RunContext
from app.llm.prompt_payloads import semantic_projection
from app.models import LLMRun


def unpack(request):
    value = request.get('input', {})
    if isinstance(value, str):
        return json.loads(value)
    payload = {}
    for message in value:
        content = message.get('content', '')
        if not isinstance(content, str):
            content = ''.join(p.get('text', '') for p in content)
        try:
            item = json.loads(content)
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict):
            payload.update(item)
    return payload


def offline(runs, campaign):
    from app.brand.embeddings import count_tokens
    from app.brand.evidence_scope import select_stage
    formats = {a['format'] for a in campaign.get('requirements', {}).get('assets', [])}
    protected = {i for f in campaign.get('conflicts', []) for i in f.get('source_ids', [])}
    measured = []
    for run in runs:
        before = unpack(run['request'])
        after = semantic_projection(before)
        scope = None
        if run['operation'] == 'campaign_plan':
            evidence = after['original_sources']
            ids, scope = select_stage(evidence, formats, protected=protected)
            after['original_sources'] = [p for p in evidence if p['chunk_id'] in ids]
        elif run['operation'] == 'campaign_plan_verification':
            rules = after['governing_rules']
            ids, scope = select_stage(rules['evidence'], formats, protected=protected)
            rules['evidence'] = [p for p in rules['evidence'] if p['chunk_id'] in ids]
        def size(value):
            return count_tokens(json.dumps(value, ensure_ascii=False, sort_keys=True))
        measured.append({'operation': run['operation'], 'provider_input_tokens': run.get('input_tokens'),
            'before_payload_tokens_estimate': size(before), 'after_payload_tokens_estimate': size(after),
            'before_fields': {k: size(v) for k, v in before.items()},
            'after_fields': {k: size(v) for k, v in after.items()},
            'scope': scope})
    return {'mode': 'offline', 'tokenizer': 'cl100k_base (payload estimate, not provider billing)',
            'note': 'Source selection receipts and model quality/latency require live evaluation. '
                    'Filtered source-group memberships are not replayable request bodies.', 'requests': measured}


class EvaluationClient(TrackedOpenAI):
    def __init__(self, engine, settings, namespace):
        super().__init__(engine, settings)
        self.namespace = namespace

    def run(self, request, **kwargs):
        request = request.model_copy(update={'instructions':
            'Evaluation namespace ' + self.namespace + '.\n' + request.instructions})
        return super().run(request, **kwargs)

    def fork(self):
        return EvaluationClient(self.engine, self.settings, self.namespace)


def live(runs, exported, database, pairs=3):
    from app.api.campaigns import PlanOutline, repair_deliverables
    from app.brand.context_scope import planning_context
    from app.brand.parallel_planning import run_campaign_plan
    from app.brand.plan_verification import verify_plan
    from app.llm.prompt_payloads import lean_plan_context, planning_prompt
    path = Path(database).resolve()
    if path.exists():
        raise ValueError('Evaluation database must be new; refuse to overwrite any database')
    engine = create_engine('sqlite:///' + str(path))
    Base.metadata.create_all(engine)
    baseline = next(r for r in runs if r['operation'] == 'campaign_plan')['request']
    task = {k: v for k, v in unpack(baseline).items() if k not in ('original_sources', 'brand_context')}
    saved = exported['plans'][-1]
    rows = []
    for configuration in ('serial', 'parallel'):
        for pair in range(pairs):
            namespace = str(uuid4())  # shared only by this cold/warm pair
            for temperature in ('cold', 'warm'):
                settings = Settings(magi_parallel_verification=configuration == 'parallel',
                                    magi_parallel_planning=configuration == 'parallel')
                campaign = SimpleNamespace(id=str(uuid4()), brand_id=exported['brand_id'], data=deepcopy(exported))
                campaign.data.pop('verification_checks', None)
                campaign.data.pop('context_scopes', None)
                campaign.data['planning_scope'] = {}
                context = {'requirements': campaign.data['requirements'],
                    'asset_packages': deepcopy(saved['asset_packages']),
                    'resolved_review': deepcopy(campaign.data.get('conflict_coverage', {}))}
                client = EvaluationClient(engine, settings, namespace)
                started = perf_counter()
                result = {'configuration': configuration, 'pair': pair, 'cache_trial': temperature,
                          'campaign_id': campaign.id}
                try:
                    context = planning_context(client, settings, campaign, campaign.data['messages'], context)
                    request = ModelRequest(operation='campaign_plan', model=baseline['model'],
                        instructions=baseline['instructions'], max_output_tokens=12000,
                        input=planning_prompt(lean_plan_context(context), task), cache_prefix_messages=1,
                        context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id))
                    plan = run_campaign_plan(client, settings, campaign, request,
                        campaign.data['requirements'], context, PlanOutline).parsed.model_dump()
                    plan = repair_deliverables(client, settings, campaign, plan, campaign.data['requirements'], context)
                    plan.update(requirements=campaign.data['requirements'], asset_packages=deepcopy(saved['asset_packages']))
                    plan = verify_plan(client, settings, campaign, campaign.data['messages'], plan)
                    result.update(status='completed', plan=plan,
                                  review_status=plan.get('plan_review', {}).get('status'))
                except Exception as exc:  # noqa: BLE001 - evaluation retains failed trials
                    result.update(status='failed', error=str(exc))
                finally:
                    client.close()
                result['elapsed_seconds'] = round(perf_counter() - started, 3)
                with Session(engine) as session:
                    recorded = session.scalars(select(LLMRun).where(
                        LLMRun.context['campaign_id'].as_string() == campaign.id)).all()
                    result['usage'] = {name: sum(getattr(r, name) or 0 for r in recorded) for name in
                        ('input_tokens', 'cached_input_tokens', 'cache_write_tokens', 'output_tokens', 'reasoning_tokens', 'duration_ms')}
                rows.append(result)
                print(configuration, pair, temperature, result['status'], result['elapsed_seconds'], flush=True)
                if result['status'] == 'failed':
                    with Session(engine) as session:
                        recorded = list(session.scalars(select(LLMRun).where(
                            LLMRun.context['campaign_id'].as_string() == campaign.id)))
                    if any(r.status in ('configuration_error', 'connection_error', 'timeout') or
                           (r.error or {}).get('type') in ('APIConnectionError', 'APITimeoutError') for r in recorded):
                        engine.dispose()
                        return {'mode': 'live', 'trials': rows, 'stopped': 'Provider unavailable; inspect saved evaluation runs before retrying.'}
    engine.dispose()
    return {'mode': 'live', 'segment': 'planning and verification (not full campaign intake)',
            'note': 'Fresh namespace isolates each cold/warm pair; provider usage is authoritative. '
                    'Review saved plans against the campaign quality checklist before enabling rollout flags.',
            'trials': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', required=True)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--pairs', type=int, default=3, choices=range(1, 4))
    parser.add_argument('--live', action='store_true', help='Make PAID model calls in isolated snapshots')
    parser.add_argument('--database', help='NEW SQLite file required for --live')
    args = parser.parse_args()
    if args.live and not args.database:
        parser.error('--live requires --database pointing to a new local file')
    runs = json.loads(Path(args.runs).read_text())
    campaign = json.loads(Path(args.campaign).read_text())
    result = live(runs, campaign, args.database, args.pairs) if args.live else offline(runs, campaign)
    result['generated_at'] = datetime.now(UTC).isoformat()
    Path(args.output).write_text(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    main()
