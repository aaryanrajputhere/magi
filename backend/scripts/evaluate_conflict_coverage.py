"""Opt-in paid semantic evaluation, isolated from campaign records.

PYTHONPATH=backend .venv/bin/python backend/scripts/evaluate_conflict_coverage.py --live
Without --live, prints the fixtures without making provider calls.
"""
import argparse
import json
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand.conflict_coverage import review_campaign
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import TrackedOpenAI
from app.models import LLMRun


def cases():
    return [
        ('hero_conflict', ('Use a working-room hero on the product page. '
         'The GA-status exception only removes the early-access label.'),
         ['Product pages must use product UI in the hero. Working rooms are for role/problem pages.'], True),
        ('hero_compatible', 'Use a working-room hero for the platform engineering solutions page.',
         ['Product pages use product UI. Role/problem pages may use a working room.'], False),
        ('hero_exception', ('Use a working-room hero on the product page. Brand has approved '
         'a written exception to the product-UI hero rule for this page through launch day.'),
         ['Product pages must use product UI. Brand may grant a written page-specific exception.'], False),
        ('scheduling_conflict', 'Schedule the follow-up how-to X post for February 2.',
         ['Launch announcements are scheduled. All other X posts are posted live by DevRel.'], True),
        ('scheduling_paraphrase', 'Queue the tutorial follow-up on X to go out automatically on February 2.',
         ['Only launch announcements may be scheduled. Other X posts must be posted live.'], True),
        ('scheduling_compatible', 'Schedule the GA launch announcement on X for launch day.',
         ['Launch announcements are scheduled. All other X posts are posted live by DevRel.'], False),
        ('cross_query_claim', 'Plan a product launch post using approved claims.',
         ['The product prevents prompt injection from becoming an incident.',
          'Never claim that this product prevents prompt injection. Use limits what a compromised agent can do.'], True),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly authorize paid model calls')
    args = parser.parse_args()
    if not args.live:
        print(json.dumps(cases(), indent=2))
        return
    settings = Settings()
    results = []
    with TemporaryDirectory(prefix='magi-coverage-eval-') as directory:
        engine = create_database_engine(f'sqlite:///{directory}/evaluation.db')
        initialize_database(engine)
        client = TrackedOpenAI(engine, settings)
        try:
            for name, brief, passages, expected in cases():
                campaign = SimpleNamespace(id=name, brand_id='evaluation', data={})
                start = time.monotonic()
                try:
                    findings = review_campaign(client, settings, campaign,
                        [{'role': 'user', 'text': brief}],
                        [{'chunk_id': f'source-{i}', 'content': content}
                         for i, content in enumerate(passages)])
                    results.append({'case': name, 'expected_conflict': expected,
                        'detected': bool(findings), 'passed': bool(findings) == expected,
                        'findings': findings, 'seconds': time.monotonic() - start})
                except (RuntimeError, ValueError) as exc:
                    results.append({'case': name, 'passed': False, 'incomplete': str(exc),
                                    'seconds': time.monotonic() - start})
            with Session(engine) as session:
                runs = list(session.scalars(select(LLMRun)))
                print(json.dumps({'results': results,
                    'cost_usd': sum(r.estimated_cost_usd or 0 for r in runs),
                    'calls': len(runs)}, indent=2))
        finally:
            client.close()
            engine.dispose()
    if not all(r['passed'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
