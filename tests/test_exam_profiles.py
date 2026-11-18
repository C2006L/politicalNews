import hashlib,json
from datetime import datetime
import pytest
from app.exam_profiles import assess
from app.database import SessionLocal
from app.models import AppState,News
from tests.test_events import row
from app.study import prepare_readings

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


@pytest.mark.parametrize('title,body,postgrad,civil',[
 ('习近平对人民法院工作作出重要指示','习近平近日对人民法院工作作出重要指示，强调完善社会主义司法制度，以司法保障高质量发展。',True,True),
 ('破除潜规则，亮起明规矩','半月谈评论员：党的二十届五中全会审议通过重要决定，部署加强党内法规制度建设。必须破除潜规则，完善治理机制。',True,True),
 ('人民锐评：农村治污何以致污','近期农村污水治理出现设备空转问题，不能简单照搬城市经验，需要根据农村实际完善管理机制。',False,True),
 ('人民锐评：大学该教什么','高校教育面临人工智能普及的新情况，大学需要明确教育责任，建立教学规范，不能将思考交给软件。',False,True),
 ('人民锐评：副刊何以生动','文章谈到写作体验和个人记忆，也提及中国式现代化。副刊需要涵养从容心态，记录文学感受与创作技巧。',False,False),
 ('同一份申论材料，国考和省考答案不同','模拟材料：国务院发布乡村治理实施方案，明确群众协商要求。下面介绍如何作答，并给出答题技巧。',False,False),
 ('雾凇有什么好看的','自然景观十分美丽，游客可以欣赏冰雪与林木，这篇文章介绍风景拍摄地点和旅行体验。',False,False),
 ('晋沪文脉相逢 佳酿重器同辉','露酒新品发布会在城市中心举办，三款产品亮相，品牌营销团队介绍产品并展示限量款商品。',False,False),
 ('人工智能交流会议举行','会议举行并介绍各方交流情况，尚未取得突破，也没有公布成果，没有新的科研进展。',False,False),
 ('政论：同舟共济','新华社政论：写在亚太经合组织领导人非正式会议举行之际。会议将推动地区合作，坚持开放方向，必须凝聚发展共识。',True,False),
])
def test_reviewed_article_shapes_require_direction_evidence(title,body,postgrad,civil):
    item=row(title=title,body=body)
    for profile,expected in [('postgrad',postgrad),('civil',civil)]:
        result=assess(item,profile,capture={'status':'extracted'})
        assert result['eligible']==expected
        for proof in result['evidence']:
            assert proof['quote'] in (title if proof['field']=='title' else body)


@pytest.mark.parametrize('province',['山东','上海','广东','天津'])
def test_local_policy_has_no_province_preference(province):
    item=row(title=province+'发布养老服务管理办法',body=BODY)
    assert assess(item,'civil')['eligible']
    assert not assess(item,'postgrad')['eligible']


def test_home_featured_tracks_filter_real_content_and_all_mode_preserves_access():
    report=row(1,title='嫦娥任务取得重大突破',body=BODY+'任务成功完成。')
    case=row(2,title='人民锐评：农村治污应因地制宜',body='农村污水治理出现设备空转，必须完善管理制度，不能照搬城市经验，需要根据群众需求制定措施。')
    assert [x.item.id for x in prepare_readings([report,case],track='postgrad',mode='featured')]==[1]
    assert [x.item.id for x in prepare_readings([report,case],track='essay',mode='featured')]==[2]
    assert len(prepare_readings([report,case],track='postgrad',mode='all'))==2


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
