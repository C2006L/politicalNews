"""Bounded public-directory collection. Reports coverage against known directories only."""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import SessionLocal
from app.models import LearningMaterial, MaterialRevision
from app.material_sources import sources
from app.link_import import import_cached_link
from app.public_albums import article_identity, parse_directory, linked_articles
from app.material_dates import page_date,recent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-articles',type=int,default=120)
    parser.add_argument('--scope',choices=['recent-affairs','learning','all'],default='recent-affairs')
    parser.add_argument('--source',action='append',help='Only collect this configured source key; repeatable')
    parser.add_argument('--linked-only',action='store_true',help='Collect same-account links in saved bodies without rescanning albums')
    args=parser.parse_args()
    if not 1<=args.max_articles<=300:raise ValueError('Batch limit must be 1..300')
    root=args.output;root.mkdir(parents=True,exist_ok=True)
    backup=root/'before.db'
    if not backup.exists():
        with closing(sqlite3.connect('test.db')) as src,closing(sqlite3.connect(backup)) as target:src.backup(target)
    with SessionLocal() as db:
        known=set();seeds={};references={}
        account_biz={s['name']:base64.b64encode(s['mp_id'].removeprefix('MP_WXS_').encode()).decode() for s in sources() if s['enabled'] and s['mp_id']}
        for row in db.query(LearningMaterial):
            try:known.add(article_identity(row.url))
            except ValueError:pass
        for revision in db.query(MaterialRevision).filter(MaterialRevision.source_html.is_not(None)):
            raw=revision.source_html or '';account=revision.snapshot['account']
            if account in account_biz:
                for item in linked_articles(raw,account_biz[account]):
                    references.setdefault(account,{})[item['identity']]=item
            seeds.setdefault(account,set()).update(re.findall(r'album_id[^0-9]{0,15}([0-9]{8,})',raw))
            # Short and long permalinks are deduplicated through independently read page metadata.
            biz=re.search(r"var\s+biz\s*=\s*['\"]([^'\"]+)['\"]",raw)
            mid=re.search(r"var\s+(?:mid|appmsgid)\s*=\s*['\"](\d+)['\"]",raw)
            idx=re.search(r"var\s+idx\s*=\s*['\"](\d+)['\"]",raw)
            if biz and mid and idx:known.add(':'.join([biz[1],mid[1],idx[1]]))
    seeds.setdefault('肖秀荣教授',set()).add('4730207453035102215')
    seeds.setdefault('考研政治徐涛',set()).add('4740233675123130372')
    attempted=0;report={'at':datetime.now(timezone.utc).isoformat(),'scope':args.scope+'; known public directories, not whole-account history','sources':[]}
    def fetch(url):
        request=Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36','X-Requested-With':'XMLHttpRequest'})
        with urlopen(request,timeout=20) as response:data=response.read(8*1024*1024+1)
        if len(data)>8*1024*1024:raise ValueError('Response too large')
        return data.decode('utf-8')
    for source in sources():
        if args.source and source['key'] not in args.source:continue
        result={'key':source['key'],'name':source['name'],'directories':[],'discovered':0,'existing':0,'imported':0,'failed':[],'blocked':False,'remaining':0}
        result.update(scope=args.scope,deferred_learning=0,outside_window=0,deferred_non_affairs=0,date_unknown=0)
        candidates=dict(references.get(source['name'],{}));result['linked_discovered']=len(candidates)
        biz=base64.b64encode(source['mp_id'].removeprefix('MP_WXS_').encode()).decode()
        for album in ([] if args.linked_only else sorted(seeds.get(source['name'],set()))[:4]):
            directory={'album':album,'declared':None,'discovered':0,'pages':0,'end':'page_limit'};seen=set();cursor=None
            for page in range(12):
                params={'action':'getalbum','__biz':biz,'album_id':album,'count':100}
                if cursor:params.update(begin_msgid=cursor[0],begin_itemidx=cursor[1])
                try:
                    raw=fetch('https://mp.weixin.qq.com/mp/appmsgalbum?'+urlencode(params));(root/f'{source["key"]}-{album}-{page}.html').write_text(raw,encoding='utf-8')
                    data=parse_directory(raw);directory['pages']+=1
                    if data['declared'] is not None:directory['declared']=data['declared']
                    fresh=[x for x in data['items'] if x['identity'] not in seen]
                    for item in fresh:
                        if not item['identity'].startswith(biz+':'):continue
                        seen.add(item['identity']);candidates[item['identity']]=item
                    if directory['declared'] is not None and len(seen)>=directory['declared']:directory['end']='declared_directory_covered';break
                    if not fresh:directory['end']='repeated_or_empty_page';break
                    if data['more'] is False:directory['end']='directory_end';break
                    last=data['items'][-1];new_cursor=(last['mid'],last['idx'])
                    if cursor==new_cursor:directory['end']='repeated_cursor';break
                    cursor=new_cursor
                except Exception as error:directory['end']=type(error).__name__;break
                finally:time.sleep(1)
            directory['discovered']=len(seen);result['directories'].append(directory)
        result['discovered']=len(candidates)
        (root/(source['key']+'-manifest.json')).write_text(json.dumps(candidates,ensure_ascii=False,indent=2),encoding='utf-8')
        eligible={}
        for identity,item in candidates.items():
            learning=bool(re.search(r'带背|背诵|考点串讲',item['title']))
            if args.scope=='recent-affairs' and learning:result['deferred_learning']+=1;continue
            if args.scope=='learning' and not learning:continue
            if args.scope=='recent-affairs' and re.search(r'颈椎|腰椎|养生|写作的底稿|\|\s*点睛|申论材料|(?:国考|省考).{0,20}答案',item['title']):
                result['deferred_non_affairs']+=1;continue
            day=page_date('var ct = "'+item['create_time']+'";')
            if args.scope=='recent-affairs' and day and not recent(day):result['outside_window']+=1;continue
            eligible[identity]=item
        result['scope_candidates']=len(eligible)
        outside_before_import=result['outside_window']
        for identity,item in eligible.items():
            url=item['url']
            directory_day=page_date('var ct = "'+item['create_time']+'";')
            if identity in known and (args.scope!='recent-affairs' or directory_day):result['existing']+=1;continue
            if attempted>=args.max_articles:result['remaining']+=1;continue
            attempted+=1;cache=root/(hashlib.sha256(url.encode()).hexdigest()+'.html')
            try:
                raw=cache.read_text(encoding='utf-8') if cache.exists() else fetch(url)
                cache.write_text(raw,encoding='utf-8')
                if 'id="js_content"' not in raw and re.search('环境异常|去验证|完成验证',raw):
                    result['blocked']=True;result['failed'].append({'url':url,'reason':'verification_required'});break
                if args.scope=='recent-affairs':
                    day=page_date(raw)
                    if not day:result['date_unknown']+=1;continue
                    if not recent(day):result['outside_window']+=1;continue
                answer=import_cached_link(source['key'],url,raw)
                result['imported' if answer['state']!='unchanged' else 'existing']+=1;known.add(identity)
            except Exception as error:result['failed'].append({'url':url,'reason':type(error).__name__})
            finally:time.sleep(1)
        result['remaining']=max(result['remaining'],len(eligible)-result['existing']-result['imported']-len(result['failed'])-(result['outside_window']-outside_before_import))
        report['sources'].append(result);(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
