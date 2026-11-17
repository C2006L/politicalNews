"""Bounded resumable public-link backfill. No account-history completeness claim."""
import sys,json,re,html,time,hashlib
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlsplit,urlunsplit,urlencode
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import SessionLocal
from app.models import LearningMaterial,MaterialRevision
from app.material_sources import sources
from app.link_import import import_cached_link

def normalized(url):
 p=urlsplit(html.unescape(url));return urlunsplit(('https',p.netloc,p.path,p.query,''))
def run():
 root=Path('D:/politicalNews/preview/backfill-20261117');root.mkdir(exist_ok=True)
 seedroot=Path('D:/politicalNews/preview/new-source-articles')
 with SessionLocal() as db:
  seen={normalized(r.url) for r in db.query(LearningMaterial)}
 report={'scope':'Public album initial directories and article links; not whole-account history','sources':[]}
 for source in sources():
  item={'key':source['key'],'discovered':0,'fetched':0,'imported':0,'existing':0,'failed':0,'identity_rejected':0,'blocked':False,'album_totals':[],'failures':[]}
  candidates=set();seeds=[]
  path=seedroot/(source['key']+'.html')
  if path.exists():seeds.append(path.read_text(encoding='utf-8'))
  with SessionLocal() as db:
   rows=db.query(MaterialRevision).filter(MaterialRevision.snapshot['account'].as_string()==source['name']).order_by(MaterialRevision.id.desc()).limit(3).all()
   seeds += [r.source_html for r in rows if r.source_html]
  def fetch(url):
   req=Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'})
   with urlopen(req,timeout=20) as r:data=r.read(8*1024*1024+1)
   if len(data)>8*1024*1024:raise ValueError('Page too large')
   return data.decode('utf-8')
  for raw in seeds:
   for album in sorted(set(re.findall(r'album_id[^0-9]{0,15}([0-9]{8,})',raw)))[:2]:
    import base64
    biz=base64.b64encode(source['mp_id'].removeprefix('MP_WXS_').encode()).decode()
    url='https://mp.weixin.qq.com/mp/appmsgalbum?'+urlencode({'__biz':biz,'action':'getalbum','album_id':album,'scene':'173'})
    try:
     page=fetch(url);(root/(source['key']+'-album-'+album+'.html')).write_text(page,encoding='utf-8')
     count=re.search(r'article_count:\s*[\x22\x27](\d+)',page)
     if count:item['album_totals'].append(int(count[1]))
     region=page[page.find('articleList:'):]
     for link in re.findall(r'url:\s*[\x22\x27]([^\x22\x27]+)',region):
      if urlsplit(html.unescape(link)).hostname=='mp.weixin.qq.com':candidates.add(normalized(link))
    except Exception:item['failed']+=1
    time.sleep(1)
   for a in BeautifulSoup(raw,'html.parser').select('a[href]'):
    link=html.unescape(a['href']);p=urlsplit(link)
    if p.hostname=='mp.weixin.qq.com' and (p.path=='/s' or p.path.startswith('/s/')):candidates.add(normalized(link))
  item['discovered']=len(candidates)
  (root/(source['key']+'-links.json')).write_text(json.dumps(sorted(candidates),ensure_ascii=False,indent=2),encoding='utf-8')
  for url in sorted(candidates)[:60]:
   if url in seen:item['existing']+=1;continue
   cache=root/(hashlib.sha256(url.encode()).hexdigest()+'.html')
   try:
    raw=cache.read_text(encoding='utf-8') if cache.exists() else fetch(url)
    soup=BeautifulSoup(raw,'html.parser')
    if not soup.select_one('#js_content'):
     item['blocked']=bool(re.search('环境异常|去验证|完成验证',soup.get_text()))
     item['failed']+=1
     item['failures'].append({'url':url,'reason':'verification_required' if item['blocked'] else 'missing_article_body'})
     (root/(source['key']+'-blocked.html')).write_text(raw,encoding='utf-8')
     if item['blocked']:break
     continue
    cache.write_text(raw,encoding='utf-8');item['fetched']+=1
    try:result=import_cached_link(source['key'],url,raw)
    except ValueError:item['identity_rejected']+=1;continue
    if result['state']=='unchanged':item['existing']+=1
    else:item['imported']+=1
    seen.add(url)
   except Exception as error:
    item['failed']+=1;item['failures'].append({'url':url,'reason':type(error).__name__})
   finally:time.sleep(1)
  report['sources'].append(item);(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(item,ensure_ascii=False),flush=True)
if __name__=='__main__':run()