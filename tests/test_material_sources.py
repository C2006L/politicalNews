import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime
from app.database import SessionLocal
from app.materials import extract_material, sync_materials
from app.material_sources import accounts
from app.models import LearningMaterial
from app.upstream import source_key

BODY = '<p>国务院发布公共服务改革方案，明确适用对象、实施日期和具体工作措施，相关部门应及时公开政策落实情况。</p>' * 4


def update(client, key, **fields):
    row = next(r for r in client.get('/api/material-sources').json()['items'] if r['key'] == key)
    change = {k: row[k] for k in ['name', 'mp_id', 'group', 'region', 'enabled']}
    return client.put('/api/material-sources/'+key, json={**change, **fields},
                      headers={'X-Sync-Token': 'sources-test', 'X-Requested-With': 'study-ui'})


def test_pending_accounts_cannot_be_enabled_without_identity_and_auth(client, monkeypatch):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'sources-test')
    entries = client.get('/api/material-sources').json()['items']
    assert len(entries) == 7 and len(accounts()) == 2
    assert sum(not r['mp_id'] for r in entries) == 5
    assert update(client, 'tui', enabled=True).status_code == 422
    assert update(client, 'tui', enabled=True, mp_id='MP_WXS_555555').status_code == 422
    assert client.put('/api/material-sources/tui', json={'name':'x'}).status_code == 422
    payload={k: entries[0][k] for k in ['name','mp_id','group','region','enabled']}
    assert client.put('/api/material-sources/xiao',json=payload).status_code == 403
    assert client.put('/api/material-sources/xiao',json=payload,headers={'X-Sync-Token':'sources-test','X-Requested-With':'study-ui','Origin':'https://evil.example'}).status_code == 403
    assert update(client,'tui',mp_id=entries[0]['mp_id'],identity_checked=True).status_code == 409
    assert client.get('/material-sources').status_code == 200


def test_dynamic_accounts_import_recovery_and_disable_are_read_only(client, monkeypatch, tmp_path):
    monkeypatch.setenv('SYNC_ADMIN_TOKEN', 'sources-test')
    old_key = source_key()
    assert update(client,'banyuetan',mp_id='MP_WXS_555555',enabled=True,identity_checked=True).status_code == 200
    assert source_key() != old_key and len(accounts()) == 3
    path=tmp_path/'source.db'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('create table articles(id,mp_id,title,url,content_html,has_content)')
        for i,identity in enumerate([*accounts(), 'MP_WXS_999999']):
            db.execute('insert into articles values(?,?,?,?,?,1)',(str(i),identity,'国务院发布改革方案',f'https://mp.weixin.qq.com/s/{i}',BODY))
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(path))
    before=path.read_bytes()
    assert sync_materials()['added'] == 3
    from app.body_recovery import source_rows
    assert len(source_rows()) == 3
    assert len(client.get('/api/learning/status').json()['acquisition']['accounts']) == 3
    assert path.read_bytes() == before
    assert update(client,'banyuetan',enabled=False).status_code == 200
    assert len(source_rows()) == 2
    for key in ['xiao','xu']:
        assert update(client,key,enabled=False).status_code == 200
    assert sync_materials()['scanned'] == 0 and source_rows() == []
    assert path.read_bytes() == before


def test_civil_never_contains_teacher_drills_or_promotions_but_shares_current_affairs(client):
    titles=['徐涛带背D28 考研知识回顾','腿姐带背D3 每日练习','考研政治每天1小时够吗','课程开售：限时抢购','国务院发布养老服务方案','时政观察：乡村振兴政策解读']
    with SessionLocal() as db:
        for i,title in enumerate(titles,1):
            db.add(LearningMaterial(source_id=str(i),account='考研政治徐涛',title=title,url=f'https://mp.weixin.qq.com/s/{i}',
                   fingerprint=hashlib.sha256(title.encode()).hexdigest(),payload=extract_material(BODY),imported_at=datetime(2026,11,16)))
        db.commit()
    all_items=client.get('/api/learning').json()
    civil=client.get('/api/learning?profile=civil').json()
    postgrad=client.get('/api/learning?profile=postgrad').json()
    assert all_items['total'] == 6
    assert {r['id'] for r in civil['items']} == {5,6}
    assert {r['id'] for r in postgrad['items']} == {1,2,3,5,6}
    assert client.get('/api/learning?profile=civil&purpose=textbook').json()['total'] == 0
    assert client.get('/api/learning?profile=invalid').status_code == 422
    page=client.get('/learning?profile=civil').text
    assert '徐涛带背D28' not in page and '国务院发布养老服务方案' in page
    assert 'profile=civil' in page and '收录覆盖与缺口' in page


def test_civil_advice_and_local_subsidy_are_not_confused_with_teacher_drills(client):
    from app.material_sources import route_material
    samples = [('半月谈', '同一份申论材料，国考和省考要的答案不一样', 'exam_advice', ['civil']),
               ('山东发布', '山东消费品以旧换新地方自主补贴品类再扩容', 'policy', ['postgrad','civil']),
               ('人民日报评论', '生活是写作的底稿 | 点睛', 'uncertain', []),
               ('腿姐考研政治课堂', '腿姐带背D28丨写给最近很累的你', 'textbook', ['postgrad'])]
    for account,title,purpose,directions in samples:
        row=LearningMaterial(account=account,title=title,url='https://mp.weixin.qq.com/s/sample',payload=extract_material(BODY))
        routing=route_material(row)
        assert routing['purpose']==purpose and routing['directions']==directions
        if purpose=='exam_advice':assert not routing['brief_candidate']
