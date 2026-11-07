"""Preserve factual commitments from approved hooks without inventing support."""
import re


FACT = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)\s*[-–]?\s*(%|hours?\b|minutes?\b|days?\b|seconds?\b)', re.I)


def facts(asset):
    hook = re.sub(r'\[[^\]]*\]|https?://\S+', '', asset.get('hook') or '')
    return list(dict.fromkeys((m.group(1), m.group(2).lower().rstrip('s')) for m in FACT.finditer(hook)))


def missing_facts(asset, body):
    actual = {(m.group(1), m.group(2).lower().rstrip('s')) for m in FACT.finditer(body)}
    return [number + ('%' if unit == '%' else ' ' + unit + 's')
            for number, unit in facts(asset) if (number, unit) not in actual]


def supporting_sources(asset, pool):
    """Use an exact approved claim from existing selected originals, not examples."""
    if not facts(asset):
        return []
    claim = re.sub(r'\[[^\]]*\]|https?://\S+', '', asset.get('hook') or '').strip(' .\n\"“”')
    normalized = lambda text: re.sub(r'\s+', ' ', text).casefold()
    candidates = []
    for source in pool.values():
        path = source.get('relative_path', '').lower()
        headings = ' '.join(source.get('heading_path') or []).lower()
        if ('/channels/' in path or '/templates/' in path or '/voice/' in path
                or 'example' in headings):
            continue
        if normalized(claim) in normalized(source['content']):
            rank = (0 if '/legal/' in path or 'approved claim' in headings else
                    1 if '/research/' in path else 2)
            candidates.append((rank, source['chunk_id']))
    return [min(candidates)[1]] if candidates else []
