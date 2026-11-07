from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import campaigns
from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize('resolved', [True, False])
def test_repair_action_reuses_recorded_defects_and_preserves_approval_gate(tmp_path, monkeypatch, resolved):
    from app.brand import plan_verification
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'repair.db'}")
    issue = {'asset_id': 'landing', 'field': 'hook', 'explanation': 'Name the audience.',
             'kind': 'content', 'rule_refs': []}
    plan = {'assets': [{'id': 'landing'}], 'questions': [],
            'recovery': {'status': 'review_required', 'issues': [issue]}}
    def verify(client, settings, campaign, messages, candidate, *, initial_defects, checkpoint=None):
        assert initial_defects == [issue]
        assert campaign.data['conflict_coverage']['snapshot_key'] == 'existing-review'
        if resolved:
            candidate.pop('recovery')
        return candidate
    monkeypatch.setattr(plan_verification, 'verify_plan', verify)
    monkeypatch.setattr(campaigns, 'TrackedOpenAI', lambda *a: SimpleNamespace(close=lambda: None))
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='needs_plan_review', data={
                'plans': [deepcopy(plan)], 'messages': [], 'conflict_coverage': {
                    'status': 'ready', 'snapshot_key': 'existing-review'}}))
            session.commit()
        result = client.post('/api/campaigns/c/repair-plan', json={'version': 0})
        assert result.status_code == 200
        saved = result.json()
        assert saved['state'] == ('awaiting_approval' if resolved else 'needs_plan_review')
        assert len(saved['plans']) == 2
        assert saved['plans'][0] == plan
        assert saved['conflict_coverage']['snapshot_key'] == 'existing-review'
        assert client.post('/api/campaigns/c/repair-plan', json={'version': 0}).status_code == 409
