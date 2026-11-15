from contextlib import closing
from pathlib import Path
import json
import sqlite3

import pytest

from scripts.import_public_album_history import import_snapshots


def snapshot(mid='123', source_id=None):
    return dict(account_id='MP_WXS_3243013434', account='肖秀荣教授', biz='MzI0MzAxMzQzNA==', mid=mid, idx='1',
                title='真实来源示例', url=('https://mp.weixin.qq.com/s/short' if source_id else
                    f'https://mp.weixin.qq.com/s?__biz=MzI0MzAxMzQzNA==&mid={mid}&idx=1'),
                source_id=source_id, html='<p>'+'这是原文段落，保留身份与依据，重复导入不能产生重复记录。'*8+'</p>',
                ct=None, captured_at='2026-11-15T00:00:00Z')


def create_source(path):
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('CREATE TABLE articles(id TEXT PRIMARY KEY,mp_id,title,url,content,content_html,has_content,status,'
                   'publish_time,create_time,created_at,updated_at,updated_at_millis,extinfo,is_read,is_favorite)')


def test_short_and_long_urls_keep_original_id_and_repeat_is_idempotent(tmp_path):
    source=tmp_path/'source.db';create_source(source)
    with closing(sqlite3.connect(source)) as db,db:
        db.execute('INSERT INTO articles(id,mp_id,title,url,content_html) VALUES(?,?,?,?,?)',
                   ('original','MP_WXS_3243013434','真实来源示例','https://mp.weixin.qq.com/s/short','original body'))
    folder=tmp_path/'snapshots';folder.mkdir()
    for filename,row in [('existing.json',snapshot(source_id='original')),('album.json',snapshot()),('new.json',snapshot('456'))]:
        (folder/filename).write_text(json.dumps(row),encoding='utf-8')
    result=import_snapshots(source,folder,tmp_path/'backup.db')
    assert result=={'added':1,'existing':1,'snapshots':2}
    again=import_snapshots(source,folder,tmp_path/'backup2.db')
    assert again['added']==0 and again['existing']==2
    with closing(sqlite3.connect(source)) as db:
        assert db.execute('SELECT count(*) FROM articles').fetchone()[0]==2
        assert db.execute("SELECT content_html FROM articles WHERE id='original'").fetchone()[0]=='original body'
        assert db.execute("SELECT create_time FROM articles WHERE id!='original'").fetchone()[0]==0


def test_identity_change_rolls_back_and_invalid_body_not_imported(tmp_path):
    source=tmp_path/'source.db';create_source(source)
    folder=tmp_path/'snapshots';folder.mkdir()
    row=snapshot(source_id='missing-original')
    (folder/'a.json').write_text(json.dumps(snapshot('456')),encoding='utf-8')
    (folder/'b.json').write_text(json.dumps(row),encoding='utf-8')
    with pytest.raises(ValueError,match='Existing source changed'):
        import_snapshots(source,folder,tmp_path/'backup.db')
    with closing(sqlite3.connect(source)) as db:assert db.execute('SELECT count(*) FROM articles').fetchone()[0]==0
    row=snapshot();row['html']='<p>当前环境异常，完成验证后即可继续访问。</p>'
    (folder/'b.json').write_text(json.dumps(row),encoding='utf-8')
    with pytest.raises(ValueError):import_snapshots(source,folder,tmp_path/'backup2.db')
    assert not (tmp_path/'backup2.db').exists()
