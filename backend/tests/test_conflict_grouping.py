from types import SimpleNamespace
import pytest
from app.brand.conflicts import FormulatedQuestions, group_duplicate_conflicts


def test_groups_duplicate_decisions_and_preserves_independent_issue_and_sources():
    findings = [{'id': str(i), 'question': question, 'source_ids': [str(i)],
                 'query_ids': [f'q{i}'], 'source_snapshot': {str(i): 'digest'}}
                for i, question in enumerate(['Which claim wording?', 'May we publish the story?',
                                             'How should the same claim be phrased?'])]
    requests = []
    def run(request, **kwargs):
        requests.append(request)
        return SimpleNamespace(run_id="test-run", parsed=FormulatedQuestions(questions=[{'finding_indices': g, 'explanation': 'Combined conflict', 'question': 'Which approach?', 'options': ['Use approved wording', 'Omit claim']} for g in [[2, 0], [1]]]))
    result = group_duplicate_conflicts(SimpleNamespace(run=run),
        SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60), SimpleNamespace(data={}), findings)
    assert len(result) == 2
    assert result[0]['source_ids'] == ['0', '2']
    assert result[0]['merged_finding_ids'] == ['0', '2']
    assert result[1]['id'] == '1'
    assert len(result[0]['related_findings']) == 2
    assert len(requests) == 1


@pytest.mark.parametrize('groups', [[[0]], [[0], [0, 1]], [[0], [2]], [[], [0, 1]]])
def test_grouping_cannot_drop_or_duplicate_findings(groups):
    client = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(run_id="test-run", parsed=FormulatedQuestions(questions=[{'finding_indices': g, 'explanation': 'Conflict', 'question': 'Which approach?', 'options': ['Omit', 'Wait']} for g in groups])))
    with pytest.raises(ValueError):
        group_duplicate_conflicts(client, SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60),
                                  SimpleNamespace(data={}), [{'id': 'a'}, {'id': 'b'}])


def test_query_detection_returns_findings_without_questions_or_coverage_calls():
    from app.brand.conflicts import detect_query_conflicts
    requests = []
    def run(request, output_model):
        requests.append(request)
        import json
        assert 'extracted_restrictions' not in json.loads(request.input)
        return SimpleNamespace(parsed=output_model.model_validate({'conflicts': [{
            'explanation': 'Publication clearance is pending.',
            'statements': ['Publish the customer story.', 'External use is on hold.'],
            'source_ids': ['chunk'], 'affected_asset_ids': ['asset']}]}))
    result = detect_query_conflicts(SimpleNamespace(run=run),
        SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60),
        SimpleNamespace(data={}, id='campaign', brand_id='brand'), [],
        [{'chunk_id': 'chunk', 'content': 'External use is on hold.'}])
    assert len(result) == 1
    assert 'question' not in result[0]
    assert result[0]['source_ids'] == ['chunk']
    assert [r.operation for r in requests] == ['campaign_conflict_review']
    assert 'evidence_restrictions' not in result[0]



def test_question_schema_restricts_indices_to_current_findings():
    from pydantic import ValidationError
    def run(request, output_model):
        with pytest.raises(ValidationError):
            output_model.model_validate({'questions': [{'finding_indices': [2],
                'explanation': 'Conflict', 'question': 'Which approach?', 'options': ['Omit', 'Wait']}]})
        return SimpleNamespace(parsed=output_model.model_validate({'questions': [{
            'finding_indices': [0, 1], 'explanation': 'Conflict',
            'question': 'Which approach?', 'options': ['Omit', 'Wait']}]}))
    assert len(group_duplicate_conflicts(SimpleNamespace(run=run),
        SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60),
        SimpleNamespace(data={}), [{'id': 'a'}, {'id': 'b'}])) == 1


def test_final_answers_reuse_intake_but_changed_evidence_does_not(monkeypatch):
    from app.brand import conflicts
    evidence = [{'chunk_id': 'chunk', 'content': 'Original evidence'}]
    campaign = SimpleNamespace(data={
        'evidence_ledger': {'entry': evidence[0]}, 'conflicts': [{'id': 'issue', 'status': 'answered'}],
        'pending_conflict_question_ids': [],
        'conflict_intake': {'issue_ids': ['issue'], 'revision': 'old', 'requirements_revision': 'old'}})
    conflicts.rebind_answered_intake(campaign, 'after-answer')
    def unexpected(*a, **kw):
        raise AssertionError('Answered original evidence must not be reviewed again')
    monkeypatch.setattr(conflicts, 'review_campaign', unexpected)
    assert conflicts.prepare_conflict_intake(None, None, campaign, [], evidence, 'after-answer') is False
    assert campaign.data['conflict_intake']['revision'] != conflicts.conflict_intake_key(
        'after-answer', [{'chunk_id': 'chunk', 'content': 'Changed evidence'}])
