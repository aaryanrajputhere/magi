import json
from types import SimpleNamespace

import pytest

from app.brand import conflicts
from app.brand.retrieval import RetrievalError


def test_agreed_channels_never_become_question_but_real_issues_remain(monkeypatch):
    campaign = SimpleNamespace(data={}, id='campaign', brand_id='brand', state='planning_busy')
    settings = SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60)
    evidence = [{'chunk_id': 'source', 'content': 'Authorization is pending. Customer use is on hold.'}]
    messages = [{'role': 'user', 'text': 'Use only LinkedIn, X, email, blog and landing pages.'}]
    findings = [{'id': key, 'status': 'unresolved', 'source_ids': ['source'],
                 'statements': statements, 'explanation': explanation}
                for key, statements, explanation in [
                    ('channels', ['Use only owned channels.', 'No restriction contradicts owned channels.'],
                     'The campaign is constrained to owned channels and no conflicting restrictions are found.'),
                    ('claim', ['Claim authorization.', 'Authorization is pending.'], 'The claim is prohibited.'),
                    ('permission', ['Publish the customer story.', 'Customer use is on hold.'], 'Clearance is pending.')]]
    assessments = [
        {'finding_index': 0, 'decision_type': 'other', 'kind': 'not_conflict', 'opposing_statements': [],
         'reason': 'The user already specified these channels and no evidence contradicts them.'},
        {'finding_index': 1, 'decision_type': 'claim_wording', 'kind': 'contradiction',
         'opposing_statements': findings[1]['statements'], 'reason': 'Authorization is not held.'},
        {'finding_index': 2, 'decision_type': 'publication_permission', 'kind': 'publication_restriction',
         'opposing_statements': findings[2]['statements'], 'reason': 'External use is on hold.'},
    ]
    calls = []

    def run(request, output_model):
        calls.append(request.operation)
        assert json.loads(request.input)['conversation'] == messages
        return SimpleNamespace(parsed=output_model.model_validate({'assessments': assessments}))

    def formulate(client, settings, campaign, accepted):
        assert [f['id'] for f in accepted] == ['claim', 'permission']
        return [{**f, 'question': 'Resolve ' + f['id'] + '?', 'options': ['Omit', 'Wait']}
                for f in accepted]

    monkeypatch.setattr(conflicts, 'review_campaign', lambda *a, **kw: findings)
    monkeypatch.setattr(conflicts, 'group_duplicate_conflicts', formulate)
    client = SimpleNamespace(run=run)
    assert conflicts.prepare_conflict_intake(client, settings, campaign, messages, evidence, 'revision')
    assert [q['conflict_id'] for q in campaign.data['questions']] == ['claim', 'permission']
    rejected = campaign.data['conflict_intake']['rejected_findings']
    assert rejected[0]['id'] == 'channels'
    assert rejected[0]['status'] == 'not_conflict'
    assert len(campaign.data['pending_conflict_question_ids']) == 2
    conflicts.prepare_conflict_intake(client, settings, campaign, messages, evidence, 'revision')
    assert calls == ['campaign_conflict_validation']


@pytest.mark.parametrize('assessments', [
    [],
    [{'finding_index': 0, 'decision_type': 'claim_wording', 'kind': 'contradiction', 'opposing_statements': [], 'reason': 'Missing opposition'}],
    [{'finding_index': 0, 'decision_type': 'claim_wording', 'kind': 'contradiction', 'opposing_statements': ['Same', ' same '], 'reason': 'Agreement'}],
    [{'finding_index': 0, 'decision_type': 'other', 'kind': 'not_conflict', 'opposing_statements': [], 'reason': 'Agreement'}] * 2,
])
def test_incomplete_or_invalid_validation_blocks_question_creation(assessments):
    client = SimpleNamespace(run=lambda request, output_model: SimpleNamespace(
        parsed=output_model.model_validate({'assessments': assessments})))
    with pytest.raises(RetrievalError, match='every finding once'):
        conflicts.validate_detected_conflicts(client,
            SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60),
            SimpleNamespace(data={}), [], [{'id': 'issue'}], [])


def test_no_findings_needs_no_validation_call():
    assert conflicts.validate_detected_conflicts(None, None, None, [], [], []) == ([], [])


def test_only_consistent_findings_leave_no_pending_question(monkeypatch):
    campaign = SimpleNamespace(data={}, state='planning_busy')
    finding = {'id': 'channels', 'status': 'unresolved', 'explanation': 'No conflict.',
               'statements': ['Use email.', 'Email is allowed.'], 'source_ids': ['source']}
    monkeypatch.setattr(conflicts, 'review_campaign', lambda *a, **kw: [finding])
    client = SimpleNamespace(run=lambda request, output_model: SimpleNamespace(
        parsed=output_model.model_validate({'assessments': [{
            'finding_index': 0, 'decision_type': 'other', 'kind': 'not_conflict', 'opposing_statements': [],
            'reason': 'The instruction is consistent.'}]})))
    assert not conflicts.prepare_conflict_intake(client,
        SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60),
        campaign, [], [{'chunk_id': 'source', 'content': 'Email is allowed.'}], 'revision')
    assert campaign.data['questions'] == []
    assert campaign.data['conflicts'] == []
    assert campaign.state == 'draft'
