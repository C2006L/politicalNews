from datetime import datetime
import hashlib,json
from app.database import SessionLocal
from app.models import News,AppState
from tests.test_events import row,BODY


def test_quality_inventory_is_bound_to_current_content_and_does_not_write(client):
    with SessionLocal() as db:
        db.add_all([row(1,published=datetime.now()),row(2,published=datetime.now()),row(3,published=datetime.now())]);db.flush()
        for i in [1,2]:
            db.add(AppState(key='news_body:'+str(i),value=json.dumps({'status':'extracted','paragraphs':1,'fingerprint':hashlib.sha256(BODY.encode()).hexdigest(),**({'last_attempt_status':'blocked'} if i==2 else {})})))
        db.commit()
    r=client.get('/api/news-quality?days=0').json()
    assert r['counts']=={'total':3,'extracted':2,'unverified':1,'attention':1}
    assert client.get('/api/news-quality?status=attention&days=0').json()['items'][0]['id']==2
    with SessionLocal() as db:
        db.get(News,1).content='正文已经改变，旧采集指纹不可继续使用。';db.commit()
        before=db.query(AppState).count()
    assert client.get('/api/news-quality?days=0&status=unverified').json()['total']==2
    assert client.get('/news-quality?days=0&source=missing').status_code==200
    assert '当前条件没有文章' in client.get('/news-quality?source=missing').text
    with SessionLocal() as db:assert db.query(AppState).count()==before


def test_quality_filters_keep_scope_and_validate_inputs(client):
    assert client.get('/api/news-quality?days=8').status_code==422
    assert client.get('/news-quality?status=bad').status_code==422
    assert client.get('/news-quality?page=0').status_code==422
    html=client.get('/news-quality?days=30&scope=all&status=unverified').text
    assert '正文质量' in html and 'name="source"' in html

def test_quality_period_matches_report_dates_and_rejects_invalid_ranges(client):
    with SessionLocal() as db:
        db.add_all([row(1,published=datetime(2026,11,14)),row(2,published=datetime(2026,11,15))]);db.commit()
    params='period_start=2026-11-15&period_end=2026-11-15'
    data=client.get('/api/news-quality?'+params).json()
    assert data['total']==1 and data['items'][0]['id']==2
    page=client.get('/news-quality?'+params).text
    assert 'name="period_start" value="2026-11-15"' in page
    assert client.get('/api/news-quality?period_start=2026-11-15').status_code==422
    assert client.get('/api/news-quality?period_start=2026-11-15&period_end=2026-11-14').status_code==422
