"""Conservative numeric CTA checks grounded in explicit original source wording."""
import re

NUMBERS = dict(zip(('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'), range(1, 11)))
RULE = re.compile(r'\bCTA\b[^.\n]{0,160}?\bat most\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(words|characters)\b[^.\n]*\.?', re.IGNORECASE)
ACTION = re.compile(r'^(read|visit|open|explore|learn|see|view|download|get|request|book|start|contact|watch|try|discover|join)\b', re.IGNORECASE)


def explicit_limits(evidence):
    found = []
    for passage in evidence:
        for match in RULE.finditer(passage['content']):
            value = match[1].lower()
            found.append({'field': 'cta', 'unit': match[2].lower(), 'minimum': None,
                          'maximum': int(value) if value.isdigit() else NUMBERS[value],
                          'chunk_id': passage['chunk_id'], 'quote': match[0]})
    return found


def label(body):
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    if not paragraphs:
        return None
    last = paragraphs[-1]
    match = re.fullmatch(r'\[([^\]]+)\]\(https?://[^)]+\)[.!]?', last)
    if match:
        return match[1]
    if '\n' in last or len(last) > 160 or not ACTION.search(last):
        return None
    return re.sub(r'https?://\S+', '', last).rstrip(' .:')
