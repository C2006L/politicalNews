"""Bounded official website supplements; never counted as WeChat subscriptions."""
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import hashlib,json,re,time
from bs4 import BeautifulSoup
from typing import Any,cast
from app.fetch_news import save_news_to_db
from app.news_body import extract_body

SOURCES={
 'banyuetan_web':('半月谈官网', 'www.banyuetan.org', ['http://www.banyuetan.org/byt/banyuetanpinglun/index.html','http://www.banyuetan.org/byt/jicengzhili/index.html','http://www.banyuetan.org/byt/shizhengjiangjie/index.html']),
 'people_opinion_web':('人民网观点·官网','opinion.people.com.cn',['http://opinion.people.com.cn/']),
}


def links(raw: str, base: str, host: str) -> list[dict[str,str]]:
    found={}
    for a in BeautifulSoup(raw,'html.parser').select('a[href]'):
        url=urljoin(base,str(a['href']));p=urlsplit(url);title=a.get_text(' ',strip=True)
        if p.scheme not in {'http','https'} or p.hostname!=host or p.username or p.password:continue
        if len(title)<8 or not re.search(r'/detail/\d{8}/|/n1/\d{4}/\d{4}/',p.path):continue
        found[url]={'url':url,'title':title}
    return list(found.values())


def publication(raw: str) -> datetime | None:
    soup=BeautifulSoup(raw,'html.parser')
    values=[str(m.get('content','')) for m in soup.select('meta') if re.search('publish|pubdate|date',str(m.get('name',''))+' '+str(m.get('property','')),re.I)]
    values += [n.get_text(' ',strip=True) for n in soup.select('.origin,.time,.pubtime,.publish-time,.source,.article-info,.people_list')]
    for value in values:
        match=re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})',value)
        if match:
            try:return datetime(int(match[1]),int(match[2]),int(match[3]))
            except ValueError:pass
    return None


def fetch(url: str) -> str:
    class SameHostRedirect(HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
            if urlsplit(newurl).hostname!=urlsplit(url).hostname:raise ValueError('offsite_redirect')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    with build_opener(SameHostRedirect()).open(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=15) as r:
        raw=r.read(6_000_001)
        if len(raw)>6_000_000:raise ValueError('response_too_large')
        charset=r.headers.get_content_charset() or 'utf-8'
        return cast(str,raw.decode(charset,errors='replace'))


def collect(output: Path | None=None, limit: int=12) -> dict[str,Any]:
    from zoneinfo import ZoneInfo
    from app.database import SessionLocal
    from app.models import News,AppState
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    report:dict[str,Any]={'day':str(today),'scope':'公开官网补充，非公众号历史覆盖','sources':[]}
    if output:output.mkdir(parents=True,exist_ok=True)
    for key,(label,host,urls) in SOURCES.items():
        row:dict[str,Any]={'source':key,'label':label,'discovered':0,'saved':0,'existing':0,'outside_window':0,'date_unknown':0,'failed':[]}
        candidates:dict[str,dict[str,str]]={}
        for url in urls:
            try:
                raw=fetch(url)
                if output:(output/(hashlib.sha256(url.encode()).hexdigest()+'.html')).write_text(raw,encoding='utf-8')
                candidates.update({x['url']:x for x in links(raw,url,host)})
            except Exception as e:row['failed'].append({'url':url,'reason':type(e).__name__})
        row['discovered']=len(candidates)
        for item in list(candidates.values())[:max(1,min(limit,30))]:
            url=item['url']
            with SessionLocal() as db:exists=db.query(News.id).filter_by(link=url).first()
            if exists:row['existing']+=1;continue
            try:
                raw=fetch(url)
                if output:(output/(hashlib.sha256(url.encode()).hexdigest()+'.html')).write_text(raw,encoding='utf-8')
                day=publication(raw)
                if day is None:row['date_unknown']+=1;continue
                if not today-timedelta(days=89)<=day.date()<=today:row['outside_window']+=1;continue
                summary,body,capture=extract_body(raw)
                if capture['status']!='extracted' or len(body)<100:
                    row['failed'].append({'url':url,'reason':'body_'+capture['status']});continue
                row['saved']+=save_news_to_db([{'source':key,'category':'时政','title':item['title'],'link':url,'published':day.isoformat(),'published_at':day,'summary':summary,'content':body,'body_capture':capture}])
            except Exception as e:row['failed'].append({'url':url,'reason':type(e).__name__})
            finally:time.sleep(1)
        report['sources'].append(row)
        if output:(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with SessionLocal() as db:
        state=db.get(AppState,'official_websites_latest')
        value=json.dumps(report,ensure_ascii=False)
        if state:state.value=value
        else:db.add(AppState(key='official_websites_latest',value=value))
        db.commit()
    return report
