"""Offline payload replay; no API calls and no changes to saved campaigns."""
import argparse
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from app.brand.evidence_scope import focused_rules, select_stage
from app.llm.prompt_payloads import stable_source_ref
from app.research import research_needs


def unpack(request):
    payload = {}
    for message in request.get('input', []):
        if not isinstance(message, dict):
            continue
        content = message.get('content', '')
        text = content if isinstance(content, str) else '\n'.join(p.get('text', '') for p in content)
        try:
            item = json.loads(text)
        except ValueError:
            continue
        if isinstance(item, dict):
            payload.update(item)
    return payload


def measure(runs, campaign):
    results = []
    formats = {a['format'] for a in (campaign.get('requirements') or {}).get('assets', [])}
    findings = []
    for run in runs:
        operation = run['operation']
        request = run.get('request') or {}
        payload = unpack(request)
        before = deepcopy(payload)
        scope = None
        if operation == 'campaign_plan':
            evidence = payload['original_sources']
            ids, scope = select_stage(evidence, formats)
            payload['original_sources'] = [p for p in evidence if p['chunk_id'] in ids]
            aliases = {p['ref']: stable_source_ref(p['chunk_id']) for p in payload['original_sources']}
            for p in payload['original_sources']:
                p['ref'] = stable_source_ref(p['chunk_id'])
            context = payload['brand_context']
            context['source_groups'] = {k: [aliases[r] for r in refs if r in aliases]
                                       for k, refs in context.get('source_groups', {}).items()}
            if not research_needs(SimpleNamespace(data=campaign), campaign.get('requirements') or {}):
                context['web_research'] = {'status': 'skipped', 'sources': [],
                                          'reason': 'No concrete unmet external factual need.'}
        elif operation == 'campaign_plan_verification':
            packet = payload['governing_rules']
            evidence = packet['evidence']
            ids, scope = select_stage(evidence, formats)
            if findings:
                refs = {ref for d in findings for ref in d.get('rule_refs', [])}
                focused, reason = focused_rules({'all': {'evidence': [p for p in evidence if p['chunk_id'] in ids]}}, refs)
                ids = {p['chunk_id'] for p in focused['all']['evidence']}
                scope['confirmation_reason'] = reason
            packet['evidence'] = [p for p in evidence if p['chunk_id'] in ids]
            aliases = {p['ref']: stable_source_ref(p['chunk_id']) for p in packet['evidence']}
            for p in packet['evidence']:
                p['ref'] = stable_source_ref(p['chunk_id'])
            packet['source_groups'] = {k: [aliases[r] for r in refs if r in aliases]
                                       for k, refs in packet.get('source_groups', {}).items()}
            text = '\n'.join(c.get('text', '') for item in (run.get('response') or {}).get('output', []) for c in item.get('content', []))
            try:
                findings = json.loads(text).get('defects', [])
            except ValueError:
                findings = []
        elif operation == 'campaign_conflict_find':
            # Recorded passages use source aliases, not chunk IDs; stable offline
            # keys are for accounting only and never change the actual campaign.
            evidence = [{'chunk_id': p['ref'], **p} for p in payload['passages']]
            ids, scope = select_stage(evidence, formats, defer_execution=False)
            payload['passages'] = [p for p in payload['passages'] if p['ref'] in ids]
        else:
            continue
        def bodies(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == 'content' and isinstance(item, str):
                        yield item
                    else:
                        yield from bodies(item)
            elif isinstance(value, list):
                for item in value:
                    yield from bodies(item)
        original_bodies = set(bodies(before))
        assert set(bodies(payload)) <= original_bodies
        results.append({'run_id': run['id'], 'operation': operation,
            'before_payload_chars': len(json.dumps(before, ensure_ascii=False)),
            'after_payload_chars': len(json.dumps(payload, ensure_ascii=False)), 'scope': scope})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', required=True)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = measure(json.loads(Path(args.runs).read_text()), json.loads(Path(args.campaign).read_text()))
    Path(args.output).write_text(json.dumps({'note': 'Offline character counts, not measured token, latency or billing savings.', 'requests': result}, indent=2))
    for row in result:
        print(row['operation'], row['before_payload_chars'], '->', row['after_payload_chars'])
