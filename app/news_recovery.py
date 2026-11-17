"""Small, rotating original-body recovery batches; no synthetic completion."""
from datetime import datetime,timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo
import json,re
from bs4 import BeautifulSoup
from app.database import SessionLocal
from app.models import News,AppState
from app.news_quality import capture_records,capture_status
from app.editorial import select_document
from app.fetch_news import _is_allowed_source_link,save_news_to_db
from app.official_sources import fetch,website_body


def run(limit: int=6) -> dict:
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with SessionLocal() as db:
        captures=capture_records(db)
        state=db.get(AppState,'legacy_news_recovery')
        try:history=json.loads(state.value).get('attempts',{}) if state else {}
        except ValueError:history={}
        rows=db.query(News).filter(News.published_at>=datetime.combine(today-timedelta(days=89),datetime.min.time())).order_by(News.published_at.desc(),News.id.desc()).all()
        candidates=[r for r in rows if r.published_at.date()<=today and capture_status(r,captures)['status']!='extracted' and select_document(r).selected and _is_allowed_source_link(r.source,r.link)]
        candidates.sort(key=lambda r:history.get(str(r.id),{}).get('at',''))
        pending=[{k:getattr(r,k) for k in ['id','title','source','category','link','summary','content','published','published_at']} for r in candidates[:max(1,min(limit,20))]]
    results=[]
    for item in pending:
        outcome={'id':item['id'],'at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'state':'not_updated'}
        try:
            raw=fetch(item['link']);soup=BeautifulSoup(raw,'html.parser')
            norm=lambda s:re.sub(r'\W+','',unescape(s))
            if not any(norm(item['title']) in norm(n.get_text()) for n in soup.select('h1,title')):raise ValueError('title_mismatch')
            summary,body,meta=website_body(raw,item['source'])
            if meta['status']!='extracted' or len(body)<max(100,len(item['content'] or '')):raise ValueError('incomplete_or_shorter')
            cache=Path('data/news-recovery');cache.mkdir(parents=True,exist_ok=True)
            (cache/f'{item["id"]}.json').write_text(json.dumps({'old':item['content'],'new':body,'url':item['link']},ensure_ascii=False),encoding='utf-8')
            save_news_to_db([{**item,'summary':summary,'content':body,'body_capture':meta,'_expected_content':item['content']}])
            outcome.update(state='verified',old_characters=len(item['content'] or ''),new_characters=len(body))
        except Exception as exc:outcome['reason']=str(exc)[:120] if isinstance(exc,ValueError) else type(exc).__name__
        history[str(item['id'])]=outcome;results.append(outcome)
    report={'pending_before':len(candidates),'results':results,'attempts':history}
    with SessionLocal() as db:
        state=db.get(AppState,'legacy_news_recovery')
        if state:state.value=json.dumps(report,ensure_ascii=False)
        else:db.add(AppState(key='legacy_news_recovery',value=json.dumps(report,ensure_ascii=False)))
        db.commit()
    return {'pending_before':len(candidates),'results':results}
