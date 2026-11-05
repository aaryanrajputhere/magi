from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.brand.conflict_coverage import run_review
from app.db import create_database_engine, initialize_database
from app.llm.client import ModelRequest, ModelRunError
from app.models import LLMRun


@pytest.mark.parametrize('reason', ['max_output_tokens', 'content_filter', None])
def test_review_retries_only_output_limit(tmp_path, reason):
    engine = create_database_engine(f'sqlite:///{tmp_path / "retry.db"}')
    initialize_database(engine)
    with Session(engine) as session:
        session.add(LLMRun(id='failed', operation='campaign_conflict_find',
            requested_model='test', status='incomplete',
            error={'details': {'reason': reason}}, request_body={}, context={}, timeout_seconds=300))
        session.commit()
    requests = []
    result = object()

    def run(request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            raise ModelRunError('failed', 'incomplete')
        return result

    request = ModelRequest(operation='campaign_conflict_find', model='test',
        instructions='Review all evidence', input='original evidence', max_output_tokens=24000)
    if reason == 'max_output_tokens':
        assert run_review(SimpleNamespace(run=run), request, None, engine) is result
        assert requests[1].max_output_tokens == 48000
        assert requests[1].input == request.input
        assert request.max_output_tokens == 24000
    else:
        with pytest.raises(ModelRunError):
            run_review(SimpleNamespace(run=run), request, None, engine)
        assert len(requests) == 1


def test_second_incomplete_response_is_not_retried(tmp_path):
    engine = create_database_engine(f'sqlite:///{tmp_path / "bounded.db"}')
    initialize_database(engine)
    with Session(engine) as session:
        session.add(LLMRun(id='failed', operation='campaign_conflict_find',
            requested_model='test', status='incomplete',
            error={'details': {'reason': 'max_output_tokens'}}, request_body={}, context={}, timeout_seconds=300))
        session.commit()
    calls = []

    def run(request, **kwargs):
        calls.append(request)
        raise ModelRunError('failed', 'incomplete')

    request = ModelRequest(operation='campaign_conflict_find', model='test',
        instructions='Review', input='evidence', max_output_tokens=24000)
    with pytest.raises(ModelRunError):
        run_review(SimpleNamespace(run=run), request, None, engine)
    assert len(calls) == 2
