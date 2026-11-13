from contextlib import closing
import json
import sqlite3

import pytest

from app import materials
from app.material_status import pipeline_status, read_status, recover_import, write_status


def source_db(tmp_path, monkeypatch):
    path = tmp_path / 'source.db'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table articles(id,mp_id,title,url,content_html,has_content,create_time)')
        body = '<p>重温重要提法</p><p>专题十五</p><p>保留原文表述，待核对教材与出处。</p>' * 5
        db.execute('insert into articles values(?,?,?,?,?,1,1)',
                   ('x', 'MP_WXS_3243013434', '测试', 'https://mp.weixin.qq.com/s/test', body))
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(path))
    return path


def test_no_new_import_does_not_claim_source_checked(tmp_path, monkeypatch):
    source_db(tmp_path, monkeypatch)
    materials.sync_materials()
    first = read_status()
    materials.sync_materials()
    assert read_status()['state'] == 'no_new_materials'
    assert read_status()['last_changed_at'] == first['last_changed_at']
    status = pipeline_status()['acquisition']
    assert status['database_readable']
    assert not status['last_check_verified']
    assert status['authorization'] == 'unknown'
    assert status['accounts'][0]['articles'] == 1


def test_failure_preserves_success_and_sanitizes_status(tmp_path, monkeypatch, client):
    source_db(tmp_path, monkeypatch)
    materials.sync_materials()
    success = read_status()['last_success_at']
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(tmp_path / 'secret-token-cookie.db'))
    with pytest.raises(FileNotFoundError):
        materials.sync_materials()
    status = client.get('/api/learning/status').json()
    assert status['import']['state'] == 'failed'
    assert status['import']['last_success_at'] == success
    assert 'secret-token-cookie' not in json.dumps(status)
    assert status['acquisition']['state'] == 'source_unavailable'
    assert not materials.LOCK.locked()


def test_verification_and_short_body_are_separate(tmp_path, monkeypatch):
    path = source_db(tmp_path, monkeypatch)
    materials.sync_materials()
    success = read_status()['last_success_at']
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('update articles set content_html=?', ('当前环境异常，请完成验证后即可继续访问。' * 10,))
        db.execute('insert into articles values(?,?,?,?,?,1,2)',
                   ('y', 'MP_WXS_3243013434', '空正文', 'https://mp.weixin.qq.com/s/y', '短文'))
    result = materials.sync_materials()
    assert result['verification_required'] == 1
    assert result['insufficient_text'] == 1
    assert read_status()['state'] == 'partial'
    assert read_status()['last_success_at'] == success


def test_status_write_failure_always_releases_lock(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError('state write failed')
    monkeypatch.setattr(materials, 'write_status', fail)
    with pytest.raises(RuntimeError):
        materials.sync_materials()
    assert not materials.LOCK.locked()


def test_restart_marks_running_as_interrupted():
    write_status(state='running', last_success_at='2026-11-13T00:00:00+00:00')
    recover_import()
    assert read_status()['state'] == 'interrupted'
    assert read_status()['last_success_at'] == '2026-11-13T00:00:00+00:00'


def test_status_page_displays_two_stages(client):
    html = client.get('/status').text
    assert '上游采集' in html and '主项目导入' in html
    assert '不能证明授权有效' in html
