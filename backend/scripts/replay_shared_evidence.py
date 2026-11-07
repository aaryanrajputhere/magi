"""Offline chronological evidence replay; never calls a model or predicts a hit rate.

Usage: PYTHONPATH=backend python backend/scripts/replay_shared_evidence.py runs.json
Token counts use cl100k_base, not provider usage. Parallel cold requests only see
prefixes from requests that had finished when they started.
"""
import json
import sys
from datetime import datetime, timedelta

import tiktoken
from app.llm.shared_evidence import OPERATIONS, canonical_messages, saved_blocks, encode


def replay(runs):
    encoding = tiktoken.get_encoding('cl100k_base')
    completed = []
    output = []
    for run in sorted(runs, key=lambda r: r['started_at']):
        body = run.get('request') or {}
        if (run.get('operation') not in OPERATIONS or not isinstance(body, dict)
                or not isinstance(body.get('input'), list)):
            continue
        # Restore ordinary message text from historical explicit breakpoint blocks.
        messages = json.loads(json.dumps(body['input']))
        for message in messages:
            if isinstance(message.get('content'), list):
                message['content'] = ''.join(p.get('text', '') for p in message['content'])
        # Existing exports may already use the shared text transport.
        text_blocks = saved_blocks(body)
        if text_blocks:
            texts = {k: v for block in text_blocks for k, v in block.items()}
            def restore(value):
                if isinstance(value, list):
                    return [restore(v) for v in value]
                if not isinstance(value, dict):
                    return value
                if 'text_ref' in value and value['text_ref'] in texts:
                    return {**{k: v for k, v in value.items() if k != 'text_ref'},
                            'content': texts[value['text_ref']]}
                return {k: restore(v) for k, v in value.items()}
            messages = messages[len(text_blocks):]
            for message in messages:
                try:
                    value = json.loads(message.get('content', ''))
                except (ValueError, TypeError):
                    continue
                message['content'] = encode(restore(value))
        start = datetime.fromisoformat(run['started_at'])
        previous = [blocks for end, blocks in completed if end <= start]
        blocks, _ = canonical_messages(messages, previous)
        raw_blocks = saved_blocks({'input': blocks})
        matched = 0
        for old in previous:
            length = 0
            for a, b in zip(raw_blocks, old):
                if a != b:
                    break
                length += len(encoding.encode(encode({'original_texts': a})))
            matched = max(matched, length)
        output.append({'id': run['id'], 'operation': run['operation'],
                       'estimated_reusable_text_block_tokens': matched,
                       'block_source_counts': [len(b) for b in raw_blocks]})
        if run.get('status') == 'completed':
            end = start + timedelta(milliseconds=run.get('duration_ms') or 0)
            completed.append((end, raw_blocks))
    return {'note': 'Estimated shared-block tokens only; excludes minimum length, provider routing and expiry. Not a cache-hit forecast.',
            'requests': output}


if __name__ == '__main__':
    print(json.dumps(replay(json.load(open(sys.argv[1]))), indent=2))
