"""One campaign decision history for every question entry point."""
import hashlib

DECISION_GUIDANCE = (
    'Records with origin reviewed_rule are verified source-grounded treatments from the '
    'campaign review. Apply them to claim wording over contradictory historical brief '
    'text and extracted requirements. They do not grant publication permission or '
    'override explicit user decisions. '
    'Records with origin confirmed_authority are source-backed automatic resolutions, not user answers; apply their factual resolution without changing user intent. Other current decisions are explicit user answers to saved questions. Apply them as updates to '
    'the original brief; the original request is historical where an answer supersedes it. '
    'Never report a conflict solely because the original brief differs from an accepted answer. '
    'Do not require the campaign goal to narrate the correction or its legal justification. '
    'An answer is campaign intent, not proof of a factual claim. If a new evidence conflict '
    'still needs a decision, ask it explicitly rather than silently overriding the answer. '
    'Do not reopen the same question unless relevant evidence or user intent changed, or the '
    'answer was ambiguous. Explain the specific change when reopening. '
)


def evidence_snapshot(evidence):
    return {e['chunk_id']: hashlib.sha256(e.get('content', '').encode()).hexdigest()
            for e in evidence}


def current_decisions(campaign):
    """Read legacy answered questions as decisions without a database migration."""
    data = getattr(campaign, 'data', {})
    records = {d['id']: dict(d) for d in data.get('decisions', [])}
    for q in data.get('questions', []):
        if q.get('answer') and q['id'] not in records:
            decision = decision_from_question(q, q['answer'])
            for index, message in enumerate(data.get('messages', [])):
                if message.get('role') == 'user' and message.get('text') == q['answer']:
                    decision['message_count'] = index + 1
            records[q['id']] = decision
    for conflict in data.get('conflicts', []):
        if conflict.get('answer') and not any(d.get('conflict_id') == conflict['id'] for d in records.values()):
            q = dict(conflict, conflict_id=conflict['id'], text=conflict.get('question', ''))
            records[q['id']] = decision_from_question(q, conflict['answer'])
    latest = {}
    for record in records.values():
        key = record.get('issue_id') or record.get('conflict_id') or record['id']
        if key not in latest or record.get('message_count', 0) >= latest[key].get('message_count', 0):
            latest[key] = record
    result = list(latest.values())
    report = data.get('conflict_coverage', {})
    if report.get('status') in ('ready', 'needs_decision'):
        for treatment in report.get('treatments', []):
            if treatment.get('requires_user_decision') or treatment.get('decision_type') == 'publication_permission':
                continue
            result.append({'id': 'reviewed_rule_' + hashlib.sha256(
                repr(treatment).encode()).hexdigest()[:24],
                'origin': 'reviewed_rule', 'status': 'resolved',
                'question': treatment.get('explanation', ''),
                'answer': treatment['treatment'],
                'resolution': {'instruction': treatment['treatment']},
                'affected_asset_ids': treatment.get('affected_asset_ids', []),
                'source_snapshot': report.get('source_snapshot', {})})
    for conflict in data.get('conflicts', []):
        if conflict.get('status') == 'resolved' and conflict.get('resolution_origin') == 'confirmed_authority':
            result.append({'id': conflict['id'], 'conflict_id': conflict['id'],
                           'question': conflict.get('question', ''),
                           'answer': conflict['resolution'], 'status': 'resolved',
                           'origin': 'confirmed_authority',
                           'source_snapshot': conflict.get('resolution_snapshot', {})})
    for record in result:
        resolution = record.get('resolution') or decision_instruction(record, record.get('answer', ''))
        if resolution:
            record['resolution'] = resolution
    return result


def decision_from_question(question, answer):
    return {'id': question['id'], 'question': question.get('text', question.get('question', '')),
            'answer': answer, 'conflict_id': question.get('conflict_id'),
            'issue_id': question.get('issue_id', question.get('conflict_id')),
            'affected_asset_ids': question.get('affected_asset_ids', []),
            'source_snapshot': question.get('source_snapshot', {}),
            'status': 'selected' if answer in question.get('options', []) else 'answered',
            'message_count': question.get('message_count', 0),
            'resolution': decision_instruction(question, answer)}


def record_answer(campaign, question, answer):
    if question.get('recovery') == 'conflict_citations_unverified':
        # A recovery choice is workflow input, not a verified rule or approval.
        campaign.data = {**campaign.data, 'pending_question': None,
            'questions': [{**q, 'answer': answer} if q['id'] == question['id'] else q
                          for q in campaign.data.get('questions', [])]}
        return
    decisions = {d['id']: dict(d) for d in campaign.data.get('decisions', [])}
    for decision in current_decisions(campaign):
        if decision.get('origin') != 'reviewed_rule':
            decisions.setdefault(decision['id'], decision)
    decisions[question['id']] = decision_from_question(question, answer)
    resolution = decision_instruction(question, answer)
    if resolution:
        decisions[question['id']]['resolution'] = resolution
    decisions[question['id']]['message_count'] = len(campaign.data.get('messages', [])) + 1
    issue = decisions[question['id']].get('issue_id') or question.get('conflict_id')
    if issue:
        for identifier, decision in decisions.items():
            if identifier != question['id'] and (decision.get('issue_id') or decision.get('conflict_id')) == issue:
                decision['superseded_by'] = question['id']
    # Rebind a matching legacy cache before the answer is appended. Never reuse
    # incomplete requirements or a cache already out of date with the brief.
    from app.brand.campaign_requirements import requirement_revision
    data = dict(campaign.data)
    cached = dict(data.get('retrieved_context', {}))
    data.pop('continuation', None)
    selected_conflict = question.get('conflict_id') and answer in question.get('options', [])
    if (selected_conflict and data.get('requirements') and not data['requirements'].get('issues')
            and cached.get('artifact_id') and data.get('requirements_revision')
            and cached.get('requirements_revision') == data['requirements_revision']):
        mode = 'extend' if data.get('generation', {}).get('drafts') else 'campaign'
        revision = requirement_revision(data.get('messages', []), mode)
        cached['requirements_revision'] = revision
        data['requirements_revision'] = revision
        data['retrieved_context'] = cached
        data['continuation'] = {'stage': 'apply_decisions', 'evidence_reused': True,
                                'question_id': question['id']}
    campaign.data = {**data, 'decisions': list(decisions.values()),
        'pending_question': None,
        'questions': [{**q, 'answer': answer} if q['id'] == question['id'] else q
                      for q in campaign.data.get('questions', [])],
        'conflicts': [{**f, 'status': 'answered', 'answer': answer}
                      if f['id'] == question.get('conflict_id') or (issue and (f.get('issue_id') or f['id']) == issue) else f
                      for f in campaign.data.get('conflicts', [])]}


def decision_instruction(question, answer):
    """Read a saved option meaning without guessing from a topic or brand name."""
    options = question.get('option_resolutions', {})
    resolution = options.get(answer)
    if resolution is not None:
        return dict(resolution)
    return None


def decision_application_errors(campaign, plan):
    """Enforce explicit literal constraints attached to selected resolutions."""
    import json
    errors = []
    for decision in current_decisions(campaign):
        resolution = decision.get('resolution') or {}
        for constraint in resolution.get('checks', []):
            pointer = constraint['field']
            value = plan
            try:
                for part in pointer.strip('/').split('/'):
                    key = part.replace('~1', '/').replace('~0', '~')
                    value = value[int(key)] if isinstance(value, list) else value[key]
            except (KeyError, IndexError, TypeError, ValueError):
                errors.append(f"Decision {decision['id']}: missing field {pointer}")
                continue
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            phrase = constraint['text']
            invalid = (phrase not in text if constraint['kind'] == 'contains' else phrase in text)
            if invalid:
                errors.append(f"Decision {decision['id']}: {pointer} must satisfy {constraint['kind']}: {phrase}")
    return errors
