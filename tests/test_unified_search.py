from datetime import datetime

from app.database import SessionLocal
from app.models import News, LearningMaterial, KnowledgePoint
from app.revisions import append_revision


def seed():
    with SessionLocal() as db:
        db.add(News(title='法治新闻', link='https://example.org/news', summary='', content='建设完整法治体系',
                    source='people_cn', published='2024-01-01', published_at=datetime(2024, 1, 1), year=2024, month=1))
        material=LearningMaterial(source_id='search-1',account='老师',title='课堂摘录',url='https://mp.weixin.qq.com/s/x',
            fingerprint='s1',imported_at=datetime(2026,1,1),payload={'evidence':[{'id':'L1','text':'完善法治体系 原文包含100%和a_b'}]})
        db.add(material);db.flush();append_revision(db,material)
        db.add(KnowledgePoint(id='law-test',title='法治体系',subject='思法'))
        db.commit()


def test_all_kinds_and_old_news_default_visible(client):
    seed()
    data=client.get('/api/search',params={'q':'法治 体系'}).json()
    assert data['total']==3
    assert {key:value['total'] for key,value in data['groups'].items()}=={'news':1,'materials':1,'knowledge':1}
    assert data['groups']['news']['items'][0]['url'].startswith('/news/')
    assert '完善法治体系' in data['groups']['materials']['items'][0]['excerpt']
    assert client.get('/api/search',params={'q':'人民网','kind':'news'}).json()['total']==1


def test_news_filters_do_not_hide_materials_or_points(client):
    seed()
    data=client.get('/api/search',params={'q':'法治','year':2026,'source':'gov_cn'}).json()
    assert data['groups']['news']['total']==0 and data['total']==2
    assert client.get('/search?year=&q=法治').status_code==200
    assert client.get('/search?year=2024').status_code==200
    assert client.get('/api/search').json()['total']==0


def test_current_evidence_only_no_json_keys_or_old_revision_match(client):
    seed()
    with SessionLocal() as db:
        m=db.query(LearningMaterial).one()
        m.payload={'evidence':[{'id':'L1','text':'更正后的当前内容'}]}
        m.fingerprint='s2';append_revision(db,m);db.commit()
    assert client.get('/api/search?q=法治&kind=materials').json()['total']==0
    assert client.get('/api/search?q=evidence&kind=materials').json()['total']==0
    assert client.get('/api/search?q=更正&kind=materials').json()['total']==1


def test_literals_validation_and_html_escape(client):
    seed()
    assert client.get('/api/search?q=%25&kind=materials').json()['total']==1
    assert client.get('/api/search?q=a_b&kind=materials').json()['total']==1
    assert client.get('/api/search?q=zz_&kind=materials').json()['total']==0
    page=client.get('/search',params={'q':'<script>alert(1)</script>'}).text
    assert '<script>alert(1)</script>' not in page and '&lt;script&gt;' in page
    for query in ('kind=no','page=0','q='+'a'*121,'year=10000'):
        assert client.get('/api/search?'+query).status_code==422


def test_stable_paging_and_subpath_links(client,monkeypatch):
    seed()
    with SessionLocal() as db:
        for i in range(23):
            db.add(KnowledgePoint(id=f'p-{i:02}',title='独立分页主题',subject='测试'))
        db.commit()
    first=client.get('/api/search?q=独立&kind=knowledge').json()
    second=client.get('/api/search?q=独立&kind=knowledge&page=2').json()
    assert first['total']==second['total']==23
    assert len(first['groups']['knowledge']['items'])==20 and len(second['groups']['knowledge']['items'])==3
    assert not {v['id'] for v in first['groups']['knowledge']['items']} & {v['id'] for v in second['groups']['knowledge']['items']}
    monkeypatch.setenv('BASE_PATH','/study')
    page=client.get('/search?q=独立&kind=knowledge').text
    assert 'href="/study/knowledge/' in page and '/study/search?' in page
