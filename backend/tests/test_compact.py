import json
from types import SimpleNamespace

import pytest

from app.brand import compact
from app.brand.retrieval import RetrievalError


def test_compaction_keeps_all_unique_chunks_and_channel_isolation(monkeypatch):
    monkeypatch.setattr(compact, 'count_tokens', lambda _: 1)
    inputs = []
    saved = []

    class Client:
        def __init__(self, *args):
            pass

        def close(self):
            pass

        def run(self, request, **kwargs):
            payload = json.loads(request.input)
            inputs.append(payload)
            return SimpleNamespace(run_id='run', parsed=compact.Summary(entries=[
                compact.Entry(text=p['content'], source_ids=[p['chunk_id']])
                for p in payload['passages']]))

    def save(*args):
        result = {'id': str(len(saved)), **args[4]}
        saved.append(result)
        return result

    monkeypatch.setattr(compact, 'TrackedOpenAI', Client)
    monkeypatch.setattr(compact, 'save_artifact', save)
    groups = [
        {'status': 'success', 'query': {'id': name}, 'results': [
            {'chunk_id': identity, 'content': content}]}
        for name, identity, content in [
            ('topic_1', 'a', 'One statement'), ('topic_2', 'a', 'One statement'),
            ('topic_3', 'b', 'A differing statement'),
            ('channel__email__rules', 'c', 'Email guidance')]]
    result = compact.compact_tracks(None, 'brand',
        {'id': 'retrieval', 'corpus_fingerprint': 'fp', 'groups': groups},
        {}, {'assets': [{'format': 'email'}]}, SimpleNamespace(magi_retrieval_planner_model='test'), 'campaign')
    assert [[p['chunk_id'] for p in item['passages']] for item in inputs] == [['a', 'b']]
    assert result['tracks']['email']['context_method'] == 'direct'
    assert result['tracks']['email']['report']['supported'] == [{'text': 'Email guidance', 'source_ids': ['c']}]
    assert result['mode'] == 'compact'
    assert result['report']['conflicts'] == []
    assert len(result['tracks']['topic']['report']['supported']) == 2


def test_failed_retrieval_prevents_compaction():
    with pytest.raises(RetrievalError, match='Retrieval failed'):
        compact.compact_tracks(None, 'brand', {'groups': [
            {'status': 'failed', 'query': {'id': 'topic_1'}}]}, {}, {'assets': []}, None, 'campaign')


def test_compact_schema_limits_citations_to_supplied_chunks():
    from pydantic import ValidationError

    from app.llm.client import strict_output_schema

    model = compact.summary_model(['chunk-a', 'chunk-b'])
    strict_output_schema(model)
    model.model_validate({'entries': [{'text': 'Fact', 'source_ids': ['chunk-a']}]})
    for ids in ([], ['document-id']):
        with pytest.raises(ValidationError):
            model.model_validate({'entries': [{'text': 'Fact', 'source_ids': ids}]})
    with pytest.raises(ValidationError):
        model.model_validate({'entries': []})
