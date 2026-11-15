from datetime import datetime

import pytest

from app.database import SessionLocal
from app.knowledge import rebuild_links
from app.materials import extract_material
from app.models import LearningMaterial
from app.revisions import append_revision

INTRO = '<p>本文保留教师原文及段落出处，条目只是规则整理，学习时应结合上下文和教材核对，不是命题预测。</p>' * 3


def test_inline_formatting_keeps_sentence_and_answer_whole():
    result=extract_material(INTRO+'<p>点击空白处查看答案</p><p>1.<span>中国梦的</span><strong>本质是什么</strong></p>'
        '<p>中国梦的本质是<span>国家富强</span>、<b>民族振兴</b>、<em>人民幸福</em>。</p><p>后台回复领取材料</p>')
    question=result['questions'][0]
    assert question['question']=='中国梦的本质是什么'
    assert question['answer']=='中国梦的本质是国家富强、民族振兴、人民幸福。'
    refs=[line['text'] for line in result['evidence'] if line['id'] in question['evidence']]
    assert refs==['1.中国梦的本质是什么','中国梦的本质是国家富强、民族振兴、人民幸福。']


def test_commentary_and_analysis_preserve_limits_and_exclude_distractors():
    result=extract_material(INTRO+'<p>命题分析</p><p>专题二 世界的物质性</p>'
        '<p>➤①物质世界<span>是客观存在的</span>，不依赖于人的意识或认识。</p><p>经典干扰项：</p>'
        '<p>✘物质世界的客观存在与人的实践和认识水平有关</p><p>这一错误选项不能摘成正面结论。</p>'
        '<p>➤②时空具有客观性，时空观念则是人们对它的认识。</p>'
        '<p>➤2010年以来真题选择题考查频率：10次。</p><p>选择题练习</p><p>这段题干不应进入讲解摘录。</p>')
    assert len(result['points'])==2
    assert '不依赖于人的意识或认识' in result['points'][0]['text']
    assert all('干扰项' not in p['text'] and '错误选项' not in p['text'] and '考查频率' not in p['text'] for p in result['points'])


def test_image_quiz_is_not_fabricated_and_commentary_does_not_become_answer():
    result=extract_material(INTRO+'<p>点击空格横线查看答案</p><svg data-lazy-bgimg="https://example.org/question.png"></svg>'
        '<p>参考答案</p><p>1.这只是参考答案编号，没有可提取的文字题目。</p>')
    assert result['questions']==[]
    assert any('未做 OCR' in note for note in result['review_notes'])


@pytest.mark.parametrize('identity,question,statement',[
    ('modernization-dream-essence','中国梦的本质是什么','实现中华民族伟大复兴的中国梦，本质是国家富强、民族振兴、人民幸福。'),
    ('modernization-supports','中国式现代化的基础性、战略性支撑是什么 （P161)','教育、科技、人才是中国式现代化的基础性、战略性支撑。'),
    ('development-primary-task','全面建设社会主义现代化国家的首要任务是什么 (P162)','高质量发展是全面建设社会主义现代化国家的首要任务。'),
    ('development-productivity-role','推动高质量发展的内在要求和重要着力点是什么','推动高质量发展的内在要求和重要着力点：发展新质生产力。'),
    ('economy-ownership-status','公有制经济和非公有制经济都是什么的重要组成部分','公有制经济与非公有制经济都是社会主义市场经济的重要组成部分，这里一定要注意“社会主义市场经济”这个定语。'),
])
def test_explicit_question_statement_pairs_keep_two_original_sources(client,identity,question,statement):
    with SessionLocal() as db:
        for source,account,html in [('q','徐涛',INTRO+f'<p>点击空白处查看答案</p><p>1.{question}</p><p>答案由原文提供，不代表已经核对教材。</p><p>后台回复</p>'),
                                    ('p','肖秀荣',INTRO+f'<p>点拨</p><p>专题三</p><p>➤{statement}</p><p>选择题练习</p>')]:
            row=LearningMaterial(source_id=source,account=account,title='原文',url='https://mp.weixin.qq.com/s/'+source,
                fingerprint=source,payload=extract_material(html),imported_at=datetime(2026,11,15))
            db.add(row);append_revision(db,row,html);db.commit()
    result=client.get('/api/knowledge/'+identity).json()
    assert len(result['evidence'])==2 and {entry['account'] for entry in result['evidence']}=={'徐涛','肖秀荣'}
    assert all(entry['status']=='candidate' and entry['evidence'] for entry in result['evidence'])
    from bs4 import BeautifulSoup
    page=BeautifulSoup(client.get('/knowledge/'+identity).text,'html.parser')
    disclosure=page.select_one('details.learning-question')
    assert disclosure is not None and not disclosure.has_attr('open')
    assert disclosure.select('a[href*="revision="]')
    assert '答案由原文提供' in disclosure.get_text()
    assert rebuild_links()['added']==0


def test_nearby_concepts_and_wrong_choices_do_not_join_new_points(client):
    html=INTRO+'<p>点拨</p><p>专题三</p><p>经典干扰项：</p><p>中国梦的本质是什么</p>'
    html+='<p>➤教育和人才是基础性支撑，但这里没有声明相同的知识命题。</p><p>选择题练习</p>'
    html+='<p>点击空白处查看答案</p><p>1.我国实现城镇化的首要任务是什么</p><p>推进农村转移人口市民化。</p><p>后台回复</p>'
    with SessionLocal() as db:
        row=LearningMaterial(source_id='negative',account='老师',title='不能泛词匹配',url='https://mp.weixin.qq.com/s/negative',
            fingerprint='negative',payload=extract_material(html),imported_at=datetime(2026,11,15))
        db.add(row);append_revision(db,row,html);db.commit()
    assert client.get('/api/knowledge').json()['items']==[]
