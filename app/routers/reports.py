from datetime import date,datetime,timezone
from html import escape
from typing import Any,Literal
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from fastapi import APIRouter,Header,HTTPException,Request
from pydantic import BaseModel,Field
from sqlalchemy import func
from app.config import get_settings
from app.database import SessionLocal
from app.models import PeriodReport
from app.period_reports import build_report
from app.review import require_token
from app.routers.learning import layout

router=APIRouter(tags=['周期回顾'])
Kind=Literal['daily','weekly']
Profile=Literal['general','postgrad','civil']


def draft(kind: str,day: date,profile: str='general') -> dict[str,Any]:
    with SessionLocal() as db:
        try:return build_report(db,kind,day,profile)
        except ValueError as error:raise HTTPException(422,str(error))


@router.get('/api/reports')
def current_report(kind: Kind='daily',day: date | None=None,profile: Profile='general') -> dict[str,Any]:
    return draft(kind,day or datetime.now(ZoneInfo('Asia/Shanghai')).date(),profile)


class SaveReport(BaseModel):
    kind: Kind
    day: date
    profile: Profile='general'
    fingerprint: str=Field(min_length=64,max_length=64)


@router.post('/api/reports/snapshots')
def save_report(change: SaveReport,request: Request,x_sync_token: str | None=Header(None),
                x_requested_with: str | None=Header(None)) -> dict[str,Any]:
    require_token(x_sync_token)
    origin=request.headers.get('origin')
    if x_requested_with!='study-ui' or origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'请从本站回顾页保存')
    with SessionLocal() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        try:data=build_report(db,change.kind,change.day,change.profile)
        except ValueError as error:raise HTTPException(422,str(error))
        if data['fingerprint']!=change.fingerprint:raise HTTPException(409,'资料已更新，请重新查看后保存')
        identity=data['fingerprint']
        if db.get(PeriodReport,identity):return {'id':identity,'created':False}
        db.add(PeriodReport(id=identity,payload=data,created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.commit()
        return {'id':identity,'created':True}


def render_report(data: dict[str,Any], saved: bool=False,changed: bool=False) -> Any:
    base=escape(get_settings().base_path,quote=True)
    kind=data['kind'];day=data['start'];title='每日回顾' if kind=='daily' else '每周回顾'
    profile=data.get('profile','general')
    if profile!='general':title='每日简报' if kind=='daily' else '每周整理'
    current=f'{base}/reports?kind={kind}&day={day}&profile={profile}'
    body=f'<section class="brief-page"><header class="brief-heading"><div class="eyebrow">THE CURRENT AFFAIRS REVIEW</div><h1>{title}</h1>'
    body+=f'<p class="brief-deck">先看重点，再查原文。<span>{data["start"]}'+(f' — {data["end"]}' if kind=='weekly' else '')+f' · {"已保存版本" if saved else "实时整理"}</span></p></header>'
    if changed:body+='<p class="learning-notice">当前资料与这个保存版本不同，原保存内容仍保留。</p>'
    from app.exam_profiles import PROFILES
    profile_options=''.join(f'<option value="{key}"{" selected" if key==profile else ""}>{value}</option>' for key,value in PROFILES.items())
    body+=f'''<form class="report-controls" action="{base}/reports" method="get"><label>备考方向<select name="profile">{profile_options}</select></label><label>回顾范围 <select name="kind"><option value="daily" {"selected" if kind=="daily" else ""}>每日</option><option value="weekly" {"selected" if kind=="weekly" else ""}>每周（周一至周日）</option></select></label><label>日期 <input type="date" name="day" value="{day}" required></label><button>查看回顾</button></form>'''
    if profile!='general':
        body+=f'<p>{escape(data.get("profile_label","备考"))} · {len(data["items"])} 条精选 · 从本期 {data["collected"]} 篇收录中试筛</p>'
    if profile=='general':body+=f'<p>库内收录 {data["collected"]} 篇 · 符合规则 {data["eligible_groups"]} 组 · 本页 {len(data["items"])} 组</p>'
    checked=sum(source.get('capture',{}).get('status')=='extracted' for item in data['items'] for source in item['sources'])
    source_total=sum(len(item['sources']) for item in data['items'])
    body+=f'<details class="report-scope brief-method"><summary>收录、正文与更新情况</summary><p>收录截止 {data["through"]}。后续补采或更正可能改变当前整理稿。本版引用 {source_total} 篇，其中 {checked} 篇有有效正文提取记录；其余正文范围待核对。<a href="{base}/news-quality?period_start={data["start"]}&amp;period_end={data["through"]}">查看本期正文质量 →</a></p></details>'
    body+=f'<details class="report-scope"><summary>选取范围与整理说明</summary><p>{escape(data["note"])}</p></details>'
    if saved:body+=f'<p><a href="{current}">对照当前整理稿 →</a> · 保存编号 {data["fingerprint"][:12]}</p>'
    save_html=''
    if not saved:
        save_html=f'''<details class="report-save"><summary>保存这一版回顾</summary><p>保存后原文摘录和出处固定保留；后续更新不会覆盖。使用本机维护口令，<a href="{base}/curation">首次设置 →</a></p><label>维护口令 <input type="password" data-report-token autocomplete="off"></label><button type="button" data-save-report data-base="{base}" data-kind="{kind}" data-profile="{profile}" data-day="{day}" data-fingerprint="{data['fingerprint']}">保存这一版</button><p data-report-message role="status"></p></details><script src="{base}/static/reports.js" defer></script>'''
    with SessionLocal() as db:
        versions=db.query(PeriodReport).filter(PeriodReport.payload['kind'].as_string()==kind,
                    PeriodReport.payload['start'].as_string()==day,
                    func.coalesce(PeriodReport.payload['profile'].as_string(),'general')==profile).order_by(PeriodReport.created_at.desc()).limit(20).all()
        if versions:
            body+='<details><summary>本期已保存版本（最近20版）</summary>'
            for row in versions:
                body+=f'<p><a href="{base}/reports/saved/{row.id}">{row.created_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")):%m-%d %H:%M} 北京时间 · {row.id[:12]}</a></p>'
            body+='</details>'
    if not data['items']:body+=f'<p class="empty-state">本期尚无符合规则的新闻，不使用其他日期或背诵材料填充。可换日期，或<a href="{base}/status">检查采集状态 →</a></p>'
    if data['items']:
        body+='<nav class="brief-index" aria-label="本期重点目录">'+''.join(f'<a href="#brief-{i}"><span>{i:02d}</span>{escape(item["title"])}</a>' for i,item in enumerate(data['items'],1))+'</nav>'
    for index,item in enumerate(data['items'],1):
        source=item['sources'][0]
        category=source.get('selection',{}).get('label','原文摘录')
        body+=f'<article class="learning-entry period-entry" id="brief-{index}"><header class="brief-item-head"><span class="brief-number">{index:02d}</span><div><p class="brief-meta">{escape(category)} · {escape(source["publisher"])}</p><h2>{escape(item["title"])}</h2></div></header>'
        relevance=item.get('relevance')
        if relevance:
            body+=f'<p class="brief-topic">{escape(relevance["label"])}</p>'
        body+='<div class="brief-points">'
        for point_index,point in enumerate(item['points']):
            if point_index==1:body+=f'<details class="brief-more"><summary>再看 {len(item["points"])-1} 条原文要点</summary>'
            e=point['evidence']
            body+=f'<section class="brief-point"><p class="report-label">{escape(point["label"])} · 原文摘录</p><blockquote>{escape(e["quote"])}</blockquote><p class="brief-citation"><small>{escape(e["publisher"])} · 刊载 {e["published_at"][:10]} · <a href="{base}/news/{e["news_id"]}">阅读当前原文 →</a></small></p></section>'
            if point['previous']:body+=f'<details><summary>近似表述对照，不能据此认定更正</summary><blockquote>{escape(point["previous"]["quote"])}</blockquote></details>'
        if len(item['points'])>1:body+='</details>'
        body+='</div><footer class="brief-item-footer">'
        body+='<details class="brief-reason"><summary>为什么选这条</summary>'+f'<p>{escape(item["reasons"][0])}</p>'
        if relevance:
            body+=f'<p>{escape(relevance["reason"])}</p><p>阅读时关注：'+ '、'.join(escape(text) for text in relevance['reading_focus'])+'</p>'
            body+='<details><summary>为什么匹配这个方向</summary>'+''.join(f'<blockquote>{escape(e["quote"])}</blockquote>' for e in relevance['evidence'])+'</details>'
        body+='</details>'
        if not item['points']:body+='<p>可摘录文字不足，请直接查看下方来源。</p>'
        body+='<details><summary>本版保存的已采集文字与出处</summary>'
        for source in item['sources']:
            snap=source['snapshot']
            quality=source.get('capture') or {'label':'旧版保存时未核实正文范围'}
            original = f'<a href="{escape(snap["url"],quote=True)}" target="_blank" rel="noopener noreferrer">原站出处 ↗</a>' if urlparse(snap['url']).scheme in {'http','https'} else ''
            body+=f'<details><summary>{escape(source["publisher"])} · {snap["published_at"][:10]} · {escape(snap["title"])}</summary><p class="report-scope">{escape(quality["label"])}</p>{original}<p class="report-original">{escape(snap["text"])}</p></details>'
        body+='</details></footer></article>'
    return layout(title,body+save_html+'</section>',active='archive')


@router.get('/reports')
def reports_page(kind: Kind='daily',day: date | None=None,profile: Profile='general') -> Any:
    return render_report(current_report(kind,day,profile))


@router.get('/reports/saved/{identity}')
def saved_report(identity: str) -> Any:
    with SessionLocal() as db:
        row=db.get(PeriodReport,identity)
        if row is None:raise HTTPException(404,'保存版本不存在')
        data=row.payload
    live=draft(data['kind'],date.fromisoformat(data['start']),data.get('profile','general'))
    return render_report(data,saved=True,changed=live['fingerprint']!=identity)
