from datetime import datetime
from app.official_sources import links,publication,collect,SOURCES
from app.news_body import extract_body


def test_links_reject_offsite_and_publication_never_uses_import_date():
    raw='<a href="/n1/2026/1117/c123-456.html">基层治理改革评论文章</a><a href="http://evil.test/n1/2026/1117/x.html">不可信任站点上的文章</a>'
    assert len(links(raw,'http://opinion.people.com.cn/','opinion.people.com.cn'))==1
    assert publication('<meta name="publishdate" content="2026-11-17">')==datetime(2026,11,17)
    assert publication('<p>无刊载时间</p>') is None
    assert extract_body('<div id="detail_content"><p>基层治理政策正文，明确实施条件和适用对象。</p></div>')[2]['status']=='extracted'


def test_collect_keeps_website_identity_and_is_idempotent(client,monkeypatch,tmp_path):
    import app.official_sources as module
    from app.database import SessionLocal
    from app.models import News
    today=datetime.now().strftime('%Y-%m-%d')
    monkeypatch.setattr(module,'SOURCES',{'banyuetan_web':SOURCES['banyuetan_web']})
    article=f'<h1>基层治理政策实施的评论</h1><meta name="publishdate" content="{today}"><div id="detail_content"><p>'+('明确基层治理政策实施条件，完善公共服务工作措施。'*12)+'</p></div>'
    monkeypatch.setattr(module,'fetch',lambda u:article if '/detail/' in u else '<a href="/pl/detail/20261117/123_1.html">基层治理政策实施的评论</a>')
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert collect(tmp_path,2)['sources'][0]['saved']==1
    assert collect(tmp_path,2)['sources'][0]['existing']==1
    with SessionLocal() as db:
        row=db.query(News).one()
        assert row.source=='banyuetan_web' and 'banyuetan.org' in row.link


def test_quality_failure_stays_out_of_featured_but_remains_in_all(client):
    import hashlib
    from tests.test_editorial import add_news
    from app.study import prepare_readings
    row=add_news()
    captures={row.id:{'status':'partial','fingerprint':hashlib.sha256(row.content.encode()).hexdigest()}}
    assert prepare_readings([row],mode='featured',captures=captures)==[]
    assert len(prepare_readings([row],mode='all',captures=captures))==1


def test_existing_links_do_not_consume_batch_budget(client,monkeypatch,tmp_path):
    import app.official_sources as module
    monkeypatch.setattr(module,'SOURCES',{'banyuetan_web':SOURCES['banyuetan_web']})
    today=datetime.now().strftime('%Y-%m-%d')
    raw=f'<meta name="publishdate" content="{today}"><div id="detail_content"><p>'+('公共服务改革明确适用范围和实施条件。'*15)+'</p></div>'
    listing=''.join(f'<a href="/pl/detail/20261117/{i}_1.html">基层治理政策解读文章{i}</a>' for i in range(3))
    fetched=[]
    def fetch(url):
        if '/detail/' in url:
            fetched.append(url);index=url.rsplit('/',1)[-1].split('_')[0]
            return '<h1>基层治理政策解读文章'+index+'</h1>'+raw
        return listing
    monkeypatch.setattr(module,'fetch',fetch);monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert collect(tmp_path,1)['sources'][0]['saved']==1
    second=collect(tmp_path,1)['sources'][0]
    assert second['saved']==1 and second['existing']==1 and second['not_attempted']==1
    assert len(set(fetched))==2


def test_dengta_static_literals_are_read_without_running_scripts():
    from app.official_sources import website_body
    raw='list.push({link: `/web/dtdjzx/test/123.html`,title: `基层治理的公开文章`});list.push({link: `${evil()}`,title: `不执行脚本表达式`})'
    found=links(raw,'https://www.dtdjzx.gov.cn/','www.dtdjzx.gov.cn')
    assert len(found)==1 and found[0]['url'].endswith('/123.html')
    assert website_body('content: `<p>依法实施基层治理改革，明确政策条件和适用范围。</p>`','dengta_web')[2]['status']=='extracted'
    assert website_body('content: `${evil()}`','dengta_web')[2]['status']=='unverified'
