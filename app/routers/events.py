"""Event review and provenance views. GET requests never create associations."""
from datetime import datetime, timezone
from html import escape
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database import SessionLocal
from app.editorial import decision_map, select_document
from app.events import content_fingerprint, current_members, needs_event_review
from app.event_digest import build_digest
from app.models import News, NewsEvent, EventArticle, EventReview
from app.news_data import source_label
from app.review import require_token
from app.routers.learning import layout

router = APIRouter(tags=['事件脉络'])
Status = Literal['candidate','confirmed','rejected']


@router.get('/api/events')
def list_events(scope: Literal['all','candidate','confirmed','rejected']='all',page: int=Query(1,ge=1)) -> dict[str, Any]:
    with SessionLocal() as db:
        groups: dict[str,list[tuple[EventArticle,News]]] = {}
        for link,row in current_members(db):groups.setdefault(link.event_id,[]).append((link,row))
        rows=[]
        for event in db.query(NewsEvent):
            members=groups.get(event.id,[])
            active=[(link,row) for link,row in members if link.status!='rejected']
            confirmed=sum(link.status=='confirmed' for link,_ in active)
            rejected=sum(link.status=='rejected' for link,_ in members)
            candidate=sum(link.status=='candidate' for link,_ in active)
            if scope=='rejected':
                if not rejected:continue
            elif len(active)<2:
                continue
            elif scope=='confirmed' and confirmed<2 or scope=='candidate' and not candidate:
                continue
            if scope!='rejected' and confirmed<2 and not needs_event_review(active):continue
            rows.append({'id':event.id,'title':event.title,'event_day':event.event_day,
                         'date_scope':event.facts['time_scope'],'phase':event.facts['facts']['phase'],
                         'article_count':len(active),'publisher_count':len({row.source for _,row in active}),
                         'confirmed':confirmed,'candidate':candidate,'rejected':rejected})
        rows.sort(key=lambda row:(row['event_day'],row['id']),reverse=True)
        return {'total':len(rows),'page':page,'items':rows[(page-1)*20:page*20]}


@router.get('/api/events/{event_id}')
def event_detail(event_id: str, history: bool=False,page: int=Query(1,ge=1)) -> dict[str, Any]:
    with SessionLocal() as db:
        event=db.get(NewsEvent,event_id)
        if event is None:raise HTTPException(404,'事件候选不存在')
        overrides=decision_map(db)
        entries=[]
        digest_entries=[]
        for link in db.query(EventArticle).filter_by(event_id=event_id).order_by(EventArticle.id):
            news=db.get(News,link.news_id)
            current=bool(news and link.fingerprint==content_fingerprint(news))
            if current and news is not None:
                digest_entries.append({'id':link.id,'news_id':link.news_id,'status':link.status,'is_current':True,
                                       'snapshot':link.snapshot,'publisher':source_label(link.snapshot['source']),
                                       'selection':select_document(news,overrides).as_dict()})
            if not history and (not current or link.status=='rejected'):continue
            selection=select_document(news,overrides).as_dict() if news else None
            entries.append({'id':link.id,'news_id':link.news_id,'status':link.status,'is_current':current,
                            'snapshot':link.snapshot,'proof':link.proof,'selection':selection,
                            'publisher':source_label(link.snapshot['source'])})
        return {'id':event.id,'title':event.title,'event_day':event.event_day,'facts':event.facts,
                'method':event.method,'total':len(entries),'page':page,'items':entries[(page-1)*20:page*20],
                'digest':build_digest(digest_entries)}


class ReviewChange(BaseModel):
    status: Status
    expected_status: Status
    note: str=Field(default='',max_length=500)


@router.put('/api/event-articles/{identity}')
def review_article(identity: int,change: ReviewChange,request: Request,
                   x_sync_token: str | None=Header(None),x_requested_with: str | None=Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    origin=request.headers.get('origin')
    if x_requested_with!='study-ui' or origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'请从本站事件页面操作')
    with SessionLocal() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        link=db.get(EventArticle,identity)
        if link is None:raise HTTPException(404,'事件关联不存在')
        news=db.get(News,link.news_id)
        if news is None or content_fingerprint(news)!=link.fingerprint:
            raise HTTPException(409,'原文已更正，旧关联不能继续确认，请核对当前稿')
        if link.status!=change.expected_status:raise HTTPException(409,'关联已变化，请重新加载')
        if link.status==change.status:return {'id':identity,'status':link.status,'changed':False}
        db.add(EventReview(article_id=identity,previous=link.status,status=change.status,note=change.note.strip(),
                           created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        link.status=change.status
        db.commit()
        return {'id':identity,'status':change.status,'changed':True}


@router.get('/api/events/{event_id}/reviews')
def event_reviews(event_id: str,page: int=Query(1,ge=1),x_sync_token: str | None=Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    with SessionLocal() as db:
        query=db.query(EventReview).join(EventArticle,EventArticle.id==EventReview.article_id).filter(EventArticle.event_id==event_id)
        return {'total':query.count(),'page':page,'items':[
            {'id':row.id,'article_id':row.article_id,'previous':row.previous,'status':row.status,'note':row.note,
             'created_at':row.created_at.isoformat()+'Z'} for row in query.order_by(EventReview.id.desc()).offset((page-1)*20).limit(20)]}


@router.get('/events')
def events_page(event_id: str | None=None,scope: Literal['all','candidate','confirmed','rejected']='all',
                history: bool=False,page: int=Query(1,ge=1)) -> Any:
    base=escape(get_settings().base_path,quote=True)
    title='对照报道，再确认同一件事。'
    if event_id:
        data=event_detail(event_id,history,page)
        title=data['title']
    return layout('事件脉络',f'''<a href="{base}/today">← 返回时政精选</a><div class="eyebrow">FOLLOW THE STORY</div>
<h1 class="material-title">{escape(title)}</h1>
<p>同一件事，保留不同报道。对照原文依据，确认后合并阅读，也可以随时拆开。</p>
<details class="event-scope"><summary>归并范围与规则</summary>
<p>规则只建立候选，不按同主题或转载数量直接合并。至少两篇当前稿确认后，首页才归并当前日期与筛选范围内的报道。</p>
<p>目前覆盖部分带届次的会议、具名政策与会见/会晤/通话结构，尚非通用语义识别。不同阶段分别保留；事件日期与刊载日期分开，无法确定事件日期时只使用刊载日窗口。</p></details>
<nav class="knowledge-subjects"><a href="{base}/events">全部候选</a><a href="{base}/events?scope=confirmed">已确认可归并</a><a href="{base}/events?scope=rejected">已拆开</a></nav>
<section data-events data-base="{base}" data-event="{escape(event_id or '',quote=True)}" data-scope="{scope}" data-history="{str(history).lower()}" data-page="{page}">
<details><summary>需要确认或拆开时</summary><label>维护口令 <input type="password" data-event-token autocomplete="off" maxlength="128"></label>
<p>使用分类调整的同一本机口令，仅读取不需要填写；口令不保存到浏览器。<a href="{base}/curation">首次设置 →</a></p></details>
<p data-event-message role="status" aria-live="polite"></p><p data-event-summary></p><div data-event-list></div>
<p><button type="button" data-event-prev>上一页</button> <span data-event-page></span> <button type="button" data-event-next>下一页</button></p>
<details data-event-history hidden><summary>最近 20 条维护记录</summary><div data-event-reviews></div></details></section>
<script src="{base}/static/events.js" defer></script>''',active='today')
