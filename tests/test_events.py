from datetime import datetime

from bs4 import BeautifulSoup
import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.events import extract_event, rebuild_events, link_event, content_fingerprint, confirmed_event_map
from app.fetch_news import save_news_to_db
from app.models import News, EventArticle, NewsEvent, EventReview

MEETING='亚太经合组织工商咨询理事会第四次会议开幕式'
BODY=f'新华社深圳2026年11月15日电，2026年11月14日，{MEETING}在深圳举行。与会代表围绕经贸合作交流意见，相关内容保留原文供核对。'


def row(identity=1,title=None,body=BODY,published=datetime(2026,11,15),source='people_cn'):
    return News(id=identity,title=title or MEETING,summary=body,content=body,source=source,category='时政',
                link=f'https://example.org/event/{identity}',published=published.strftime('%Y-%m-%d'),
                published_at=published,year=published.year,month=published.month)


def pair():
    first=row(1,'何立峰出席'+MEETING,source='gov_cn')
    second=row(2,'ABAC第4次会议开幕式在深圳举行')
    with SessionLocal() as db:
        db.add_all([first,second]);db.flush();link_event(db,first);link_event(db,second);db.commit()
        event=db.query(NewsEvent).one();links=db.query(EventArticle).order_by(EventArticle.id).all()
        return event.id,[link.id for link in links]


def review(client,identity,status,previous='candidate'):
    return client.put(f'/api/event-articles/{identity}',headers={'X-Sync-Token':'event-test','X-Requested-With':'study-ui'},
                      json={'status':status,'expected_status':previous,'note':'关系核对，不代表事实已经独立核验'})


def test_same_event_different_titles_keep_exact_evidence_and_publication_date():
    first=extract_event(row())
    second=extract_event(row(2,'ABAC第4次会议开幕式在深圳举行',BODY.replace('第四次','第4次')))
    assert first['id']==second['id']
    assert first['event_day']=='2026-11-14'
    for proof in first['proof']['evidence']:
        original=MEETING if proof['field']=='title' else BODY
        assert original[proof['start']:proof['end']]==proof['quote']


@pytest.mark.parametrize('change',[
    lambda s:s.replace('第四次','第五次'),
    lambda s:s.replace('开幕式','闭幕式'),
    lambda s:s.replace('开幕式','新闻发布会'),
    lambda s:s.replace('11月14日','11月13日'),
    lambda s:s.replace('在深圳','在北京'),
])
def test_same_topic_but_different_session_stage_date_or_venue_is_not_merged(change):
    first=extract_event(row())
    body=change(BODY)
    title=change(MEETING)
    other=extract_event(row(2,title,body))
    assert other is None or other['id']!=first['id']


def test_generic_topic_and_marketing_have_no_event_signature():
    assert extract_event(row(title='亚太合作的未来',body='亚太合作有很多发展方向，需要持续研究经贸往来与文化交流。'*3)) is None
    assert extract_event(row(title='课程开售：'+MEETING)) is None
    assert extract_event(row(title='辟谣：'+MEETING)) is None
    title='习近平在新德里出席金砖国家领导人第十八次会晤第二阶段会议'
    assert extract_event(row(title=title,body=title+'。与会各方交流相关意见，具体表述应回查原文及上下文。')) is None


def test_old_explicit_date_does_not_become_publication_day_and_future_is_labeled():
    assert extract_event(row(body=BODY.replace('2026年11月14日','2020年11月14日'))) is None
    future=extract_event(row(body=BODY.replace('2026年11月14日','2026年11月18日')))
    assert future['facts']['facts']['phase']=='planned'
    assert future['id']!=extract_event(row())['id']


def test_wire_dateline_is_not_the_event_date():
    body=f'新华社北京2026年11月15日电，{MEETING}将于2026年11月18日在深圳举行。代表将交流相关安排，具体内容仍需要回查原文。'
    future=extract_event(row(body=body))
    assert future['event_day']=='2026-11-18' and future['facts']['facts']['phase']=='planned'
    unknown=extract_event(row(body=f'新华社北京2026年11月15日电，{MEETING}在深圳举行。具体活动日期未写明，请按报道原文核对相关事实。'))
    assert unknown['proof']['basis']=='publication_day'


def test_announcement_day_does_not_override_scheduled_meeting_day():
    meeting='亚太经合组织第三十三次领导人非正式会议'
    for announcement in ['外交部发言人13日宣布：','11月13日，中国外交部发言人宣布：']:
        body=f'新华社北京11月13日电 {announcement}{meeting}将于11月18日至19日在深圳举行，会议主题为共同繁荣。'
        parsed=extract_event(row(title='会议安排：'+meeting,body=body,published=datetime(2026,11,13)))
        assert parsed['event_day']=='2026-11-18'
        assert parsed['facts']['facts']['phase']=='planned'
        assert parsed['proof']['evidence'][-1]['quote']=='11月18日'


def test_parenthetical_apec_institution_alias_keeps_main_event_not_background():
    body='新华社深圳11月14日电 11月14日，何立峰在深圳出席亚太经合组织（APEC）工商咨询理事会第四次会议开幕式并致辞。何立峰指出，亚太经合组织第三十三次领导人非正式会议即将在深圳举行。'
    first=extract_event(row(title=MEETING,body=body,published=datetime(2026,11,14)))
    second=extract_event(row(title='ABAC第4次会议开幕式',body=body.replace('（APEC）',''),published=datetime(2026,11,14)))
    assert first['id']==second['id'] and first['event_day']=='2026-11-14'
    assert first['facts']['facts']['organ']=='亚太经合组织工商咨询理事会'


def test_confirmed_event_respects_publication_day_topic_and_no_reprint_score_boost():
    from app.study import prepare_readings
    items=[row(1,title='政策：'+MEETING),row(2,title='国际：'+MEETING),
           row(3,title='次日报道：'+MEETING,published=datetime(2026,11,16))]
    confirmed={1:'same-event',2:'same-event',3:'same-event'}
    groups=prepare_readings(items,confirmed_events=confirmed)
    assert sorted(len(g.articles) for g in groups)==[1,2]
    policy=prepare_readings(items,topic='policy',confirmed_events=confirmed)
    assert len(policy)==1 and [r.id for r in policy[0].articles]==[1]
    one=prepare_readings(items[:1],confirmed_events=confirmed)[0]
    merged=prepare_readings(items[:2],confirmed_events=confirmed)[0]
    assert merged.score==max(prepare_readings([r])[0].score for r in items[:2])
    assert one.key==merged.key=='1'


def test_same_day_same_title_reprints_do_not_create_new_review_noise(client):
    from app.events import event_hint_map
    with SessionLocal() as db:
        for i in [1,2]:
            item=row(i);db.add(item);db.flush();link_event(db,item)
        db.commit()
        assert event_hint_map(db)=={}
    assert client.get('/api/events').json()['total']==0


def test_policy_issuer_name_and_stage_are_required_and_effective_date_is_not_release_date():
    title='国务院发布《公共服务保障条例》'
    body='2026年11月14日，国务院发布《公共服务保障条例》，明确职责与保障措施，该条例将于2026年12月1日起施行。'
    first=extract_event(row(title=title,body=body))
    assert first and first['event_day']=='2026-11-14'
    other=extract_event(row(2,title='解读国务院发布《公共服务保障条例》',body=body))
    assert other['id']==first['id']
    changed=extract_event(row(3,title='教育部发布《公共服务保障条例》',body=body.replace('国务院','教育部')))
    assert changed['id']!=first['id']
    effective=extract_event(row(4,title='国务院实施《公共服务保障条例》',body=body.replace('发布','实施')))
    assert effective['id']!=first['id']
    no_date=extract_event(row(5,title=title,body='国务院发布《公共服务保障条例》，明确相关职责及具体保障措施，将于2026年12月1日起施行。'))
    assert no_date['proof']['basis']=='publication_day' and no_date['event_day']=='2026-11-15'
    denied=extract_event(row(6,title='国务院并未发布《公共服务保障条例》',body=body.replace('国务院发布','国务院并未发布')))
    assert denied is None


def test_bilateral_participants_venue_and_day_only_dates():
    body='新华社曼谷11月15日电，李强14日在曼谷会见泰国国王哇集拉隆功。双方就经贸及人文合作进行交流，具体表述应对照原文。'
    first=extract_event(row(title='李强会见泰国国王哇集拉隆功',body=body))
    assert first and first['event_day']=='2026-11-14' and first['proof']['basis']=='explicit_day'
    full=extract_event(row(2,title='李强在曼谷会见泰国国王哇集拉隆功',body=body.replace('李强14日','李强11月14日')))
    assert first['id']==full['id']
    for phrase,replacement in [('曼谷','北京'),('哇集拉隆功','另一位外国来宾'),('会见','会晤')]:
        other=extract_event(row(3,title='李强会见外国来宾',body=body.replace(phrase,replacement)))
        assert other is None or other['id']!=first['id']


def test_cross_year_month_day_is_inferred_with_bounds():
    body=f'2026年1月1日电，12月31日，{MEETING}在深圳举行。代表交流了相关安排，全文应查原文，这里不生成综合答案。'
    parsed=extract_event(row(body=body,published=datetime(2026,1,1)))
    assert parsed['event_day']=='2025-12-31'


def test_candidates_do_not_merge_until_two_current_members_confirmed(client,monkeypatch):
    event_id,links=pair()
    url='/api/study/live?day=2026-11-15'
    before=client.get(url).json()
    assert len(BeautifulSoup(before['html'],'html.parser').select('.reading-card'))==2
    assert '同一事件候选' in before['html']
    assert review(client,links[0],'confirmed').status_code==403
    monkeypatch.setenv('SYNC_ADMIN_TOKEN','event-test')
    assert review(client,links[0],'confirmed').status_code==200
    assert len(BeautifulSoup(client.get(url).json()['html'],'html.parser').select('.reading-card'))==2
    assert review(client,links[1],'confirmed').status_code==200
    merged=client.get(url).json()
    soup=BeautifulSoup(merged['html'],'html.parser')
    assert len(soup.select('.reading-card'))==1 and '已确认同一事件' in merged['html']
    assert before['revision']!=merged['revision']
    assert len(soup.select('.source-links a[target=_blank]'))==2
    assert {a['href'] for a in soup.select('.source-links a:not([target])')}=={'/news/1','/news/2'}
    assert '同标题稿件合并' not in soup.select_one('.reading-card .evidence').text
    assert client.get('/api/events?scope=confirmed').json()['total']==1
    assert client.get(f'/api/events/{event_id}').json()['total']==2
    assert review(client,links[0],'rejected','confirmed').status_code==200
    assert len(BeautifulSoup(client.get(url).json()['html'],'html.parser').select('.reading-card'))==2
    assert rebuild_events()['added']==0
    with SessionLocal() as db:assert db.get(EventArticle,links[0]).status=='rejected'
    assert client.get('/api/events?scope=rejected').json()['total']==1
    assert review(client,links[0],'candidate','rejected').status_code==200
    assert client.get('/api/events?scope=confirmed').json()['total']==0


def test_changed_source_invalidates_old_confirmation_keeps_snapshot_and_new_candidate(client,monkeypatch):
    event_id,links=pair();monkeypatch.setenv('SYNC_ADMIN_TOKEN','event-test')
    for link in links:review(client,link,'confirmed')
    old=client.get(f'/api/events/{event_id}?history=true').json()['items'][0]
    with SessionLocal() as db:
        news=db.get(News,1);news.content+='来源更正后的补充文字。';db.flush();link_event(db,news);db.commit()
    assert review(client,links[0],'rejected','confirmed').status_code==409
    data=client.get(f'/api/events/{event_id}?history=true').json()
    retained=next(row for row in data['items'] if row['id']==old['id'])
    assert retained['snapshot']==old['snapshot'] and not retained['is_current']
    latest=client.get(f'/api/events/{event_id}').json()['items']
    assert len(latest)==2 and sum(row['status']=='confirmed' for row in latest)==1
    with SessionLocal() as db:assert confirmed_event_map(db)=={}


def test_review_race_noop_and_failure_rollback(client,monkeypatch):
    event_id,links=pair();monkeypatch.setenv('SYNC_ADMIN_TOKEN','event-test')
    assert review(client,links[0],'confirmed').status_code==200
    assert review(client,links[0],'rejected').status_code==409
    assert not review(client,links[0],'confirmed','confirmed').json()['changed']
    def fail(self):raise RuntimeError('injected review failure')
    with monkeypatch.context() as patch:
        patch.setattr(Session,'commit',fail)
        with pytest.raises(RuntimeError):review(client,links[1],'confirmed')
    with SessionLocal() as db:
        assert db.get(EventArticle,links[1]).status=='candidate' and db.query(EventReview).count()==1
    assert client.get(f'/api/events/{event_id}/reviews',headers={'X-Sync-Token':'event-test'}).json()['total']==1


def test_event_reads_do_not_write_and_filter_pagination_remain_correct(client):
    event_id,_=pair()
    with SessionLocal() as db:before=db.query(EventArticle).count()
    assert client.get('/api/events?page=2').json()['items']==[]
    assert client.get('/api/events?page=0').status_code==422
    assert client.get('/api/events/not-a-real-id').status_code==404
    assert client.get('/events?event_id='+event_id).status_code==200
    assert client.get('/api/events?scope=all').json()['total']==1
    with SessionLocal() as db:assert db.query(EventArticle).count()==before and db.query(EventReview).count()==0


def test_news_save_updates_title_and_rolls_back_if_event_link_fails(monkeypatch):
    original=row()
    values={key:getattr(original,key) for key in ('title','summary','content','source','category','link','published','published_at','year','month')}
    assert save_news_to_db([values])==1
    with SessionLocal() as db:assert db.query(EventArticle).count()==1
    changed={**values,'title':'更正标题：'+MEETING}
    assert save_news_to_db([changed])==0
    with SessionLocal() as db:assert db.query(News).one().title==changed['title']
    def fail(*args):raise RuntimeError('injected event link error')
    monkeypatch.setattr('app.events.link_event',fail)
    with pytest.raises(RuntimeError):save_news_to_db([{**changed,'title':'不得落盘的更正标题'}])
    with SessionLocal() as db:assert db.query(News).one().title==changed['title']
