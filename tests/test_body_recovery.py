from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import body_recovery as recovery
from app.database import SessionLocal
from app.materials import ACCOUNTS, SCAN_KEY
from app.models import BodyRecovery, LearningMaterial, MaterialRevision, AppState
from app.upstream import ProbeFailure

BODY = '<p>重取恢复验收的原文正文，需要完整保留表述及依据，并在校验后才标记为恢复成功。</p>' * 5


@pytest.fixture
def source(tmp_path, monkeypatch):
    path=tmp_path/'source.db'
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('create table articles(id text primary key,mp_id,title,url,content_html,has_content,create_time)')
        for index in range(4):
            db.execute('insert into articles values(?,?,?,?,?,0,1)',(f's{index}',list(ACCOUNTS)[index%2],f'缺正文 {index}',f'https://mp.weixin.qq.com/s/{index}',''))
        db.execute('insert into articles values(?,?,?,?,?,1,1)',('ready',list(ACCOUNTS)[0],'已有正文','https://mp.weixin.qq.com/s/ready',BODY))
        db.execute('insert into articles values(?,?,?,?,?,0,1)',('other','OTHER','其他账号','https://mp.weixin.qq.com/s/other',''))
        db.execute('insert into articles values(?,?,?,?,?,0,1)',('invalid',list(ACCOUNTS)[0],'非微信网址','https://evil.example/x',''))
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(path))
    monkeypatch.setenv('WERSS_API_URL','http://127.0.0.1:8123')
    monkeypatch.setenv('WERSS_RECOVERY_TOKEN_FILE',str(tmp_path/'private-recovery-key'))
    monkeypatch.setattr(recovery,'LAST_RUN',-100000.0)
    return path


def fill(path, identity, body=BODY):
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('update articles set has_content=1,content_html=? where id=?',(body,identity))


def row(identity):
    with SessionLocal() as db:
        return db.get(BodyRecovery,identity)


def accept(path, **kwargs):
    identity=path.split('/')[2]
    return {'code':0,'data':{'article_id':identity,'task_id':str(uuid4()),'status':'pending','cookie':'PRIVATE'}}


def test_queue_caps_filters_and_serial_submission(source,monkeypatch):
    before=source.read_bytes()
    ids=recovery.enqueue()
    assert len(ids)==3 and recovery.status()['missing_count']==4
    with pytest.raises(recovery.RecoveryError,match='queue_full'):recovery.enqueue()
    calls=[]
    monkeypatch.setattr(recovery,'service_request',lambda path,**kw:calls.append(path) or accept(path,**kw))
    recovery.run()
    assert len(calls)==1 and row(ids[0]).state=='queued'
    recovery.run()
    assert sum(path.endswith('/refresh') for path in calls)==1 and source.read_bytes()==before
    assert sum(row(identity).state=='pending' for identity in ids)==2


def test_remote_success_is_not_recovery_until_body_valid_and_imported(source,monkeypatch):
    identity=recovery.enqueue()[0]
    monkeypatch.setattr(recovery,'service_request',accept)
    recovery.run()
    task=row(identity)
    monkeypatch.setattr(recovery,'service_request',lambda *a,**kw:{'code':0,'data':{'task_id':task.task_id,'article_id':task.source_id,'status':'success'}})
    recovery.run()
    assert row(identity).state=='verifying'
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count()==0
        db.add(AppState(key=SCAN_KEY,value='{"after":"unchanged"}'));db.commit()
    fill(source,task.source_id)
    recovery.run()
    assert row(identity).state=='completed'
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count()==db.query(MaterialRevision).count()==1
        assert db.get(AppState,SCAN_KEY).value=='{"after":"unchanged"}'
    assert 'PRIVATE' not in json.dumps(recovery.status())


def test_verification_page_never_marks_completed(source,monkeypatch):
    identity=recovery.enqueue()[0]
    fill(source,row(identity).source_id,'当前环境异常，请完成验证后即可继续访问。'*20)
    recovery.run()
    assert row(identity).state=='invalid_content'
    with SessionLocal() as db:assert db.query(LearningMaterial).count()==0


def test_lost_submission_response_is_not_blindly_resubmitted(source,monkeypatch):
    identity=recovery.enqueue()[0];calls=[]
    def fail(*a,**kw):calls.append(1);raise ProbeFailure('service_unavailable')
    monkeypatch.setattr(recovery,'service_request',fail)
    recovery.run();recovery.run()
    assert len(calls)==1 and row(identity).state=='unknown'
    fill(source,row(identity).source_id)
    recovery.run()
    assert row(identity).state=='completed'


def test_restart_and_lost_upstream_task_are_unknown_not_resubmitted(source,monkeypatch):
    identity=recovery.enqueue()[0]
    recovery.update(identity,state='submitting')
    recovery.recover_interrupted()
    assert row(identity).state=='unknown'
    recovery.update(identity,state='queued',task_id=str(uuid4()))
    def missing(*a,**kw):raise ProbeFailure('resource_missing')
    monkeypatch.setattr(recovery,'service_request',missing)
    recovery.run()
    assert row(identity).state=='unknown' and row(identity).error=='task_unconfirmed'


def test_retry_requires_cooldown_and_supersedes_only_latest_attempt(source):
    identity=recovery.enqueue()[0]
    recovery.update(identity,state='unknown')
    with pytest.raises(recovery.RecoveryError,match='not_retryable'):recovery.enqueue(identity)
    with SessionLocal() as db:
        db.get(BodyRecovery,identity).created_at-=timedelta(seconds=601);db.commit()
    replacement=recovery.enqueue(identity)
    assert len(replacement)==1 and replacement[0]!=identity and row(identity).state=='superseded'
    assert row(replacement[0]).state=='pending'
    with pytest.raises(recovery.RecoveryError):recovery.enqueue(identity)


def test_concurrent_enqueue_does_not_duplicate_or_exceed_cap(source):
    def enqueue():
        try:return len(recovery.enqueue())
        except recovery.RecoveryError:return 0
    with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(lambda _:enqueue(),range(2)))
    assert sum(counts)==3
    with SessionLocal() as db:
        rows=db.query(BodyRecovery).all()
        assert len(rows)==len({record.source_id for record in rows})==3


def test_import_commit_then_queue_update_failure_is_idempotent(source,monkeypatch):
    identity=recovery.enqueue()[0];fill(source,row(identity).source_id)
    original=recovery.update;failed=[]
    def update(record,**changes):
        if changes.get('state')=='completed' and not failed:
            failed.append(1);raise RuntimeError('local write failed')
        original(record,**changes)
    monkeypatch.setattr(recovery,'update',update)
    recovery.run();assert row(identity).state=='unknown'
    recovery.run();assert row(identity).state=='completed'
    with SessionLocal() as db:assert db.query(MaterialRevision).count()==1


def test_changed_source_context_is_not_processed(source,monkeypatch):
    identity=recovery.enqueue()[0]
    monkeypatch.setenv('WERSS_API_URL','http://localhost:8123')
    monkeypatch.setattr(recovery,'service_request',lambda *a,**kw:pytest.fail('must not call new service for old queue'))
    recovery.run()
    assert row(identity).state=='pending' and recovery.status()['total']==0


def test_wrong_task_identity_never_completes_or_resubmits(source,monkeypatch):
    identity=recovery.enqueue()[0]
    monkeypatch.setattr(recovery,'service_request',accept)
    recovery.run()
    task=row(identity)
    monkeypatch.setattr(recovery,'service_request',lambda *a,**kw:{'code':0,'data':{
        'task_id':task.task_id,'article_id':'different-article','status':'success'}})
    recovery.run()
    assert row(identity).state=='unknown' and row(identity).error=='task_unconfirmed'
    monkeypatch.setattr(recovery,'service_request',lambda *a,**kw:pytest.fail('must observe without resubmitting'))
    recovery.run()


def test_timeout_and_import_contention_preserve_pending_verification(source,monkeypatch):
    identity=recovery.enqueue()[0]
    recovery.update(identity,state='verifying',submitted_at=recovery.now()-timedelta(seconds=601))
    recovery.run()
    assert row(identity).state=='unknown' and row(identity).error=='task_timeout'
    fill(source,row(identity).source_id)
    original=recovery.import_recovered
    def busy(*a):raise recovery.ImportBusy()
    monkeypatch.setattr(recovery,'import_recovered',busy)
    recovery.run()
    assert row(identity).state=='verifying' and row(identity).error=='import_busy'
    monkeypatch.setattr(recovery,'import_recovered',original)
    recovery.run()
    assert row(identity).state=='completed'


def test_thread_failure_releases_lock(source,monkeypatch):
    def fail(*a,**kw):raise RuntimeError('thread unavailable')
    monkeypatch.setattr(recovery,'threading',SimpleNamespace(Thread=fail))
    with pytest.raises(RuntimeError):recovery.start()
    assert not recovery.LOCK.locked()


def test_api_auth_and_explicit_pending_receipt(client,source,monkeypatch):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','management')
    monkeypatch.setattr(recovery,'start',lambda:False)
    assert client.post('/api/body-recovery/start').status_code==403
    response=client.post('/api/body-recovery/start',headers={'X-Sync-Token':'management'})
    assert response.status_code==202 and response.json()=={'accepted':3,'worker_started':False}
    assert client.get('/api/body-recovery/status').json()['active_count']==3
    assert client.get('/api/body-recovery/status?page=0').status_code==422
    assert client.post('/api/body-recovery/check',headers={'X-Sync-Token':'management'}).status_code==429
