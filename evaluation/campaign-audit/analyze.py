"""Recompute the read-only campaign audit from exported inspector records."""
import collections
import datetime as dt
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).parent
NAMES = {'688e97e7-67af-4782-90d1-5b5831d837f4': 'Regulated teams', '67bac97f-81fb-4021-8a94-e3d62ac29689': 'Awareness', 'af3bdcc1-cb98-489a-8ed8-a05c250d968f': 'Leashgate GA'}

def text_content(message):
    value = message.get('content', '')
    return value if isinstance(value, str) else '\n'.join(x.get('text', '') for x in value)

def words(value):
    value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', value)
    return sum(bool(re.search(r'\w', word)) for word in value.split())

def totals(rows):
    text = [r for r in rows if r['model'] != 'text-embedding-3-small']
    incoming = sum(r.get('input_tokens') or 0 for r in text)
    cached = sum(r.get('cached_input_tokens') or 0 for r in text)
    return {'requests': len(rows), 'text_requests': len(text), 'reported_text_input': incoming,
        'reported_output': sum(r.get('output_tokens') or 0 for r in rows),
        'cache_reads': cached, 'cache_ratio': cached / incoming if incoming else None,
        'cache_writes': sum(((r.get('response') or {}).get('usage') or {}).get('input_tokens_details', {}).get('cache_write_tokens', 0) for r in text),
        'reasoning_tokens': sum(((r.get('response') or {}).get('usage') or {}).get('output_tokens_details', {}).get('reasoning_tokens', 0) for r in text),
        'sum_request_seconds': sum(r.get('duration_ms') or 0 for r in rows) / 1000,
        'estimated_cost_subtotal_usd': sum(float(r.get('cost') or 0) for r in rows),
        'missing_usage_requests': [r['id'] for r in rows if r.get('input_tokens') is None],
        'missing_cost_requests': [r['id'] for r in rows if r.get('cost') is None]}

summary = {}
for campaign_id, name in NAMES.items():
    rows = json.loads((ROOT / f'{campaign_id}-runs.json').read_text())
    campaign = json.loads((ROOT / f'{campaign_id}.json').read_text())
    data = totals(rows)
    first = min(dt.datetime.fromisoformat(r['started_at']) for r in rows)
    last = max(dt.datetime.fromisoformat(r['started_at']) + dt.timedelta(milliseconds=r.get('duration_ms') or 0) for r in rows)
    data.update(name=name, state=campaign['state'], observed_span_seconds=(last-first).total_seconds(),
        operations={op: totals([r for r in rows if r['operation'] == op]) for op in dict.fromkeys(r['operation'] for r in rows)},
        drafts=[{'asset_id': d['asset_id'], 'format': d['format'], 'body_words': words(d['body']),
                 'validation': d.get('validation', {}).get('status'), 'readiness': d.get('publication_readiness'),
                 'unresolved_dependencies': sum(x['status'] != 'resolved' for x in d.get('dependency_records', []))}
                for d in campaign.get('generation', {}).get('drafts', [])])
    summary[campaign_id] = data
    lines = [f'# {name}: every recorded request', '', 'Numbering is request start order. Raw request and response JSON are in the adjacent `-runs.json`; full extracted outputs are in `-runs-outputs.txt`. Tokens are provider-reported; missing values remain unknown. Each entry inventories the actual input and result. Model completion does not establish application-level acceptance.', '']
    for i, r in enumerate(rows, 1):
        req = r.get('request') or {}
        resp = r.get('response') or {}
        output = '\n'.join(c['text'] for o in resp.get('output', []) for c in o.get('content', []) if c.get('text'))
        calls = [o for o in resp.get('output', []) if o.get('type') == 'function_call']
        lines += [f'## {i}. {r["operation"]}', '', f'Run `{r["id"]}`; {r["model"]}; {r["status"]}; {r.get("duration_ms")} ms; input {r.get("input_tokens")}; output {r.get("output_tokens")}; cached {r.get("cached_input_tokens")}; estimated cost {r.get("cost")}.', '']
        inp = req.get('input')
        messages = inp if isinstance(inp, list) else [{'role':'input', 'content':inp or ''}]
        for j, m in enumerate(messages):
            if isinstance(m, str):
                m = {'role': 'embedding_input', 'content': m}
            value = text_content(m)
            try:
                d = json.loads(value)
                fields = ', '.join(f'{k} ({len(json.dumps(v, ensure_ascii=False)):,} chars)' for k, v in sorted(d.items(), key=lambda kv: len(json.dumps(kv[1])), reverse=True)) if isinstance(d, dict) else type(d).__name__
            except (ValueError, TypeError):
                fields = value[:240].replace('\n', ' ')
            lines.append(f'- Input {j+1}, {m.get("role", m.get("type"))}: {len(value):,} chars; explicit boundary: {isinstance(m.get("content"), list)}. {fields}')
        lines += ['', f'Schema SHA256: `{hashlib.sha256(json.dumps(req.get("text"), sort_keys=True).encode()).hexdigest()[:16]}`. Serialized request: {len(json.dumps(req)):,} chars.']
        if r.get('error'):
            lines += ['', 'Error: ' + json.dumps(r['error'])]
        if calls:
            lines += ['', 'Tool selections: ' + json.dumps(calls)]
        if output:
            lines += ['', 'Actual structured output:', '', '```json', output, '```']
        if r['operation'] in {'generate_campaign_asset', 'campaign_asset_validation', 'campaign_asset_repair'}:
            lines += ['', 'Assessment: compare this result with the saved validation history and dependencies; a completed request may still produce copy requiring repairs or publication inputs.']
        if r['operation'].startswith('campaign_conflict_'):
            lines += ['', 'Assessment: verify cited sources are in this batch, and reconcile equivalent findings across batches before asking questions. Cache reuse depends on identical passage membership/order, instructions and schema.']
        if r['status'] == 'timeout':
            lines += ['', 'Assessment: no completed response or usage was recorded. This is unknown provider billing, not zero tokens or zero cost. No retry is present in this campaign export.']
        lines += ['']
    (ROOT / f'{campaign_id}-request-review.md').write_text('\n'.join(lines))
(ROOT / 'summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps({k: {key: value for key, value in v.items() if key not in {'operations','drafts'}} for k,v in summary.items()}, indent=2))
