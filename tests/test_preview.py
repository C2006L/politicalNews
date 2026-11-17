from fastapi.testclient import TestClient
from app import preview


def test_sharing_blocks_mutations_and_all_maintenance_paths(monkeypatch):
    def forbidden(*args):
        raise AssertionError('Blocked request reached upstream')
    monkeypatch.setattr(preview, 'fetch', forbidden)
    with TestClient(preview.app) as client:
        for path in ['/sync', '/sync-view', '/backfill-view', '/api/learning/sync', '/curation',
                     '/api/reports/snapshots', '/openapi.json', '/static/../config.py']:
            assert client.get(path).status_code == 403
        for method in ['post', 'put', 'patch', 'delete']:
            assert getattr(client, method)('/reports').status_code == 403


def test_sharing_forwards_reads_without_browser_auth_and_hides_maintenance(monkeypatch):
    calls = []
    def fake(path, query, accept):
        calls.append((path, query, accept))
        return 200, 'text/html; charset=utf-8', b'<html><head></head><body><h1>Read</h1><details class="report-save">secret controls</details><a href="/sync">sync</a><a href="/news/1">read</a></body></html>', None
    monkeypatch.setattr(preview, 'fetch', fake)
    with TestClient(preview.app) as client:
        r = client.get('/reports?profile=civil', headers={'Authorization': 'private', 'Cookie': 'private', 'X-Sync-Token': 'private'})
        assert r.status_code == 200 and 'secret controls' not in r.text
        assert 'href="/sync"' not in r.text and 'href="/news/1"' in r.text
        assert calls == [('/reports', 'profile=civil', '*/*')]
        assert r.headers['cache-control'] == 'no-store'
        assert client.head('/reports').content == b''


def test_redirects_cannot_escape_read_only_routes(monkeypatch):
    with TestClient(preview.app) as client:
        for location in ['https://evil.example/today', '/sync', 'http://localhost:8003/']:
            monkeypatch.setattr(preview, 'fetch', lambda *args: (302, 'text/html', b'', location))
            assert client.get('/today', follow_redirects=False).status_code == 403
