"""Exercise real import, evidence links, review and fixed-version recall from an empty DB."""
from contextlib import closing
import sqlite3

from app import sync_service
from app.materials import ACCOUNTS


def source_db(path, body):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table articles(id text primary key,mp_id,title,url,content_html,has_content,create_time)')
        db.execute('insert into articles values(?,?,?,?,?,1,1)', (
            'flow-1',next(iter(ACCOUNTS)),'自测流程验收','https://mp.weixin.qq.com/s/flow-1',body))


BODY = ('<p>点击空白处查看答案</p><p>1. 新质生产力的核心要素是什么？</p><p>科技创新。</p>'
        '<p>后台回复结束</p><p>' + '保留有出处的原文，先回想再对照答案，规则摘录还需要核对教材。' * 6 + '</p>')


def test_empty_import_review_and_versioned_recall(client,tmp_path,monkeypatch):
    path=tmp_path/'source.db';source_db(path,BODY)
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(path))
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','workflow-test-token')
    headers={'X-Sync-Token':'workflow-test-token'}
    assert client.get('/api/getting-started').json()['state']=='empty'
    before=path.read_bytes()
    assert client.post('/api/learning/sync',headers=headers).json()['added']==1
    assert path.read_bytes()==before
    ready=client.get('/api/getting-started').json()
    assert ready['state']=='ready' and ready['news_state']=='empty' and ready['news_count']==0
    assert ready['material_count']==ready['knowledge_count']==ready['recall_entry_count']==1
    material=client.get('/api/learning').json()['items'][0]
    detail=client.get(f'/learning/{material["id"]}').text
    assert 'data-practice-add' in detail and '科技创新' in detail
    hits=client.get('/api/search?q=新质生产力&kind=materials').json()
    assert hits['total']==1
    point=client.get('/api/knowledge/productivity-innovation').json()
    assert point['evidence']
    queue=client.get('/api/review/queue',headers=headers).json()
    assert queue['total']==1
    link=queue['items'][0]
    reviewed=client.put(f'/api/knowledge-links/{link["id"]}/review',headers=headers,
        json={'status':'confirmed','expected_status':'candidate','note':'流程验收，关联而非答案核对'})
    assert reviewed.status_code==200
    assert client.get('/api/review/events',headers=headers).json()['total']==1
    card_path=f'/api/practice/card/{material["id"]}/1/questions/0'
    old=client.get(card_path).json()
    assert old['answer']=='科技创新。' and old['evidence'] and old['revision']==1
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('update articles set content_html=?',(BODY.replace('科技创新。','科技创新。这是更正后的原文表述。'),))
    assert client.post('/api/learning/sync',headers=headers).json()['updated']==1
    assert client.get(card_path).json()==old
    assert client.get('/api/review/queue',headers=headers).json()['total']==1
    new=client.get(f'/api/practice/card/{material["id"]}/2/questions/0').json()
    assert new['revision']==2 and '更正' in new['answer'] and new['key']!=old['key']
    assert 'data-learning-path' in client.get('/getting-started').text
    listing=client.get('/learning').text
    assert '第一次用？先试一道题' in listing and '打开这道题，试着回想' in listing
    assert f'/learning/{material["id"]}?revision=2#question-0' in listing
    assert 'id="question-0"' in client.get(f'/learning/{material["id"]}?revision=2').text


def test_materials_remain_ready_when_news_failed_and_no_recall_items(client,tmp_path,monkeypatch):
    path=tmp_path/'source.db'
    source_db(path,'<p>'+'只保存完整原文，尚未识别到支持的自测段落。'*10+'</p>')
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(path))
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','workflow-test-token')
    client.post('/api/learning/sync',headers={'X-Sync-Token':'workflow-test-token'})
    sync_service.set_app_state('sync_finished_at','2026-11-15T00:00:00Z')
    state=client.get('/api/getting-started').json()
    assert state['state']=='ready' and state['news_state']=='no_content'
    assert state['material_count']==1 and state['recall_entry_count']==state['knowledge_count']==0
    listing=client.get('/learning').text
    assert '原文资料 · 阅读已保存的文字正文' in listing and '阅读原文资料' in listing
    assert '第一次用？先试一道题' not in listing
    material=client.get('/api/learning').json()['items'][0]
    detail=client.get(f'/learning/{material["id"]}').text
    assert 'id="source-text"' in detail and '只保存完整原文' in detail
    assert 'data-practice-add' not in detail
