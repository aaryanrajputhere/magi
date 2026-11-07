"""Stable cache routing and reusable message prefixes."""
import hashlib
import json


def prompt_cache_key(brand_id, operation):
    key = f"magi:v1:{brand_id}:{operation}"
    if len(key) <= 64:
        return key
    # Hash the full scope so long identifiers remain distinct within the API limit.
    scope = json.dumps([brand_id, operation], ensure_ascii=True)
    return "magi:v1:" + hashlib.sha256(scope.encode()).hexdigest()[:56]


def cached_prompt(payload, shared_fields):
    shared = {k: payload[k] for k in shared_fields if k in payload}
    changing = {k: v for k, v in payload.items() if k not in shared}
    return [{"role": "user", "content": json.dumps(p, sort_keys=True, ensure_ascii=False)}
            for p in (shared, changing)]


def supports_breakpoints(model):
    import re
    generation = re.match(r'^gpt-(\d+)(?:\.(\d+))?(?:-|$)', model)
    return bool(generation and (int(generation[1]) >= 6
        or (int(generation[1]) == 5 and int(generation[2] or 0) >= 6)))


def cacheable_input(value, model, prefix_messages=None, ttl=None):
    """Mark reusable message endings only on models supporting breakpoints."""
    from copy import deepcopy
    if not supports_breakpoints(model) or not prefix_messages:
        return value
    if not isinstance(value, list) or prefix_messages > len(value):
        raise ValueError('Cache prefix must reference existing input messages')
    result = deepcopy(value)
    for message in result[:prefix_messages]:
        content = message.get('content')
        if not isinstance(content, str):
            raise TypeError('Explicit cache prefix requires text messages')
        marker = {'mode': 'explicit'}
        if ttl is not None:
            marker['ttl'] = ttl
        message['content'] = [{'type': 'input_text', 'text': content,
                               'prompt_cache_breakpoint': marker}]
    return result


def prefix_metadata(body, prefix_messages, boundary_indices=None):
    """Fingerprint actual prefix settings without adding metadata to provider input."""
    prefix = {k: body[k] for k in ('model', 'instructions', 'tools', 'text',
              'parallel_tool_calls', 'service_tier') if k in body}
    prefix['input'] = body['input'][:prefix_messages]
    serialized = json.dumps(prefix, sort_keys=True, ensure_ascii=False)
    return {'template_version': 'prompt_v2',
            'prefix_fingerprint': hashlib.sha256(serialized.encode()).hexdigest(),
            'boundary_fingerprints': [hashlib.sha256(json.dumps(
                {**prefix, 'input': prefix['input'][:i]}, sort_keys=True, ensure_ascii=False
                ).encode()).hexdigest() for i in (boundary_indices or range(1, prefix_messages + 1))],
            'prefix_chars': len(serialized), 'prefix_messages': prefix_messages,
            'cache_mode': body.get('prompt_cache_options', {}).get('mode', 'implicit')}
