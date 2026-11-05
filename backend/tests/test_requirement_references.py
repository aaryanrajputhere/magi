import pytest
from app.brand.campaign_requirements import normalize_requirements
from app.brand.retrieval import RetrievalError


def test_forward_audience_reference_resolves_without_mutating_input():
    data = {'assets': [{'id': 'a', 'audience': 'Same as b'},
                       {'id': 'b', 'audience': 'Security leaders'}]}
    result = normalize_requirements(data)
    assert result['assets'][0]['audience'] == 'Security leaders'
    assert data['assets'][0]['audience'] == 'Same as b'


@pytest.mark.parametrize('assets', [
    [{'id': 'a', 'audience': 'Same as missing'}],
    [{'id': 'a', 'audience': 'Same as b'}, {'id': 'b', 'audience': 'Same as a'}]])
def test_invalid_references_fail_explicitly(assets):
    with pytest.raises(RetrievalError, match='reference'):
        normalize_requirements({'assets': assets})


def test_document_expansion_retains_missing_hold_without_duplicates(monkeypatch):
    from types import SimpleNamespace
    from app.brand import context
    class Session:
        def __init__(self, engine):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    doc = SimpleNamespace(id='doc', title='Customer story', relative_path='story.md')
    hold = SimpleNamespace(id='hold', heading_path=['Publication'], start_line=10, end_line=12,
                           content='Customer has not cleared external use')
    monkeypatch.setattr(context, 'Session', Session)
    def rows(session, brand):
        assert brand == 'brand'
        return [(hold, doc)], 0
    monkeypatch.setattr(context, 'corpus_rows', rows)
    groups = [{'results': [{'chunk_id': 'intro', 'document_id': 'doc'}]},
              {'results': [{'chunk_id': 'intro', 'document_id': 'doc'}]}]
    context.expand_document_sections(None, 'brand', groups)
    assert groups[0]['results'][1]['chunk_id'] == 'hold'
    assert groups[0]['results'][1]['content'] == hold.content
    assert len(groups[1]['results']) == 1
