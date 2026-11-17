from datetime import datetime
from bs4 import BeautifulSoup
from app.database import SessionLocal
from app.materials import extract_material
from app.models import LearningMaterial
from app.material_readout import readout


def seed():
    rows=[('腿姐考研政治课堂','早鸟带背D5丨18校公布不合格名单!!原因是：'),
          ('人民日报评论','生活是写作的底稿 | 点睛'),
          ('山东发布','山东消费品补贴范围再扩容')]
    body='<p>有关部门公布政策安排，明确服务对象和实施条件，各单位需要依法落实具体措施，并及时公布有关工作进展。</p>'*4
    with SessionLocal() as db:
        for i,(account,title) in enumerate(rows,1):
            db.add(LearningMaterial(source_id=str(i),account=account,title=title,url=f'https://mp.weixin.qq.com/s/{i}',fingerprint=str(i),payload=extract_material(body),imported_at=datetime(2026,11,17)))
        db.commit()


def test_teacher_early_drill_never_enters_civil_or_current_affairs_readout(client):
    seed()
    data=client.get('/api/learning?profile=civil').json()
    assert [x['id'] for x in data['items']]==[3]
    item=client.get('/api/learning/1').json()
    assert readout(item)==[]
    detail=client.get('/learning/1').text
    assert '时政速读' not in detail
    assert 'material-reading-notes' in detail


def test_empty_filter_explains_collection_and_has_working_recovery_links(client):
    seed()
    response=client.get('/api/learning?profile=civil&account=人民日报评论').json()
    assert response['total']==0 and response['collected']==1
    page=BeautifulSoup(client.get('/learning?profile=civil&account=人民日报评论').text,'html.parser')
    assert '文章已收录' in page.select_one('.materials-empty').text
    recovery=page.select_one('.materials-empty a')['href']
    assert '生活是写作的底稿' in client.get(recovery).text
    assert len(page.select('[aria-label="资料方向"] a'))==3
    assert len(page.select('.materials-navigation'))==1


def test_filter_state_counts_and_direction_switch_reset(client):
    seed()
    page=BeautifulSoup(client.get('/learning?profile=civil&purpose=policy&account=山东发布').text,'html.parser')
    assert page.select_one('.materials-source [name=profile]')['value']=='civil'
    assert page.select_one('.materials-source [name=purpose]')['value']=='policy'
    assert page.select_one('option[selected]')['value']=='山东发布'
    switch=page.select_one('.materials-directions a')['href']
    assert 'account=' not in switch and 'purpose=' not in switch
    assert client.get('/learning?page=999').status_code==200


def test_saved_body_is_readable_outside_collapsed_notes(client):
    seed()
    page=BeautifulSoup(client.get('/learning/2').text,'html.parser')
    body=page.select_one('#source-text')
    assert '有关部门公布政策安排' in body.text
    assert body.find_parent('details') is None
    assert page.select_one('.material-reading-notes').select_one('#source-text') is None
    assert '已保存' in body.text and '不保证涵盖原文全部信息' in body.text


def test_source_filter_has_explicit_native_get_action(client):
    seed()
    page=BeautifulSoup(client.get('/learning?profile=civil&purpose=policy').text,'html.parser')
    form=page.select_one('.materials-source')
    assert form['method']=='get' and form['action']=='/learning'
    assert form.select_one('button[type=submit]').text=='应用筛选'
    response=BeautifulSoup(client.get(form['action'],params={'profile':'civil','purpose':'policy','account':'山东发布'}).text,'html.parser')
    assert response.select_one('option[selected]')['value']=='山东发布'
    assert len(response.select('.material-record'))==1
