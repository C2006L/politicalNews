from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from app import upstream
from app.database import SessionLocal
from app.materials import ACCOUNTS
from app.models import AppState
from app.tasks import setup_scheduler


def configure(tmp_path, monkeypatch):
    token = tmp_path / 'private-token'
    token.write_text('private-secret', encoding='utf-8')
    monkeypatch.setenv('WERSS_API_URL', 'http://127.0.0.1:8003')
    monkeypatch.setenv('WERSS_API_TOKEN_FILE', str(token))
    return token


def success(account_id):
    return {'code': 0, 'data': {'mp_id': account_id, 'latest_title': '最新文章', 'article_count': 1,
                               'cookie': 'NEVER_PERSIST'}, 'message': 'NEVER_PERSIST'}


def test_success_is_scoped_fresh_and_sanitized(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    monkeypatch.setattr(upstream, 'api_request', success)
    upstream.run_check()
    report = upstream.status()
    assert report['fresh'] and report['state'] == 'checked' and len(report['accounts']) == 2
    assert all(row['state'] == 'accessible' and not row['body_verified'] for row in report['accounts'])
    assert all(row['authorization'] == 'not_rejected_at_check' for row in report['accounts'])
    serialized = json.dumps(report)
    assert 'NEVER_PERSIST' not in serialized and 'private-secret' not in serialized
    assert 'source_key' not in report and report['coverage'] == 'latest_only_not_complete'
    with SessionLocal() as db:
        stored = db.get(AppState, upstream.KEY).value
        assert 'NEVER_PERSIST' not in stored and 'private-secret' not in stored
    upstream.save(finished_at=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat())
    assert not upstream.status()['fresh']


@pytest.mark.parametrize('code,state,authorization', [
    (-2012,'authorization_or_limit','unknown'), (-2013,'login_required','rejected'),
    (-2041,'restricted_or_unsupported','unknown'), (-2010,'restricted_or_unsupported','unknown'),
    ('network_error','network_error','unknown'), ('timeout','network_error','unknown'),
])
def test_auth_network_and_unsupported_are_not_conflated(code, state, authorization):
    report = upstream.normalize({'code':400,'message':'private cookie details','data':{'code':code}}, list(ACCOUNTS)[0])
    assert report == {'state':state,'authorization':authorization}


def test_service_credential_failure_preserves_last_accessible_time(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    monkeypatch.setattr(upstream, 'api_request', success)
    upstream.run_check()
    previous = upstream.status()['last_accessible_at']
    def fail(account_id):
        raise upstream.ProbeFailure('service_auth_required')
    monkeypatch.setattr(upstream, 'api_request', fail)
    upstream.run_check()
    report = upstream.status()
    assert report['state'] == 'failed' and report['error'] == 'service_auth_required'
    assert report['last_accessible_at'] == previous and not report['fresh']
    assert not upstream.LOCK.locked()


def test_failed_status_write_and_thread_start_release_lock(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError('private failure')
    monkeypatch.setattr(upstream, 'save', fail)
    upstream.run_check()
    assert not upstream.LOCK.locked()
    monkeypatch.setattr(upstream, 'threading', SimpleNamespace(Thread=fail))
    with pytest.raises(RuntimeError):
        upstream.start_check()
    assert not upstream.LOCK.locked()


def test_interrupted_and_changed_configuration_do_not_show_old_fresh_result(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    monkeypatch.setattr(upstream, 'api_request', success)
    upstream.run_check()
    upstream.save(state='running')
    assert upstream.status()['state'] == 'interrupted'
    monkeypatch.setenv('WERSS_API_URL','http://localhost:8003')
    assert upstream.status()['state'] == 'not_checked'
    upstream.run_check()
    assert upstream.status()['state'] == 'checked'


def test_manual_api_requires_auth_and_limits_repeat_checks(client, tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','management')
    assert client.post('/api/upstream/check').status_code == 403
    monkeypatch.setattr(upstream, 'start_check', lambda: True)
    headers = {'X-Sync-Token':'management'}
    assert client.post('/api/upstream/check',headers=headers).status_code == 202
    upstream.save(source_key=upstream.source_key(),state='checked',started_at=upstream.stamp(),finished_at=upstream.stamp())
    assert client.post('/api/upstream/check',headers=headers).status_code == 429
    assert upstream.LOCK.acquire(blocking=False)
    try:
        assert client.post('/api/upstream/check',headers=headers).status_code == 409
    finally:
        upstream.LOCK.release()


@pytest.mark.parametrize('url',['https://remote.example','http://remote.example','http://name:password@127.0.0.1:8003','http://127.0.0.1:8003/?token=secret','http://[invalid'])
def test_credentials_are_never_sent_to_remote_or_userinfo_urls(tmp_path, monkeypatch, url):
    configure(tmp_path, monkeypatch)
    monkeypatch.setenv('WERSS_API_URL',url)
    with pytest.raises(upstream.ProbeFailure,match='invalid_configuration'):
        upstream.api_request(list(ACCOUNTS)[0])


@pytest.mark.parametrize('error,category', [(401,'service_auth_required'),(403,'service_auth_required'),(302,'incompatible_service'),(404,'incompatible_service'),(500,'service_error')])
def test_http_failure_is_sanitized(tmp_path, monkeypatch, error, category):
    configure(tmp_path, monkeypatch)
    class Opener:
        def open(self,*args,**kwargs):
            raise HTTPError('private url',error,'private token',{},None)
    monkeypatch.setattr(upstream,'build_opener',lambda *args:Opener())
    with pytest.raises(upstream.ProbeFailure,match=category) as result:
        upstream.api_request(list(ACCOUNTS)[0])
    assert 'private' not in str(result.value)


def test_access_key_header_and_only_whitelisted_output(tmp_path,monkeypatch):
    path=configure(tmp_path,monkeypatch)
    path.write_text(json.dumps({'key':'ak','secret':'sk'}))
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def read(self,*args):return json.dumps(success(list(ACCOUNTS)[0])).encode()
    class Opener:
        def open(self,request,**kwargs):
            seen.append(request.get_header('Authorization'))
            return Response()
    monkeypatch.setattr(upstream,'build_opener',lambda *args:Opener())
    result=upstream.normalize(upstream.api_request(list(ACCOUNTS)[0]),list(ACCOUNTS)[0])
    assert seen==['AK-SK ak:sk'] and result['state']=='accessible'
    assert 'NEVER_PERSIST' not in json.dumps(result)


def test_probe_schedule_is_opt_in(tmp_path,monkeypatch):
    assert setup_scheduler().get_job('werss_channel_check') is None
    configure(tmp_path,monkeypatch)
    assert setup_scheduler().get_job('werss_channel_check').trigger.interval.total_seconds()==7200


@pytest.mark.parametrize('payload', [
    {'code':False,'data':{'mp_id':list(ACCOUNTS)[0],'article_count':1,'latest_title':'错误'}},
    {'code':0,'data':None}, {'code':0,'data':{'mp_id':'OTHER','article_count':1,'latest_title':'其他账号'}},
])
def test_malformed_or_wrong_account_is_not_a_success(payload):
    assert upstream.normalize(payload,list(ACCOUNTS)[0])['state']=='incompatible_response'


def test_new_service_failure_does_not_inherit_old_service_success(tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch)
    monkeypatch.setattr(upstream,'api_request',success)
    upstream.run_check()
    monkeypatch.setenv('WERSS_API_URL','http://localhost:8004')
    def fail(account):raise upstream.ProbeFailure('service_unavailable')
    monkeypatch.setattr(upstream,'api_request',fail)
    upstream.run_check()
    assert 'last_accessible_at' not in upstream.status()


def test_manual_worker_owns_and_releases_lock(tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch)
    monkeypatch.setattr(upstream,'api_request',success)
    class InlineThread:
        def __init__(self,target,kwargs,**unused):self.target=target;self.kwargs=kwargs
        def start(self):self.target(**self.kwargs)
    monkeypatch.setattr(upstream,'threading',SimpleNamespace(Thread=InlineThread))
    assert upstream.start_check()
    assert not upstream.LOCK.locked() and upstream.status()['state']=='checked'
    assert upstream.LOCK.acquire(blocking=False)
    try:
        assert not upstream.start_check()
        assert upstream.LOCK.locked()
    finally:upstream.LOCK.release()
