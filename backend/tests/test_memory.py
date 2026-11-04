from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.brand import Brand


def test_brand_memory_persists_and_is_editable(tmp_path):
    settings = Settings(_env_file=None, magi_database_url=f'sqlite:///{tmp_path}/memory.db')
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(Brand(id='a', name='A', source_directory='a'))
            session.add(Brand(id='b', name='B', source_directory='b'))
            session.commit()
        base = '/api/brands/a/memories'
        m = client.post(base, json={'text':'LinkedIn: short paragraphs'}).json()[0]
        assert client.get('/api/brands/b/memories').json() == []
        assert client.put(f'{base}/{m["id"]}', json={'text':'No hashtags', 'revision':1}).status_code == 200
        assert client.put(f'{base}/{m["id"]}', json={'text':'stale', 'revision':1}).status_code == 409
    with TestClient(create_app(settings)) as client:
        assert client.get(base).json()[0]['text'] == 'No hashtags'
        assert client.delete(f'{base}/{m["id"]}?revision=2').status_code == 200
        assert client.get(base).json() == []
