from contextlib import closing
from datetime import datetime
import sqlite3

import pytest
from sqlalchemy import create_engine, text

from app.database import SessionLocal
from app.migrations import migrate
from app.models import News, AppState, LearningMaterial, MaterialRevision
from app.materials import sync_materials
from scripts.database_backup import backup


def test_legacy_database_upgrade_and_restore(tmp_path):
    original = tmp_path / 'legacy.db'
    engine = create_engine('sqlite:///' + original.as_posix())
    try:
        with engine.begin() as conn:
            for table in (News.__table__, AppState.__table__, LearningMaterial.__table__):
                table.create(conn)
            conn.execute(LearningMaterial.__table__.insert().values(
                id=42, source_id='old', title='旧资料', url='https://mp.weixin.qq.com/s/old',
                account='老师', fingerprint='v1', payload={'evidence': [{'id': 'L1', 'text': '旧依据'}]},
                imported_at=datetime(2026, 11, 13)))
        backup(original, tmp_path / 'before.db')
        migrate(engine)
        migrate(engine)
        with engine.connect() as conn:
            assert conn.execute(text('select count(*) from material_revisions')).scalar() == 1
            assert conn.execute(text('select material_id from material_revisions')).scalar() == 42
            assert conn.execute(text('select max(version) from schema_migrations')).scalar() == 3
        backup(tmp_path / 'before.db', tmp_path / 'restored.db')
        restored = create_engine('sqlite:///' + (tmp_path / 'restored.db').as_posix())
        try:
            migrate(restored)
            with restored.connect() as conn:
                assert conn.execute(text('select title from learning_materials where id=42')).scalar() == '旧资料'
        finally:
            restored.dispose()
        with pytest.raises(FileExistsError):
            backup(original, tmp_path / 'restored.db')
    finally:
        engine.dispose()


def test_newer_schema_is_rejected_without_modification(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'future.db').as_posix())
    try:
        migrate(engine)
        with engine.begin() as conn:
            conn.execute(text("insert into schema_migrations values(99, 'future')"))
        with pytest.raises(ValueError, match='newer'):
            migrate(engine)
        with engine.connect() as conn:
            assert conn.execute(text('select count(*) from schema_migrations')).scalar() == 4
    finally:
        engine.dispose()


def test_import_versions_keep_old_evidence_and_material_id(tmp_path, monkeypatch, client):
    source = tmp_path / 'werss.db'
    html = '<p>重温重要提法</p><p>专题十五</p><p>旧的限定条件和依据必须保留，不能被后来的更正覆盖。</p>' * 5
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute('create table articles(id,mp_id,title,url,content_html,has_content,create_time)')
        conn.execute('insert into articles values(?,?,?,?,?,1,1)',
                     ('s1','MP_WXS_3243013434','第一版','https://mp.weixin.qq.com/s/test',html))
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(source))
    sync_materials()
    sync_materials()
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute('update articles set title=?, content_html=?', ('修订标题',html.replace('旧的限定','新的限定')))
    sync_materials()
    old = client.get('/api/learning/1/revisions/1').json()
    current = client.get('/api/learning/1').json()
    assert old['title'] == '第一版' and current['title'] == '修订标题'
    assert old['revision'] == 1 and current['revision'] == 2
    assert '旧的限定' in old['evidence'][2]['text']
    assert client.get('/learning/1?revision=1').status_code == 200
    assert client.get('/api/learning/1/revisions/999').status_code == 404
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 1
        assert db.query(MaterialRevision).count() == 2
        assert db.query(MaterialRevision).filter_by(revision=1).one().source_html == html


def test_failed_snapshot_rolls_back_current_material(tmp_path, monkeypatch):
    from app import materials
    path = tmp_path / 'source.db'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('create table articles(id,mp_id,title,url,content_html,has_content,create_time)')
        conn.execute('insert into articles values(?,?,?,?,?,1,1)',
                     ('s','MP_WXS_3243013434','新资料','https://mp.weixin.qq.com/s/test','<p>正文材料完整，需要保证资料和快照在同一事务里提交。</p>' * 10))
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(path))
    def fail(*args):
        raise RuntimeError('snapshot write failure')
    monkeypatch.setattr(materials, 'append_revision', fail)
    with pytest.raises(RuntimeError):
        sync_materials()
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 0
        assert db.query(MaterialRevision).count() == 0
