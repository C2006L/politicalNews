from datetime import datetime
import json
from pathlib import Path
import subprocess

from bs4 import BeautifulSoup
import pytest

from app.database import SessionLocal
from app.materials import extract_material
from app.models import LearningMaterial, MaterialRevision, News
from app.static_export import build, finish_html, static_link, validate


def test_public_edition_keeps_bodies_and_filters_without_shipping_private_data(tmp_path):
    now = datetime(2026, 11, 18, 12)
    raw = '<html><div id="js_content">' + '<p>国务院发布公共服务改革方案，明确实施日期和适用对象，相关部门将按职责落实具体措施。</p>' * 4 + '</div><script>PRIVATE_ORIGINAL</script></html>'
    payload = extract_material(raw)
    with SessionLocal() as db:
        for identity, day in [(1, datetime(2026, 11, 18)), (2, datetime(2025, 1, 1))]:
            db.add(News(id=identity, title='国务院发布公共服务改革方案', source='gov_cn', category='时政',
                        link=f'https://example.org/{identity}', content='正文中的完整句子。<script>alert(1)</script>',
                        summary='摘要不能代替正文', published_at=day, published=day.date().isoformat(), year=day.year, month=day.month))
        material = LearningMaterial(id=1, source_id='private-upstream-id', title='国务院发布公共服务改革方案',
                                    account='半月谈', url='https://mp.weixin.qq.com/s/public', fingerprint='x',
                                    payload=payload, imported_at=now)
        db.add(material); db.flush()
        db.add(MaterialRevision(material_id=1, revision=1, snapshot={'title': material.title, 'account': material.account,
               'url': material.url, 'payload': payload, 'imported_at': now.isoformat()}, source_html=raw, origin='test'))
        db.commit()
        result = build(db, tmp_path / 'site', now=now)
    site = tmp_path / 'site'
    assert (result['news'], result['materials']) == (1, 1)
    assert not (site / 'news/2/index.html').exists()
    detail = (site / 'news/1/index.html').read_text(encoding='utf-8')
    assert '正文中的完整句子' in detail and '<script>alert' not in detail
    learning = (site / 'learning/1/index.html').read_text(encoding='utf-8')
    assert '相关部门将按职责落实具体措施' in learning
    joined = '\n'.join(p.read_text(encoding='utf-8') for p in site.rglob('*.html'))
    assert 'PRIVATE_ORIGINAL' not in joined and 'private-upstream-id' not in joined
    assert '/api/' not in joined and 'data-practice-add' not in joined
    assert not BeautifulSoup(joined, 'html.parser').select('script:not([src])')
    categories = BeautifulSoup((site / 'categories/index.html').read_text(encoding='utf-8'), 'html.parser')
    assert categories.select_one('.mast nav a[aria-current=page]')['href'] == '/categories/'
    assert categories.select_one('[data-topic=policy]')['href'] == '/categories/?topic=policy'
    assert categories.select_one('select[name=topic]')
    assert categories.select_one('.static-total').get_text() == '1'
    assert not categories.select_one('.date-sheet .day')
    data = json.loads((site / 'static/catalog.json').read_text(encoding='utf-8'))
    assert len(data) == 2 and '正文中的完整句子' in data[0]['text']
    validate(site)
    with pytest.raises(ValueError, match='已存在'):
        with SessionLocal() as db:
            build(db, site, now=now)


def test_static_links_drop_backend_operations_and_rewrite_direction_filters():
    pages = {'/news/1/', '/learning/1/'}
    assert static_link('/today?track=civil', pages) == '/today/civil/'
    assert static_link('/learning?profile=civil&account=半月谈&page=2', pages) == '/learning/?profile=civil&source=%E5%8D%8A%E6%9C%88%E8%B0%88'
    assert static_link('/learning/1?revision=1', pages) == '/learning/1/'
    assert static_link('/sync', pages) is None
    assert static_link('//evil.example/x', pages) == '#'
    assert static_link('javascript:alert(1)', pages) == '#'
    html = finish_html('<html><head><script>fetch("/api/private")</script></head><body><a href="/sync">维护</a><a href="/news/1">阅读</a></body></html>', pages, '2026-11-18')
    assert b'/sync' not in html and b'/api/' not in html and b'href="/news/1/"' in html


def test_static_catalog_filters_direction_source_and_full_text(tmp_path):
    node = Path('D:/nodejs/node.exe')
    if not node.exists():
        import shutil
        candidate = shutil.which('node')
        if not candidate:
            pytest.skip('Node is needed for browser filtering logic')
        node = Path(candidate)
    script = Path(__file__).parents[1] / 'app/static/static_edition.js'
    test = '''require(process.argv[1]);
const rows=[{kind:'material',profiles:['postgrad'],purpose:'textbook',source:'tui',title:'带背',text:'理论原句',source_label:'腿姐',date:''},
{kind:'material',profiles:['civil','postgrad'],purpose:'policy',source:'半月谈',title:'权威发布',text:'政策正文中的适用对象',source_label:'半月谈',date:'2026-11-18'}];
const f=StaticEdition.filterRecords;
if(f(rows,{profile:'civil'},'material').length!==1)throw Error('教材混入公考');
if(f(rows,{q:'适用对象',source:'半月谈'},'all')[0].title!=='权威发布')throw Error('正文检索失败');
if(f(rows,{purpose:'textbook',profile:'civil'},'all').length!==0)throw Error('组合筛选失败');
if(f(rows,{},'news').length!==0)throw Error('来源类型隔离失败');
'''
    subprocess.run([str(node), '-e', test, str(script)], check=True)
