from datetime import datetime
from contextlib import closing
import sqlite3
from app.database import SessionLocal
from app.models import LearningMaterial,MaterialRevision
from app.materials import extract_material,sync_materials
from app.revisions import append_revision


def test_converted_sync_cannot_replace_recovered_full_page(client,tmp_path,monkeypatch):
    fragment='<p>'+('公共政策明确适用对象与实施条件，应完整保留原文表述。'*10)+'</p>'
    path=tmp_path/'source.db'
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('create table articles(id,mp_id,title,url,content_html,has_content,create_time)')
        db.execute('insert into articles values(?,?,?,?,?,1,1)',('a','MP_WXS_3243013434','政策说明','https://mp.weixin.qq.com/s/a',fragment))
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(path))
    assert sync_materials()['added']==1
    full='<div id="js_content">'+fragment+'</div>'
    with SessionLocal() as db:
        row=db.query(LearningMaterial).one();row.fingerprint='full-page';append_revision(db,row,full);db.commit()
    assert sync_materials()['skipped']==1
    with SessionLocal() as db:
        assert db.query(MaterialRevision).count()==2
        assert db.query(MaterialRevision).order_by(MaterialRevision.revision.desc()).first().source_html==full


def test_strict_featured_rejects_unknown_but_all_keeps_it(client,monkeypatch):
    from tests.test_editorial import add_news
    from app.study import prepare_readings
    row=add_news();monkeypatch.setenv('STRICT_TEXT_QUALITY','1')
    assert not prepare_readings([row],mode='featured',captures={})
    assert prepare_readings([row],mode='all',captures={})


def test_legacy_recovery_failure_does_not_block_next_record(client,monkeypatch,tmp_path):
    from tests.test_editorial import add_news,BODY
    import app.news_recovery as module
    one=add_news(1);two=add_news(2)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module,'_is_allowed_source_link',lambda *args:True)
    def fetch(url):
        return '<h1>错误标题</h1>' if url.endswith('/2') else '<h1>'+one.title+'</h1><div id="detail_content"><p>'+BODY*4+'</p></div>'
    monkeypatch.setattr(module,'fetch',fetch)
    assert module.run(1)['results'][0]['state']=='not_updated'
    assert module.run(1)['results'][0]['state']=='verified'
    with SessionLocal() as db:
        assert db.get(__import__('app.models',fromlist=['News']).News,two.id).content==BODY
