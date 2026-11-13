from datetime import datetime

from app.database import SessionLocal
from app.knowledge import rebuild_links, point_evidence
from app.materials import extract_material
from app.models import LearningMaterial, MaterialRevision, KnowledgeLink
from app.revisions import append_revision


def material(db, source, account, html):
    row = LearningMaterial(source_id=source, title='原文 <script>x</script>', account=account,
                           url='https://mp.weixin.qq.com/s/test', fingerprint=source,
                           imported_at=datetime(2026, 11, 14), payload=extract_material(html))
    db.add(row)
    append_revision(db, row, html)
    db.commit()
    return row


BODY = '<p>阅读方法只是引导，不能自动作为结论依据，需要对照原文和教材复核，保留来源及版本信息。</p><p>重温重要提法</p><p>专题十五</p><p>全面依法治国的总抓手：建设中国特色社会主义法治体系。</p><p>科学立法是前提，严格执法是关键，公正司法是重点，全民守法是基础。</p>'


def test_same_point_two_accounts_keeps_separate_sources(client):
    with SessionLocal() as db:
        material(db, 'a', '老师甲', BODY)
        material(db, 'b', '老师乙', BODY)
    point = client.get('/api/knowledge/law-system').json()
    assert {e['account'] for e in point['evidence']} == {'老师甲', '老师乙'}
    assert all(e['status'] == 'candidate' for e in point['evidence'])
    page = client.get('/knowledge/law-system').text
    assert '?revision=1#e-' in page
    assert '<script>x</script>' not in page
    assert client.get('/api/knowledge/unknown').status_code == 404


def test_rebuild_does_not_duplicate_or_override_review(client, monkeypatch):
    with SessionLocal() as db:
        material(db, 'a', '老师甲', BODY)
        link_id = db.query(KnowledgeLink).one().id
    assert client.put(f'/api/knowledge-links/{link_id}/review', json={'status': 'confirmed'}).status_code == 403
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'test-key')
    assert client.put(f'/api/knowledge-links/{link_id}/review', json={'status': 'confirmed'},
                      headers={'X-Sync-Token': 'test-key'}).status_code == 200
    assert rebuild_links()['added'] == 0
    assert client.get('/api/knowledge/law-system').json()['evidence'][0]['status'] == 'confirmed'


def test_old_version_is_retained_but_not_current():
    with SessionLocal() as db:
        row = material(db, 'a', '老师甲', BODY)
        row.payload = extract_material('<p>这是后续不同主题的讲解，不再包含之前那条考点的原文依据。</p>' * 10)
        append_revision(db, row, 'new html')
        db.commit()
        assert point_evidence(db, 'law-system') == []
        historical = point_evidence(db, 'law-system', history=True)
        assert len(historical) == 1 and historical[0]['revision'] == 1
        assert not historical[0]['is_current']


def test_keyword_mention_does_not_form_relationship():
    unrelated = '<p>重温重要提法</p><p>专题十五</p><p>有人提及全面依法治国的总抓手，但本段讨论的是学习方法，不给出对应的考点结论。</p>' * 4
    with SessionLocal() as db:
        material(db, 'a', '老师甲', unrelated)
        assert db.query(KnowledgeLink).count() == 0


def test_missing_evidence_cannot_link():
    with SessionLocal() as db:
        row = material(db, 'a', '老师甲', '<p>其他内容不涉及当前模板范围，应该保留原文但不能强行归类。</p>' * 10)
        row.payload = extract_material(BODY)
        row.payload = {**row.payload, 'evidence': []}
        append_revision(db, row)
        db.commit()
        assert db.query(KnowledgeLink).count() == 0


def test_rejected_links_stay_hidden_after_rebuild(client, monkeypatch):
    with SessionLocal() as db:
        material(db, 'a', '老师甲', BODY)
        link_id = db.query(KnowledgeLink).one().id
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'test-key')
    headers = {'X-Sync-Token': 'test-key'}
    route = f'/api/knowledge-links/{link_id}/review'
    assert client.put(route, json={'status': 'other'}, headers=headers).status_code == 422
    assert client.put(route, json={'status': 'rejected'}, headers=headers).status_code == 200
    assert rebuild_links()['added'] == 0
    assert client.get('/api/knowledge/law-system?history=true').json()['evidence'] == []
