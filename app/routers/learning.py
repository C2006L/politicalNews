"""Learning material views; external content is always rendered as escaped text."""
from __future__ import annotations

import secrets
import sqlite3
from html import escape
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.chrome import header, collection, bookmark
from app.config import get_settings
from app.database import SessionLocal
from app.materials import sync_materials
from app.material_status import pipeline_status
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
    if not required:
        raise HTTPException(503, "请先配置 SYNC_ADMIN_TOKEN；也可使用本地导入命令")
    if not secrets.compare_digest(x_sync_token or "", required):
        raise HTTPException(403, "需要管理员令牌")
    try:
        return sync_materials()
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "公众号资料源未就绪，请检查本地配置") from exc


def layout(title: str, body: str) -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    return HTMLResponse(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · 时政自习室</title>
<link rel="stylesheet" href="{base}/static/study.css?v=4"><link rel="stylesheet" href="{base}/static/learning.css"><script src="{base}/static/study.js?v=4" defer></script></head>
<body>{header(get_settings().base_path, "learning")}
<main class="learning-page">{body}</main>{collection()}</body></html>''')


@router.get("/learning", response_class=HTMLResponse)
def learning_page(account: str | None = None, page: int = Query(1, ge=1)) -> HTMLResponse:
    data = list_materials(account, page)
    base = escape(get_settings().base_path, quote=True)
    body = '<div class="eyebrow">THE STUDY NOTES</div><h1>公众号研读</h1><p class="learning-notice">考点摘录与自测问答 · 规则整理，尚未人工审核；保留原文出处。</p>'
    for m in data["items"]:
        body += f'''<article class="learning-entry"><div class="meta">{escape(m['account'])}</div>
<h2><a href="{base}/learning/{m['id']}">{escape(m['title'])}</a></h2>
<p>{len(m['points'])} 条考点摘录 · {len(m['questions'])} 条自测问答</p>
<p>{escape(m['topic'])}</p>{bookmark("learning:" + str(m["id"]), m["title"], get_settings().base_path + "/learning/" + str(m["id"]))} <a href="{base}/learning/{m['id']}">查看考点与依据 →</a></article>'''
    if not data["items"]:
        body += '<p>暂无已整理资料。配置公众号资料源并完成导入后将在此显示。</p>'
    if page > 1:
        body += f'<a href="?page={page - 1}">上一页</a> '
    if page * 20 < data["total"]:
        body += f'<a href="?page={page + 1}">下一页</a>'
    return layout("公众号研读", body)


@router.get("/learning/{material_id}", response_class=HTMLResponse)
def learning_detail(material_id: int, revision: int | None = Query(None, ge=1)) -> HTMLResponse:
    m = find_material(material_id, revision)
    base = escape(get_settings().base_path, quote=True)
    body = f'<a href="{base}/learning">← 公众号研读</a><p class="eyebrow">{escape(m["account"])}</p><h1>{escape(m["title"])}</h1>'
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
        body += '<h2>考点摘录</h2>'
        section = ''
        for point in m['points']:
            if point['section'] != section:
                section = point['section']
                body += '<h3>' + escape(section) + '</h3>'
            refs = ' '.join(f'<a href="#e-{escape(r)}">{escape(r)}</a>' for r in point['evidence'])
            body += f'<p>{escape(point["text"])} <small>{refs}</small></p>'
    if m['questions']:
        body += '<h2>先回想，再展开答案</h2>'
        for q in m['questions']:
            badge = ' · 原作者重点标记' if q['starred_by_author'] else ''
            badge += ' · 待核对' if q['needs_review'] else ''
            refs = ' '.join(f'<a href="#e-{escape(r)}">{escape(r)}</a>' for r in q['evidence'])
            body += f'<details class="learning-question"><summary>{q["number"]}. {escape(q["question"])}{badge}</summary><p>{escape(q["answer"])}</p><small>原文依据：{refs}</small></details>'
    cited = {ref for p in m['points'] + m['questions'] for ref in p['evidence']}
    body += '<details class="learning-evidence"><summary>查看上述内容的原文依据</summary>'
    body += ''.join(f'<p id="e-{escape(line["id"])}"><small>{escape(line["id"])}</small> {escape(line["text"])}</p>' for line in m['evidence'] if line['id'] in cited)
    body += '</details><p>页面整理不包含音频转写，图片内文字也可能未覆盖；请结合原文与教材学习。</p>'
    return layout(m['title'], body)
