"""Learning material views; external content is always rendered as escaped text."""
from __future__ import annotations

import sqlite3
from html import escape
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func

from app.chrome import header, collection, bookmark
from app.config import get_settings
from app.database import SessionLocal
from app.materials import sync_materials, ImportBusy, ACCOUNTS
from app.material_status import pipeline_status
from app.knowledge import material_points
from app.practice import add_button
from app.review import require_token, password_configured
from app.models import LearningMaterial, MaterialRevision
from app.material_sources import sources, route_material
from app.editorial import decision_map, CATEGORIES
from app.material_quality import inspect, LABELS
from typing import Literal

router = APIRouter(tags=["公众号研读"])


@router.get("/api/learning/status")
def learning_status() -> dict[str, Any]:
    return pipeline_status()


def material_dict(m: LearningMaterial) -> dict[str, Any]:
    with SessionLocal() as db:
        latest = db.query(MaterialRevision).filter_by(material_id=m.id).order_by(MaterialRevision.revision.desc()).first()
        revision = latest.revision if latest else None
        snap: dict[str, Any] = latest.snapshot if latest else {"title": m.title, "account": m.account, "url": m.url,
                                              "payload": m.payload, "imported_at": m.imported_at.isoformat() + "Z"}
        return {"id": m.id, "title": snap["title"], "account": snap["account"], "url": snap["url"],
                "imported_at": snap["imported_at"], **snap["payload"], "quality":inspect(snap['payload'],latest.source_html if latest else None), "revision": revision,
                "version_url": f"{get_settings().base_path}/learning/{m.id}?revision={revision}" if revision else None}


def find_material(material_id: int, revision: int | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        m = db.get(LearningMaterial, material_id)
        if m is None:
            raise HTTPException(404, "学习资料不存在")
        if revision is None:
            return material_dict(m)
        saved = db.query(MaterialRevision).filter_by(material_id=material_id, revision=revision).first()
        if saved is None:
            raise HTTPException(404, "资料版本不存在")
        snap = saved.snapshot
        return {"id": m.id, "title": snap["title"], "account": snap["account"], "url": snap["url"],
                "imported_at": snap["imported_at"], **snap["payload"], "quality":inspect(snap['payload'],saved.source_html), "revision": revision,
                "version_url": f"{get_settings().base_path}/learning/{m.id}?revision={revision}"}


@router.get("/api/learning/{material_id}/revisions")
def material_history(material_id: int) -> dict[str, Any]:
    find_material(material_id)
    with SessionLocal() as db:
        versions = db.query(MaterialRevision).filter_by(material_id=material_id).order_by(MaterialRevision.revision.desc()).all()
        return {"items": [{"revision": v.revision, "title": v.snapshot["title"],
                           "imported_at": v.snapshot["imported_at"], "origin": v.origin}
                          for v in versions]}


@router.get("/api/learning/{material_id}/revisions/{revision}")
def material_version(material_id: int, revision: int) -> dict[str, Any]:
    return find_material(material_id, revision)


@router.get("/api/learning")
def list_materials(account: str | None = None, page: int = Query(1, ge=1),
                   profile: Literal['general','postgrad','civil']='general',
                   purpose: Literal['all','policy','report','analysis','textbook','exam_advice','promotion','uncertain']='all') -> dict[str, Any]:
    from collections import Counter
    with SessionLocal() as db:
        overrides = decision_map(db)
        pool = [(row, route_material(row, overrides)) for row in db.query(LearningMaterial).order_by(LearningMaterial.imported_at.desc(), LearningMaterial.id.desc())]
        directions = {key: sum(key == 'general' or key in routing['directions'] for row, routing in pool) for key in ['general','postgrad','civil']}
        collected = sum(not account or row.account == account for row, routing in pool)
        by_direction = [(row,routing) for row,routing in pool if profile == 'general' or profile in routing['directions']]
        purpose_counts = Counter(routing['purpose'] for row,routing in by_direction if not account or row.account == account)
        candidates = [(row,routing) for row,routing in by_direction if purpose == 'all' or purpose == routing['purpose']]
        source_counts = Counter(row.account for row,routing in candidates)
        source_collected = Counter(row.account for row,routing in pool)
        entries = [(row,routing) for row,routing in candidates if not account or row.account == account]
        pages = max(1, (len(entries)+19)//20)
        page = min(max(1,page),pages)
        return {'total':len(entries),'page':page,'pages':pages,'profile':profile,'purpose':purpose,
                'collected':collected,'direction_counts':directions,'purpose_counts':dict(purpose_counts),
                'source_counts':dict(source_counts),'source_collected':dict(source_collected),
                'usable':sum(bool((row.payload or {}).get('questions') or (row.payload or {}).get('points')) for row,_ in entries),
                'items':[{**material_dict(row),'routing':routing} for row,routing in entries[(page-1)*20:page*20]]}


@router.get("/api/learning/{material_id}")
def get_material(material_id: int) -> dict[str, Any]:
    return find_material(material_id)


@router.post("/api/learning/sync")
def import_materials(x_sync_token: str | None = Header(None)) -> dict[str, int]:
    required = get_settings().sync_admin_token
    if not required and not password_configured():
        raise HTTPException(503, "请先在本机设置管理口令，或配置 SYNC_ADMIN_TOKEN")
    require_token(x_sync_token)
    try:
        return sync_materials()
    except ImportBusy:
        raise HTTPException(409, '已有资料导入任务在运行，请稍候') from None
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "公众号资料源未就绪，请检查本地配置") from exc


def layout(title: str, body: str, active: str = 'learning') -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    return HTMLResponse(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · 时政自习室</title>
<link rel="stylesheet" href="{base}/static/study.css?v=6"><link rel="stylesheet" href="{base}/static/learning.css?v=library5"><link rel="stylesheet" href="{base}/static/material_library.css?v=2"><script src="{base}/static/study.js?v=6" defer></script><script src="{base}/static/material_library.js?v=1" defer></script></head>
<body>{header(get_settings().base_path, active)}
<main class="learning-page" id="top">{body}</main>{collection(get_settings().base_path)}</body></html>''')


@router.get("/learning", response_class=HTMLResponse)
def learning_page(account: str | None = None, page: int = Query(1, ge=1),
                  profile: Literal['general','postgrad','civil']='general',
                  purpose: Literal['all','policy','report','analysis','textbook','exam_advice','promotion','uncertain']='all') -> HTMLResponse:
    data = list_materials(account, page, profile, purpose)
    page = data['page']
    base = escape(get_settings().base_path, quote=True)
    def link(**params: Any) -> str:
        return base+'/learning?'+escape(urlencode({k:v for k,v in params.items() if v is not None}),quote=True)
    labels={'general':'全部收录','postgrad':'考研资料','civil':'公考资料'}
    body='<section class="materials-desk"><aside class="materials-rail" aria-label="公众号资料索引"><div class="materials-imprint"><span>公众号 / LIBRARY</span><strong>'+str(data['direction_counts']['general'])+'</strong><div><span>篇已收录</span><span>原文资料库</span></div></div><p class="rail-label">阅读方向 / INDEX</p><nav class="materials-directions" aria-label="资料方向">'
    for index,(key,label) in enumerate(labels.items(),1):
        body+=f'<a href="{link(profile=key)}" {"aria-current=page" if key==profile else ""}><i aria-hidden="true">{index:02d}</i><strong>{label}</strong><span>{data["direction_counts"][key]} 篇</span></a>'
    body+='</nav><p class="materials-rail-note">时政、评论与带背，分开阅读。<br>保留原文，也保留判断的余地。</p><a class="materials-maintenance" href="'+base+'/material-coverage">收录覆盖与缺口 ↗</a></aside><div class="materials-reading"><header class="materials-heading"><div><div class="eyebrow">THE SOURCE READING ROOM</div><h1>公众号研读</h1><p>循着来源，读懂脉络。</p></div><span class="materials-edition">原文 · 摘录 · 研读</span></header>'
    body+='<section class="materials-navigation" aria-label="资料分类"><nav class="materials-purposes" aria-label="文章用途"><span class="materials-filter-label">内容类型</span>'
    visible={'all':'全部类型',**CATEGORIES}
    for key,label in visible.items():
        if profile=='civil' and key=='textbook':continue
        count=sum(data['purpose_counts'].values()) if key=='all' else data['purpose_counts'].get(key,0)
        if count == 0 and key not in {'all', purpose}:
            continue
        body+=f'<a href="{link(profile=profile,purpose=key,account=account)}" {"aria-current=page" if key==purpose else ""}>{label}<span>{count}</span></a>'
    body+='</nav><form class="materials-source" action="'+base+'/learning" method="get"><input type="hidden" name="profile" value="'+profile+'"><input type="hidden" name="purpose" value="'+purpose+'"><label for="material-account">公众号来源</label><select id="material-account" name="account" aria-describedby="material-filter-hint"><option value="">全部来源</option>'
    names=list(dict.fromkeys([row['name'] for row in sources() if row['enabled']]+list(data['source_collected'])))
    for name in names:
        body+=f'<option value="{escape(name,quote=True)}" {"selected" if account==name else ""}>{escape(name)} · 已收录 {data["source_collected"].get(name,0)} / 匹配 {data["source_counts"].get(name,0)}</option>'
    body+='</select><button type="submit">应用筛选</button><a href="'+link(profile=profile)+'">重置筛选</a><small id="material-filter-hint" class="materials-filter-hint" data-filter-hint>选择来源后，点击应用筛选。</small></form></section>'
    body+=f'<div class="materials-result"><span>{labels[profile]} / {visible[purpose]} / {escape(account or "全部来源")}</span><strong>{data["total"]} 篇匹配 <small>· 已收录 {data["collected"]} 篇</small></strong></div>'
    if not data['items']:
        if data['collected']:
            body+=f'<section class="materials-empty"><h2>文章已收录，当前条件没有匹配</h2><p>已收录 {data["collected"]} 篇。部分可能是文化、生活、带背或待判断内容，未列入当前方向；并非采集数量为零。</p><a href="{link(profile="general",account=account)}">查看这个范围的全部原文 →</a> <a href="{link(profile=profile)}">清除筛选 →</a></section>'
        else:
            body+=f'<section class="materials-empty"><h2>这个范围暂未取得正文</h2><p>请先查看来源覆盖情况，订阅成功不代表历史正文已取得。</p><a href="{base}/material-coverage">检查来源覆盖 →</a></section>'
    body+='<div class="material-list">'
    for index,m in enumerate(data['items'],(page-1)*20+1):
        summary='原文资料 · 阅读已保存的文字正文' if not m['questions'] and not m['points'] else f'{len(m["questions"])} 道原文问答 · {len(m["points"])} 条重点提法'
        if m['routing']['purpose'] in {'policy','analysis','report'}:summary='原文资料 · 保留出处，可展开查看原文摘录'
        if m['routing']['purpose']=='exam_advice':summary='备考方法与提醒 · 不列入时政简报'
        body+=f'<article class="learning-entry material-record"><span class="material-number">{index:02d}</span><div><div class="meta">{escape(m["routing"]["label"])} · {escape(m["account"])} · 来源地域 {escape(m["routing"]["region"])}</div><h2><a href="{base}/learning/{m["id"]}">{escape(m["title"])}</a></h2><p>{escape(summary)}</p><div class="material-actions">'+bookmark('learning:'+str(m['id']),m['title'],get_settings().base_path+'/learning/'+str(m['id']))+f'<a href="{base}/learning/{m["id"]}">阅读原文资料 →</a></div></div></article>'
    body+='</div>'
    if data['pages']>1:
        body+='<nav class="review-filters" aria-label="资料分页">'
        for number in range(max(1,page-2),min(data['pages'],page+2)+1):
            body+=f'<a href="{link(profile=profile,purpose=purpose,account=account,page=number)}" {"aria-current=page" if page==number else ""}>{number}</a>'
        body+='</nav>'
    body+='<details class="materials-help"><summary>学习辅助与收录说明</summary><p>只显示已取得正文的文章，图片和音频可能未完整处理；方向筛选不代表必考预测。</p>'
    tutorial_candidates=[m for m in data['items'] if m.get('questions')][:3]
    example = next(((m, i, q) for m in tutorial_candidates for i, q in enumerate(m.get('questions', []))
                    if q.get('question') and q.get('answer') and q.get('evidence') and m.get('revision')
                    and set(q['evidence']).issubset({line['id'] for line in m.get('evidence', [])})), None)
    if example and profile != 'civil' and purpose in {'all','textbook'}:
        material, index, question = example
        body += f'''<section class="learning-entry"><h2>第一次用？先试一道题</h2>
<p>下面的问题来自 {escape(material['account'])} 的《{escape(material['title'])}》。</p>
<blockquote>{escape(question['question'])}</blockquote>
<ol><li>打开题目，先自己想答案，再点题目展开原文答案。</li>
<li>想留到以后复习，就点题目旁的“加入复习”。</li>
<li>到“我的复习”记下这次有没有想起来，下次按到期时间再看。</li></ol>
<p><a href="{base}/learning/{material['id']}?revision={material['revision']}#question-{index}">打开这道题，试着回想 →</a> · <a href="{base}/practice">我的复习 →</a></p></section>'''
    if profile != 'civil':
        body += f'''<details><summary>“按考点看”是做什么的？</summary>
    <p>把“国有经济的主导作用”“全面依法治国的总抓手”这样的知识点当作目录。点开一个知识点，就能找到已经匹配的老师原文、问答及相关新闻，方便围绕同一问题阅读。</p>
    <p>它不表示这些内容一定会考，也不是两位老师已经互相印证。只有一个来源时，就只显示那一个来源；你也可以直接从下面的文章开始。</p>
    <p><a href="{base}/knowledge">按知识点查找原文 →</a></p></details>
    <p><a href="{base}/knowledge">按考点研读 →</a> · <a href="{base}/practice">我的复习 →</a></p>
    <details><summary>为什么文章少？怎样检查更新？</summary><p>文章少可能是上游尚未采到，不能只靠反复导入补齐。当前采集入口可能只取得最新一篇，历史文章和同次多篇更新仍可能缺失。没有识别到问答，也不代表原文没有价值。</p>
    <p><a href="{base}/learning-import">检查已采集数量、导入与正文恢复 →</a></p></details>'''
    body+='</details><div class="materials-end"><span>本页完 · 阅读按自己的节奏</span><a href="#top">回到页首 ↑</a></div></div></section>'
    return layout('公众号研读',body)


@router.get('/material-quality', response_class=HTMLResponse)
def material_quality_page() -> HTMLResponse:
    from collections import Counter
    with SessionLocal() as db:
        rows=[material_dict(m) for m in db.query(LearningMaterial).order_by(LearningMaterial.id)]
    counts=Counter(m['quality']['label'] for m in rows)
    body='<h1>公众号正文质量</h1><p>检查已保存文字及媒体缺口，不代表已核验原文完整性或事实准确性。</p><p>'+' · '.join(f'{escape(k)} {v} 篇' for k,v in counts.items())+'</p>'
    for m in rows:
        q=m['quality']
        body+=f'<article class="learning-entry"><h2><a href="{escape(get_settings().base_path)}/learning/{m["id"]}">{escape(m["title"])}</a></h2><p>{escape(m["account"])} · {q["label"]} · {q["characters"]} 字 · {q["images"]} 张图片</p></article>'
    return layout('公众号正文质量',body)


@router.get("/learning/{material_id}", response_class=HTMLResponse)
def learning_detail(material_id: int, revision: int | None = Query(None, ge=1)) -> HTMLResponse:
    m = find_material(material_id, revision)
    base = escape(get_settings().base_path, quote=True)
    body = f'<a href="{base}/learning">← 公众号研读</a> · <a href="{base}/practice">我的复习 →</a><p class="eyebrow">{escape(m["account"])}</p><h1 class="material-title">{escape(m["title"])}</h1>'
    related = material_points(material_id, m.get('revision'))
    if related:
        body += '<p>关联考点：' + ' · '.join(
            f'<a href="{base}/knowledge/{p["id"]}">{escape(p["title"])}</a>' for p in related) + '</p>'
    versions = material_history(material_id)["items"]
    if versions:
        latest = max(v['revision'] for v in versions)
        status = '已是最新保存版本' if m['revision'] == latest else f'<a href="{base}/learning/{material_id}">查看最新保存版本 →</a>'
        body += f'<details class="material-version"><summary>当前查看版本 {m["revision"]} · {status}</summary>'
        others = ' / '.join(f'<a href="{base}/learning/{material_id}?revision={v["revision"]}">版本 {v["revision"]}</a>' for v in versions if v["revision"] != m["revision"])
        if others:
            body += f'<p>其他保存版本：{others}</p>'
        body += '<small>版本指本站已保存的内容，不代表公众号刚刚更新。</small></details>'
    body += f'<a href="{escape(m["url"], quote=True)}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>'
    body += bookmark('learning:' + str(m['id']), m['title'], get_settings().base_path + '/learning/' + str(m['id']))
    body+=f'<p class="report-scope">正文状态：{m["quality"]["label"]} · {m["quality"]["characters"]} 字 · <a href="{base}/material-quality">查看正文质量 →</a></p>'
    from app.material_readout import readout
    highlights = readout(m)
    if highlights:
        body += '<section class="learning-entry"><h2>时政速读 · 原文摘录</h2><p>按文章类型提取，保留原句中的条件与限定；作者观点、案例不等于政策事实，未提取的字段请查原文。</p>'
        for point in highlights:
            body += f'<h3>{escape(point["label"])}</h3><blockquote>{escape(point["quote"])}</blockquote><small>原文位置 {escape(point["evidence_id"])}</small>'
        body += '</section>'
    body += '<details class="material-reading-notes"><summary>原文完整度与整理说明</summary><ul>'
    body += ''.join(f'<li>{escape(note)}</li>' for note in m['review_notes']) + '</ul></details>'
    if m['pages']:
        body += '<p>' + escape(' · '.join(m['pages'])) + '</p>'
    if m['points']:
        body += '<h2 id="points">原文重点提法</h2>'
        section = ''
        for index, point in enumerate(m['points']):
            if point['section'] != section:
                section = point['section']
                body += '<h3>' + escape(section) + '</h3>'
            refs = ' '.join(f'<a href="#e-{escape(r)}">{escape(r)}</a>' for r in point['evidence'])
            body += f'<div class="learning-point"><span class="point-sequence" aria-hidden="true">{index + 1:02d}</span><div><p class="knowledge-text">{escape(point["text"])} <small>{refs}</small></p><div class="point-actions">' + add_button(material_id, m['revision'], 'points', index) + '</div></div></div>'
    if m['questions']:
        body += '<h2 id="questions">先回想，再展开答案</h2><p>先自己想一遍，再点题目展开答案。“加入复习”会保留这道题，不会自动把它记为学会。</p>'
        for index, q in enumerate(m['questions']):
            badge = ' · 原作者重点标记' if q['starred_by_author'] else ''
            badge += ' · 待核对' if q['needs_review'] else ''
            refs = ' '.join(f'<a href="#e-{escape(r)}">{escape(r)}</a>' for r in q['evidence'])
            body += f'<details id="question-{index}" class="learning-question"><summary>{q["number"]}. {escape(q["question"])}{badge}</summary><p class="knowledge-text">{escape(q["answer"])}</p><small>原文依据：{refs}</small></details>' + add_button(material_id, m['revision'], 'questions', index)
    cited = {ref for p in m['points'] + m['questions'] for ref in p['evidence']}
    if cited:
        body += '<details class="learning-evidence"><summary>查看上述内容的原文依据</summary>'
        body += ''.join(f'<p id="e-{escape(line["id"])}"><small>{escape(line["id"])}</small> {escape(line["text"])}</p>' for line in m['evidence'] if line['id'] in cited)
        body += '</details>'
    source_text = ''.join(f'<p>{escape(line["text"])}</p>' for line in m['evidence'])
    size = sum(len(line['text']) for line in m['evidence'])
    scope_note = f'<p class="material-text-scope">已保存 {size} 字文字；图片内文字、音频和视频尚未整理，不保证涵盖原文全部信息。<a href="{escape(m["url"], quote=True)}" target="_blank" rel="noopener noreferrer">对照公众号原文 ↗</a></p>'
    if not source_text:
        body += '<section id="source-text"><h2>尚未取得可读正文</h2><p>当前只保存了文章信息，请通过上方链接查看公众号原文。</p></section>'
    elif not m['points'] and not m['questions']:
        body += '<section id="source-text" class="material-source-text"><h2>已保存的文字正文</h2>'+scope_note+source_text+'</section>'
    else:
        body += '<details id="source-text" class="material-source-text"><summary>阅读已保存的文字正文</summary>'+scope_note+source_text+'</details>'
    return layout(m['title'], body)
