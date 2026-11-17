from datetime import datetime
import importlib.util
from pathlib import Path
from app.database import SessionLocal
from app.models import LearningMaterial,MaterialRevision
from app.materials import extract_material
from app.revisions import append_revision

spec=importlib.util.spec_from_file_location('recover_pages',Path(__file__).resolve().parents[1]/'scripts/recover_material_pages.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_compatibility_rejects_shorter_or_different_source():
    old={'evidence':[{'text':'保留原文条件和否定限定'}]}
    assert module.compatible(old,{'evidence':[{'text':'前文，保留原文条件和否定限定，后文。'}]})
    assert not module.compatible(old,{'evidence':[{'text':'仅有标题'}]})


def test_recovery_appends_revision_and_keeps_saved_fragment(client,tmp_path):
    text='原文保留公共服务政策的适用范围与实施条件，不应遗漏任何限定和否定。'*6
    fragment='<p>'+text+'</p>'
    with SessionLocal() as db:
        row=LearningMaterial(source_id='recovery',account='肖秀荣教授',title='公共服务政策说明',url='https://mp.weixin.qq.com/s/recovery',fingerprint='old',payload=extract_material(fragment),imported_at=datetime(2026,11,17))
        db.add(row);append_revision(db,row,fragment);db.commit();identity=row.id
    raw='<meta property="og:title" content="公共服务政策说明"><span id="js_name">肖秀荣教授</span><script>var biz = "MzI0MzAxMzQzNA==";</script><div id="js_content">'+fragment+'</div>'
    cache=tmp_path/'cache';cache.mkdir();(cache/'page.html').write_text(raw,encoding='utf-8')
    report=module.run([cache],tmp_path/'report',True)
    assert report['restored']==1
    with SessionLocal() as db:
        versions=db.query(MaterialRevision).filter_by(material_id=identity).order_by(MaterialRevision.revision).all()
        assert len(versions)==2 and versions[0].source_html==fragment
        assert versions[1].source_html==raw
