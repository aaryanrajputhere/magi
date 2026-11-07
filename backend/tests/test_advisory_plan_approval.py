import pytest
from fastapi.testclient import TestClient
from app.api import campaigns
from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize('action', ['approve', 'execute'])
def test_plan_corrections_are_logged_without_blocking_approval(tmp_path, monkeypatch, action):
    monkeypatch.setattr(campaigns, 'generate_drafts', lambda *args: None)
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'approval.db'}")
    recovery = {'status': 'review_required', 'issues': [{'explanation': 'CTA destination missing'}]}
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='needs_plan_review', data={
                'plans': [{'questions': [], 'assets': [{'id': 'landing'}], 'recovery': recovery}],
                'messages': []}))
            session.commit()
        response = client.post(f'/api/campaigns/c/{action}', json={'version': 0})
        assert response.status_code == 200, response.text
        assert response.json()['state'] == ('approved' if action == 'approve' else 'generating')
        assert 'approvals' not in response.json()
        with client.app.state.sessions() as session:
            entry = session.get(campaigns.Campaign, 'c').data['approvals'][0]
            assert entry['recovery'] == recovery
            assert entry['review_notice']
            assert entry['version'] == 0
        assert client.post(f'/api/campaigns/c/{action}', json={'version': 99}).status_code == 409
