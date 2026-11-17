from datetime import date,datetime,timedelta
from html import escape
from typing import Any,Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Query,HTTPException
from app.database import SessionLocal
from app.models import News
from app.editorial import decision_map,select_document
from app.news_quality import capture_records,capture_status
from app.news_data import source_label
from app.config import get_settings
from app.routers.learning import layout

router=APIRouter(tags=['正文质量'])


@router.get('/api/news-quality')
def inventory(days: int=Query(7,ge=0,le=30),scope: Literal['all','featured']='featured',
              status: Literal['all','unverified','extracted','attention']='all',
              source: str=Query('',max_length=80),page: int=Query(1,ge=1),
              period_start: date | None=None,period_end: date | None=None) -> dict[str,Any]:
    if days not in {0,7,30}:raise HTTPException(422,'时间范围只支持7天、30天或全部')
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if bool(period_start)!=bool(period_end) or period_start and period_end and (period_start>period_end or period_end>today):
        raise HTTPException(422,'请提供有效且不含未来日期的完整范围')
    with SessionLocal() as db:
        query=db.query(News).filter(News.published_at<datetime.combine(today+timedelta(days=1),datetime.min.time()))
        if period_start and period_end:
            query=query.filter(News.published_at>=datetime.combine(period_start,datetime.min.time()),
                               News.published_at<datetime.combine(period_end+timedelta(days=1),datetime.min.time()))
        elif days:query=query.filter(News.published_at>=datetime.combine(today-timedelta(days=days-1),datetime.min.time()))
        records=capture_records(db);decisions=decision_map(db)
        rows=[];sources:dict[str,int]={}
        for row in query.order_by(News.published_at.desc(),News.id.desc()):
            selection=select_document(row,decisions)
            if scope=='featured' and not selection.selected:continue
            sources[row.source]=sources.get(row.source,0)+1
            if source and source!=row.source:continue
            capture=capture_status(row,records)
            rows.append({'id':row.id,'title':row.title,'source':source_label(row.source),'published':row.published_at.date().isoformat(),
                         'selected':selection.selected,**capture})
        counts={'total':len(rows),'extracted':sum(r['status']=='extracted' for r in rows),
                'unverified':sum(r['status']=='unverified' for r in rows),
                'attention':sum(r['status'] not in {'unverified','extracted'} or bool(r['last_attempt']) for r in rows)}
        filtered=[r for r in rows if status=='all' or status=='attention' and (r['status'] not in {'unverified','extracted'} or r['last_attempt']) or r['status']==status]
        return {'counts':counts,'total':len(filtered),'page':page,'today':today.isoformat(),
                'sources':[{'key':key,'label':source_label(key),'count':count} for key,count in sorted(sources.items())],
                'items':filtered[(page-1)*20:page*20]}


@router.get('/news-quality')
def quality_page(days: int=Query(7,ge=0,le=30),scope: Literal['all','featured']='featured',
                 status: Literal['all','unverified','extracted','attention']='all',source: str=Query('',max_length=80),page: int=Query(1,ge=1),
                 period_start: date | None=None,period_end: date | None=None) -> Any:
    from urllib.parse import urlencode
    data=inventory(days,scope,status,source,page,period_start,period_end)
    base=escape(get_settings().base_path,quote=True)
    def options(values: dict[Any,str],current: Any) -> str:
        return ''.join(f'<option value="{escape(str(key),quote=True)}"{" selected" if key==current else ""}>{escape(label)}</option>' for key,label in values.items())
    sources={'':'全部来源',**{r['key']:r['label']+' ('+str(r['count'])+')' for r in data['sources']}}
    if source and source not in sources:sources[source]=source_label(source)
    body=f'<a href="{base}/reports">← 回到回顾稿</a><div class="eyebrow">SOURCE CHECK</div><h1>正文质量</h1><p>先核对正文，再整理重点。这里检查库内提取状态，不会因打开页面自动抓取。</p>'
    time_controls=(f'<label>起始日期<input type="date" name="period_start" value="{period_start}" required></label><label>结束日期<input type="date" name="period_end" value="{period_end}" required></label>'
                   if period_start else f'<label>时间范围<select name="days">{options({7:"最近7天",30:"最近30天",0:"全部历史"},days)}</select></label>')
    body+=f'''<form class="report-controls" action="{base}/news-quality">{time_controls}<label>内容范围<select name="scope">{options({'featured':'符合时政筛选','all':'全部收录'},scope)}</select></label><label>状态<select name="status">{options({'all':'全部状态','unverified':'待核对','extracted':'已提取正文','attention':'需关注'},status)}</select></label><label>来源<select name="source">{options(sources,source)}</select></label><button>查看</button><a href="{base}/news-quality">重置</a></form>'''
    c=data['counts'];body+=f'<p>当前范围 {c["total"]} 篇 · 已提取 {c["extracted"]} · 待核对 {c["unverified"]} · 需关注 {c["attention"]}</p>'
    body+='<p class="report-scope">“已提取”表示识别到正文容器且未主动截断，不保证图片、附件、动态加载或所有分页已覆盖；待核对不等于正文一定缺失。需关注也包含保留原正文但最近重取失败的情况，与已提取可重叠。</p>'
    for row in data['items']:
        body+=f'<article class="learning-entry"><small>{escape(row["source"])} · {row["published"]} · {escape(row["label"])}</small><h2><a href="{base}/news/{row["id"]}">{escape(row["title"])}</a></h2><p>{row["characters"]} 字'+(f' · {row["paragraphs"]} 段' if row['paragraphs'] is not None else '')+(' · 最近重取未成功，旧正文保留' if row['last_attempt'] else '')+'</p></article>'
    if not data['items']:body+='<p class="empty-state">当前条件没有文章，可切换范围或重置筛选。</p>'
    for number,label in [(page-1,'上一页'),(page+1,'下一页')]:
        if number<1 or (number>page and page*20>=data['total']):continue
        params={'days':str(days),'scope':scope,'status':status,'source':source,'page':str(number)}
        if period_start and period_end:params.update(period_start=period_start.isoformat(),period_end=period_end.isoformat())
        query=urlencode(params)
        body+=f'<a href="{base}/news-quality?{escape(query,quote=True)}">{label} →</a> '
    return layout('正文质量',body,active='archive')
