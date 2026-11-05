"""Campaign-scoped, bounded external research. Never imports web text as brand truth."""
import hashlib
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ResearchRun(Base):
    __tablename__ = 'research_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class ResearchDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str
    queries: list[str] = Field(max_length=2)


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        return False
    try:
        return all(ipaddress.ip_address(item[4][0]).is_global
                   for item in socket.getaddrinfo(parts.hostname, parts.port or 443))
    except (OSError, ValueError):
        return False


def research(session, campaign, requirements, client, settings):
    from app.llm.client import ModelRequest
    from app.llm.context import RunContext
    fingerprint = hashlib.sha256(json.dumps(requirements, sort_keys=True).encode()).hexdigest()
    previous = list(session.scalars(select(ResearchRun).where(ResearchRun.campaign_id == campaign.id)))
    for run in reversed(previous):
        if run.data['fingerprint'] == fingerprint and time.time() - run.data['created_at'] < 86400:
            return run.data
    data = {'fingerprint': fingerprint, 'created_at': time.time(), 'status': 'skipped', 'sources': [],
                'requests': [], 'estimated_credits': 0, 'reason': 'Firecrawl key is not configured'}
    if not settings.firecrawl_api_key:
        return data
    run = ResearchRun(id=str(uuid4()), campaign_id=campaign.id, data=data)
    session.add(run)
    session.commit()
    try:
        decision = client.run(ModelRequest(operation='campaign_research_plan',
            model=settings.magi_retrieval_planner_model, max_output_tokens=800,
            instructions='Decide whether external industry research helps this campaign. Return no queries if unnecessary. Never search for the brand/company itself. Queries must be generic public industry questions, without private details, brand identities or internal metrics. At most two queries. Research cannot establish brand facts.',
            input=json.dumps(requirements), context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id)), output_model=ResearchDecision)
        data['reason'] = decision.parsed.reason
        data['decision_run_id'] = decision.run_id
        brand = session.get(__import__('app.models', fromlist=['Brand']).Brand, campaign.brand_id)
        queries = [q for q in decision.parsed.queries if brand.name.casefold() not in q.casefold()]
        remaining = min(7, 21 - sum(r.data.get('estimated_credits', 0) for r in previous))
        deadline = time.monotonic() + 90
        with httpx.Client(headers={'Authorization': 'Bearer ' + settings.firecrawl_api_key.get_secret_value()}, timeout=30) as http:
            def call(endpoint, payload, credits):
                nonlocal remaining
                if remaining < credits or time.monotonic() >= deadline:
                    raise ValueError('Research budget or deadline exhausted')
                remaining -= credits
                data['estimated_credits'] += credits
                data['requests'].append({'endpoint': endpoint, 'payload': payload})
                run.data = dict(data)
                session.commit()  # reserve before dispatch; uncertain billing stays reserved
                response = http.post('https://api.firecrawl.dev/v2/' + endpoint, json=payload, timeout=min(30, max(0.1, deadline-time.monotonic())))
                response.raise_for_status()
                result = response.json()
                if not result.get('success'):
                    raise ValueError('Provider returned unsuccessful research')
                return result['data']
            candidates = []
            for query in queries:
                result = call('search', {'query': query, 'limit': 5}, 2)
                candidates.extend(result.get('web', []) if isinstance(result, dict) else result)
            seen = set()
            for candidate in candidates:
                url = candidate.get('url', '')
                if url in seen or not public_url(url) or remaining < 1 or len(data['sources']) >= 3:
                    continue
                seen.add(url)
                page = call('scrape', {'url': url, 'formats': ['markdown'], 'onlyMainContent': True, 'parsers': [], 'timeout': 25000}, 1)
                markdown = page.get('markdown', '')
                if markdown:
                    data['sources'].append({'id': 'web:' + str(uuid4()), 'url': url, 'title': candidate.get('title', url), 'content': markdown[:16000], 'truncated': len(markdown)>16000, 'retrieved_at': time.time()})
            data['status'] = 'completed' if queries else 'skipped'
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        data['status'] = 'partial' if data['sources'] else 'failed'
        data['reason'] = type(exc).__name__ + ': research unavailable; omit unsupported claims'
    finally:
        run.data = dict(data)
        session.commit()
    return data
