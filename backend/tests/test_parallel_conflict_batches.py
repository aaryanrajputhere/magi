"""Independent batches overlap, while verification waits for discovery."""
import json
from threading import Barrier, Lock
from types import SimpleNamespace

from app.brand import conflict_coverage as coverage
from app.config import Settings


def test_batches_overlap_and_passes_remain_ordered(monkeypatch):
    monkeypatch.setattr(coverage, 'count_tokens', lambda text: 1)
    monkeypatch.setattr(coverage, 'review_inputs', lambda content, fits: [content, content])
    barrier = Barrier(2)
    lock = Lock()
    completed = []

    def run(request, *, output_model):
        payload = json.loads(request.input)
        if request.operation == 'campaign_conflict_check':
            with lock:
                assert completed.count('campaign_conflict_find') == 2
            assert 'proposed_review' in payload
        barrier.wait(timeout=5)
        with lock:
            completed.append(request.operation)
        return SimpleNamespace(parsed=output_model.model_validate({
            'conflicts': [], 'limitations': ['Same limitation'], 'blocking_gaps': []}), run_id='test')

    result = coverage.review_campaign(SimpleNamespace(run=run), Settings(_env_file=None),
        SimpleNamespace(id='c', brand_id='b', data={}),
        [{'role': 'user', 'text': 'Write a launch post.'}],
        [{'chunk_id': 's', 'content': 'Use approved wording.'}])
    assert result['report']['review_batches'] == {'conflict_find': 2, 'conflict_check': 2}
    assert result['report']['limitations'] == ['Same limitation']
    assert len(completed) == 4
