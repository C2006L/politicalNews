"""Recover cached full pages without overwriting immutable material revisions."""
import argparse,json,re,sys,html,gc
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from app.database import SessionLocal
from app.models import LearningMaterial,MaterialRevision
from app.material_quality import latest_quality
from app.material_sources import sources
from app.materials import extract_material
from app.link_import import import_cached_link


def compatible(old: dict,new: dict) -> bool:
    norm=lambda s:re.sub(r'\s+','',s)
    previous=norm(''.join(x['text'] for x in old.get('evidence',[])))
    current=norm(''.join(x['text'] for x in new.get('evidence',[])))
    return bool(previous) and len(current)>=len(previous) and previous in current


def run(directories: list[Path],output: Path,apply: bool=False) -> dict:
    output.mkdir(parents=True,exist_ok=True)
    with SessionLocal() as db:
        pending=[{'id':m.id,'url':m.url,'account':m.account,'title':m.title,'payload':m.payload} for m in db.query(LearningMaterial) if latest_quality(db,m)['status']=='unverified']
        # Only prior versions of pending articles are relevant, never the whole archive.
        identities=[m['id'] for m in pending]
        raw_pages=[r.source_html for r in db.query(MaterialRevision).filter(MaterialRevision.material_id.in_(identities)) if r.source_html and 'js_content' in r.source_html]
    cache_files=[]
    for directory in directories:
        if not directory.is_dir():raise ValueError('Cache directory missing')
        cache_files.extend(p for p in directory.glob('*.html') if p.stat().st_size<8_000_000)
    targets={(m['account'],m['title']):m for m in pending};found={}
    source_keys={s['name']:s['key'] for s in sources() if s['enabled']}
    report={'pending_before':len(pending),'candidates':[],'restored':0,'apply':apply}
    from itertools import chain
    titles={m['title'] for m in pending}
    for raw in chain(raw_pages,(p.read_text(encoding='utf-8') for p in cache_files)):
        if 'js_content' not in raw:continue
        title_match=re.search(r'<meta\s+property=[\x22\x27]og:title[\x22\x27]\s+content=[\x22\x27]([^\x22\x27]+)',raw)
        if title_match and html.unescape(title_match[1]) not in titles:continue
        soup=BeautifulSoup(raw,'html.parser');name=soup.select_one('#js_name');title=soup.select_one('meta[property="og:title"]')
        if not name or not title:continue
        target=targets.get((name.get_text(' ',strip=True),str(title.get('content',''))))
        if not target or target['id'] in found:continue
        try:
            if not compatible(target['payload'],extract_material(raw)):continue
            found[target['id']]=True
            row={'id':target['id'],'state':'ready'}
            if apply:
                answer=import_cached_link(source_keys[target['account']],target['url'],raw)
                row['state']=str(answer['state']);report['restored']+=answer['state']=='imported'
            report['candidates'].append(row)
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            gc.collect()
        except (ValueError,KeyError) as exc:report['candidates'].append({'id':target['id'],'state':'rejected','reason':type(exc).__name__})
    report['not_recovered']=len(pending)-report['restored']
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache-dir',type=Path,action='append',default=[])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args();print(json.dumps(run(args.cache_dir,args.output,args.apply),ensure_ascii=True))
