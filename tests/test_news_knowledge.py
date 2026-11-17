from datetime import datetime

import pytest

from app.database import SessionLocal
from app.fetch_news import save_news_to_db
from app.models import News, NewsKnowledgeLink
from app.news_knowledge import candidates, link_news, news_snapshot, rebuild_news_links


def article(content='科技创新推动新质生产力发展。', title='产业发展观察', **kwargs):
    return dict(source='gov_cn', category='时政', title=title, link='https://www.gov.cn/test',
                summary='导读', content=content, published='2026-11-14',
                published_at=datetime(2026, 11, 14), **kwargs)


def test_import_automatically_links_and_has_bidirectional_navigation(client):
    assert save_news_to_db([article()]) == 1
    point = client.get('/api/knowledge/productivity-innovation').json()
    assert point['news']['total'] == 1
    link = point['news']['items'][0]
    proof = link['evidence']
    assert link['status'] == 'candidate' and link['is_current']
    assert proof['field'] == 'content'
    assert link['snapshot'][proof['field']][proof['start']:proof['end']] == proof['text']
    assert {'新质生产力', '科技创新'}.issubset(proof['matched_terms'])
    reverse = client.get('/api/news/1/knowledge').json()['items']
    assert reverse[0]['id'] == 'productivity-innovation'
    assert '/knowledge/productivity-innovation' in client.get('/news/1').text
    # The homepage now prioritizes news. The existing point link remains on the
    # article page, while auxiliary tools are collapsed on the homepage.
    assert '知识点与复习等辅助工具' in client.get('/today').text
    assert '联系新闻阅读' in client.get('/knowledge/productivity-innovation').text
    assert '科技创新推动新质生产力' in client.get(f'/knowledge/news-links/{link["id"]}').text
    assert client.get('/api/news/999/knowledge').status_code == 404
    assert client.get('/api/knowledge-news-links/999').status_code == 404


@pytest.mark.parametrize('title,body', [
    ('创新观察', '科技创新促进产业发展。新质生产力受到关注。'),
    ('新质生产力', '科技创新促进产业发展。'),
    ('创新分享', '某公司宣布推出新款手机。'),
    ('参访信息', '来宾参访新质生产力代表性企业，感受科技创新的活力。'),
    ('产业消息', ''),
    ('科技创新', '新质生产力' + '其他信息' * 300),
])
def test_single_keyword_or_different_sentences_do_not_match(title, body):
    snap = {'title': title, 'content': body, 'summary': '科技创新推动新质生产力'}
    assert candidates(snap) == {}


def test_title_match_is_explicitly_labeled_not_claimed_as_body(client):
    save_news_to_db([article('', title='科技创新推动新质生产力发展')])
    row = client.get('/api/knowledge/productivity-innovation').json()['news']['items'][0]
    assert row['evidence']['field'] == 'title'


def test_edit_retires_old_link_and_keeps_immutable_snapshot(client, monkeypatch):
    save_news_to_db([article()])
    original = client.get('/api/knowledge/productivity-innovation').json()['news']['items'][0]
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'test-key')
    route = f'/api/knowledge-news-links/{original["id"]}/review'
    assert client.put(route, json={'status': 'confirmed'}).status_code == 403
    assert client.put(route, json={'status': 'confirmed'}, headers={'X-Sync-Token': 'test-key'}).status_code == 200
    save_news_to_db([article('科技创新为新质生产力提供新的支撑。')])
    current = client.get('/api/knowledge/productivity-innovation').json()['news']
    assert current['total'] == 1 and current['items'][0]['status'] == 'candidate'
    assert current['items'][0]['id'] != original['id']
    old = client.get(f'/api/knowledge-news-links/{original["id"]}').json()
    assert old['snapshot'] == original['snapshot'] and not old['is_current']
    assert old['status'] == 'confirmed'
    assert client.get('/api/knowledge/productivity-innovation?history=true').json()['news']['total'] == 2
    save_news_to_db([article('如今报道只保留人物采访和日常动态。')])
    assert client.get('/api/knowledge/productivity-innovation').json()['news']['total'] == 0
    assert client.get('/api/news/1/knowledge').json()['items'] == []
    assert rebuild_news_links()['added'] == 0


def test_review_rejection_survives_rebuild(client, monkeypatch):
    save_news_to_db([article()])
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'test-key')
    route = '/api/knowledge-news-links/1/review'
    headers = {'X-Sync-Token': 'test-key'}
    assert client.put(route, json={'status': 'other'}, headers=headers).status_code == 422
    assert client.put(route, json={'status': 'rejected'}, headers=headers).status_code == 200
    assert rebuild_news_links() == {'added': 0}
    assert client.get('/api/knowledge-news-links/1').status_code == 404
    assert client.get('/api/knowledge/productivity-innovation?history=true').json()['news']['items'] == []
    assert client.get('/api/news/1/knowledge').json()['items'] == []


def test_pagination_and_html_escaping(client):
    with SessionLocal() as db:
        for i in range(23):
            news = News(title='<script>alert(1)</script>', content='实体经济是现代化产业体系的根基。',
                        link=f'https://example.org/{i}', source='gov_cn', published_at=datetime(2026, 11, 14),
                        year=2026, month=11)
            db.add(news)
            link_news(db, news)
        db.commit()
    first = client.get('/api/knowledge/economy-real').json()['news']
    second = client.get('/api/knowledge/economy-real?page=2').json()['news']
    assert first['total'] == 23 and len(first['items']) == 20 and len(second['items']) == 3
    assert client.get('/api/knowledge/economy-real?page=0').status_code == 422
    page = client.get('/knowledge/economy-real').text
    assert '<script>alert(1)</script>' not in page and '&lt;script&gt;' in page
    assert 'page=2' in page
    assert '<script>alert(1)</script>' not in client.get('/knowledge/news-links/1').text


def test_link_failure_rolls_back_news_and_associations(monkeypatch):
    import app.news_knowledge as module
    original = module.link_news
    def fail(db, news):
        original(db, news)
        raise RuntimeError('association write failed')
    monkeypatch.setattr(module, 'link_news', fail)
    with pytest.raises(RuntimeError):
        save_news_to_db([article()])
    with SessionLocal() as db:
        assert db.query(News).count() == 0
        assert db.query(NewsKnowledgeLink).count() == 0
