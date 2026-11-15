from datetime import datetime
from contextlib import closing
import os
import sqlite3
import time

from app.models import LearningMaterial
from app.source_coverage import summarize
from app.database import SessionLocal


def row(identity, title, account='考研政治徐涛', payload=None):
    return LearningMaterial(id=identity, source_id=str(identity), title=title, account=account,
                            url='https://example.org', fingerprint=str(identity),
                            imported_at=datetime(2026, 11, 15), payload=payload or {})


def test_title_variants_internal_gaps_only_and_duplicate_periods_not_merged():
    report = summarize([row(1, '选择题带背D1‼'), row(2, '徐涛带背D2！'),
                        row(3, '徐涛带背D4！'), row(4, '徐涛带背D4旧年度'), row(5, '2027考研说明')])
    result = report['accounts'][1]
    assert result['observed_periods'] == [1, 2, 4]
    assert result['possible_internal_gaps'] == [3]
    assert result['duplicate_periods'] == [4]
    assert result['items'][-1]['material_ids'] == [3, 4]
    assert not report['complete_history_verified']
    assert summarize([row(1, '徐涛带背D21'), row(2, '徐涛带背D23')])['accounts'][1]['possible_internal_gaps'] == [22]


def test_unknown_period_and_media_coverage_never_become_complete():
    result = summarize([row(1, 'D2027不属于带背'), row(2, '徐涛带背D0'), row(3, '徐涛带背D999'),
                        row(4, '徐涛带背D3', payload={'questions': [{'question': '题目'}], 'audio': [{'title': '录音'}]})])['accounts'][1]
    assert result['observed_periods'] == [3] and result['possible_internal_gaps'] == []
    assert result['audio_not_transcribed'] == 1 and result['images_not_verified'] == 4
    assert result['unstructured'] == 3


def test_status_reads_only_local_coverage_and_keeps_empty_accounts(client):
    with SessionLocal() as db:
        db.add(row(1, '徐涛带背D21'))
        db.add(row(2, '徐涛带背D23'))
        db.commit()
    response = client.get('/api/learning/status')
    report = response.json()['coverage_report']
    assert report['scope'] == 'local_imported_materials'
    assert report['accounts'][0]['materials'] == 0
    assert report['accounts'][1]['possible_internal_gaps'] == [22]
    assert 'data-coverage-accounts' in client.get('/learning-import').text


def test_exported_snapshot_age_is_visible_without_pretending_it_is_current(client, tmp_path, monkeypatch):
    path = tmp_path / 'snapshot.db'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table articles(mp_id,has_content,content_html)')
    os.utime(path, (time.time() - 240, time.time() - 240))
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(path))
    monkeypatch.setenv('WERSS_SOURCE_IS_SNAPSHOT', '1')
    report = client.get('/api/learning/status').json()['acquisition']
    assert report['storage_mode'] == 'exported_snapshot'
    assert report['snapshot_age_seconds'] >= 240
    assert report['database_readable'] and report['authorization'] == 'unknown'
