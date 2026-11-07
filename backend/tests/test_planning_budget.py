"""Planning is optimized by reuse, never stopped by a token-spending gate."""
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.api.campaigns import Campaign
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import ModelRequest, TrackedOpenAI
from app.llm.context import RunContext
from app.models.run import LLMRun


def test_planning_logging_does_not_count_or_block_over_previous_budget(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'no-budget.db'}")
    initialize_database(engine)
    with Session(engine) as session:
        session.add(Campaign(id='c', brand_id='b', data={}))
        session.add(LLMRun(id='previous', operation='campaign_plan', requested_model='test',
            request_body={}, context={'campaign_id': 'c'}, timeout_seconds=60,
            input_tokens=600_000, status='completed'))
        session.commit()
    # No input-token counter exists on this SDK; _start must never ask for one.
    client = TrackedOpenAI(engine, Settings(_env_file=None), sdk=SimpleNamespace())
    request = ModelRequest(operation='campaign_plan', model='gpt-6-luna', instructions='Rules',
        input='Plan', max_output_tokens=100, context=RunContext(campaign_id='c'))
    client._start('new', request, {'model': request.model, 'input': request.input})
    with Session(engine) as session:
        run = session.get(LLMRun, 'new')
        assert run.status == 'started'
        assert 'planning_reserved_input_tokens' not in run.context
    engine.dispose()
