from types import SimpleNamespace
from app.brand.conflicts import Finding, publish_conflict, has_conflicts


def test_conflict_question_preserves_saved_work():
    campaign = SimpleNamespace(data={'messages': [], 'plans': [{'goal': 'Awareness'}]}, state='awaiting_approval')
    finding = Finding(explanation='Brief requests awareness but plan asks for sales calls',
        statements=['Awareness first', 'Book a demo'], source_ids=[], affected_asset_ids=['post'],
        question='Which goal should govern?', options=['Keep awareness', 'Change the brief']).model_dump()
    publish_conflict(campaign, [{**finding, 'id': 'goal', 'status': 'unresolved'}])
    assert has_conflicts(campaign)
    assert campaign.state == 'clarifying'
    assert campaign.data['plans'][0]['goal'] == 'Awareness'
    assert campaign.data['pending_question']['conflict_id'] == 'goal'
    assert len(campaign.data['pending_question']['options']) == 2


def test_invalid_citations_do_not_hide_conflict_questions():
    from app.brand.conflicts import Findings, normalize_findings
    response = Findings(conflicts=[Finding(
        explanation='The brief and retrieved status disagree.',
        statements=['Authorized', 'Pursuing authorization'],
        source_ids=['brief-message-id', 'valid-chunk', 'unknown-document-id'],
        affected_asset_ids=[], question='Which wording should the plan use?',
        options=['Use pursuing wording', 'Change the brief'])])
    findings = normalize_findings(response, [{'chunk_id': 'valid-chunk'}])
    assert findings[0]['source_ids'] == ['valid-chunk']
    assert findings[0]['unverified_source_ids'] == ['brief-message-id', 'unknown-document-id']
    campaign = SimpleNamespace(data={'messages': [], 'planning_error': 'Previous failure'}, state='planning_error')
    publish_conflict(campaign, findings)
    assert campaign.state == 'clarifying'
    assert campaign.data['pending_question']['options'] == response.conflicts[0].options
    assert 'could not be verified' in campaign.data['pending_question']['text']
    assert 'planning_error' not in campaign.data


def test_citation_schema_only_allows_supplied_chunk_ids():
    import pytest
    from pydantic import ValidationError
    from app.brand.conflicts import findings_schema
    from app.llm.client import strict_output_schema
    payload = dict(explanation='Conflict', statements=['A', 'B'], source_ids=['valid'],
                   affected_asset_ids=[], question='Choose?', options=['A', 'B'])
    schema = findings_schema([{'chunk_id': 'valid'}])
    strict_output_schema(schema)
    assert schema.model_validate({'conflicts': [payload], 'source_checks': {'source_0': {'status': 'consistent', 'reason': 'Applicable', 'conflict_indices': []}}}).conflicts[0].source_ids == ['valid']
    with pytest.raises(ValidationError):
        schema.model_validate({'conflicts': [{**payload, 'source_ids': ['invented']}], 'source_checks': {}})
    empty = findings_schema([])
    strict_output_schema(empty)
    assert empty.model_validate({'conflicts': [{**payload, 'source_ids': []}], 'source_checks': {}})
    with pytest.raises(ValidationError):
        empty.model_validate({'conflicts': [payload], 'source_checks': {}})


def test_legacy_answers_are_decisions_and_queued_questions_survive_refresh():
    from app.brand.decisions import current_decisions, record_answer
    first = {'id': 'old-question', 'text': 'Which wording?', 'options': ['Pursuing'], 'answer': 'Pursuing'}
    campaign = SimpleNamespace(data={'questions': [first], 'messages': []}, state='planning')
    assert current_decisions(campaign)[0]['answer'] == 'Pursuing'
    def item(identity):
        return dict(id=identity, status='unresolved', explanation=identity, question='Choose?',
                    options=['Omit', 'Keep'], source_snapshot={}, affected_asset_ids=[])
    publish_conflict(campaign, [item('customer'), item('report')])
    q = campaign.data['pending_question']
    publish_conflict(campaign, [])
    assert campaign.data['pending_question']['id'] == q['id']
    record_answer(campaign, q, 'Omit')
    campaign.data = {**campaign.data, 'messages': [*campaign.data['messages'], {'role': 'user', 'text': 'Omit'}]}
    publish_conflict(campaign, [])
    assert campaign.data['pending_question']['conflict_id'] == 'report'
    assert len(current_decisions(campaign)) == 2
    assert campaign.data['conflicts'][0]['status'] == 'answered'


def test_semantic_matching_suppresses_answered_conflict_but_keeps_new_customer_issue():
    import json
    from app.brand.conflicts import reconcile_decisions
    from app.config import Settings
    campaign = SimpleNamespace(id='c', brand_id='b', data={'questions': [{
        'id': 'fedramp-answer', 'text': 'Use the legal wording?',
        'options': ['Use pursuing'], 'answer': 'Use pursuing'}]})
    findings = [{'id': 'reworded', 'explanation': 'Authorized or pursuing?'},
                {'id': 'customer', 'explanation': 'Customer quote not cleared'}]
    class Client:
        def run(self, request, **kwargs):
            payload = json.loads(request.input)
            assert payload['decisions'][0]['id'] == 'fedramp-answer'
            return SimpleNamespace(parsed=kwargs['output_model'].model_validate({'matches': [
                dict(finding_index=0, decision_id='fedramp-answer', disposition='resolved',
                     reason='Same subject, wording changed', changed_source_ids=[], changed_user_instruction=None),
                dict(finding_index=1, decision_id=None, disposition='new',
                     reason='Customer permission is separate', changed_source_ids=[], changed_user_instruction=None)]}))
    assert reconcile_decisions(Client(), Settings(_env_file=None), campaign, findings, [], []) == [findings[1]]


def test_selected_answer_reopens_only_when_relevant_evidence_changed():
    from app.brand.conflicts import reconcile_decisions
    from app.brand.decisions import evidence_snapshot
    from app.config import Settings
    old = [{'chunk_id': 'legal', 'content': 'Pursuing authorization'}]
    campaign = SimpleNamespace(id='c', brand_id='b', data={'decisions': [{
        'id': 'answer', 'question': 'Choose status', 'answer': 'Pursuing', 'status': 'selected',
        'source_snapshot': evidence_snapshot(old), 'message_count': 0}]})
    finding = {'id': 'status', 'explanation': 'Updated certification status'}
    class Client:
        def run(self, request, **kwargs):
            return SimpleNamespace(parsed=kwargs['output_model'].model_validate({'matches': [dict(
                finding_index=0, decision_id='answer', disposition='reopen', reason='New certification record',
                changed_source_ids=['legal'], changed_user_instruction=None)]}))
    settings = Settings(_env_file=None)
    assert reconcile_decisions(Client(), settings, campaign, [finding], old, []) == []
    changed = [{'chunk_id': 'legal', 'content': 'Now authorized'}]
    result = reconcile_decisions(Client(), settings, campaign, [finding], changed, [])
    assert result[0]['reopened_decision_id'] == 'answer'


def test_detected_source_conflict_without_question_is_published():
    from app.brand.conflicts import review_conflicts
    from app.config import Settings
    evidence = [{'chunk_id': 'hold', 'content': 'Customer quote is not cleared for external use.'}]
    campaign = SimpleNamespace(id='c', brand_id='b', data={'messages': []}, state='planning')
    class Client:
        def run(self, request, **kwargs):
            return SimpleNamespace(parsed=kwargs['output_model'].model_validate({
                'conflicts': [], 'source_checks': {'source_0': {
                    'status': 'conflict', 'reason': 'The requested customer quote is not cleared.',
                    'conflict_indices': [99]}}}))
    findings = review_conflicts(Client(), Settings(_env_file=None), campaign, [], evidence)
    publish_conflict(campaign, findings)
    assert campaign.state == 'clarifying'
    assert campaign.data['pending_question']['options']
    assert campaign.data['conflicts'][0]['source_ids'] == ['hold']


def test_latest_decision_wins_for_same_issue():
    from app.brand.decisions import current_decisions
    campaign = SimpleNamespace(data={'decisions': [
        {'id': 'old', 'issue_id': 'customer', 'message_count': 3},
        {'id': 'other', 'issue_id': 'certification', 'message_count': 4},
        {'id': 'new', 'issue_id': 'customer', 'message_count': 5},
    ]})
    assert {d['id'] for d in current_decisions(campaign)} == {'new', 'other'}


def test_answer_supersedes_history_and_resolves_same_issue_queue():
    from app.brand.decisions import record_answer, current_decisions
    campaign = SimpleNamespace(data={
        'messages': [], 'questions': [],
        'decisions': [{'id': 'old', 'issue_id': 'clearance', 'message_count': 0}],
        'conflicts': [{'id': 'first', 'issue_id': 'clearance', 'status': 'unresolved'},
                      {'id': 'duplicate', 'issue_id': 'clearance', 'status': 'unresolved'},
                      {'id': 'other', 'issue_id': 'certification', 'status': 'unresolved'}]})
    record_answer(campaign, {'id': 'new', 'issue_id': 'clearance', 'conflict_id': 'first',
                            'options': ['Omit', 'Wait']}, 'Omit')
    assert len(campaign.data['decisions']) == 2
    assert campaign.data['decisions'][0]['superseded_by'] == 'new'
    assert [d['id'] for d in current_decisions(campaign)] == ['new']
    assert [f['status'] for f in campaign.data['conflicts']] == ['answered', 'answered', 'unresolved']


def test_selected_option_constraints_are_generic_and_scoped():
    from app.brand.decisions import record_answer, decision_application_errors
    campaign = SimpleNamespace(data={'messages': [], 'questions': [], 'conflicts': []})
    question = {'id': 'q', 'issue_id': 'status', 'options': ['Use source wording'],
                'option_resolutions': {'Use source wording': {
                    'instruction': 'Use the current status from the source.',
                    'checks': [{'field': '/assets/0/hook', 'kind': 'contains',
                                'text': 'Application under review'}]}}}
    record_answer(campaign, question, 'Use source wording')
    assert decision_application_errors(campaign, {'assets': [{'hook': 'Fully approved'}]})
    assert not decision_application_errors(campaign, {'assets': [{'hook': 'Application under review'}]})


def test_topic_names_do_not_create_implicit_decision_rules():
    from app.brand.decisions import decision_instruction
    assert decision_instruction({'text': 'FedRAMP status?'}, 'Follow the source guidance') is None


def test_conflict_answer_keeps_valid_evidence_cache_current():
    from app.brand.decisions import record_answer
    from app.brand.campaign_requirements import requirement_revision
    question = {'id': 'q', 'conflict_id': 'issue', 'options': ['Use source wording', 'Keep request']}
    messages = [{'role': 'user', 'text': 'Campaign brief'},
                {'role': 'assistant', 'question': question}]
    revision = requirement_revision(messages, 'campaign')
    campaign = SimpleNamespace(data={
        'messages': messages, 'requirements': {'assets': [], 'issues': []},
        'requirements_revision': revision,
        'retrieved_context': {'artifact_id': 'saved-evidence', 'requirements_revision': revision}})
    record_answer(campaign, question, 'Use source wording')
    campaign.data['messages'].append({'role': 'user', 'text': 'Use source wording'})
    assert campaign.data['requirements_revision'] == requirement_revision(campaign.data['messages'], 'campaign')
    assert campaign.data['retrieved_context']['artifact_id'] == 'saved-evidence'
    assert campaign.data['continuation']['evidence_reused']


def test_intake_groups_coverage_passages_for_same_missing_question():
    from app.brand.conflicts import review_conflicts
    from app.config import Settings
    campaign = SimpleNamespace(id='c', brand_id='b', data={'messages': []})
    evidence = [{'chunk_id': 'status', 'content': 'Status is pending'},
                {'chunk_id': 'status-copy', 'content': 'Do not claim completed status'},
                {'chunk_id': 'hold', 'content': 'Customer story is not cleared'}]
    class Client:
        def run(self, request, **kwargs):
            return SimpleNamespace(parsed=kwargs['output_model'].model_validate({
                'conflicts': [], 'source_checks': {
                    'source_0': {'status': 'conflict', 'reason': 'Requested status conflicts', 'conflict_indices': [0]},
                    'source_1': {'status': 'conflict', 'reason': 'Same status restriction', 'conflict_indices': [0]},
                    'source_2': {'status': 'conflict', 'reason': 'Customer permission conflict', 'conflict_indices': [1]}}}))
    findings = review_conflicts(Client(), Settings(_env_file=None), campaign, [], evidence)
    assert len(findings) == 2
    assert findings[0]['source_ids'] == ['status', 'status-copy']
    assert findings[1]['source_ids'] == ['hold']


def test_intake_is_reused_until_brief_or_evidence_changes(monkeypatch):
    from app.brand import conflicts
    calls = []
    def review(*args, **kwargs):
        calls.append(args)
        return []
    monkeypatch.setattr(conflicts, 'review_campaign', review)
    campaign = SimpleNamespace(data={'messages': []})
    evidence = [{'chunk_id': 'source', 'content': 'Original status'}]
    assert not conflicts.prepare_conflict_intake(None, None, campaign, [], evidence, 'brief1')
    assert not conflicts.prepare_conflict_intake(None, None, campaign, [], evidence, 'brief1')
    assert len(calls) == 1
    assert not conflicts.prepare_conflict_intake(None, None, campaign, [], evidence, 'brief2')
    assert len(calls) == 2
    changed = [{'chunk_id': 'new', 'content': 'New evidence'}]
    assert not conflicts.prepare_conflict_intake(None, None, campaign, [], changed, 'brief2')
    assert len(calls) == 3
