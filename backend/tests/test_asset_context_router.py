from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.brand.retrieval import RetrievalError
from app.llm.asset_context_router import route_context, routed_package


def test_router_runs_once_and_preserves_originals():
    packages = {'a': {'evidence': [{'chunk_id': 'original-id', 'content': 'Exact original.'}],
                      'rule_pack': {'format': 'linkedin', 'limits': []}}}
    before = deepcopy(packages)
    plan = {'assets': [{'id': 'a', 'format': 'linkedin'}]}
    calls = []
    def run(request, output_model):
        calls.append(request)
        return SimpleNamespace(run_id='routing', parsed=output_model.model_validate({'assets': [{
            'asset_id': 'a', 'writing': ['s0'], 'validation': ['s0'], 'image': [],
            'production_dependencies': ['Missing approved URL']}]}))
    client = SimpleNamespace(run=run)
    campaign = SimpleNamespace(id='c', brand_id='b')
    settings = SimpleNamespace(magi_retrieval_planner_model='test')
    routing = route_context(client, settings, campaign, plan, packages)
    assert route_context(client, settings, campaign, plan, packages, routing) == routing
    assert len(calls) == 1
    result = routed_package(packages['a'], routing, 'a', 'writing', packages)
    assert result['evidence'] == packages['a']['evidence']
    assert packages == before
    assert result['production_dependencies'] == ['Missing approved URL']


def test_router_rejects_unknown_references():
    def run(request, output_model):
        return SimpleNamespace(run_id='r', parsed=output_model.model_validate({'assets': [{
            'asset_id': 'a', 'writing': ['fake'], 'validation': ['fake'], 'image': [],
            'production_dependencies': []}]}))
    with pytest.raises(RetrievalError, match='unknown source'):
        route_context(SimpleNamespace(run=run), SimpleNamespace(magi_retrieval_planner_model='test'),
                      SimpleNamespace(id='c', brand_id='b'), {'assets': [{'id': 'a'}]},
                      {'a': {'evidence': [{'chunk_id': 'real', 'content': 'Original'}]}})


def test_missing_image_is_not_sent_to_text_repair():
    from app.brand.draft_validation import validate_and_repair
    calls = []
    def run(request, output_model):
        calls.append(request.operation)
        return SimpleNamespace(run_id='check', parsed=output_model.model_validate({'findings': [{
            'scope': 'production', 'draft_quote': '', 'rule_chunk_id': 'image',
            'source_span': 'image:0', 'dependency_kind': 'image_attachment',
            'rule_quote': 'Every post needs an image.', 'correction': 'Select an approved image.'}]}))
    package = {'evidence': [{'chunk_id': 'image', 'content': 'Every post needs an image.'}],
               'rule_pack': {'format': 'linkedin', 'limits': [], 'version': 'v1',
                             'evidence': [{'chunk_id': 'image', 'content': 'Every post needs an image.'}]}}
    result = validate_and_repair(SimpleNamespace(run=run),
        SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={'messages': []}),
        {'id': 'a', 'format': 'linkedin'}, {'body': 'Original copy', 'source_ids': []}, package)
    assert calls == ['campaign_asset_validation']
    assert result['status'] == 'needs_review'
    assert result['production_dependencies'] == ['Select an approved image.']
