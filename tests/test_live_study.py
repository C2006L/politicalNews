from datetime import datetime

from bs4 import BeautifulSoup

from app.database import SessionLocal
from app.models import News, AppState


def add_news(day, title='当天资料', source='people_cn'):
    with SessionLocal() as db:
        row=News(title=title,source=source,category='时政',link=f'https://example.org/{day}/{title}',
                 summary='可读的原文导读',content='原文正文',published=f'2026-11-{day:02d}',
                 published_at=datetime(2026,11,day),year=2026,month=11)
        db.add(row);db.commit();return row.id


def test_calendar_follows_requested_day_without_pretending_old_news_is_current(client):
    add_news(14,'昨天的资料')
    result=client.get('/api/study/live?day=2026-11-15&mode=all').json()
    soup=BeautifulSoup(result['html'],'html.parser')
    assert soup.select_one('[data-clock-day]').text=='15'
    assert '2026.11.14' in soup.select_one('.reading-date').text
    assert '今日暂无入库' in soup.select_one('[data-live-message]').text
    assert soup.select_one('[data-live-study]')['data-live-revision']==result['revision']
    assert result==client.get('/api/study/live?day=2026-11-15&mode=all').json()


def test_midnight_and_new_ingestion_change_content_and_revision(client):
    add_news(14,'十四日资料');add_news(16,'未来日期资料')
    before=client.get('/api/study/live?day=2026-11-15&mode=all').json()
    assert '未来日期资料' not in before['html']
    add_news(15,'十五日新资料')
    current=client.get('/api/study/live?day=2026-11-15&mode=all').json()
    assert before['revision']!=current['revision']
    assert '十五日新资料' in current['html'] and '十四日资料' not in current['html']
    tomorrow=client.get('/api/study/live?day=2026-11-16&mode=all').json()
    assert tomorrow['day']=='2026-11-16' and '未来日期资料' in tomorrow['html']


def test_correction_refresh_keeps_filters_and_reads_do_not_start_acquisition(client):
    identity=add_news(15,'政策方案发布','gov_cn');add_news(15,'科技新闻','people_cn')
    query='/api/study/live?day=2026-11-15&source=gov_cn&track=postgrad&topic=policy&page=1&mode=all'
    before=client.get(query)
    assert before.headers['cache-control']=='no-store'
    assert '科技新闻' not in before.json()['html']
    with SessionLocal() as db:
        row=db.get(News,identity);row.summary='更正后的导读';db.commit()
        state_count=db.query(AppState).count()
    after=client.get(query).json()
    assert '更正后的导读' in after['html'] and before.json()['revision']!=after['revision']
    assert 'source=gov_cn' in after['html'] and 'track=postgrad' in after['html']
    with SessionLocal() as db:assert db.query(AppState).count()==state_count
    for args in ['day=bad','day=2026-02-30','page=0','track=bad','topic=bad']:
        assert client.get('/api/study/live?'+args).status_code==422
