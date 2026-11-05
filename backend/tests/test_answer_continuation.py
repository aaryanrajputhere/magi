from sqlalchemy.orm import Session

from app.api import campaigns
from app.config import Settings
from app.db import create_database_engine, initialize_database


def test_final_answer_continues_with_structured_route_and_saved_answers(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'continuation.db'}")
    initialize_database(engine)
    seen = []
    def resume(identifier, body, session, settings, forced_route=None, internal=False):
        assert internal is True
        assert forced_route.route == 'campaign'
        assert forced_route.target_asset_id is None
        saved = campaigns.load(session, identifier)
        assert saved.data['questions'][0]['answer'] == 'Omit'
        assert saved.data['pending_conflict_question_ids'] == []
        assert saved.data['retrieved_context']['requirements_revision'] == saved.data['requirements_revision']
        assert body.version == saved.version
        seen.append(identifier)
        return campaigns.snapshot(saved)
    monkeypatch.setattr(campaigns, 'process_message', resume)
    try:
        with Session(engine) as session:
            q = {'id': 'q', 'text': 'Include the story?', 'kind': 'choices',
                 'options': ['Omit', 'Wait'], 'answer': None, 'conflict_id': 'conflict'}
            campaign = campaigns.Campaign(id='c', brand_id='b', state='clarifying', data={
                'messages': [{'role': 'user', 'text': 'Brief'}], 'questions': [q],
                'pending_conflict_question_ids': ['q'],
                'requirements': {'topic': 'Campaign', 'assets': [], 'issues': []},
                'retrieved_context': {'artifact_id': 'evidence'}})
            session.add(campaign)
            session.commit()
            campaigns.message('c', campaigns.Message(text='Omit', question_id='q', version=campaign.version),
                              session, Settings(_env_file=None))
            assert seen == ['c']
    finally:
        engine.dispose()
