from copy import deepcopy
from types import SimpleNamespace

from app.brand.campaign_requirements import require_deliverables
from app.brand.plan_recovery import recover_plan
from app.brand.plan_verification import approval_error


def campaign():
    return SimpleNamespace(version=3, state='planning_error', data={
        'requirements': {'topic': 'Launch', 'constraints': [], 'assets': [
            {'id': 'blog-article-1', 'format': 'blog', 'audience': 'Leaders',
             'purpose': 'Explain launch', 'timing': '2027-02-18'},
            {'id': 'post', 'format': 'linkedin', 'audience': 'Engineers',
             'purpose': 'Introduce product', 'timing': None}]},
        'plans': [], 'messages': [], 'planning_error': 'failed',
        'pending_candidate': {'assets': [{'id': 'blog-article-1', 'timing': 'wrong',
                                         'angle': 'Product walkthrough', 'hook': 'Unverified claim'}]}})


def test_failed_plan_returns_complete_saved_contract_and_blocks_unverified_approval():
    c = campaign()
    original = deepcopy(c.data['requirements'])
    plan = recover_plan(c, ValueError('invalid deliverables'))
    require_deliverables(plan, original)
    assert len(plan['assets']) == 2
    assert plan['assets'][0]['angle'] == 'Product walkthrough'
    assert 'Unverified claim' not in str(plan)
    assert c.data['requirements'] == original
    assert c.data['plans'][-1] == plan
    assert c.data['messages'][-1]['plan_index'] == 0
    assert 'planning_error' not in c.data
    assert 'pending_candidate' not in c.data
    assert c.version == 4
    assert c.state == 'awaiting_approval'
    assert approval_error(c)


def test_recovery_does_not_invent_requirements():
    c = SimpleNamespace(data={}, version=1)
    assert recover_plan(c, ValueError()) is None
    assert c.data == {}
    assert c.version == 1


def test_addition_recovery_keeps_existing_plan_and_drafts():
    c = campaign()
    base = {'assets': [{'id': 'existing', 'format': 'email', 'timing': None,
                       'audience': 'Customers', 'hook': 'Existing verified hook'}]}
    c.data['plans'] = [base]
    c.data['generation'] = {'drafts': [{'asset_id': 'existing', 'body': 'Saved copy'}]}
    before = deepcopy(c.data)
    plan = recover_plan(c, ValueError('failed'), addition=True)
    assert plan['assets'][0] == base['assets'][0]
    assert plan['extension'] == {'base_plan_index': 0,
                                'added_asset_ids': ['blog-article-1', 'post']}
    require_deliverables(plan, plan['requirements'])
    assert c.data['generation'] == before['generation']
    assert c.data['plans'][0] == before['plans'][0]


def test_preparation_failure_preserves_requirements_without_creating_plan(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from app.api import campaigns as api
    from app.brand.retrieval import RetrievalError
    from app.config import Settings
    from app.db import create_database_engine, initialize_database

    engine = create_database_engine(f'sqlite:///{tmp_path / "recovery.db"}')
    initialize_database(engine)
    monkeypatch.setattr(api, 'TrackedOpenAI', lambda *args: SimpleNamespace(close=lambda: None))

    def fail(*args, **kwargs):
        kwargs['requirements_ready'](campaign().data['requirements'])
        raise RetrievalError('Plan does not match saved campaign requirements', 'invalid_deliverables', 422)

    monkeypatch.setattr(api, 'plan_retrieval', fail)
    with Session(engine) as session:
        session.add(api.Campaign(id='c', brand_id='b', data={'messages': [], 'plans': []}))
        session.commit()
        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException, match='Plan does not match saved campaign requirements'):
            api.process_message('c', api.Message(text='Create campaign', version=0),
                session, Settings(_env_file=None),
                forced_route=api.MessageRoute(route='campaign', target_asset_id=None, reply=''))
        saved = api.load(session, 'c')
        assert saved.data['plans'] == []
        assert saved.data['requirements']['assets']
        assert saved.state == 'planning_error'
        assert saved.data['planning_error']


def test_agent_preparation_failure_does_not_recover_unreviewed_plan(tmp_path, monkeypatch):
    import json

    import pytest
    from fastapi import HTTPException
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app import campaign_agent
    from app.api import campaigns as api
    from app.brand.retrieval import RetrievalError
    from app.config import Settings
    from app.db import create_database_engine, initialize_database
    from app.models.agent import AgentTurn, CampaignLease

    engine = create_database_engine(f'sqlite:///{tmp_path / "agent-failure.db"}')
    initialize_database(engine)
    calls = iter(['extract_requirements', 'retrieve_context'])

    class Client:
        def __init__(self, *args):
            pass

        def run(self, request):
            name = next(calls)
            return SimpleNamespace(response={'output': [{'type': 'function_call',
                'name': name, 'arguments': json.dumps({'addition': False}), 'call_id': name}]})

        def close(self):
            pass

    def extract(*args, **kwargs):
        return {**campaign().data['requirements'], 'unknowns': [], 'issues': []}

    def fail(*args, **kwargs):
        raise RetrievalError('Channel limit does not match the quoted numbers')

    monkeypatch.setattr(campaign_agent, 'TrackedOpenAI', Client)
    monkeypatch.setattr(api, 'retrieve_campaign_context', fail)
    from app.brand import campaign_requirements
    monkeypatch.setattr(campaign_requirements, 'extract_requirements', extract)
    with Session(engine) as session:
        session.add(api.Campaign(id='c', brand_id='b', data={'messages': [], 'plans': []}))
        session.commit()
        with pytest.raises(HTTPException):
            campaign_agent.run_turn('c', api.Message(text='Create campaign', version=0,
                request_id='failure'), session, Settings(_env_file=None))
        saved = api.load(session, 'c')
        assert saved.data['plans'] == []
        assert saved.state == 'planning_error'
        assert 'Channel limit' in saved.data['planning_error']
        assert session.get(AgentTurn, 'c:failure').status == 'failed'
        assert session.scalar(select(CampaignLease)) is None
