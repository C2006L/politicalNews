import os
from contextlib import closing
import sqlite3

import pytest

from desktop import acquire_lock, backup_before_upgrade, LocalService


@pytest.mark.skipif(os.name != 'nt', reason='Windows desktop lock')
def test_only_one_instance_per_data_directory(tmp_path):
    first = acquire_lock(tmp_path)
    assert first is not None
    assert acquire_lock(tmp_path) is None
    first.close()
    second = acquire_lock(tmp_path)
    assert second is not None
    second.close()


def test_upgrade_backup_is_unchanged_and_does_not_overwrite(tmp_path):
    path = tmp_path / 'study.db'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table example(value text)')
        db.execute("insert into example values('original')")
    backup_before_upgrade(tmp_path)
    backup_before_upgrade(tmp_path)
    copies = list((tmp_path / 'backups').glob('*.db'))
    assert len(copies) == 2
    for copy in copies:
        with closing(sqlite3.connect(copy)) as db:
            assert db.execute('select value from example').fetchone()[0] == 'original'
    assert path.exists()


def test_local_server_reserves_loopback_port_without_importing_database():
    service = LocalService()
    assert service.sock.getsockname()[0] == '127.0.0.1'
    assert service.sock.getsockname()[1] != 0
    service.sock.close()
