import pytest
from app.brand.campaign_requirements import normalize_requirements
from app.brand.retrieval import RetrievalError


def test_same_as_above_resolves_chains_without_mutating_input():
    original = {'assets': [{'id': 'a', 'audience': 'Security leaders'},
                           {'id': 'b', 'audience': 'Same as above'},
                           {'id': 'c', 'audience': 'Same as above.'}]}
    result = normalize_requirements(original)
    assert [a['audience'] for a in result['assets']] == ['Security leaders'] * 3
    assert original['assets'][1]['audience'] == 'Same as above'


def test_above_requires_a_preceding_asset():
    with pytest.raises(RetrievalError, match='No preceding asset'):
        normalize_requirements({'assets': [{'id': 'a', 'audience': 'Same as above'}]})


def test_explicit_unknown_references_still_fail():
    with pytest.raises(RetrievalError, match='Unknown requirement reference'):
        normalize_requirements({'assets': [{'id': 'a', 'audience': 'Same as missing'}]})


def test_unique_shortened_asset_reference():
    result = normalize_requirements({'assets': [
        {'id': 'asset1-linkedin1', 'audience': 'Security leaders'},
        {'id': 'asset2-linkedin2', 'audience': 'Same as asset1'}]})
    assert result['assets'][1]['audience'] == 'Security leaders'


def test_ambiguous_shortened_reference_is_rejected():
    with pytest.raises(RetrievalError, match='Ambiguous requirement reference'):
        normalize_requirements({'assets': [
            {'id': 'asset1-linkedin', 'audience': 'Security leaders'},
            {'id': 'asset1-email', 'audience': 'Engineers'},
            {'id': 'asset2', 'audience': 'Same as asset1'}]})


def test_shortened_reference_does_not_match_partial_number():
    with pytest.raises(RetrievalError, match='Unknown requirement reference'):
        normalize_requirements({'assets': [
            {'id': 'asset10-linkedin', 'audience': 'Security leaders'},
            {'id': 'asset2', 'audience': 'Same as asset1'}]})


def test_cyclic_references_are_rejected():
    with pytest.raises(RetrievalError, match='Cyclic requirement reference'):
        normalize_requirements({'assets': [
            {'id': 'a', 'audience': 'Same as b'},
            {'id': 'b', 'audience': 'Same as a'}]})


@pytest.mark.parametrize('change_assets,still_invalid', [(False, False), (True, False), (False, True)])
def test_extraction_repairs_invalid_references_once(change_assets, still_invalid):
    from types import SimpleNamespace
    from app.brand.campaign_requirements import CampaignRequirements, extract_requirements
    base = {'topic': 'Campaign', 'topic_details': [], 'constraints': [], 'unknowns': [], 'issues': [],
            'assets': [{'id': 'post', 'format': 'linkedin', 'audience': 'Same as missing',
                        'purpose': 'Introduce product', 'timing': None, 'sender_or_byline': None,
                        'constraints': []}]}
    import copy
    fixed = copy.deepcopy(base)
    fixed['assets'][0]['audience'] = 'Same as missing' if still_invalid else 'Security leaders'
    if change_assets:
        fixed['assets'][0]['id'] = 'renamed'
    requests = []
    def run(request, **kwargs):
        requests.append(request)
        return SimpleNamespace(parsed=CampaignRequirements.model_validate(base if len(requests) == 1 else fixed),
                               run_id=str(len(requests)))
    client = SimpleNamespace(run=run)
    settings = SimpleNamespace(magi_retrieval_planner_model='model')
    if change_assets or still_invalid:
        with pytest.raises(RetrievalError) as caught:
            extract_requirements(client, settings, 'brand', 'campaign', {'conversation': 'Security leaders'})
        assert caught.value.code == 'invalid_requirement_repair'
    else:
        result = extract_requirements(client, settings, 'brand', 'campaign', {'conversation': 'Security leaders'})
        assert result['assets'][0]['audience'] == 'Security leaders'
        assert result['extraction_run_id'] == '1'
    assert [r.operation for r in requests] == ['campaign_requirements', 'campaign_requirements_repair']
