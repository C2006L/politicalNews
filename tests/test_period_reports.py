from datetime import datetime
from bs4 import BeautifulSoup
import pytest
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import News,PeriodReport
from tests.test_events import row,BODY


@pytest.fixture
def sample():
    with SessionLocal() as db:
        db.add_all([row(1,'会议情况通报',published=datetime(2026,11,15)),
                    row(2,'会议后续报道',published=datetime(2026,11,14)),
                    row(3,'未来新闻',published=datetime(2099,1,1))]);db.commit()


def save(client,data,token='report-test'):
    return client.post('/api/reports/snapshots',headers={'X-Sync-Token':token,'X-Requested-With':'study-ui'},
                       json={'kind':data['kind'],'day':data['start'],'fingerprint':data['fingerprint']})


def test_daily_weekly_periods_and_empty_date_do_not_borrow_content(client,sample):
    daily=client.get('/api/reports?kind=daily&day=2026-11-15').json()
    assert daily['collected']==1 and daily['items'][0]['sources'][0]['news_id']==1
    week=client.get('/api/reports?kind=weekly&day=2026-11-15').json()
    assert week['start']=='2026-11-09' and week['end']=='2026-11-15' and week['collected']==2
    assert client.get('/api/reports?day=2026-11-13').json()['items']==[]
    assert client.get('/api/reports?day=2099-01-01').status_code==422
    assert client.get('/api/reports?kind=bad').status_code==422
    with SessionLocal() as db:assert db.query(PeriodReport).count()==0


def test_explicit_save_is_immutable_idempotent_and_stale_drafts_are_rejected(client,sample,monkeypatch):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','report-test')
    draft=client.get('/api/reports?day=2026-11-15').json()
    assert save(client,draft,'wrong').status_code==403
    first=save(client,draft).json();assert first['created']
    assert not save(client,draft).json()['created']
    with SessionLocal() as db:
        saved=db.get(PeriodReport,first['id']).payload
        n=db.get(News,1);n.content=n.summary=BODY+'新增服务安排与具体落实要求，相关单位需要结合实际情况继续完善。';db.commit()
    assert save(client,draft).status_code==409
    live=client.get('/api/reports?day=2026-11-15').json()
    assert live['fingerprint']!=draft['fingerprint']
    response=client.get('/reports/saved/'+first['id'])
    assert '当前资料与这个保存版本不同' in response.text
    with SessionLocal() as db:assert db.get(PeriodReport,first['id']).payload==saved
    assert save(client,live).json()['created']
    assert client.get('/reports/saved/missing').status_code==404


def test_save_commit_failure_rolls_back_and_html_has_sources(client,sample,monkeypatch):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','report-test')
    data=client.get('/api/reports?day=2026-11-15').json()
    def fail(self):raise RuntimeError('injected snapshot failure')
    with monkeypatch.context() as patch:
        patch.setattr(Session,'commit',fail)
        with pytest.raises(RuntimeError):save(client,data)
    with SessionLocal() as db:assert db.query(PeriodReport).count()==0
    soup=BeautifulSoup(client.get('/reports?day=2026-11-15').text,'html.parser')
    assert soup.select_one('.period-entry blockquote')
    assert soup.select_one('.report-original').text==data['items'][0]['sources'][0]['snapshot']['text']
    assert soup.select_one('.report-controls [name=day]')['value']=='2026-11-15'

def test_weekly_only_merges_confirmed_event_members_inside_period(client,sample):
    from app.events import link_event
    from app.models import EventArticle
    before=client.get('/api/reports?kind=weekly&day=2026-11-15').json()
    assert len(before['items'])==2
    with SessionLocal() as db:
        for identity in [1,2]:link_event(db,db.get(News,identity))
        db.flush()
        for link in db.query(EventArticle):link.status='confirmed'
        db.commit()
    merged=client.get('/api/reports?kind=weekly&day=2026-11-15').json()
    assert len(merged['items'])==1
    assert len(merged['items'][0]['sources'])==2
    assert merged['fingerprint']!=before['fingerprint']
    daily=client.get('/api/reports?day=2026-11-15').json()
    assert len(daily['items'][0]['sources'])==1
