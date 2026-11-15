from datetime import datetime

from bs4 import BeautifulSoup

from app.database import SessionLocal
from app.models import KnowledgeLink, KnowledgePoint, LearningMaterial, MaterialRevision


def material(account, title, *, questions=True, evidence=True):
    with SessionLocal() as db:
        row = LearningMaterial(source_id=title, account=account, title=title, url='https://example.org/a',
                               fingerprint=title, imported_at=datetime(2026, 11, 15), payload={})
        db.add(row)
        db.flush()
        payload = {'evidence': [{'id': 'L1', 'text': '原文依据'}] if evidence else [],
                   'questions': [{'question': '问题', 'answer': '答案', 'evidence': ['L1']}] if questions else [],
                   'points': []}
        revision = MaterialRevision(material_id=row.id, revision=1, origin='test', snapshot={
            'title': title, 'account': account, 'payload': payload})
        db.add(revision)
        db.commit()
        return row.id, revision.id


def test_empty_home_has_useful_path_without_fake_progress(client):
    soup = BeautifulSoup(client.get('/today').text, 'html.parser')
    desk = soup.select_one('.learning-desk')
    assert '还没有可自测' in desk.text and '还没有两位老师' in desk.text
    assert desk.select_one('[data-desk-review-summary]').text == '正在读取本浏览器复习安排…'
    assert desk.select_one('[data-desk-review-link]')['href'] == '/practice'
    assert desk.select_one('a[href="/getting-started"]')
    assert len(soup.select('script[data-practice-script]')) == 1


def test_shortlist_keeps_sources_separate_and_ignores_unusable_items(client):
    first, _ = material('老师甲', '第一份')
    newer, _ = material('老师甲', '<新材料>')
    second, _ = material('老师乙', '另一来源')
    material('老师丙', '只有原文', questions=False)
    material('老师丁', '依据缺失', evidence=False)
    desk = BeautifulSoup(client.get('/today').text, 'html.parser').select_one('.learning-desk')
    links = [a['href'] for a in desk.select('li a')]
    assert links == [f'/learning/{second}?revision=1#question-0', f'/learning/{newer}?revision=1#question-0']
    assert f'/learning/{first}?revision=1#question-0' not in links
    assert '&lt;新材料&gt;' in str(desk)
    assert '不是今日发布或已学进度' in desk.text


def test_comparison_excludes_old_versions_and_rejected_links_and_changes_live_revision(client):
    first, rev_a = material('甲', '材料甲')
    _, rev_b = material('乙', '材料乙')
    with SessionLocal() as db:
        db.add(KnowledgePoint(id='shared', title='共同考点', subject='新思想'))
        db.flush()
        for revision in (rev_a, rev_b):
            db.add(KnowledgeLink(point_id='shared', revision_id=revision, kind='questions',
                                 item_index=0, evidence_ids=['L1'], status='candidate', method='test'))
        db.commit()
    shared = client.get('/api/study/live?day=2026-11-15').json()
    assert 'href="/knowledge/shared"' in shared['html']
    with SessionLocal() as db:
        db.query(KnowledgeLink).filter_by(revision_id=rev_b).update({'status': 'rejected'})
        db.commit()
    rejected = client.get('/api/study/live?day=2026-11-15').json()
    assert 'href="/knowledge/shared"' not in rejected['html']
    assert rejected['revision'] != shared['revision']
    with SessionLocal() as db:
        db.query(KnowledgeLink).filter_by(revision_id=rev_b).update({'status': 'confirmed'})
        previous = db.get(MaterialRevision, rev_a)
        db.add(MaterialRevision(material_id=first, revision=2, snapshot=previous.snapshot, origin='correction'))
        db.commit()
    old = client.get('/api/study/live?day=2026-11-15').json()
    assert 'href="/knowledge/shared"' not in old['html']
    assert f'/learning/{first}?revision=2#question-0' in old['html']
