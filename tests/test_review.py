from datetime import datetime
import base64

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.fetch_news import save_news_to_db
from app.models import LearningMaterial, KnowledgeLink, RelationReviewEvent, NewsKnowledgeLink, AppState
from app.revisions import append_revision
from app.knowledge import rebuild_links
from app.news_knowledge import rebuild_news_links


@pytest.fixture
def review_data(monkeypatch):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'review-test-secret')
    with SessionLocal() as db:
        payload = {'points': [{'text': '全面依法治国的总抓手：建设中国特色社会主义法治体系。', 'evidence': ['L1']}],
                   'questions': [], 'evidence': [{'id': 'L1', 'text': '原文 <script>danger()</script>'}]}
        material = LearningMaterial(source_id='r1', title='资料 <script>danger()</script>', account='老师',
                                    url='https://example.org', fingerprint='one', imported_at=datetime(2026, 11, 14), payload=payload)
        db.add(material)
        append_revision(db, material)
        db.commit()
    save_news_to_db([dict(source='gov_cn', category='时政', title='产业发展', link='https://gov.cn/example',
                         summary='摘要', content='科技创新推动新质生产力发展。', published='2026-11-14',
                         published_at=datetime(2026, 11, 14))])
    return {'X-Sync-Token': 'review-test-secret'}


def test_queue_requires_auth_and_has_full_evidence(client, review_data, monkeypatch):
    assert client.get('/api/review/queue').status_code == 403
    assert client.get('/api/review/events').status_code == 403
    page = client.get('/review').text
    assert 'data-review-login' in page and 'review-test-secret' not in page
    response = client.get('/api/review/queue', headers=review_data).json()
    assert response['total'] == 2
    assert {item['kind'] for item in response['items']} == {'material', 'news'}
    assert all(item['context'] and item['evidence'] for item in response['items'])
    monkeypatch.delenv('SYNC_ADMIN_TOKEN')
    assert client.get('/api/review/queue', headers=review_data).status_code == 403


@pytest.mark.parametrize('kind,route,model', [
    ('material', '/api/knowledge-links/1/review', KnowledgeLink),
    ('news', '/api/knowledge-news-links/1/review', NewsKnowledgeLink),
])
def test_review_audit_conflict_and_undo(client, review_data, kind, route, model):
    body = {'status': 'confirmed', 'expected_status': 'candidate', 'note': '对照原文确认'}
    assert client.put(route, headers=review_data, json=body).status_code == 200
    assert client.put(route, headers=review_data, json={**body, 'status': 'rejected'}).status_code == 409
    events = client.get('/api/review/events', headers=review_data).json()['items']
    assert len(events) == 1 and events[0]['kind'] == kind
    assert events[0]['previous'] == 'candidate' and events[0]['status'] == 'confirmed'
    assert events[0]['note'] == body['note'] and events[0]['created_at'].endswith('Z')
    assert client.put(route, headers=review_data, json={'status': 'confirmed'}).json()['changed'] is False
    assert client.put(route, headers=review_data, json={'status': 'rejected', 'expected_status': 'confirmed'}).status_code == 200
    rebuild_links(); rebuild_news_links()
    rows = client.get(f'/api/review/queue?kind={kind}&status=rejected', headers=review_data).json()
    assert rows['total'] == 1
    assert client.put(route, headers=review_data, json={'status': 'candidate', 'expected_status': 'rejected'}).status_code == 200
    assert client.get('/api/review/events', headers=review_data).json()['total'] == 3
    with SessionLocal() as db:
        assert db.get(model, 1).status == 'candidate'


def test_audit_failure_rolls_back_relation(client, review_data):
    def fail(db, *args):
        if any(isinstance(row, RelationReviewEvent) for row in db.new):
            raise RuntimeError('audit write failed')
    event.listen(Session, 'before_flush', fail)
    try:
        with pytest.raises(RuntimeError, match='audit write failed'):
            client.put('/api/knowledge-links/1/review', headers=review_data, json={'status': 'confirmed'})
    finally:
        event.remove(Session, 'before_flush', fail)
    with SessionLocal() as db:
        assert db.get(KnowledgeLink, 1).status == 'candidate'
        assert db.query(RelationReviewEvent).count() == 0


def test_old_material_and_news_snapshots_not_queued(client, review_data):
    with SessionLocal() as db:
        row = db.get(LearningMaterial, 1)
        row.payload = {'points': [], 'questions': [], 'evidence': []}
        append_revision(db, row)
        db.commit()
    save_news_to_db([dict(source='gov_cn', category='时政', title='产业发展', link='https://gov.cn/example',
                         summary='摘要', content='正文更正，已移除之前的关联句子。', published='2026-11-14',
                         published_at=datetime(2026, 11, 14))])
    assert client.get('/api/review/queue', headers=review_data).json()['total'] == 0


def test_validation_and_pagination(client, review_data):
    assert client.get('/api/review/queue?page=0', headers=review_data).status_code == 422
    assert client.get('/api/review/queue?kind=other', headers=review_data).status_code == 422
    assert client.put('/api/knowledge-links/1/review', headers=review_data,
                      json={'status': 'confirmed', 'note': 'x' * 501}).status_code == 422
    assert client.get('/api/review/queue?page=2', headers=review_data).json()['items'] == []
    with SessionLocal() as db:
        for i in range(22):
            db.add(RelationReviewEvent(kind='material', link_id=1, point_id='law-system', previous='candidate',
                                      status='confirmed', note=f'note {i}', created_at=datetime(2026, 11, 14)))
        db.commit()
    assert len(client.get('/api/review/events', headers=review_data).json()['items']) == 20
    assert len(client.get('/api/review/events?page=2', headers=review_data).json()['items']) == 2


def test_local_first_use_password_is_hashed_and_works_after_new_session(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.delenv('SYNC_ADMIN_TOKEN', raising=False)
    password = '本机审核口令-123456'
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as browser:
        assert browser.get('/api/review/access').json() == {'configured': False, 'can_setup': True}
        route = '/api/review/access'
        assert browser.post(route, json={'password': password}).status_code == 403
        headers = {'X-Requested-With': 'study-ui'}
        assert browser.post(route, headers={**headers, 'Origin': 'https://other.example'}, json={'password': password}).status_code == 403
        assert browser.post(route, headers=headers, json={'password': 'short'}).status_code == 422
        assert browser.post(route, headers=headers, json={'password': password}).status_code == 201
        assert browser.post(route, headers=headers, json={'password': password}).status_code == 409
        assert browser.get(route).json()['configured']
    with SessionLocal() as db:
        value = db.get(AppState, 'review_password_v1').value
        assert password not in value and 'digest' in value and 'salt' in value
    credential = 'base64:' + base64.b64encode(password.encode()).decode()
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as browser:
        assert browser.get('/api/review/queue', headers={'X-Sync-Token': credential}).status_code == 200
        assert browser.get('/api/review/queue', headers={'X-Sync-Token': 'wrong'}).status_code == 403
        assert browser.get('/api/review/queue', headers={'X-Sync-Token': 'base64:%%%'}).status_code == 403


def test_password_bootstrap_denies_remote_and_untrusted_host(client, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.delenv('SYNC_ADMIN_TOKEN', raising=False)
    assert not client.get('/api/review/access').json()['can_setup']
    assert client.post('/api/review/access', json={'password': 'abcdefghijk'}, headers={'X-Requested-With': 'study-ui'}).status_code == 403
    with TestClient(app, base_url='http://untrusted.example', client=('127.0.0.1', 50000)) as browser:
        assert not browser.get('/api/review/access').json()['can_setup']
