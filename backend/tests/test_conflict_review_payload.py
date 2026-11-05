"""Answer continuation must budget source text, not duplicated audit metadata."""
import copy
import json
from types import SimpleNamespace

from app.brand import conflict_coverage as coverage
from app.config import Settings


def test_answered_review_omits_hashes_without_losing_dialogue_or_sources(monkeypatch):
    snapshot = {f'chunk-{i}': 'a' * 64 for i in range(4000)}
    question = {'id': 'q', 'text': 'Which hero?', 'options': ['Working room', 'Product UI'],
                'answer': 'Working room', 'source_snapshot': snapshot}
    messages = [{'role': 'user', 'text': 'Use a working room.'},
                {'role': 'assistant', 'text': 'Which hero?', 'question': question},
                {'role': 'user', 'text': 'Working room'}]
    campaign = SimpleNamespace(id='c', brand_id='b', data={
        'messages': messages, 'questions': [question]})
    saved = copy.deepcopy(campaign.data)
    evidence = [{'chunk_id': 'hero', 'content': 'Product pages use product UI.'}]
    seen = []
    monkeypatch.setattr(coverage, 'count_tokens', lambda text: len(text) // 4)
    monkeypatch.setattr(coverage, 'MAX_INPUT_TOKENS', 10000)

    def run(request, *, output_model):
        payload = json.loads(request.input)
        seen.append(payload)
        assert 'source_snapshot' not in request.input
        assert payload['conversation'][0] == messages[0]
        assert payload['conversation'][2] == messages[2]
        assert payload['conversation'][1]['question']['options'] == question['options']
        assert payload['current_decisions'][0]['answer'] == 'Working room'
        assert payload['passages'][0]['content'] == evidence[0]['content']
        return SimpleNamespace(parsed=output_model.model_validate({
            'conflicts': [], 'limitations': [], 'blocking_gaps': []}), run_id='test')

    result = coverage.review_campaign(SimpleNamespace(run=run), Settings(_env_file=None),
        campaign, messages, evidence)
    assert result['findings'] == []
    assert len(seen) == 2
    assert campaign.data == saved


def test_automatic_resolution_keeps_origin_and_answer():
    campaign = SimpleNamespace(data={'conflicts': [{
        'id': 'claim', 'question': 'Which wording?', 'status': 'resolved',
        'resolution_origin': 'confirmed_authority', 'resolution': 'Approved exact wording',
        'resolution_snapshot': {'chunk': 'hash'}}]})
    decision, = coverage.review_decisions(campaign)
    assert decision['origin'] == 'confirmed_authority'
    assert decision['answer'] == 'Approved exact wording'
    assert 'source_snapshot' not in decision
