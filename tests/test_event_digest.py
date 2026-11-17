from app.event_digest import build_digest

TEXT='国务院发布相关公共服务保障措施，明确建立统一的服务标准并完善实施机制。'


def entry(i,text=TEXT,status='candidate',day='2026-11-15',selected=True,current=True):
    return {'id':i,'news_id':i,'status':status,'is_current':current,'selection':{'selected':selected},
            'publisher':'来源'+str(i),'snapshot':{'title':'报道'+str(i),'text':text,'published_at':day+'T00:00:00'}}


def test_exact_reprints_are_not_extra_points_and_quotes_have_exact_offsets():
    result=build_digest([entry(2),entry(1)])
    assert result['mode']=='candidate_preview'
    assert len(result['points'])==1
    assert result['articles'][1]['state']=='repeated'
    proof=result['points'][0]['evidence']
    assert TEXT[proof['start']:proof['end']]==proof['quote'] and proof['news_id']==1


def test_wire_attribution_and_newspaper_footer_are_not_new_event_information():
    first='新华社深圳11月14日电（记者张三）'+TEXT
    second='中新社 深圳11月14日电 '+TEXT+'\n《 人民日报 》 （2026年11月15日03版）'
    result=build_digest([entry(1,first),entry(2,second)])
    assert len(result['points'])==1
    assert result['articles'][1]['state']=='repeated'
    assert result['points'][0]['evidence']['quote']==first


def test_numbers_negation_and_conditions_are_retained_for_side_by_side_comparison():
    texts=['服务措施面向符合条件的企业，申报期限为30日，必须提供完整材料后方可申请补贴。',
           '服务措施面向符合条件的企业，申报期限为60日，必须提供完整材料后方可申请补贴。',
           '服务措施面向符合条件的企业，申报期限为30日，不必提供完整材料后方可申请补贴。']
    result=build_digest([entry(i+1,text) for i,text in enumerate(texts)])
    differences=[p for p in result['points'] if p['kind']=='changed_wording']
    assert len(differences)==2
    assert '60日' in differences[0]['evidence']['quote']
    assert all(p['previous']['quote']==texts[0] for p in differences)


def test_only_confirmed_current_selected_members_form_confirmed_dossier():
    result=build_digest([entry(1,status='confirmed'),entry(2,status='confirmed'),
                         entry(3,status='rejected'),entry(4,current=False),entry(5,selected=False),entry(6)])
    assert result['mode']=='confirmed'
    assert [r['news_id'] for r in result['articles']]==[1,2]
    assert result['excluded']==4


def test_progress_wording_is_only_a_clue_and_not_inferred_within_same_day():
    progress='该公共服务项目已完成第一阶段建设，新增三项便民服务，后续仍将继续完善具体实施安排。'
    result=build_digest([entry(1),entry(2,progress,day='2026-11-16')])
    assert any(p['kind']=='progress_clue' for p in result['points'])
    same=build_digest([entry(1),entry(2,progress)])
    assert not any(p['kind']=='progress_clue' for p in same['points'])
    planned=build_digest([entry(1),entry(2,'该公共服务项目尚未正式实施，相关单位仍在研究具体条件与时间安排，请以正式公告为准。',day='2026-11-16')])
    assert not any(p['kind']=='progress_clue' for p in planned['points'])


def test_empty_text_limits_and_duplicate_within_one_article_are_not_reprints():
    assert build_digest([entry(1,'太短')])['points']==[]
    result=build_digest([entry(1,TEXT+TEXT)])
    assert len(result['points'])==1 and result['articles'][0]['repeated']==0
    capped=build_digest([entry(i) for i in range(30)])
    assert len(capped['articles'])==24 and capped['excluded']==6


def test_digest_api_is_read_only_and_history_does_not_resurrect_excluded_sources(client,monkeypatch):
    from tests.test_events import pair,review
    from app.database import SessionLocal
    from app.models import EventReview
    event,ids=pair();monkeypatch.setenv('SYNC_ADMIN_TOKEN','event-test')
    data=client.get('/api/events/'+event).json()
    assert data['digest']['mode']=='candidate_preview'
    assert review(client,ids[0],'confirmed').status_code==200
    assert review(client,ids[1],'confirmed').status_code==200
    assert client.get('/api/events/'+event).json()['digest']['mode']=='confirmed'
    assert review(client,ids[1],'rejected','confirmed').status_code==200
    for suffix in ['', '?history=true','?page=2']:
        d=client.get('/api/events/'+event+suffix).json()['digest']
        assert len(d['articles'])==1 and d['mode']=='candidate_preview'
    with SessionLocal() as db: assert db.query(EventReview).count()==3
