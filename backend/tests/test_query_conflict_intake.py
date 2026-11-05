from types import SimpleNamespace

from app.brand import conflicts


def test_reviews_shared_pool_then_shows_all_findings_and_stops(monkeypatch):
    campaign = SimpleNamespace(data={'messages': []}, state='planning_busy')
    seen = []

    def review(client, settings, campaign, messages, evidence, **kwargs):
        seen.append([e['chunk_id'] for e in evidence])
        return [{'id': identity, 'status': 'unresolved', 'explanation': identity,
                 'question': f'Resolve {identity}?', 'options': ['Omit', 'Wait']} for identity in [e['chunk_id'] for e in evidence]]

    monkeypatch.setattr(conflicts, 'review_campaign', review)
    monkeypatch.setattr(conflicts, 'validate_detected_conflicts',
                        lambda client, settings, campaign, messages, findings, evidence: (findings, []))
    monkeypatch.setattr(conflicts, 'group_duplicate_conflicts', lambda client, settings, campaign, findings: findings)
    groups = [{'query': {'id': name}, 'results': [{'chunk_id': name, 'content': name}]}
              for name in ('fedramp', 'customer')]
    evidence = [g['results'][0] for g in groups]
    assert conflicts.prepare_conflict_intake(None, None, campaign, [], evidence, 'revision',
                                            query_groups=groups)
    assert seen == [['fedramp', 'customer']]
    assert len(campaign.data['conflicts']) == 2
    assert 'Resolve fedramp?' in campaign.data['messages'][-2]['question']['text']
    assert 'Resolve customer?' in campaign.data['messages'][-1]['question']['text']
    assert len(campaign.data['pending_conflict_question_ids']) == 2
    assert 'pending_question' not in campaign.data
    assert conflicts.prepare_conflict_intake(None, None, campaign, [], evidence, 'revision',
                                            query_groups=groups)
    assert len(seen) == 1


def test_review_receives_all_queries_together(monkeypatch):
    campaign = SimpleNamespace(data={'messages': []}, state='planning_busy')
    seen = []
    def review(*args, **kwargs):
        seen.append(([e['chunk_id'] for e in args[4]], kwargs['query_groups']))
        return []
    monkeypatch.setattr(conflicts, 'review_campaign', review)
    groups = [{'query': {'id': str(i)}, 'results': [{'chunk_id': str(i)}]}
              for i in range(3)]
    conflicts.prepare_conflict_intake(None, None, campaign, [], [], 'r', query_groups=groups)
    assert seen == [(['0', '1', '2'], groups)]
    assert campaign.data['conflict_intake']['query_reviews'][0]['query_id'] == 'campaign_wide'


def test_question_answers_save_without_model_calls(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    from app.api import campaigns
    from app.config import Settings
    from app.db import create_database_engine, initialize_database

    engine = create_database_engine(f"sqlite:///{tmp_path / 'answers.db'}")
    initialize_database(engine)
    monkeypatch.setattr(campaigns, 'process_message',
                        lambda identifier, body, session, settings, **kwargs:
                        campaigns.snapshot(campaigns.load(session, identifier)))
    questions = [{'id': name, 'text': name, 'options': ['Omit', 'Wait'], 'answer': None,
                  'kind': 'choices', 'conflict_id': name} for name in ('first', 'second')]
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='clarifying', data={
                'questions': questions, 'pending_conflict_question_ids': ['first', 'second'],
                'messages': [{'role': 'assistant', 'text': q['text'], 'question': q} for q in questions]}))
            session.commit()
            version = campaigns.detail('c', session)['version']
            for name in ('second', 'first'):
                result = campaigns.message('c', campaigns.Message(text='Omit', question_id=name,
                                           version=version), session, Settings(_env_file=None))
                version = result['version']
            assert result['pending_conflict_question_ids'] == []
            assert all(q['answer'] == 'Omit' for q in result['questions'])
            assert len(result['decisions']) == 2
            assert not result.get('plans')
    finally:
        engine.dispose()
