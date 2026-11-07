from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.api import campaigns as api
from app.config import Settings
from app.db import create_database_engine, initialize_database


def test_saved_recheck_preserves_plan_image_and_records_unresolved_url(tmp_path, monkeypatch):
    from app.models import Brand
    engine = create_database_engine(f'sqlite:///{tmp_path}/recheck.db')
    initialize_database(engine)
    asset = {'id': 'a', 'format': 'linkedin', 'purpose': 'Explain modes'}
    plan = {'assets': [asset], 'goal': 'Awareness'}
    draft = {'asset_id': 'a', 'format': 'linkedin', 'plan_index': 0, 'title': 'Modes',
             'body': 'Observe mode records.', 'source_ids': [], 'image': {'image_id': 'pic', 'alt_text': 'Original alt'}}
    source = {'chunk_id': 'link', 'content': 'Use one approved destination link.'}
    package = {'evidence': [source], 'rule_pack': {'version': 'v1', 'format': 'linkedin',
                                                 'limits': [], 'evidence': [source]}}
    monkeypatch.setattr(api, 'load_asset_package', lambda *a: deepcopy(package))
    monkeypatch.setattr(api, 'image_catalog', lambda *a: (None, [{'id': 'pic'}]))
    monkeypatch.setattr(api, 'memory_context', lambda *a: [])
    class Client:
        def __init__(self, *args): pass
        def close(self): pass
        def run(self, request, output_model):
            assert request.operation == 'campaign_asset_validation'
            return SimpleNamespace(run_id='test', parsed=output_model.model_validate({'findings': [{
                'scope': 'production', 'dependency_kind': 'destination_url', 'source_span': 'link:0',
                'draft_quote': '', 'rule_chunk_id': 'link', 'rule_quote': 'Ignored regenerated quote',
                'correction': 'Supply the approved URL.'}]}))
    monkeypatch.setattr(api, 'TrackedOpenAI', Client)
    with Session(engine) as session:
        session.add(Brand(id='b', name='Brand', source_directory='/tmp'))
        session.commit()
        session.add(api.Campaign(id='c', brand_id='b', state='needs_review', version=1,
            data={'messages': [], 'plans': [plan], 'generation': {'plan_index': 0,
                'drafts': [draft, {**deepcopy(draft), 'asset_id': 'keep', 'body': 'Do not review or rewrite',
                                   'validation': {'status': 'passed'}, 'status': 'completed'}]}}))
        session.commit()
        api.recheck_saved_drafts('c', api.DraftRecheck(version=1, asset_ids=['a']), session, Settings(_env_file=None))
        session.expire_all()
        saved = api.load(session, 'c')
        assert saved.data['plans'] == [plan]
        assert saved.data['generation']['drafts'][1]['body'] == 'Do not review or rewrite'
        assert saved.data['generation']['drafts'][1]['validation']['status'] == 'passed'
        result = saved.data['generation']['drafts'][0]
        assert result['body'] == draft['body']
        assert result['image'] == draft['image']
        assert result['validation']['status'] == 'passed'
        assert result['publication_readiness'] == 'blocked'
        assert result['dependency_records'][0]['kind'] == 'destination_url'


def test_publication_details_validate_inputs_and_keep_plan_and_images(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException

    from app.models import Brand
    engine = create_database_engine(f'sqlite:///{tmp_path}/details.db')
    initialize_database(engine)
    plan = {'assets': [{'id': 'a'}], 'goal': 'Original plan'}
    draft = {'asset_id': 'a', 'body': 'Original copy', 'image': {'image_id': 'pic'},
             'validation': {'status': 'passed'}, 'dependency_records': []}
    monkeypatch.setattr(api, 'image_catalog', lambda *a: (None, [{'id': 'pic'}]))
    with Session(engine) as session:
        session.add(Brand(id='b', name='Brand', source_directory='/tmp'))
        session.commit()
        session.add(api.Campaign(id='c', brand_id='b', state='needs_review', version=1,
            data={'messages': [], 'plans': [plan], 'generation': {'plan_index': 0, 'drafts': [draft]}}))
        session.commit()
        with pytest.raises(HTTPException):
            api.update_publication_details('c', 'a', api.PublicationDetails(
                version=1, destination_url='javascript:bad'), session, Settings(_env_file=None))
        api.update_publication_details('c', 'a', api.PublicationDetails(version=1,
            destination_url='https://example.test/approved', byline='Confirmed Author, Engineer',
            publication_date='2027-01-12'), session, Settings(_env_file=None))
        session.expire_all()
        saved = api.load(session, 'c')
        result = saved.data['generation']['drafts'][0]
        assert saved.data['plans'] == [plan]
        assert result['body'] == draft['body']
        assert result['image'] == draft['image']
        assert result['byline'] == 'Confirmed Author, Engineer'
        assert result['validation']['status'] == 'incomplete'
        assert result['publication_readiness'] == 'blocked'
