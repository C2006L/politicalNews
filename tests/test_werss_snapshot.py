from contextlib import closing
import sqlite3

import pytest

from scripts.werss_snapshot_export import export_snapshot


def test_snapshot_includes_committed_wal_and_is_readable_without_shared_memory(tmp_path):
    source, target = tmp_path / 'live.db', tmp_path / 'snapshot.db'
    with closing(sqlite3.connect(source)) as db:
        db.execute('pragma journal_mode=WAL')
        db.execute('create table articles(id integer primary key,body text)')
        db.execute("insert into articles values(1,'first')")
        db.commit()
        export_snapshot(source, target)
        with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as snapshot:
            assert snapshot.execute('select body from articles').fetchone()[0] == 'first'
            assert snapshot.execute('pragma journal_mode').fetchone()[0] == 'delete'
        db.execute("insert into articles values(2,'next')")
        db.commit()
        export_snapshot(source, target)
    with closing(sqlite3.connect(target)) as snapshot:
        assert snapshot.execute('select count(*) from articles').fetchone()[0] == 2
        assert snapshot.execute('pragma integrity_check').fetchone()[0] == 'ok'
    assert not list(tmp_path.glob('snapshot-*'))


def test_export_failure_keeps_previous_snapshot_and_never_overwrites_source(tmp_path, monkeypatch):
    source, target = tmp_path / 'live.db', tmp_path / 'snapshot.db'
    with closing(sqlite3.connect(source)) as db:
        db.execute('create table rows(id)')
        db.commit()
    export_snapshot(source, target)
    before = target.read_bytes()
    def busy(*args):
        raise PermissionError('target is being read')
    monkeypatch.setattr('scripts.werss_snapshot_export.os.replace', busy)
    with pytest.raises(PermissionError):
        export_snapshot(source, target)
    assert target.read_bytes() == before
    assert not list(tmp_path.glob('snapshot-*'))
    with pytest.raises(ValueError):
        export_snapshot(source, source)
