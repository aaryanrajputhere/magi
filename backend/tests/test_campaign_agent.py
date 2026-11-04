import json
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import campaign_agent
from app.api import campaigns
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.models.agent import AgentTurn, CampaignLease


def test_agent_proposes_then_finishes_without_repeating_action(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'agent.db'}")
    initialize_database(engine)
    requests = []
    actions = []

    class Client:
        def __init__(self, *args):
            pass

        def run(self, request):
            requests.append(request)
            assert request.tool_choice == "required"
            name = "propose_plan" if len(requests) == 1 else "finish_turn"
            args = ({"instruction": "Five posts", "addition": False}
                    if name == "propose_plan" else {"text": "Plan ready for approval."})
            return SimpleNamespace(response={"output": [{
                "type": "function_call", "name": name,
                "arguments": json.dumps(args), "call_id": str(len(requests)),
            }]})

        def close(self):
            pass

    def process(identifier, body, session, settings, **kwargs):
        actions.append(body.text)
        c = campaigns.load(session, identifier)
        c.state = "awaiting_approval"
        c.version += 1
        session.commit()
        return campaigns.snapshot(c)

    monkeypatch.setattr(campaign_agent, "TrackedOpenAI", Client)
    monkeypatch.setattr(campaigns, "process_message", process)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id="c", brand_id="b", data={"messages": []}))
            session.commit()
            body = campaigns.Message(text="Five posts", version=0, request_id="test")
            result = campaign_agent.run_turn("c", body, session, Settings(_env_file=None))
            assert result["state"] == "awaiting_approval"
            assert session.get(AgentTurn, "c:test").status == "completed"
            assert session.scalar(select(CampaignLease)) is None
            assert len(actions) == 1
            assert len(requests) == 1
            assert campaign_agent.run_turn("c", body, session, Settings(_env_file=None)) == result
            assert len(actions) == 1
    finally:
        engine.dispose()


def test_clarification_then_approval_does_not_replan(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'clarification.db'}")
    initialize_database(engine)
    actions = []

    def process(identifier, body, session, settings, **kwargs):
        actions.append((body.text, kwargs['forced_route'].route))
        c = campaigns.load(session, identifier)
        c.data = {**c.data, 'plans': [{'questions': [], 'assets': [{'id': 'post'}]}]}
        c.state = 'awaiting_approval'
        c.version += 1
        session.commit()
        return campaigns.snapshot(c)

    def no_model(*args, **kwargs):
        raise AssertionError('Clarification and approval must not call the agent model')

    monkeypatch.setattr(campaigns, 'process_message', process)
    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', no_model)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='clarifying',
                data={'messages': [], 'plans': [{'questions': ['Proceed without statistics?'], 'assets': []}]}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='confirm', version=0),
                                             session, Settings(_env_file=None))
            assert result['state'] == 'awaiting_approval'
            result = campaign_agent.run_turn('c', campaigns.Message(text='approved', version=result['version']),
                                             session, Settings(_env_file=None))
            assert actions == [('confirm', 'campaign')]
            assert 'Approve & generate' in result['messages'][-1]['text']
            assert 'generation' not in result
            assert session.scalar(select(CampaignLease)) is None
    finally:
        engine.dispose()


def test_addition_without_drafts_uses_normal_plan(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'addition.db'}")
    initialize_database(engine)
    routes = []

    def run(self, request):
        return SimpleNamespace(response={'output': [{'type': 'function_call',
            'name': 'propose_plan', 'arguments': json.dumps({'instruction': 'Five assets', 'addition': True}),
            'call_id': 'plan'}]})

    def process(identifier, body, session, settings, **kwargs):
        routes.append(kwargs['forced_route'].route)
        c = campaigns.load(session, identifier)
        c.state = 'awaiting_approval'
        session.commit()
        return campaigns.snapshot(c)

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    monkeypatch.setattr(campaigns, 'process_message', process)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': [], 'plans': []}))
            session.commit()
            campaign_agent.run_turn('c', campaigns.Message(text='Five assets', version=0),
                                    session, Settings(_env_file=None))
            assert routes == ['campaign']
    finally:
        engine.dispose()
