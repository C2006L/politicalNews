from datetime import datetime

from bs4 import BeautifulSoup
import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.editorial import classify, decision_map, select_document
from app.models import News, LearningMaterial, ContentDecision
from app.study import prepare_readings

BODY = '国务院召开会议，公布有关公共服务的安排，明确实施时间、适用范围和具体措施。相关部门将按职责落实，详细内容请以发布的原文为准。'


def add_news(identity=1, title='国务院发布公共服务改革方案', source='gov_cn', text=BODY):
    with SessionLocal() as db:
        row = News(id=identity, source=source, category='时政', title=title, link=f'https://example.org/{identity}',
                   summary=text, content=text, published='2026-11-15', published_at=datetime(2026,11,15), year=2026,month=11)
        db.add(row);db.commit()
        db.refresh(row);db.expunge(row)
        return row


def entry(client, identity=1, kind='news'):
    return client.get(f'/api/curation?kind={kind}&item={identity}').json()['items'][0]


def correction(client, item, **changes):
    return client.put(f'/api/curation/{item["kind"]}/{item["id"]}',
        headers={'X-Sync-Token':'editorial-test','X-Requested-With':'study-ui'}, json={
            'fingerprint':item['fingerprint'],'expected_decision':item['decision_id'],**changes})


@pytest.mark.parametrize('title,text,expected,selected',[
    ('国务院发布公共服务改革方案',BODY,'policy',True),
    ('解读：国务院发布公共服务改革方案',BODY,'analysis',True),
    ('新华社评论员：改革持续推进',BODY,'analysis',True),
    ('时政月度梳理：公共服务新进展',BODY,'analysis',True),
    ('11月时政梳理与自测',BODY+'点击空白处查看答案','analysis',True),
    ('两国领导人举行会晤',BODY,'report',True),
    ('总理会见外国来宾',BODY,'report',True),
    ('【观察】亚太经济合作新动向',BODY,'analysis',True),
    ('直播海报：亚太会议新闻发布会',BODY,'uncertain',False),
    ('我国卫星发射取得成功',BODY,'report',True),
    ('【背诵手册D28】思法专题',BODY,'textbook',False),
    ('徐涛带背D28！考研订酒店',BODY,'textbook',False),
    ('看似时政的标题',BODY+'点击空白处查看答案','textbook',False),
    ('免费领！这些院校公布真题',BODY,'promotion',False),
    ('课程开售：限时抢购',BODY,'promotion',False),
    ('考研每天1小时够吗',BODY,'exam_advice',False),
    ('《8套卷》使用说明',BODY,'exam_advice',False),
    ('复习建议：如何理解政策改革',BODY,'exam_advice',False),
    ('消费券免费领，市政府公布促消费政策',BODY,'policy',True),
    ('调查：扫码领课背后的广告乱象',BODY,'report',True),
    ('关于改革的读书随笔','这里仅谈个人阅读方法，不包含任何近期事件或发布的信息。'*3,'uncertain',False),
    ('发布了一项政策','短文','uncertain',False),
    ('国务院发布公共服务改革方案','国务院发布公共服务改革方案','uncertain',False),
])
def test_classification_keeps_current_affairs_separate_from_study_and_marketing(title,text,expected,selected):
    result = classify(title,text,'gov_cn','https://example.org/source')
    assert (result.category,result.selected)==(expected,selected)
    assert result.reasons
    for proof in result.evidence:
        assert proof['quote'] in (title if proof['field']=='title' else text.replace('\n',' '))


def test_invalid_original_link_never_gets_automatic_selection():
    result=classify('国务院发布公共服务改革方案',BODY,'gov_cn','javascript:alert(1)')
    assert not result.selected


def test_home_caps_each_topic_and_all_retains_filtered_items(client):
    for identity in range(1,13):add_news(identity,f'国务院发布第{identity}项公共服务方案')
    add_news(13,'课程开售：限时抢购')
    result=client.get('/api/study/live?day=2026-11-15').json()
    soup=BeautifulSoup(result['html'],'html.parser')
    assert len(soup.select('.reading-card'))==2
    assert soup.select_one('.curation-modes a[aria-current]')['href'].endswith('mode=featured')
    assert '课程开售' not in result['html']
    all_items=client.get('/api/study/live?day=2026-11-15&mode=all&page=2&track=civil&topic=all').json()
    assert '课程开售' in all_items['html'] and 'mode=all' in all_items['html']
    assert '未入选' in all_items['html']
    assert '知识点与复习等辅助工具' in result['html'] and '.learning-desk' not in result['html']
    assert client.get('/today?mode=invalid').status_code==422


def test_bodyless_and_uncertain_content_stays_accessible_but_not_featured(client):
    add_news(text='只有标题')
    assert not entry(client)['selected']
    assert not BeautifulSoup(client.get('/today').text,'html.parser').select('.reading-card')
    assert BeautifulSoup(client.get('/today?mode=all').text,'html.parser').select('.reading-card')


def test_repeated_summary_is_not_double_counted_as_sufficient_body():
    row=add_news(text='这是一条还不足三十字的简短导读。')
    assert not select_document(row).selected


def test_reprints_do_not_raise_score():
    first=add_news(1,'部门举行公开会议','people_cn')
    repeat=add_news(2,'部门举行公开会议','xinhuanet')
    assert prepare_readings([first])[0].score == prepare_readings([first,repeat])[0].score


def test_explicit_manual_inclusion_is_prioritized_within_daily_limit(client,monkeypatch):
    for identity in range(1,12):add_news(identity,f'国务院发布第{identity}项公共服务方案')
    add_news(12,'未识别为时政的标题',text='原文文字仍可回查，但这是一份需要人工判断类型的资料。'*3)
    original=entry(client,12)
    assert not original['selected']
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','editorial-test')
    assert correction(client,original,category='report',selection='include',note='核对后优先纳入').status_code==200
    page=BeautifulSoup(client.get('/api/study/live?day=2026-11-15').json()['html'],'html.parser')
    assert len(page.select('.reading-card'))==3
    assert page.select_one('.reading-card')['data-id']=='12'


def test_correction_is_authenticated_version_bound_reversible_and_audited(client,monkeypatch):
    add_news()
    original=entry(client)
    assert correction(client,original,selection='exclude').status_code==403
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','editorial-test')
    bad=client.put('/api/curation/news/1',headers={'X-Sync-Token':'editorial-test','X-Requested-With':'study-ui','Origin':'https://evil.test'},
                   json={'fingerprint':original['fingerprint'],'expected_decision':0,'selection':'exclude'})
    assert bad.status_code==403
    before=client.get('/api/study/live?day=2026-11-15').json()
    changed=correction(client,original,category='report',selection='exclude',note='<script>不要入选</script>')
    assert changed.status_code==200
    excluded=entry(client)
    assert not excluded['selected'] and excluded['manual']
    after=client.get('/api/study/live?day=2026-11-15').json()
    assert before['revision']!=after['revision']
    assert not BeautifulSoup(after['html'],'html.parser').select('.reading-card')
    all_html=client.get('/api/study/live?day=2026-11-15&mode=all').json()['html']
    assert '&lt;script&gt;' in all_html and '<script>不要' not in all_html
    assert correction(client,original,selection='include').status_code==409
    assert not correction(client,excluded,category='report',selection='exclude',note='<script>不要入选</script>').json()['changed']
    reset=correction(client,excluded,category=None,selection='auto')
    assert reset.status_code==200 and entry(client)['selected'] and not entry(client)['manual']
    history=client.get('/api/curation/news/1/history',headers={'X-Sync-Token':'editorial-test'}).json()
    assert history['total']==2 and all(row['is_current'] for row in history['items'])


def test_old_correction_does_not_follow_changed_article(client,monkeypatch):
    add_news();monkeypatch.setenv('SYNC_ADMIN_TOKEN','editorial-test')
    original=entry(client)
    assert correction(client,original,selection='exclude').status_code==200
    with SessionLocal() as db:
        row=db.get(News,1);row.content += '这是来源更正后的新增文字。';db.commit()
    updated=entry(client)
    assert updated['selected'] and not updated['manual'] and updated['decision_id']==0
    assert correction(client,original,selection='include').status_code==409
    history=client.get('/api/curation/news/1/history',headers={'X-Sync-Token':'editorial-test'}).json()
    assert not history['items'][0]['is_current']


def test_failed_correction_rolls_back(client,monkeypatch):
    add_news();monkeypatch.setenv('SYNC_ADMIN_TOKEN','editorial-test');original=entry(client)
    def fail(self):raise RuntimeError('injected commit failure')
    monkeypatch.setattr(Session,'commit',fail)
    with pytest.raises(RuntimeError):correction(client,original,selection='exclude')
    with SessionLocal() as db:assert db.query(ContentDecision).count()==0
    assert entry(client)['selected']


def test_materials_use_body_structure_and_never_get_a_forced_daily_slot(client):
    with SessionLocal() as db:
        db.add(LearningMaterial(id=1,source_id='a',account='考研政治徐涛',title='看起来谈时政的文章',url='https://mp.weixin.qq.com/s/one',
                payload={'evidence':[{'id':'L1','text':'背诵知识点。'+BODY}]},fingerprint='a',imported_at=datetime(2026,11,15)))
        db.add(LearningMaterial(id=2,source_id='b',account='肖秀荣教授',title='解读：国务院发布公共服务改革方案',url='https://mp.weixin.qq.com/s/two',
                payload={'evidence':[{'id':'L1','text':BODY}]},fingerprint='b',imported_at=datetime(2026,11,15)))
        db.commit()
    assert entry(client,1,'material')['category']=='textbook'
    assert entry(client,2,'material')['selected']
    assert not BeautifulSoup(client.get('/today').text,'html.parser').select('.account-curation a[href="/learning/2"]')
    from app.revisions import append_revision
    with SessionLocal() as db:
        row=db.get(LearningMaterial,2)
        row.payload={'evidence':[{'id':'L1','text':BODY*3}]}
        append_revision(db,row,'<div id="js_content"><p>'+BODY*3+'</p></div>')
        db.commit()
    soup=BeautifulSoup(client.get('/today').text,'html.parser')
    notes=soup.select_one('.account-curation')
    assert not notes.select('a[href="/learning/1"]')
    assert notes.select('a[href="/learning/2"]')
    assert '不把导入时间当成发布时间' in notes.text


def test_empty_home_has_a_news_path_and_correction_filters_accept_all(client):
    soup=BeautifulSoup(client.get('/today').text,'html.parser')
    assert soup.select_one('a[href="/getting-started"]')
    assert len(soup.select('script[data-practice-script]'))==1
    assert not soup.select('[data-desk-review]')
    assert client.get('/curation?kind=material&category=&scope=all').status_code==200
    assert client.get('/api/curation?category=bad').status_code==422
