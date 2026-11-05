from types import SimpleNamespace

import pytest

from app.brand.conflicts import DecisionCoverageCheck, conflict_review_errors, findings_schema, review_conflicts
from app.brand.retrieval import RetrievalError

EVIDENCE = [{'chunk_id': 'chunk-a', 'content': 'An externally published result needs approval.'}]


def parsed(status='conflict', indices=None, findings=True):
    return findings_schema(EVIDENCE).model_validate({
        'conflicts': [{'explanation': 'Publication permission is pending.',
                       'statements': ['Publish the result.', 'Approval is pending.'],
                       'source_ids': ['chunk-a'], 'affected_asset_ids': [],
                       'question': 'Should this campaign wait for approval?',
                       'options': ['Wait for approval', 'Omit the result']}] if findings else [],
        'source_checks': {'source_0': {'status': status, 'reason': 'Permission is pending.',
                                      'conflict_indices': [0] if indices is None else indices}}})


def test_missing_findings_and_broken_indices_are_invalid():
    errors = conflict_review_errors(parsed(indices=[0, 1, 2], findings=False), EVIDENCE)
    assert len([e for e in errors if 'does not exist' in e]) == 3


def test_status_and_citation_links_must_agree():
    assert conflict_review_errors(parsed(status='consistent'), EVIDENCE) == []
    assert conflict_review_errors(parsed(indices=[]), EVIDENCE)
    assert conflict_review_errors(parsed(indices=[-1]), EVIDENCE)
    assert conflict_review_errors(parsed(), EVIDENCE) == []
    assert conflict_review_errors(parsed(status='consistent', indices=[], findings=False), EVIDENCE) == []


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_one_correction_uses_same_evidence_and_never_invents_fallback(repair_succeeds):
    requests = []
    def run(request, **kwargs):
        requests.append(request)
        if request.operation == 'campaign_conflict_coverage':
            return SimpleNamespace(parsed=DecisionCoverageCheck(problems=[]), run_id='coverage-run')
        value = parsed() if len(requests) == 2 and repair_succeeds else parsed(findings=False)
        return SimpleNamespace(parsed=value, run_id='review-run')
    client = SimpleNamespace(run=run)
    campaign = SimpleNamespace(id='campaign', brand_id='brand', data={})
    settings = SimpleNamespace(magi_retrieval_planner_model='test-model', magi_openai_timeout_seconds=60)
    if repair_succeeds:
        findings = review_conflicts(client, settings, campaign, [], EVIDENCE, include_history=False)
        assert len(findings) == 1
    else:
        with pytest.raises(RetrievalError, match='remained incomplete'):
            review_conflicts(client, settings, campaign, [], EVIDENCE, include_history=False)
    assert [r.operation for r in requests] == ['campaign_conflict_review', 'campaign_conflict_review_repair'] + (['campaign_conflict_coverage'] if repair_succeeds else [])
    import json
    repair = json.loads(requests[1].input)
    assert repair['original_review_input']['evidence'] == EVIDENCE
    assert repair['validation_errors']


def test_supporting_passage_can_link_to_existing_finding_without_primary_citation():
    evidence = [*EVIDENCE, {'chunk_id': 'chunk-b', 'content': 'Related publication guidance.'}]
    value = parsed().model_dump()
    value['source_checks']['source_1'] = {'status': 'consistent', 'reason': 'Supports the finding.',
                                        'conflict_indices': [0]}
    result = findings_schema(evidence).model_validate(value)
    assert conflict_review_errors(result, evidence) == []


@pytest.mark.parametrize('corrected', [True, False])
def test_wrong_subject_link_is_repaired_or_blocks_the_review(corrected):
    requests = []
    def run(request, **kwargs):
        requests.append(request)
        if request.operation == 'campaign_conflict_coverage':
            after_repair = any(r.operation == 'campaign_conflict_review_repair' for r in requests)
            problems = [] if after_repair and corrected else [
                {'source_key': 'source_0',
                 'reason': 'Publication permission is linked to a finding about unrelated wording.'}]
            return SimpleNamespace(parsed=DecisionCoverageCheck(problems=problems), run_id='coverage-run')
        return SimpleNamespace(parsed=parsed(status='consistent'), run_id='review-run')
    client = SimpleNamespace(run=run)
    campaign = SimpleNamespace(id='campaign', brand_id='brand', data={})
    settings = SimpleNamespace(magi_retrieval_planner_model='test', magi_openai_timeout_seconds=60)
    if corrected:
        assert len(review_conflicts(client, settings, campaign, [], EVIDENCE, include_history=False)) == 1
    else:
        with pytest.raises(RetrievalError, match='Publication permission'):
            review_conflicts(client, settings, campaign, [], EVIDENCE, include_history=False)
    assert [r.operation for r in requests] == [
        'campaign_conflict_review', 'campaign_conflict_coverage',
        'campaign_conflict_review_repair', 'campaign_conflict_coverage']
    import json
    repair = json.loads(requests[2].input)
    assert 'unrelated wording' in repair['validation_errors'][0]
    assert repair['original_review_input']['evidence'] == EVIDENCE
