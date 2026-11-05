from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.campaigns import Campaign, Message
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import TrackedOpenAI
from app.memory import (
    Interpretation,
    Memory,
    MemoryEvent,
    context,
    is_campaign_only,
    learn,
    memory_request_only,
)


def test_routing_is_independent_of_memory():
    assert is_campaign_only('Add two relevant hashtags to the first post for this campaign only.')
    assert not memory_request_only('Add hashtags to the first post.')
    assert memory_request_only('Remember: use short paragraphs for future campaigns.')
    assert not memory_request_only('Remember short paragraphs, and shorten post two.')


@pytest.mark.parametrize('text,changes,expected,only', [
    ('Add hashtags to the first post.', [{'existing_id':'m', 'text':'Always use hashtags', 'scope':'lasting', 'evidence':'Add hashtags to the first post.'}], 'Never use hashtags', False),
    ('For this post, use hashtags.', [{'existing_id':'m', 'text':'Always use hashtags', 'scope':'campaign', 'evidence':'For this post, use hashtags.'}], 'Never use hashtags', False),
    ('From now on, always use hashtags.', [{'existing_id':'m', 'text':'Always use hashtags', 'scope':'lasting', 'evidence':'From now on, always use hashtags.'}], 'Always use hashtags', False),
    ('Remember for future campaigns: use short paragraphs and no hashtags in LinkedIn posts.', [{'existing_id':'m', 'text':'Use short paragraphs in LinkedIn posts.', 'scope':'lasting', 'evidence':'use short paragraphs'}], 'Use short paragraphs in LinkedIn posts.', True),
    ('Remember: always use hashtags.', [{'existing_id':'m', 'text':'Always use hashtags', 'scope':'lasting', 'evidence':'Remember: always use hashtags.'}], 'Always use hashtags', True),
    ('Remember short paragraphs, and shorten post two.', [{'existing_id':'m', 'text':'Use short paragraphs', 'scope':'lasting', 'evidence':'Remember short paragraphs'}], 'Use short paragraphs', False),
])
def test_learning_scope_and_execution(tmp_path, monkeypatch, text, changes, expected, only):
    engine = create_database_engine(f'sqlite:///{tmp_path}/memory.db')
    initialize_database(engine)
    calls = []
    def run(self, request, **kwargs):
        calls.append(request)
        return SimpleNamespace(parsed=Interpretation(changes=changes), run_id='run')
    monkeypatch.setattr(TrackedOpenAI, 'run', run)
    with Session(engine) as session:
        campaign = Campaign(id='c', brand_id='b', data={'messages':[], 'plans':[], 'trace':[{'name':'Revise draft', 'status':'completed'}]})
        session.add_all([campaign, Memory(id='m', brand_id='b', text='Never use hashtags', updated_at='now')])
        session.commit()
        body = Message(text=text, version=0, request_id='request')
        settings = Settings(_env_file=None, openai_api_key='fake')
        assert learn(session, campaign, body, settings) is only
        assert context(session, 'b')[0]['text'] == expected
        if only:
            assert campaign.data['trace'] == []
        assert learn(session, campaign, body, settings) is only
        assert len(calls) == 1
        events = list(session.scalars(select(MemoryEvent)))
        assert len(events) == (expected != 'Never use hashtags')
        if events:
            assert events[0].data['before']['text'] == 'Never use hashtags'
            assert events[0].data['source']['message_id'] == 'request'
    engine.dispose()


def test_concurrent_change_is_preserved(tmp_path, monkeypatch):
    from app.memory import Entry, update
    engine = create_database_engine(f'sqlite:///{tmp_path}/race.db')
    initialize_database(engine)
    def run(self, request, **kwargs):
        with Session(engine) as other:
            update('b', 'm', Entry(text='Manual correction', revision=1), other)
        return SimpleNamespace(parsed=Interpretation(changes=[{
            'existing_id':'m', 'text':'Always use hashtags', 'scope':'lasting',
            'evidence':'Always use hashtags.'}]), run_id='run')
    monkeypatch.setattr(TrackedOpenAI, 'run', run)
    with Session(engine) as session:
        campaign = Campaign(id='c', brand_id='b', data={'messages':[]})
        session.add_all([campaign, Memory(id='m', brand_id='b', text='Original', updated_at='now')])
        session.commit()
        assert learn(session, campaign, Message(text='Always use hashtags.', version=0, request_id='r'),
                     Settings(_env_file=None, openai_api_key='fake')) is False
        assert context(session, 'b')[0]['text'] == 'Manual correction'
        assert 'could not' in campaign.data['memory_notice']
    engine.dispose()


def test_invalid_optional_evidence_does_not_warn_or_save(tmp_path, monkeypatch):
    engine = create_database_engine(f'sqlite:///{tmp_path}/optional.db')
    initialize_database(engine)
    def run(self, request, **kwargs):
        return SimpleNamespace(parsed=Interpretation(changes=[{
            'existing_id': None, 'text': 'Always use hashtags', 'scope': 'lasting',
            'evidence': 'Always use hashtags.'}]), run_id='run')
    monkeypatch.setattr(TrackedOpenAI, 'run', run)
    with Session(engine) as session:
        campaign = Campaign(id='c', brand_id='b', data={'messages': []})
        session.add(campaign)
        session.commit()
        assert learn(session, campaign, Message(text='Create a launch campaign.', version=0, request_id='r'),
                     Settings(_env_file=None, openai_api_key='fake')) is False
        assert context(session, 'b') == []
        assert campaign.data['memory_notice'] is None
    engine.dispose()
