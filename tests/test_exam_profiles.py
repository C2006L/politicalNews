import hashlib,json
from datetime import datetime
import pytest
from app.exam_profiles import assess
from app.database import SessionLocal
from app.models import AppState,News
from tests.test_events import row

BODY='国务院发布有关政策措施，明确适用对象和实施安排，相关单位需要建立工作机制并及时向社会公布具体进展。'


@pytest.mark.parametrize('profile,title,body,expected',[
 ('postgrad','中央经济工作会议部署年度重点任务',BODY,True),
 ('postgrad','纪念重要历史事件胜利80周年大会举行',BODY,True),
 ('postgrad','嫦娥任务取得重大突破',BODY+'任务成功完成，公布科研成果。',True),
 ('postgrad','人工智能交流活动举行','各方举行人工智能交流活动，主要介绍嘉宾背景和相关讨论内容，没有明确成果信息。',False),
 ('postgrad','多领域专家探讨非遗推动历史经典产业高质量发展路径',BODY,False),
 ('civil','部署乡村振兴与基层治理工作',BODY,True),
 ('civil','上海发布《养老服务管理办法》',BODY,True),
 ('civil','天津发布《医疗保障十五五规划》',BODY,True),
 ('civil','巧手妆成国风诗意',BODY,False),
 ('civil','人工智能课程限时抢购',BODY,False),
])
def test_direction_positive_and_negative_examples(profile,title,body,expected):
    r=assess(row(title=title,body=body),profile,capture={'status':'extracted'})
    assert r['eligible']==expected
    if expected:
        assert r['evidence'] and r['reading_focus']
        for e in r['evidence']:assert e['quote'] in (title if e['field']=='title' else body)


def test_keyword_only_and_unverified_body_are_not_shortlist_material():
    item=row(title='国务院发布乡村振兴实施方案',body='我们关注乡村振兴这一话题，它是一项广泛的讨论，相关概念经常出现，本文主要介绍若干背景知识。')
    assert not assess(item,'civil',capture={'status':'extracted'})['eligible']
    assert not assess(row(title='中央经济工作会议召开',body=BODY),'postgrad',capture={'status':'unverified'})['eligible']


def test_profiles_limit_topics_and_save_versions_separately(client,monkeypatch):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','profiles-test')
    with SessionLocal() as db:
        for i in range(1,8):
            n=row(i,title=f'国务院发布乡村振兴实施方案第{i}项',body=BODY,published=datetime(2026,11,15));db.add(n);db.flush()
            db.add(AppState(key='news_body:'+str(i),value=json.dumps({'status':'extracted','fingerprint':hashlib.sha256(BODY.encode()).hexdigest()})))
        db.commit()
    civil=client.get('/api/reports?day=2026-11-15&profile=civil').json()
    postgrad=client.get('/api/reports?day=2026-11-15&profile=postgrad').json()
    assert len(civil['items'])==2 and civil['eligible_groups']==7
    assert postgrad['items']==[]
    assert civil['fingerprint']!=postgrad['fingerprint']
    headers={'X-Sync-Token':'profiles-test','X-Requested-With':'study-ui'}
    for data in [civil,postgrad]:
        r=client.post('/api/reports/snapshots',headers=headers,json={'kind':'daily','day':'2026-11-15','profile':data['profile'],'fingerprint':data['fingerprint']})
        assert r.status_code==200
        html=client.get('/reports/saved/'+r.json()['id']).text
        assert 'profile='+data['profile'] in html
    assert client.get('/api/reports?profile=invalid').status_code==422
