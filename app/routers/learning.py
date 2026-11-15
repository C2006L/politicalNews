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
                "imported_at": snap["imported_at"], **snap["payload"], "revision": revision,
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
                "imported_at": snap["imported_at"], **snap["payload"], "revision": revision,
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
def list_materials(account: str | None = None, page: int = Query(1, ge=1)) -> dict[str, Any]:
    with SessionLocal() as db:
        query = db.query(LearningMaterial)
        if account:
            query = query.filter(LearningMaterial.account == account)
        total = query.count()
        rows = query.order_by(LearningMaterial.imported_at.desc(), LearningMaterial.id.desc()).offset((page - 1) * 20).limit(20).all()
        return {"total": total, "page": page, "items": [material_dict(m) for m in rows]}


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
<link rel="stylesheet" href="{base}/static/study.css?v=4"><link rel="stylesheet" href="{base}/static/learning.css"><script src="{base}/static/study.js?v=4" defer></script></head>
<body>{header(get_settings().base_path, active)}
<main class="learning-page">{body}</main>{collection(get_settings().base_path)}</body></html>''')


@router.get("/learning", response_class=HTMLResponse)
def learning_page(account: str | None = None, page: int = Query(1, ge=1)) -> HTMLResponse:
    data = list_materials(account, page)
    base = escape(get_settings().base_path, quote=True)
    with SessionLocal() as db:
        query = db.query(LearningMaterial)
        if account:
            query = query.filter(LearningMaterial.account == account)
        q_count = func.coalesce(func.json_array_length(LearningMaterial.payload, '$.questions'), 0)
        p_count = func.coalesce(func.json_array_length(LearningMaterial.payload, '$.points'), 0)
        usable = query.filter(q_count + p_count > 0).count()
        tutorial_candidates = [material_dict(m) for m in query.filter(q_count > 0).order_by(
            LearningMaterial.imported_at.desc(), LearningMaterial.id.desc()).limit(3).all()]
    body = '<div class="eyebrow">THE STUDY NOTES</div><h1>公众号研读</h1><p>从老师的文章里读重点、试着回想，再留下需要复习的内容。</p>'
    body += f'<p class="learning-notice">当前本机{("所选账号" if account else "")}已导入 {data["total"]} 篇，其中 {usable} 篇识别到问答或重点提法。这里只显示已取得的样本，不是公众号完整文章列表；文章里的图片、音视频可能尚未整理。</p>'
    body += f'<nav class="review-filters" aria-label="公众号来源"><a href="{base}/learning">全部文章</a>'
    for name in ACCOUNTS.values():
        href = base + '/learning?' + urlencode({'account': name})
        body += f'<a href="{escape(href, quote=True)}" {"aria-current=page" if account == name else ""}>{escape(name)}</a>'
    body += '</nav>'
    example = next(((m, i, q) for m in tutorial_candidates for i, q in enumerate(m.get('questions', []))
                    if q.get('question') and q.get('answer') and q.get('evidence') and m.get('revision')
                    and set(q['evidence']).issubset({line['id'] for line in m.get('evidence', [])})), None)
    if example:
        material, index, question = example
        body += f'''<section class="learning-entry"><h2>第一次用？先试一道题</h2>
<p>下面的问题来自 {escape(material['account'])} 的《{escape(material['title'])}》。</p>
<blockquote>{escape(question['question'])}</blockquote>
<ol><li>打开题目，先自己想答案，再点题目展开原文答案。</li>
<li>想留到以后复习，就点题目旁的“加入复习”。</li>
<li>到“我的复习”记下这次有没有想起来，下次按到期时间再看。</li></ol>
<p><a href="{base}/learning/{material['id']}?revision={material['revision']}#question-{index}">打开这道题，试着回想 →</a> · <a href="{base}/practice">我的复习 →</a></p></section>'''
    body += f'''<details><summary>“按考点看”是做什么的？</summary>
<p>把“国有经济的主导作用”“全面依法治国的总抓手”这样的知识点当作目录。点开一个知识点，就能找到已经匹配的老师原文、问答及相关新闻，方便围绕同一问题阅读。</p>
<p>它不表示这些内容一定会考，也不是两位老师已经互相印证。只有一个来源时，就只显示那一个来源；你也可以直接从下面的文章开始。</p>
<p><a href="{base}/knowledge">按知识点查找原文 →</a></p></details>
<p><a href="{base}/knowledge">按考点研读 →</a> · <a href="{base}/practice">我的复习 →</a></p>
<details><summary>为什么文章少？怎样检查更新？</summary><p>文章少可能是上游尚未采到，不能只靠反复导入补齐。当前采集入口可能只取得最新一篇，历史文章和同次多篇更新仍可能缺失。没有识别到问答，也不代表原文没有价值。</p>
<p><a href="{base}/learning-import">检查已采集数量、导入与正文恢复 →</a></p></details>'''
    for m in data["items"]:
        if m['questions']:
            summary, action, anchor = f'{len(m["questions"])} 道原文问答 · 可逐题回想', '开始自测', '#questions'
            if m['points']: summary += f' · {len(m["points"])} 条重点提法'
        elif m['points']:
            summary, action, anchor = f'{len(m["points"])} 条原文重点提法 · 可阅读并加入复习', '阅读重点提法', '#points'
        else:
            summary, action, anchor = '原文资料 · 暂未识别到支持的问答或重点提法，可直接阅读正文', '阅读原文资料', '#source-text'
        body += f'''<article class="learning-entry"><div class="meta">{escape(m['account'])}</div>
<h2><a href="{base}/learning/{m['id']}">{escape(m['title'])}</a></h2>
<p>{escape(summary)}</p>
<p>{escape(m['topic'])}</p>{bookmark("learning:" + str(m["id"]), m["title"], get_settings().base_path + "/learning/" + str(m["id"]))} <a href="{base}/learning/{m['id']}{anchor}">{action} →</a></article>'''
    if not data["items"]:
        body += f'<p>暂无已整理资料。配置公众号资料源并完成导入后将在此显示。</p><p><a href="{base}/getting-started">准备学习资料 →</a></p>'
    if page > 1:
        previous = urlencode({'page': page - 1, **({'account': account} if account else {})})
        body += f'<a href="?{escape(previous, quote=True)}">上一页</a> '
    if page * 20 < data["total"]:
        following = urlencode({'page': page + 1, **({'account': account} if account else {})})
        body += f'<a href="?{escape(following, quote=True)}">下一页</a>'
    return layout("公众号研读", body)


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
        body += f'<p class="learning-notice">当前查看版本 {m["revision"]} · '
        body += ' / '.join(f'<a href="{base}/learning/{material_id}?revision={v["revision"]}">版本 {v["revision"]}</a>' for v in versions if v["revision"] != m["revision"])
        body += f' · <a href="{base}/learning/{material_id}">最新内容</a></p>'
    body += f'<a href="{escape(m["url"], quote=True)}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>'
    body += bookmark('learning:' + str(m['id']), m['title'], get_settings().base_path + '/learning/' + str(m['id']))
    body += '<aside class="learning-notice"><strong>阅读前请注意</strong><ul>'
    body += ''.join(f'<li>{escape(note)}</li>' for note in m['review_notes']) + '</ul></aside>'
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
    body += '<details class="learning-evidence"><summary>查看上述内容的原文依据</summary>'
    body += ''.join(f'<p id="e-{escape(line["id"])}"><small>{escape(line["id"])}</small> {escape(line["text"])}</p>' for line in m['evidence'] if line['id'] in cited)
    body += '</details><p>页面整理不包含音频转写，图片内文字也可能未覆盖；请结合原文与教材学习。</p>'
    if not m['points'] and not m['questions']:
        body += '<section id="source-text"><h2>已取得的文字正文</h2><p>这篇文章没有匹配到支持的问答或提法格式，以下保留原文，不自动生成自测题。</p>'
        body += ''.join(f'<p>{escape(line["text"])}</p>' for line in m['evidence']) + '</section>'
    return layout(m['title'], body)
