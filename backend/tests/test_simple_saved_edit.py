from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.api import campaigns as api
from app.config import Settings
from app.db import create_database_engine, initialize_database


@pytest.mark.parametrize('new_body', ['word ' * 700, 'Changed wording.'])
def test_editorial_edit_is_one_call_and_ignores_blog_minimum(tmp_path, monkeypatch, new_body):
    from app.models import Brand
    engine = create_database_engine(f'sqlite:///{tmp_path}/edit.db')
    initialize_database(engine)
    draft = {'asset_id': 'blog', 'format': 'blog', 'plan_index': 0, 'title': 'Title',
        'body': 'word ' * 902, 'source_ids': ['original'], 'image': {'image_id': 'keep'},
        'publication_details': {'byline': 'Confirmed author'}, 'byline': 'Confirmed author',
        'validation': {'status': 'passed', 'findings': []}}
    other = {**deepcopy(draft), 'asset_id': 'other'}
    calls = []
    def forbidden(*a, **kw):
        pytest.fail('Simple edits must not retrieve evidence, select images, or review conflicts')
    monkeypatch.setattr(api, 'load_asset_package', forbidden)
    monkeypatch.setattr(api, 'review_conflicts', forbidden)
    monkeypatch.setattr(api, 'image_catalog', forbidden)
    class Client:
        def __init__(self, *a): pass
        def close(self): pass
        def run(self, request, output_model):
            import json

            from app.llm.client import strict_output_schema
            strict_output_schema(output_model)
            calls.append(request)
            assert request.operation == 'edit_saved_draft'
            payload = json.loads(request.input)
            assert set(payload) == {'instruction', 'format', 'draft'}
            return SimpleNamespace(run_id='edit', parsed=output_model.model_validate({
                **payload['draft'], 'body': new_body, 'requires_conflict_review': False}))
    monkeypatch.setattr(api, 'TrackedOpenAI', Client)
    with Session(engine) as session:
        session.add(Brand(id='b', name='Brand', source_directory='/tmp'))
        session.commit()
        session.add(api.Campaign(id='c', brand_id='b', state='needs_review', version=1, data={
            'messages': [], 'plans': [], 'generation': {'plan_index': 0, 'drafts': [draft, other]}}))
        session.commit()
        api.process_message('c', api.Message(text='Make the blog shorter' if 'word' in new_body else 'Change this wording', version=1),
            session, Settings(_env_file=None), forced_route=api.MessageRoute(route='revise', target_asset_id='blog', reply=''))
        saved = api.load(session, 'c').data['generation']
        assert len(calls) == 1
        assert saved['drafts'][0]['body'] == new_body
        for key in ('image', 'source_ids', 'publication_details'):
            assert saved['drafts'][0][key] == draft[key]
        assert saved['drafts'][0]['validation']['status'] == 'incomplete'
        assert saved['drafts'][1] == other
        assert saved['revision_history'][0] == draft
    engine.dispose()
