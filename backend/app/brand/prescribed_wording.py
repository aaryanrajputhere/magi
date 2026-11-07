"""Exact mandated claim wording from current confirmed governing sources."""
import re

PATTERN = re.compile(r'approved (?:sentence|wording) (?:is|:)\s*["“]([^"”\n]+)["”]', re.IGNORECASE)


def prescribed_wording(evidence, authority, formats):
    groups = [g for g in (authority or {}).get('groups', []) if g.get('governing')
              and set(g.get('topics', [])) & {'claims', 'certification'}
              and (not g.get('channels') or set(formats) <= set(g['channels']))]
    result = []
    for passage in evidence:
        governing = [g for g in groups if passage.get('document_id') in g['document_ids']]
        if not governing:
            continue
        for match in PATTERN.finditer(passage.get('content', '')):
            topic = 'certification' if any('certification' in g['topics'] for g in governing) else 'claims'
            peers = [g for g in groups if topic in g['topics']]
            if peers and min(g['priority'] for g in governing) != min(g['priority'] for g in peers):
                continue
            result.append({'approved_wording': match[1], 'source_id': passage['chunk_id'],
                           'rule_quote': match[0], 'topic': topic,
                           'instruction': 'Use this exact prescribed wording when this claim is included. Do not ask the user to approve a factual correction. This does not grant publication permission or override a decision to omit the claim.'})
    return result


def resolves_question(question, directives):
    if not isinstance(question, dict):
        return False
    text = question.get('text', '') + ' ' + question.get('reason', '')
    if re.search(r'permission|consent|customer|clearance|quote approval', text, re.IGNORECASE):
        return False
    return any(d['approved_wording'] in text for d in directives)


def resolve_saved_question(campaign, directives):
    question = campaign.data.get('pending_question')
    if not resolves_question(question, directives):
        return False
    resolution = {'origin': 'source_rule', 'source_ids': [d['source_id'] for d in directives],
                  'instruction': 'Apply exact source-prescribed factual wording; no user preference answer is required.'}
    campaign.data = {**campaign.data, 'pending_question': None,
        'questions': [{**q, 'source_resolution': resolution} if q.get('id') == question['id'] else q
                      for q in campaign.data.get('questions', [])]}
    campaign.state = 'draft'
    return True
