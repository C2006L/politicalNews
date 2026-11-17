from contextlib import closing
import sqlite3
import pytest
from app.link_import import import_cached_link
from app.database import SessionLocal
from app.models import LearningMaterial, MaterialRevision
from app.materials import sync_materials

URL='https://mp.weixin.qq.com/s/verified-example'
HTML='''<meta property="og:title" content="核对来源的测试文章"><script>var biz="MzI0MzAxMzQzNA==";</script><span id="js_name">肖秀荣教授</span><div id="js_content">'''+('<p>这是用户分享的公开文章正文，只摘录可回查的原文，保留来源信息，不自动生成知识结论。</p>'*5)+'</div>'


def test_shared_link_versions_are_idempotent_and_merge_with_upstream(client,tmp_path,monkeypatch):
    first=import_cached_link('xiao',URL,HTML)
    assert import_cached_link('xiao',URL,HTML)['state']=='unchanged'
    with SessionLocal() as db:
        assert db.query(MaterialRevision).count()==1
    path=tmp_path/'source.db'
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('create table articles(id,mp_id,title,url,content_html,has_content)')
        db.execute('insert into articles values(?,?,?,?,?,1)',('upstream-1','MP_WXS_3243013434','核对来源的测试文章',URL,HTML))
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(path))
    assert sync_materials()['skipped']==1
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count()==1
        assert db.get(LearningMaterial,first['id']).source_id=='upstream-1'
        assert db.query(MaterialRevision).count()==1
    revised=import_cached_link('xiao',URL,HTML.replace('只摘录可回查的原文','增加一条更正，仍保留原文'))
    assert revised['id']==first['id'] and revised['revision']==2


@pytest.mark.parametrize('key,url,html',[
    ('xiao','http://mp.weixin.qq.com/s/test',HTML),
    ('xiao','https://mp.weixin.qq.com.evil.test/s/test',HTML),
    ('tui',URL,HTML),
    ('xiao',URL,HTML.replace('MzI0MzAxMzQzNA==','MzA4MDUwMzAxNA==')),
    ('xiao',URL,HTML.replace('肖秀荣教授','冒名账号')),
    ('xiao',URL,HTML.replace('这是用户分享的公开文章正文','当前环境异常，请完成验证')),
])
def test_reject_wrong_account_or_verification_without_writes(client,key,url,html):
    with pytest.raises(ValueError):import_cached_link(key,url,html)
    with SessionLocal() as db:assert db.query(LearningMaterial).count()==0


def test_long_album_url_requires_matching_page_article_identity(client):
    html=HTML+'<script>var mid="123456789";var idx="1";</script>'
    good='https://mp.weixin.qq.com/s?__biz=MzI0MzAxMzQzNA==&mid=123456789&idx=1&sn=example'
    assert import_cached_link('xiao',good,html)['state']=='imported'
    for bad in [good.replace('mid=123456789','mid=987654321'),good.replace('idx=1','idx=2'),good.replace('MzI0MzAxMzQzNA==','MzA4MDUwMzAxNA==')]:
        with pytest.raises(ValueError):import_cached_link('xiao',bad,html)


def test_identity_accepts_literal_fallback_but_never_executes_expressions(client):
    good='https://mp.weixin.qq.com/s?__biz=MzI0MzAxMzQzNA==&mid=123456789&idx=1'
    literal=HTML+'''<script>var mid="" || "123456789";var idx='' || '1';</script>'''
    assert import_cached_link('xiao',good,literal)['state']=='imported'
    for expression in ['"" || getMid()', '"" || "987654321"', '"123456789" + "0"']:
        raw=HTML+'<script>var mid='+expression+';var idx="1";</script>'
        with pytest.raises(ValueError):import_cached_link('xiao',good,raw)
