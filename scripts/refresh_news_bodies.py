"""Bounded original-URL recovery. Fetch first, review JSON, then apply cached originals."""
import argparse,json,time,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from app.database import SessionLocal
from app.models import News
from app.fetch_news import _fetch_url,_is_allowed_source_link,save_news_to_db
from app.news_body import extract_body


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--ids',nargs='+',type=int,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--page-title',help='Exact independently checked page title, for one explicit ID only')
    args=parser.parse_args()
    if len(args.ids)>20:raise ValueError('At most 20 explicit IDs per recovery batch')
    if args.page_title and len(args.ids)!=1:raise ValueError('A manually checked title requires one explicit ID')
    args.cache.mkdir(parents=True,exist_ok=True)
    for identity in args.ids:
        with SessionLocal() as db:
            row=db.get(News,identity)
            if row is None or not _is_allowed_source_link(row.source,row.link):raise ValueError('Unknown or disallowed original URL')
            values={k:getattr(row,k) for k in ['source','category','title','link','summary','content','published','published_at','year','month']}
            old=row.content or ''
        try:
            cache=args.cache/(str(identity)+'.json')
            if not args.apply:
                html=_fetch_url(values['link'])
                soup=BeautifulSoup(html,'html.parser')
                headings=[node.get_text(' ',strip=True) for node in soup.find_all(['h1','title'])]
                norm=lambda s:re.sub(r'\W+','',s)
                expected=args.page_title or values['title']
                if not norm(expected) or not any(norm(expected) in norm(h) for h in headings):raise ValueError('Page title mismatch')
                summary,body,meta=extract_body(html)
                if meta['status']!='extracted' or len(body)<len(old):raise ValueError('Incomplete or shorter than existing text')
                payload={'id':identity,'url':values['link'],'title':values['title'],'verified_page_title':expected,
                         'old':old,'summary':summary,'body':body,'capture':meta}
                cache.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
                print(json.dumps({'id':identity,'old':len(old),'new':len(body),'paragraphs':meta['paragraphs'],'state':'ready'},ensure_ascii=False),flush=True)
                time.sleep(1)
            else:
                data=json.loads(cache.read_text(encoding='utf-8'))
                if data['id']!=identity or data['url']!=values['link'] or data['title']!=values['title']:raise ValueError('Original identity changed')
                if old==data['body']:
                    pass  # Still record the successful capture check.
                elif old!=data['old']:raise ValueError('Original changed since fetch; refusing overwrite')
                save_news_to_db([{**values,'summary':data['summary'],'content':data['body'],'body_capture':data['capture'],'_expected_content':old}])
                print(json.dumps({'id':identity,'state':'updated','characters':len(data['body'])}),flush=True)
        except Exception as error:
            print(json.dumps({'id':identity,'state':'not_updated','error':type(error).__name__,'detail':str(error)[:140]},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
