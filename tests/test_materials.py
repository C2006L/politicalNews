from contextlib import closing
from datetime import datetime

import sqlite3

import pytest

from app.database import SessionLocal
from app.materials import extract_material, sync_materials
from app.models import LearningMaterial


def test_focus_extraction_preserves_source_and_excludes_advertising():
    result = extract_material('<script>ignore()</script><p>图书推广及其他非考点内容，仅用于验证提取区域能排除正文前的广告和说明。</p><p>重温重要提法</p>'
                              '<p>专题十五 法治</p><p>总抓手：建设中国特色社会主义法治体系。</p>'
                              '<p>有些权利和义务具有复合性，不能扩大理解为所有。</p>'
                              '<p>选择题练习</p><p>这是题目而不是重点摘录。</p>')
    assert len(result['points']) == 2
    assert all(p['section'] == '专题十五 法治' for p in result['points'])
    assert result['points'][0]['evidence']
    assert result['method'] == 'rules-2'


def test_quiz_separates_answer_and_preserves_author_star():
    result = extract_material('<p>前文只是学习建议，不应成为考点。</p><p>点击空白处查看答案👇</p>'
                              '<p>🌟1.现代化产业体系的骨干是什么</p><p>现代化产业体系的骨干是先进制造业。</p>'
                              '<p>2.某一概念的关键是什么</p><p>甲/乙，两种表述需要对照教材核对。</p>'
                              '<p>后台回复领取资料</p><p>这些是营销文案，不是自测答案。</p>')
    first, second = result['questions']
    assert first['starred_by_author']
    assert first['answer'] == '现代化产业体系的骨干是先进制造业。'
    assert second['needs_review']
    assert '营销' not in second['answer']


def test_verification_page_is_rejected():
    with pytest.raises(ValueError):
        extract_material('<p>当前环境异常，请完成验证后即可继续访问。</p>' * 20)


def test_sync_is_read_only_idempotent_and_updates_changed_text(tmp_path, monkeypatch):
    path = tmp_path / 'werss.db'
    html = '<p>重温重要提法</p><p>专题十五 法治</p><p>这是一条需要保留来源依据的考点表述。</p>' * 4
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table articles(id,mp_id,title,url,content_html,has_content,create_time)')
        db.execute('insert into articles values(?,?,?,?,?,1,1)',
                   ('1', 'MP_WXS_3243013434', '测试材料', 'https://mp.weixin.qq.com/s/test', html))
        db.execute('insert into articles values(?,?,?,?,?,1,1)',
                   ('2', 'OTHER', '非白名单', 'https://mp.weixin.qq.com/s/test2', html))
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(path))
    before = path.read_bytes()
    assert sync_materials()['added'] == 1
    assert sync_materials()['skipped'] == 1
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('update articles set content_html=? where id=?', (html + '<p>修订后的新表述。</p>', '1'))
    assert sync_materials()['updated'] == 1
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 1


def test_sync_api_requires_configured_admin_token(client, monkeypatch):
    assert client.post('/api/learning/sync').status_code == 503
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'test-secret')
    assert client.post('/api/learning/sync', headers={'X-Sync-Token': 'wrong'}).status_code == 403


def test_import_job_is_registered_only_with_explicit_source(monkeypatch):
    from app.tasks import setup_scheduler
    assert setup_scheduler().get_job('werss_materials_import') is None
    monkeypatch.setenv('WERSS_DATABASE_PATH', '/configured/werss.db')
    assert setup_scheduler().get_job('werss_materials_import').trigger.interval.total_seconds() == 900


def test_split_number_and_question_saved_html():
    result = extract_material('<p>前文提供学习范围及复习方法，提取器应将其与问答正文分开保存，不得擅自补充答案。</p><p>点击空白处查看答案👇</p><p>1.</p><p>如何理解这条理论表述</p>'
                              '<p>这是来自原文的答案，保留完整的限定条件与表述，不用模型补充。</p>'
                              '<p>后台回复</p><p>后文与复习卡片无关的其他说明，不应混入前面的答案。</p>')
    assert result['questions'][0]['question'] == '如何理解这条理论表述'
    assert '后文' not in result['questions'][0]['answer']


def test_learning_views_escape_content_and_handle_empty(client):
    assert '暂无已整理资料' in client.get('/learning').text
    assert client.get('/api/learning/999').status_code == 404
    with SessionLocal() as db:
        db.add(LearningMaterial(source_id='x', account='肖秀荣教授', title='<script>alert(1)</script>',
                                url='https://mp.weixin.qq.com/s/x', fingerprint='abc',
                                payload=extract_material('<p>重温重要提法</p><p>专题十五</p>'
                                                         '<p>保留这条原文表述用于安全渲染验证。</p>' * 5),
                                imported_at=datetime(2026, 11, 13)))
        db.commit()
    assert '&lt;script&gt;' in client.get('/learning').text
    assert '<script>alert' not in client.get('/learning/1').text
    assert client.get('/api/learning').json()['total'] == 1
