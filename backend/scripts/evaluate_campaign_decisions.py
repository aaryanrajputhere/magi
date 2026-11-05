"""Replay a saved campaign audit with the real model, isolated from campaign writes.

PYTHONPATH=backend .venv/bin/python backend/scripts/evaluate_campaign_decisions.py \
  --campaign /tmp/magi-audit-error.json --runs /tmp/magi-audit-error-runs.json

Provider attempts are logged in a temporary database. No campaign is changed.
"""
import argparse
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.campaigns import validate_campaign_plan
from app.brand.conflicts import review_conflicts
from app.brand.plan_audit import PlanNeedsDecision
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import TrackedOpenAI
from app.models import LLMRun


def evaluate(campaign_path, runs_path):
    campaign_data = json.loads(Path(campaign_path).read_text())
    runs = json.loads(Path(runs_path).read_text())
    audit = next(r for r in runs if r['operation'] == 'campaign_plan_check')
    payload = json.loads(audit['request']['input'])
    candidate = payload['candidate_plan']
    evidence = payload['source_evidence']
    reviewed = {'evidence': evidence, 'requirements': payload.get('requirements'),
                'tracks': payload.get('tracks')}
    campaign = SimpleNamespace(id=campaign_data['id'], brand_id=campaign_data['brand_id'], data=campaign_data)
    settings = Settings(_env_file=Path(__file__).resolve().parents[2] / '.env')
    report = {'model': settings.magi_retrieval_planner_model, 'campaign_id': campaign.id}
    with TemporaryDirectory(prefix='magi-decision-eval-') as root:
        engine = create_database_engine(f'sqlite:///{root}/runs.db')
        initialize_database(engine)
        client = TrackedOpenAI(engine, settings)
        try:
            findings = review_conflicts(client, settings, campaign, campaign_data['messages'], evidence, candidate)
            questions = ' '.join(f['question'] for f in findings).lower()
            report['conflict_questions'] = [f['question'] for f in findings]
            report['fedramp_not_repeated'] = 'fedramp' not in questions
            report['customer_conflict_detected'] = any(
                'vaskell' in (f['question'] + f['explanation']).lower() for f in findings)
            try:
                validate_campaign_plan(client, settings, campaign, campaign_data['messages'], reviewed, candidate)
                report['audit_customer_question'] = False
            except PlanNeedsDecision as decision:
                report['audit_questions'] = [f['question'] for f in decision.findings]
                report['audit_customer_question'] = any('vaskell' in (
                    f['question'] + f['explanation']).lower() for f in decision.findings)
                report['audit_fedramp_not_repeated'] = all(
                    'fedramp' not in f['question'].lower() for f in decision.findings)
            # A matching approved status must pass without demanding that the goal narrate the correction.
            clean = copy.deepcopy(candidate)
            clean['goal'] = 'Generate qualified demo requests from regulated security teams'
            clean['assumptions'] = []
            clean['constraints'] = ['Use the selected pursuing-authorization wording.']
            clean['assets'] = [dict(candidate['assets'][0],
                purpose='Explain the selected certification status', angle='Current certification status',
                hook='Wickhold is pursuing FedRAMP Moderate authorization.', cta='Book a demo')]
            clean_review = {**reviewed, 'requirements': None}
            checked = validate_campaign_plan(client, settings, campaign, campaign_data['messages'], clean_review, clean)
            report['corrected_status_passes'] = checked == clean
        except (RuntimeError, ValueError) as exc:
            report['error'] = str(exc)
        finally:
            with Session(engine) as session:
                records = list(session.scalars(select(LLMRun).order_by(LLMRun.started_at)))
                report['model_outputs'] = [{'operation': r.operation, 'output': [
                    c.get('text', '') for o in (r.response_body or {}).get('output', [])
                    for c in o.get('content', []) if c.get('type') == 'output_text']}
                    for r in records]
                report['runs'] = [{'operation': r.operation, 'status': r.status,
                    'output_tokens': r.output_tokens, 'error': r.error,
                    'incomplete_details': (r.response_body or {}).get('incomplete_details')}
                    for r in records]
            client.close()
            engine.dispose()
    checks = ['fedramp_not_repeated', 'customer_conflict_detected', 'audit_customer_question',
              'audit_fedramp_not_repeated', 'corrected_status_passes']
    report['passed'] = all(report.get(key, False) for key in checks)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--runs', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    report = evaluate(args.campaign, args.runs)
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'model_outputs'}, indent=2), flush=True)
    raise SystemExit(0 if report['passed'] else 1)
