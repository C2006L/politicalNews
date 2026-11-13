"""Knowledge-point index, version-bound evidence, and protected relation review."""
from html import escape
import secrets
from typing import Any, Literal

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.database import SessionLocal
from app.knowledge import point_evidence
from app.models import KnowledgePoint, KnowledgeLink
from app.routers.learning import layout

router = APIRouter(tags=["考点关联"])


@router.get("/api/knowledge")
def list_points() -> dict[str, Any]:
    with SessionLocal() as db:
        items = []
        for point in db.query(KnowledgePoint).order_by(KnowledgePoint.subject, KnowledgePoint.id).all():
            evidence = point_evidence(db, point.id)
            items.append({"id": point.id, "title": point.title, "subject": point.subject,
                          "evidence_count": len(evidence), "account_count": len({e['account'] for e in evidence})})
        return {"items": items}


@router.get("/api/knowledge/{point_id}")
def get_point(point_id: str, history: bool = False) -> dict[str, Any]:
    with SessionLocal() as db:
        point = db.get(KnowledgePoint, point_id)
        if point is None:
            raise HTTPException(404, "考点不存在")
        return {"id": point.id, "title": point.title, "subject": point.subject,
                "curriculum_version": None, "evidence": point_evidence(db, point_id, history)}


@router.put("/api/knowledge-links/{link_id}/review")
def review_link(link_id: int, status: Literal["confirmed", "rejected", "candidate"] = Body(..., embed=True),
                x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    required = get_settings().sync_admin_token
    if not required or not secrets.compare_digest(x_sync_token or "", required):
        raise HTTPException(403, "需要已配置的管理员令牌")
    with SessionLocal() as db:
        link = db.get(KnowledgeLink, link_id)
        if link is None:
            raise HTTPException(404, "关联不存在")
        link.status = status
        db.commit()
    return {"id": link_id, "status": status}


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page() -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    body = f'<a href="{base}/learning">← 公众号研读</a><h1>按考点研读</h1><p class="learning-notice">明确表述模板建立候选关联，需人工确认。不同来源分别呈现，不自动拼成统一结论；教材版本尚未指定。</p>'
    points = list_points()['items']
    for point in points:
        body += f'<article class="learning-entry"><div class="meta">{escape(point["subject"])}</div><h2><a href="{base}/knowledge/{point["id"]}">{escape(point["title"])}</a></h2><p>{point["evidence_count"]} 条当前版本依据 · {point["account_count"]} 个账号</p></article>'
    if not points:
        body += '<p>尚未发现匹配的考点材料。</p>'
    return layout("按考点研读", body)


@router.get("/knowledge/{point_id}", response_class=HTMLResponse)
def knowledge_detail(point_id: str, history: bool = False) -> HTMLResponse:
    point = get_point(point_id, history)
    base = escape(get_settings().base_path, quote=True)
    body = f'<a href="{base}/knowledge">← 考点索引</a><h1>{escape(point["title"])}</h1><p class="learning-notice">关联用于组织阅读。候选关系及原文答案尚待核对，作者重点标记不等于系统押题。</p>'
    body += f'<p><a href="?history={"false" if history else "true"}">{"只看当前版本" if history else "包含历史版本"}</a></p>'
    for entry in point['evidence']:
        item = entry['item']
        state = '已确认关联' if entry['status'] == 'confirmed' else '候选关联 · 待确认'
        if not entry['is_current']:
            state += ' · 历史版本'
        body += f'<article class="learning-entry"><div class="meta">{escape(entry["account"])} · 版本 {entry["revision"]} · {state}</div><h2>{escape(entry["title"])}</h2>'
        if entry['kind'] == 'questions':
            body += f'<details class="learning-question"><summary>{escape(item["question"])}</summary><p>{escape(item["answer"])}</p></details>'
            if item.get('needs_review'):
                body += '<p>原文存在多表述或信息不足，需对照教材核对。</p>'
            if item.get('starred_by_author'):
                body += '<p>原作者重点标记</p>'
        else:
            body += '<p>' + escape(item['text']) + '</p>'
        for line in entry['evidence']:
            url = f'{base}/learning/{entry["material_id"]}?revision={entry["revision"]}#e-{escape(line["id"])}'
            body += f'<p><small><a href="{url}">版本 {entry["revision"]} / {escape(line["id"])}</a></small> {escape(line["text"])}</p>'
        body += '</article>'
    if not point['evidence']:
        body += '<p>当前版本暂无可展示关联，可查看历史版本。</p>'
    return layout(point['title'], body)
