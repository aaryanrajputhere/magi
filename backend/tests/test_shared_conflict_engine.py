from types import SimpleNamespace

from test_conflict_coverage import ScriptedClient, scenario

from app.brand.conflict_coverage import review_campaign
from app.config import Settings


def test_review_returns_report_without_mutating_campaign(monkeypatch):
    monkeypatch.setattr('app.brand.conflict_coverage.count_tokens', lambda text: len(text) // 4)
    request, rule = scenario()
    client = ScriptedClient(request, rule)
    campaign = SimpleNamespace(id='c', brand_id='b', data={})
    result = review_campaign(client, Settings(_env_file=None), campaign,
        [{'role': 'user', 'text': request}], [{'chunk_id': 's1', 'content': rule}],
        candidate={'body': 'candidate'})
    assert campaign.data == {}
    assert result['report']['coverage_challenged']
    assert result['findings'][0]['source_ids'] == ['s1']
    assert len(client.requests) == 2
    assert all(request.model == 'gpt-6-luna' for request in client.requests)


def test_review_model_can_be_overridden_without_changing_planner(monkeypatch):
    monkeypatch.setattr('app.brand.conflict_coverage.count_tokens', lambda text: len(text) // 4)
    request, rule = scenario()
    client = ScriptedClient(request, rule)
    settings = Settings(_env_file=None, magi_conflict_review_model='test-review-model')
    campaign = SimpleNamespace(id='c', brand_id='b', data={})
    review_campaign(client, settings, campaign, [{'role': 'user', 'text': request}],
                    [{'chunk_id': 's1', 'content': rule}])
    assert all(call.model == 'test-review-model' for call in client.requests)
    assert settings.magi_retrieval_planner_model == 'gpt-4.1-mini'


def test_essential_gap_still_fails_closed(monkeypatch):
    import pytest

    from app.brand.retrieval import RetrievalError

    monkeypatch.setattr('app.brand.conflict_coverage.count_tokens', lambda text: len(text) // 4)
    calls = []

    def run(request, output_model):
        calls.append(request.operation)
        return SimpleNamespace(run_id='gap', parsed=output_model.model_validate({
            'conflicts': [], 'limitations': [],
            'blocking_gaps': ['The requested factual result has no supporting evidence.']}))

    campaign = SimpleNamespace(id='c', brand_id='b', data={})
    with pytest.raises(RetrievalError, match='no supporting evidence'):
        review_campaign(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], [])
    assert calls == ['campaign_conflict_find', 'campaign_conflict_check']
    assert campaign.data == {}
