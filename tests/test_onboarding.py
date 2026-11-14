from types import SimpleNamespace

import pytest

from app import sync_service as sync
from app.routers import onboarding


def test_empty_state_and_start_is_explicit(client, monkeypatch):
    calls = []
    monkeypatch.setattr(onboarding, 'start_background_sync', lambda *a, **kw: calls.append(kw) or True)
    assert client.get('/api/getting-started').json()['state'] == 'empty'
    assert '/getting-started' in client.get('/today').text
    assert 'data-setup-start' in client.get('/getting-started').text
    assert calls == []
    response = client.post('/api/getting-started/news', headers={'X-Requested-With': 'study-ui'})
    assert response.status_code == 202
    assert calls == [dict(months=1, max_pages=2, max_items=40)]


def test_start_auth_and_cross_site_protection(client, monkeypatch):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'example')
    monkeypatch.setattr(onboarding, 'start_background_sync', lambda *a, **kw: True)
    route = '/api/getting-started/news'
    assert client.post(route).status_code == 403
    headers = {'X-Requested-With': 'study-ui', 'X-Sync-Token': 'example'}
    assert client.post(route, headers={**headers, 'Origin': 'https://other.example'}).status_code == 403
    assert client.post(route, headers={'X-Requested-With': 'study-ui'}).status_code == 403
    assert client.post(route, headers=headers).status_code == 202
    assert client.get(route).status_code == 405


def test_no_content_conflict_and_startup_failure_are_not_success(client, monkeypatch):
    sync.set_app_state('sync_finished_at', '2026-11-14T00:00:00Z')
    assert client.get('/api/getting-started').json()['state'] == 'no_content'
    monkeypatch.setattr(onboarding, 'start_background_sync', lambda *a, **kw: False)
    route, headers = '/api/getting-started/news', {'X-Requested-With': 'study-ui'}
    assert client.post(route, headers=headers).status_code == 409
    def fail(*a, **kw):
        raise OSError('private filesystem path')
    monkeypatch.setattr(onboarding, 'start_background_sync', fail)
    response = client.post(route, headers=headers)
    assert response.status_code == 503 and 'private' not in response.text


@pytest.mark.parametrize('entry', ['run_sync_now', 'start_background_sync', 'start_batched_backfill'])
def test_state_initialization_failure_releases_lock(monkeypatch, entry):
    def fail(*a, **kw):
        raise OSError('database locked')
    monkeypatch.setattr(sync, 'set_app_state', fail)
    with pytest.raises(OSError):
        if entry == 'start_batched_backfill':
            sync.start_batched_backfill('test', 1)
        else:
            getattr(sync, entry)('test')
    assert not sync.SYNC_LOCK.locked()


def test_worker_start_failure_even_with_state_failure_releases_lock(monkeypatch):
    def fail(*a, **kw):
        raise OSError('cannot write')
    monkeypatch.setattr(sync, '_initialize_sync', lambda *a, **kw: None)
    monkeypatch.setattr(sync, 'set_app_state', fail)
    monkeypatch.setattr(sync, 'threading', SimpleNamespace(Thread=fail))
    with pytest.raises(OSError):
        sync.start_background_sync('test')
    assert not sync.SYNC_LOCK.locked()


@pytest.mark.parametrize('entry', ['_run_background_sync', '_run_batched_backfill', 'run_sync_now'])
def test_final_state_failure_releases_lock_and_attempts_clear(monkeypatch, entry):
    calls = []
    def save(key, value):
        calls.append(key)
        if key == 'sync_finished_at':
            raise OSError('cannot write')
    monkeypatch.setattr(sync, 'set_app_state', save)
    monkeypatch.setattr(sync, '_initialize_sync', lambda *a, **kw: None)
    monkeypatch.setattr(sync, 'fetch_and_save_news', lambda **kw: {'fetched': 0, 'saved': 0})
    if entry != 'run_sync_now':
        assert sync.SYNC_LOCK.acquire(blocking=False)
    if entry == '_run_batched_backfill':
        sync._run_batched_backfill('test', 0)
    else:
        getattr(sync, entry)('test')
    assert not sync.SYNC_LOCK.locked()
    assert 'sync_in_progress' in calls


def test_stale_running_state_allows_recovery(client):
    sync.set_app_state('sync_in_progress', '1')
    assert client.get('/api/getting-started').json()['state'] == 'interrupted'
    assert sync.SYNC_LOCK.acquire(blocking=False)
    try:
        assert client.get('/api/getting-started').json()['state'] == 'running'
    finally:
        sync.SYNC_LOCK.release()
