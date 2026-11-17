from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs,urlparse
from bs4 import BeautifulSoup
import pytest


@pytest.fixture
def browsing(monkeypatch):
    from app.routers import web
    items=[]
    for i in range(24):
        dt=datetime(2026 if i<20 else 2025,11 if i<20 else 10,15)
        items.append(SimpleNamespace(id=i+1,title=f'新闻{i+1}',link=f'https://www.gov.cn/{i}',
            source='gov_cn' if i<16 else 'people_cn',category='时政' if i<12 else '权威发布',
            summary='原文摘要',content='原文正文',published=dt.strftime('%Y-%m-%d'),published_at=dt,year=dt.year,month=dt.month))
    monkeypatch.setattr(web,'query_news',lambda **kwargs:(items,[2026,2025]))
    return items


def test_category_switch_source_submit_and_paging_keep_scope(client,browsing):
    soup=BeautifulSoup(client.get('/categories?source=gov_cn&category=shizheng').text,'html.parser')
    assert not soup.select('.nav-row,.search-form,.stats,.category-grid')
    nav=soup.select_one('[aria-label="专题切换"]')
    for a in nav.select('a'):
        assert parse_qs(urlparse(a['href']).query)['source']==['gov_cn']
    assert nav.select_one('[aria-current]').select_one('span').text=='时政'
    form=soup.select_one('.browse-source')
    assert form['action']=='/categories'
    assert form.select_one('[name=category]')['value']=='shizheng'
    assert form.select_one('[name=source] option[selected]')['value']=='gov_cn'
    assert len(soup.select('.news-card'))==8
    pager=soup.select_one('.pager a[href]')
    params=parse_qs(urlparse(pager['href']).query)
    assert params['category']==['shizheng'] and params['source']==['gov_cn']
    old=BeautifulSoup(client.get('/category/shizheng?source=gov_cn&page=2').text,'html.parser')
    assert old.select_one('[aria-label="专题切换"]')
    assert len(old.select('.news-card'))==4


def test_empty_category_is_explained_and_can_be_reset(client,browsing):
    soup=BeautifulSoup(client.get('/categories?category=waijiao&source=gov_cn').text,'html.parser')
    assert soup.select_one('.empty-state')
    assert soup.select_one('.browse-source a')['href']=='/categories'
    assert len(soup.select('[aria-label="主导航"] [aria-current]'))==1


def test_archive_year_is_local_filter_and_all_years_is_valid(client,browsing):
    soup=BeautifulSoup(client.get('/archive?year=2025&source=people_cn').text,'html.parser')
    assert not soup.select('.nav-row,.search-form,.stats')
    assert len(soup.select('.month-block'))==1
    assert '2025年10月' in soup.select_one('.month-title').text
    assert not soup.select_one('.month-block').has_attr('open')
    assert soup.select_one('[name=year] option[selected]')['value']=='2025'
    assert client.get('/archive?year=0&source=').status_code==200
    assert '当前年份和来源下没有文章' in client.get('/archive?year=2027').text
