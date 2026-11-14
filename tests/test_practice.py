from datetime import datetime

from app.database import SessionLocal
from app.models import LearningMaterial, MaterialRevision
from app.revisions import append_revision


def seed():
    with SessionLocal() as db:
        material = LearningMaterial(source_id='practice-1', title='学习资料', account='老师', url='https://example.org',
                                    fingerprint='p1', imported_at=datetime(2026, 11, 14), payload={
            'evidence': [{'id': 'L1', 'text': '问题与答案原文'}],
            'questions': [{'number': 1, 'question': '原文问题', 'answer': '原文答案', 'evidence': ['L1'], 'needs_review': True, 'starred_by_author': True}],
            'points': [{'section': '专题十五 全面依法治国的总抓手——建设中国特色社会主义法治体系', 'text': '全面依法治国的总抓手：建设中国特色社会主义法治体系。', 'evidence': ['L1']}],
            'pages': [], 'review_notes': [], 'topic': '思法'})
        db.add(material)
        append_revision(db, material)
        db.commit()


def test_question_snapshot_keeps_flags_evidence_and_version(client):
    seed()
    card = client.get('/api/practice/card/1/1/questions/0').json()
    assert card['key'] == 'material:1:r1:questions:0'
    assert card['question'] == '原文问题' and card['answer'] == '原文答案'
    assert card['needs_review'] and card['starred_by_author']
    assert card['url'] == '/learning/1?revision=1#e-L1'
    assert card['evidence'][0]['text'] == '问题与答案原文'
    with SessionLocal() as db:
        material = db.get(LearningMaterial, 1)
        material.payload = {**material.payload, 'questions': [{**material.payload['questions'][0], 'answer': '更正后的答案'}]}
        append_revision(db, material)
        db.commit()
    assert client.get('/api/practice/versions').json()['versions'] == {'1': 2}
    assert client.get('/api/practice/card/1/1/questions/0').json()['answer'] == '原文答案'
    assert client.get('/api/practice/card/1/2/questions/0').json()['answer'] == '更正后的答案'


def test_statement_recall_is_labeled_and_buttons_are_integrated(client):
    seed()
    card = client.get('/api/practice/card/1/1/points/0').json()
    assert card['kind'] == 'points' and '回想原文提法' in card['question']
    assert '建设中国特色社会主义法治体系' not in card['question']
    assert '总抓手' in card['answer']
    page = client.get('/learning/1').text
    assert page.count('data-practice-add') == 2
    assert 'data-practice-add' in client.get('/knowledge/law-system').text
    assert '/practice' in client.get('/learning').text
    assert 'data-practice-page' in client.get('/practice').text
    assert '/static/practice.js' in client.get('/today').text


def test_missing_card_and_invalid_evidence_do_not_create_fake_cards(client):
    seed()
    assert client.get('/api/practice/card/1/1/questions/-1').status_code == 422
    assert client.get('/api/practice/card/1/1/questions/99').status_code == 404
    assert client.get('/api/practice/card/1/1/other/0').status_code == 422
    assert client.get('/api/practice/card/999/1/questions/0').status_code == 404
    with SessionLocal() as db:
        material = db.get(LearningMaterial, 1)
        material.payload = {**material.payload, 'evidence': []}
        append_revision(db, material)
        db.commit()
    assert client.get('/api/practice/card/1/2/questions/0').status_code == 422
    with SessionLocal() as db:
        assert db.query(MaterialRevision).count() == 2
