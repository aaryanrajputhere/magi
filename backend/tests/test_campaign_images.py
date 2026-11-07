import json
from review_helpers import review_payload
from types import SimpleNamespace

from app.api.campaigns import image_catalog


def test_catalog_excludes_restricted_missing_and_escaping_files(tmp_path):
    root = tmp_path / 'images'
    root.mkdir()
    (root / 'safe.png').write_bytes(b'image')
    (tmp_path / 'outside.png').write_bytes(b'image')
    (root / 'escape.png').symlink_to(tmp_path / 'outside.png')
    records = [dict(id=name, filename=filename, usage_rights='Brand owned', restrictions=restriction)
               for name, filename, restriction in [
                   ('safe', 'safe.png', ''), ('restricted', 'safe.png', 'Internal only'),
                   ('missing', 'missing.png', ''), ('escape', 'escape.png', ''),
                   ('path', '../outside.png', '')]]
    (root / 'metadata.json').write_text(json.dumps(records))
    session = SimpleNamespace(get=lambda *args: SimpleNamespace(source_directory=str(tmp_path)))
    _, catalog = image_catalog(session, SimpleNamespace(brand_id='b'), SimpleNamespace(magi_image_directory='images'))
    assert [r['id'] for r in catalog] == ['safe']


def test_image_selection_requires_eligible_image_and_description():
    import pytest
    from pydantic import ValidationError

    from app.api.campaigns import image_selection_schema
    from app.llm.client import strict_output_schema

    schema = image_selection_schema([{'id': 'product-diagram'}])
    strict_output_schema(schema)
    valid = {'image_id': 'product-diagram', 'alt_text': 'Runtime security diagram', 'reason': 'Explains the post topic'}
    assert schema.model_validate(valid).image_id == 'product-diagram'
    assert schema.model_validate({**valid, 'image_id': None}).image_id is None
    for invalid in ['invented']:
        with pytest.raises(ValidationError):
            schema.model_validate({**valid, 'image_id': invalid})
    with pytest.raises(ValidationError):
        schema.model_validate({**valid, 'alt_text': ''})


def test_catalog_uses_configured_mount_for_single_brand(tmp_path):
    root = tmp_path / 'images'
    root.mkdir()
    (root / 'photo.jpg').write_bytes(b'image')
    (root / 'metadata.json').write_text(json.dumps([
        {'id': 'photo', 'filename': 'photo.jpg', 'usage_rights': 'Brand owned'}
    ]))
    brand = SimpleNamespace(source_directory=str(tmp_path / 'missing-host-path'))
    session = SimpleNamespace(get=lambda *args: brand,
                              scalars=lambda *args: SimpleNamespace(all=lambda: [brand]))
    _, records = image_catalog(session, SimpleNamespace(brand_id='b'),
                               SimpleNamespace(magi_image_directory='images', magi_brand_source_dir=tmp_path))
    assert records[0]['id'] == 'photo'


def test_save_image_changes_only_target_and_rejects_unknown(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException
    from sqlalchemy.orm import Session

    from app.api import campaigns
    from app.db import create_database_engine, initialize_database

    engine = create_database_engine(f"sqlite:///{tmp_path / 'images.db'}")
    initialize_database(engine)
    monkeypatch.setattr(campaigns, 'image_catalog', lambda *args: (tmp_path, [
        {'id': 'hero', 'title': 'Containment illustration'}]))
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='ready', version=0,
                data={'generation': {'drafts': [
                    {'asset_id': 'one', 'body': 'Short post'},
                    {'asset_id': 'two', 'body': 'Other post', 'image': None}]}}))
            session.commit()
            result = campaigns.save_draft_image('c', 'one',
                campaigns.ImageAttachment(version=0, image_id='hero'), session, None)
            drafts = result['generation']['drafts']
            assert drafts[0]['image']['image_id'] == 'hero'
            assert drafts[0]['body'] == 'Short post'
            assert drafts[1] == {'asset_id': 'two', 'body': 'Other post', 'image': None}
            assert result['version'] == 1
            with pytest.raises(HTTPException) as error:
                campaigns.save_draft_image('c', 'one',
                    campaigns.ImageAttachment(version=1, image_id='unknown'), session, None)
            assert error.value.status_code == 422
    finally:
        engine.dispose()


def test_add_images_preserves_attachments_and_reports_gaps(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    from app.api import campaigns
    from app.config import Settings
    from app.db import create_database_engine, initialize_database
    engine = create_database_engine(f'sqlite:///{tmp_path}/images.db')
    initialize_database(engine)
    monkeypatch.setattr(campaigns, 'image_catalog', lambda *a: (None, []))
    monkeypatch.setattr(campaigns, 'load_asset_package', lambda *a: {})
    with Session(engine) as session:
        c = campaigns.Campaign(id='c', brand_id='b', data={
            'plans': [{'assets': [{'id': 'a'}, {'id': 'b'}]}],
            'generation': {'drafts': [
                {'asset_id': 'a', 'plan_index': 0, 'image': {'image_id': 'keep'}},
                {'asset_id': 'b', 'plan_index': 0}]}})
        session.add(c)
        session.commit()
        output = campaigns.add_campaign_images(session, c, Settings(_env_file=None), [], False, 'Follow brief')
        assert [r['status'] for r in output['results']] == ['preserved', 'no_suitable_image']
        assert c.data['generation']['drafts'][0]['image']['image_id'] == 'keep'
    engine.dispose()


def test_catalog_resolves_duplicate_host_container_brand(tmp_path):
    root = tmp_path / 'images'
    root.mkdir()
    (root / 'photo.jpg').write_bytes(b'image')
    (root / 'metadata.json').write_text(json.dumps([
        {'id': 'photo', 'filename': 'photo.jpg', 'usage_rights': 'Brand owned'}]))
    brand = SimpleNamespace(name='Wickhold', source_directory='/missing/container/mount')
    peer = SimpleNamespace(name='Wickhold', source_directory=str(tmp_path))
    session = SimpleNamespace(get=lambda *args: brand,
        scalars=lambda *args: SimpleNamespace(all=lambda: [brand, peer]))
    _, records = image_catalog(session, SimpleNamespace(brand_id='b'),
        SimpleNamespace(magi_image_directory='images', magi_brand_source_dir=tmp_path))
    assert [r['id'] for r in records] == ['photo']
    peer.name = 'Unrelated brand'
    assert image_catalog(session, SimpleNamespace(brand_id='b'),
        SimpleNamespace(magi_image_directory='images', magi_brand_source_dir=tmp_path))[1] == []


def test_batch_selection_shortlists_and_rejects_cross_asset_choice(tmp_path, monkeypatch):
    from app.brand import image_selection as module
    from app.config import Settings
    targets = [{'asset': {'id': name}, 'draft': {'body': name}, 'package': {}, 'plan': {}}
               for name in ('a', 'b')]
    catalog = [{'id': str(i), 'description': 'security'} for i in range(20)]
    monkeypatch.setattr(module, 'shortlists', lambda *a: [['0', '1'], ['2', '3']])
    calls = []
    class Client:
        def run(self, request, output_model):
            payload = review_payload(request)
            calls.append(payload)
            assert len(payload['catalog']) <= 4
            assert len(payload['campaign_contexts']) == 1
            from app.llm.client import strict_output_schema
            strict_output_schema(output_model)
            schema = output_model.model_json_schema()
            assert schema['$defs']['Choice']['properties']['asset_id']['type'] == 'string'
            assert 'enum' not in str(schema)
            return SimpleNamespace(run_id='batch', parsed=output_model.model_validate({'selections': [
                {'asset_id': 'a', 'image_id': '0', 'alt_text': 'Security', 'reason': 'Relevant'},
                {'asset_id': 'b', 'image_id': '2', 'alt_text': 'Security', 'reason': 'Relevant'}]}))
    campaign = SimpleNamespace(id='c', brand_id='b', data={})
    result = module.select_images(Client(), None, Settings(_env_file=None), campaign, targets, catalog)
    assert len(calls) == 1
    assert result['a']['run_id'] == result['b']['run_id'] == 'batch'
    monkeypatch.setattr(module, 'shortlists', lambda *a: [['2'], ['0']])
    result = module.select_images(Client(), None, Settings(_env_file=None), campaign, targets, catalog,
                                  retry_invalid=False)
    assert result == {'a': None, 'b': None}
    monkeypatch.setattr(module, 'shortlists', lambda *a: [['0'], ['0']])
    result = module.select_images(Client(), None, Settings(_env_file=None), campaign, targets, catalog,
                                  retry_invalid=False)
    assert result['a']['image_id'] == '0'
    assert result['b'] is None


def test_batch_selection_retries_only_invalid_asset(monkeypatch):
    from app.brand import image_selection as module
    from app.config import Settings

    targets = [{'asset': {'id': name}, 'draft': {}, 'package': {}, 'plan': {}}
               for name in ('a', 'b')]
    calls = []
    monkeypatch.setattr(module, 'shortlists',
                        lambda *args: [['0'] if q else [] for q in args[-1]])

    class Client:
        def run(self, request, output_model):
            assets = review_payload(request)['assets']
            calls.append([a['asset_id'] for a in assets])
            return SimpleNamespace(run_id=f'run-{len(calls)}', parsed=output_model.model_validate({
                'selections': [{'asset_id': a['asset_id'],
                    'image_id': 'wrong' if a['asset_id'] == 'b' and len(calls) == 1 else '0',
                    'alt_text': 'Office', 'reason': 'Relevant'} for a in assets]}))

    result = module.select_images(Client(), None, Settings(_env_file=None),
        SimpleNamespace(id='c', brand_id='b', data={}), targets, [{'id': '0'}])
    assert calls == [['a', 'b'], ['b']]
    assert result['a']['run_id'] == 'run-1'
    assert result['b']['run_id'] == 'run-2'


def test_image_metadata_cache_invalidation_and_keyword_fallback(tmp_path):
    from app.brand.image_selection import shortlists
    from app.db import create_database_engine, initialize_database
    engine = create_database_engine(f'sqlite:///{tmp_path}/image-cache.db')
    initialize_database(engine)
    calls = []
    class Client:
        def embed(self, request):
            calls.append(request)
            return SimpleNamespace(vectors=[[1.0, 0.0] for _ in request.input])
    campaign = SimpleNamespace(id='c', brand_id='b')
    catalog = [{'id': str(i), 'description': 'security' if i == 10 else 'office'} for i in range(12)]
    assert len(shortlists(Client(), engine, campaign, catalog, ['security'])[0]) == 8
    assert [r.operation for r in calls] == ['embed_image_catalog', 'embed_image_queries']
    calls.clear()
    shortlists(Client(), engine, campaign, catalog, ['security'])
    assert calls == []  # Exact query and catalog embeddings are both reused.
    catalog[0]['description'] = 'changed'
    calls.clear()
    shortlists(Client(), engine, campaign, catalog, ['security'])
    assert len(calls[0].input) == 1
    class Offline:
        def embed(self, request):
            raise RuntimeError('offline')
    assert shortlists(Offline(), engine, campaign, catalog, ['security new query'])[0][0] == '10'
    engine.dispose()


def test_visual_rules_exclude_unrelated_corpus():
    from app.brand.image_selection import visual_rules
    package = {'evidence': [
        {'chunk_id': 'product', 'relative_path': 'brand/products/details.md', 'content': 'Product details.'},
        {'chunk_id': 'visual', 'relative_path': 'brand/visuals/common.md',
         'content': 'Never use console screenshots. Exceptions require approval.'}],
        'rule_pack': {'evidence': [{'chunk_id': 'hero', 'relative_path': 'brand/visuals/hero.md',
                                   'content': 'Use illustrations for the hero.'}]},
        'image_catalog': [{'id': 'irrelevant'}]}
    assert set(visual_rules(package)) == {
        'Never use console screenshots. Exceptions require approval.', 'Use illustrations for the hero.'}


def test_add_images_uses_one_call_for_four_assets(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    from app.api import campaigns
    from app.brand import image_selection
    from app.config import Settings
    from app.db import create_database_engine, initialize_database
    engine = create_database_engine(f'sqlite:///{tmp_path}/batch.db')
    initialize_database(engine)
    catalog = [{'id': 'hero', 'description': 'Security illustration'}]
    monkeypatch.setattr(campaigns, 'image_catalog', lambda *a: (None, catalog))
    monkeypatch.setattr(campaigns, 'load_asset_package', lambda *a: {})
    monkeypatch.setattr(image_selection, 'shortlists', lambda *a: [['hero']] * 4)
    calls = []
    class Client:
        def __init__(self, *a):
            pass
        def close(self):
            pass
        def run(self, request, output_model):
            calls.append(request)
            # Four results must have room for reasoning and the complete JSON response.
            assert request.max_output_tokens >= 8192
            from app.llm.client import strict_output_schema
            strict_output_schema(output_model)
            return SimpleNamespace(run_id='shared', parsed=output_model.model_validate({'selections': [
                {'asset_id': f'a{i}', 'image_id': 'hero' if i < 3 else None,
                 'alt_text': 'Security illustration', 'reason': 'Fits' if i < 3 else 'No match'} for i in range(4)]}))
    monkeypatch.setattr(campaigns, 'TrackedOpenAI', Client)
    with Session(engine) as session:
        c = campaigns.Campaign(id='c', brand_id='b', version=0, data={
            'plans': [{'assets': [{'id': f'a{i}'} for i in range(4)]}],
            'generation': {'drafts': [{'asset_id': f'a{i}', 'plan_index': 0, 'body': 'Keep this text'} for i in range(4)]}})
        session.add(c)
        session.commit()
        result = campaigns.add_campaign_images(session, c, Settings(_env_file=None), [], False, '')
        assert len(calls) == 1
        assert [r['status'] for r in result['results']] == ['attached'] * 3 + ['no_suitable_image']
        drafts = c.data['generation']['drafts']
        assert all(d['body'] == 'Keep this text' for d in drafts)
        assert all(d['image']['run_id'] == 'shared' for d in drafts[:3])
    engine.dispose()
