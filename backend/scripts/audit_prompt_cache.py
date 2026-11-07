"""Summarize saved inspector traces without making model calls.

Usage: python backend/scripts/audit_prompt_cache.py before.json [after.json]
Embedding tokens and missing provider cache counters are excluded from hit rates.
Durations are summed request durations, not wall time when requests overlap.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


def summarize(runs):
    groups = defaultdict(lambda: {'requests': 0, 'input_tokens': 0, 'output_tokens': 0,
        'reasoning_tokens': 0, 'cache_measured_input_tokens': 0, 'cached_input_tokens': 0, 'cache_write_tokens': 0,
        'requests_without_cache_usage': 0, 'requests_without_write_usage': 0,
        'summed_duration_ms': 0, 'estimated_cost_usd': Decimal(0), 'requests_without_cost': 0})
    for run in runs:
        group = groups[run['operation']]
        group['requests'] += 1
        group['input_tokens'] += run.get('input_tokens') or 0
        group['output_tokens'] += run.get('output_tokens') or 0
        group['summed_duration_ms'] += run.get('duration_ms') or 0
        usage = (run.get('response') or {}).get('usage') or {}
        group['reasoning_tokens'] += (usage.get('output_tokens_details') or {}).get('reasoning_tokens') or 0
        details = usage.get('input_tokens_details') or {}
        cached = details.get('cached_tokens', run.get('cached_input_tokens'))
        inputs = run.get('input_tokens')
        if isinstance(cached, int) and isinstance(inputs, int) and inputs > 0 and 0 <= cached <= inputs:
            group['cached_input_tokens'] += cached
            group['cache_measured_input_tokens'] += inputs
        else:
            group['requests_without_cache_usage'] += 1
        writes = details.get('cache_write_tokens', run.get('cache_write_tokens'))
        if isinstance(writes, int) and writes >= 0:
            group['cache_write_tokens'] += writes
        else:
            group['requests_without_write_usage'] += 1
        cost = run.get('cost')
        if cost is None:
            group['requests_without_cost'] += 1
        else:
            group['estimated_cost_usd'] += Decimal(str(cost))
    totals = {key: sum(group[key] for group in groups.values()) for key in next(iter(groups.values()), {})}
    for group in [*groups.values(), totals]:
        inputs = group.get('cache_measured_input_tokens', 0)
        group['cache_hit_percent'] = round(100 * group.get('cached_input_tokens', 0) / inputs, 2) if inputs else None
        group['estimated_cost_usd'] = str(group.get('estimated_cost_usd', 0))
    spans = [(datetime.fromisoformat(run['started_at']), run['duration_ms']) for run in runs
             if run.get('started_at') and run.get('duration_ms') is not None]
    totals['wall_span_ms_including_pauses'] = (round(1000 * (
        max(start + timedelta(milliseconds=duration) for start, duration in spans)
        - min(start for start, _ in spans)).total_seconds()) if spans else None)
    merged = []
    for start, duration in sorted(spans):
        end = start + timedelta(milliseconds=duration)
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    totals['processing_active_ms'] = round(sum((end - start).total_seconds() * 1000 for start, end in merged))
    prefixes = defaultdict(lambda: {'requests': 0, 'input_tokens': 0, 'cached_input_tokens': 0, 'cache_write_tokens': 0})
    for run in runs:
        metadata = run.get('prompt_metadata', {})
        fingerprint = metadata.get('prefix_fingerprint')
        if fingerprint:
            group = prefixes[fingerprint]
            group['requests'] += 1
            for field in ('input_tokens', 'cached_input_tokens', 'cache_write_tokens'):
                group[field] += run.get(field) or 0
    evidence_blocks = defaultdict(lambda: {'requests': 0, 'operations': []})
    for run in runs:
        for fingerprint in set(run.get('prompt_metadata', {}).get('evidence_blocks', [])):
            block = evidence_blocks[fingerprint]
            block['requests'] += 1
            if run['operation'] not in block['operations']:
                block['operations'].append(run['operation'])
    # Provider usage belongs to whole requests, so never attribute token reads
    # to individual blocks from metadata alone.
    return {'totals': totals, 'operations': dict(groups), 'prefixes': dict(prefixes),
            'evidence_blocks': dict(evidence_blocks)}


if __name__ == '__main__':
    print(json.dumps({path: summarize(json.loads(Path(path).read_text())) for path in sys.argv[1:]}, indent=2))
