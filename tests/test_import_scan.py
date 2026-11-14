from contextlib import closing
import base64
import json
import sqlite3

import pytest

from app import materials
from app.database import SessionLocal
from app.material_status import read_status, pipeline_status
from app.models import LearningMaterial, AppState
from app.review import set_initial_password

BODY = '<p>正文需保留原文依据与表述，不能因为分页回补而丢失旧文章或跳过修订。</p>' * 5


def source(tmp_path, monkeypatch, count=7, name='source.db'):
    path = tmp_path / name
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table articles(id text primary key,mp_id,title,url,content_html,has_content,create_time)')
        for index in range(count):
            db.execute('insert into articles values(?,?,?,?,?,1,?)',
                       (f'{index:04}', list(materials.ACCOUNTS)[index % 2], f'文章 {index}',
                        f'https://mp.weixin.qq.com/s/{index}', BODY, 100-index))
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(path))
    return path


def checkpoint():
    with SessionLocal() as db:
        row = db.get(AppState, materials.SCAN_KEY)
        return json.loads(row.value) if row else None


def test_more_than_200_old_articles_are_eventually_imported(tmp_path, monkeypatch):
    path = source(tmp_path, monkeypatch, 205)
    original = path.read_bytes()
    first = materials.sync_materials()
    assert first['scanned'] == first['added'] == 200 and first['remaining'] == 5
    assert read_status()['state'] == 'catching_up'
    assert checkpoint()['after'] == '0199'
    second = materials.sync_materials()
    assert second['added'] == 5 and second['remaining'] == 0
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 205
    scan = pipeline_status()['scan']
    assert scan['last_cycle_scanned'] == 205 and scan['last_cycle_rejected'] == 0
    assert checkpoint()['after'] is None and path.read_bytes() == original


def test_revisit_updates_and_late_body_behind_cursor(tmp_path, monkeypatch):
    path = source(tmp_path, monkeypatch)
    monkeypatch.setattr(materials, 'BATCH_SIZE', 3)
    materials.sync_materials()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('update articles set content_html=? where id=?', (BODY+'<p>后来修订。</p>', '0000'))
        db.execute('insert into articles values(?,?,?,?,?,0,0)', ('0000-late',list(materials.ACCOUNTS)[0],'晚到正文','https://mp.weixin.qq.com/s/late',BODY))
    assert materials.sync_materials()['added'] == 3
    assert materials.sync_materials()['added'] == 1
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("update articles set has_content=1 where id='0000-late'")
    result = materials.sync_materials()
    assert result['updated'] == 1 and result['added'] == 1
    with SessionLocal() as db:
        assert db.query(LearningMaterial).filter_by(source_id='0000-late').count() == 1


def test_failure_rolls_back_cursor_and_batch_then_resume(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    monkeypatch.setattr(materials, 'BATCH_SIZE', 2)
    materials.sync_materials()
    before = checkpoint()
    original = materials.append_revision
    def fail(*a, **kw):
        raise RuntimeError('snapshot failure')
    monkeypatch.setattr(materials, 'append_revision', fail)
    with pytest.raises(RuntimeError):
        materials.sync_materials()
    assert checkpoint() == before
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 2
    assert not materials.LOCK.locked()
    monkeypatch.setattr(materials, 'append_revision', original)
    assert materials.sync_materials()['added'] == 2
    assert checkpoint()['after'] == '0003'


def test_rejected_rows_do_not_starve_rest_and_can_recover(tmp_path, monkeypatch):
    path = source(tmp_path, monkeypatch, 3)
    monkeypatch.setattr(materials, 'BATCH_SIZE', 2)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("update articles set content_html='短文' where id='0000'")
    first = materials.sync_materials()
    assert first['rejected'] == 1 and first['remaining'] == 1
    assert materials.sync_materials()['added'] == 1
    assert pipeline_status()['scan']['last_cycle_rejected'] == 1
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("update articles set content_html=? where id='0000'", (BODY,))
    assert materials.sync_materials()['added'] == 1


def test_new_source_resets_checkpoint_and_does_not_expose_paths(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    monkeypatch.setattr(materials, 'BATCH_SIZE', 2)
    materials.sync_materials()
    second = source(tmp_path, monkeypatch, 1, 'secret-source-file.db')
    assert pipeline_status()['scan']['remaining'] is None
    assert materials.sync_materials()['scanned'] == 1
    assert checkpoint()['after'] is None
    serialized = json.dumps(pipeline_status())
    assert str(second) not in serialized and 'secret-source-file' not in serialized


def test_empty_and_wrong_schema_have_distinct_status(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch, 0)
    assert materials.sync_materials()['scanned'] == 0
    assert read_status()['state'] == 'no_new_materials'
    success = read_status()['last_success_at']
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(tmp_path/'missing.db'))
    with pytest.raises(FileNotFoundError):
        materials.sync_materials()
    assert read_status()['error_detail'] == 'source_missing'
    assert read_status()['result'] == {} and read_status()['last_success_at'] == success
    path = tmp_path / 'bad.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('create table incompatible(x)')
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(path))
    with pytest.raises(sqlite3.OperationalError):
        materials.sync_materials()
    assert read_status()['error_detail'] == 'source_schema'


def test_local_management_password_can_retry_and_busy_is_not_source_failure(client, tmp_path, monkeypatch):
    source(tmp_path, monkeypatch, 1)
    monkeypatch.delenv('SYNC_ADMIN_TOKEN', raising=False)
    set_initial_password('local-password-123')
    headers = {'X-Sync-Token': 'base64:'+base64.b64encode(b'local-password-123').decode()}
    assert client.post('/api/learning/sync', headers=headers).status_code == 200
    assert materials.LOCK.acquire(blocking=False)
    try:
        assert client.post('/api/learning/sync', headers=headers).status_code == 409
        assert read_status()['state'] == 'updated'
    finally:
        materials.LOCK.release()
    assert 'data-material-import' in client.get('/learning-import').text


def test_completion_status_failure_is_retryable_without_restarting(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch, 1)
    original = materials.write_status
    def fail_completion(**changes):
        if changes.get('state') != 'running':
            raise RuntimeError('status storage temporarily unavailable')
        original(**changes)
    monkeypatch.setattr(materials, 'write_status', fail_completion)
    with pytest.raises(RuntimeError):
        materials.sync_materials()
    assert not materials.LOCK.locked()
    assert read_status()['state'] == 'running'
    assert pipeline_status()['import']['state'] == 'interrupted'
    assert checkpoint()['after'] is None
    monkeypatch.setattr(materials, 'write_status', original)
    assert materials.sync_materials()['skipped'] == 1
    assert pipeline_status()['import']['state'] == 'no_new_materials'


@pytest.mark.parametrize('value', ['not json', '[]', '{"after": 12}', '{"cycle_scanned": "bad"}'])
def test_invalid_checkpoint_restarts_scan_without_losing_materials(tmp_path, monkeypatch, value):
    source(tmp_path, monkeypatch, 1)
    materials.sync_materials()
    with SessionLocal() as db:
        db.get(AppState, materials.SCAN_KEY).value = value
        db.commit()
    assert pipeline_status()['scan']['remaining'] is None
    assert materials.sync_materials()['skipped'] == 1
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 1
