import json
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
