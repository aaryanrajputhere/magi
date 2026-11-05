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
    return [{"role": "user", "content": json.dumps(p)} for p in (shared, changing)]
