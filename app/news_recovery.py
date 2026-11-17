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
            summary,body,meta=website_body(raw,item['source'])
            headings=soup.select('h1,title')
            if item['source']=='qstheory':headings+= [n for n in soup.select('h2') if n.get_text(strip=True).startswith('——')]
            title_matches=any(norm(item['title']) in norm(n.get_text()) for n in headings)
            if item['source']=='qstheory':
                core=re.sub(r'^(?:习近平|《求是》杂志评论员)[:：]\s*','',item['title'])
                title_matches=title_matches or any(norm(core) in norm(n.get_text()) for n in headings)
            old_text=norm(item['content'] or '')
            continuity=bool(headings) and len(old_text)>=200 and old_text in norm(body)
            compact=lambda value:re.sub(r'\s+','',re.sub(r'\s*【编辑[:：][^】]{1,30}】\s*$','',value))
            old_compact=compact(item['content'] or '');new_compact=compact(body)
            footer_cleanup=bool(new_compact) and old_compact.startswith(new_compact) and len(old_compact)-len(new_compact)<=100 and bool(re.search(r'版权所有|ICP备|责任编辑|编辑[:：]|下载文件',old_compact[len(new_compact):]))
            if not title_matches and not continuity and not (len(new_compact)>=200 and footer_cleanup):raise ValueError('title_mismatch')
            # Existing official briefs may genuinely be shorter than 100 characters.
            # Identity + explicit body container + no shrinkage remain mandatory.
            if meta['status']!='extracted' or len(new_compact)<30 or (len(new_compact)<len(old_compact) and not footer_cleanup):raise ValueError('incomplete_or_shorter')
            cache=Path('data/news-recovery');cache.mkdir(parents=True,exist_ok=True)
            (cache/f'{item["id"]}.json').write_text(json.dumps({'old':item['content'],'new':body,'url':item['link']},ensure_ascii=False),encoding='utf-8')
            save_news_to_db([{**item,'summary':summary,'content':body,'body_capture':meta,'_expected_content':item['content']}])
            outcome.update(state='verified',old_characters=len(item['content'] or ''),new_characters=len(body),identity_basis='page_title' if title_matches else 'same_url_existing_text_contained')
        except Exception as exc:outcome['reason']=str(exc)[:120] if isinstance(exc,ValueError) else type(exc).__name__
        history[str(item['id'])]=outcome;results.append(outcome)
    report={'pending_before':len(candidates),'results':results,'attempts':history}
    with SessionLocal() as db:
        state=db.get(AppState,'legacy_news_recovery')
        if state:state.value=json.dumps(report,ensure_ascii=False)
        else:db.add(AppState(key='legacy_news_recovery',value=json.dumps(report,ensure_ascii=False)))
        db.commit()
    return {'pending_before':len(candidates),'results':results}
