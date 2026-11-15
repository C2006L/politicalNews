"""Knowledge-point index, version-bound evidence, and protected relation review."""
from html import escape
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.database import SessionLocal
from app.knowledge import point_evidence
from app.models import KnowledgePoint, KnowledgeLink, NewsKnowledgeLink, News
from app.news_knowledge import related_news, news_points, news_link_dict
from app.news_data import source_label
from app.routers.learning import layout
from app.review import require_token, apply_review
from app.practice import add_button

router = APIRouter(tags=["考点关联"])


@router.get("/api/knowledge")
def list_points() -> dict[str, Any]:
    with SessionLocal() as db:
        items = []
        for point in db.query(KnowledgePoint).order_by(KnowledgePoint.subject, KnowledgePoint.id).all():
            evidence = point_evidence(db, point.id)
            items.append({"id": point.id, "title": point.title, "subject": point.subject,
                          "evidence_count": len(evidence), "account_count": len({e['account'] for e in evidence}),
                          "news_count": related_news(db, point.id)['total']})
        return {"items": items}


@router.get("/api/knowledge/{point_id}")
def get_point(point_id: str, history: bool = False, page: int = Query(1, ge=1)) -> dict[str, Any]:
    with SessionLocal() as db:
        point = db.get(KnowledgePoint, point_id)
        if point is None:
            raise HTTPException(404, "考点不存在")
        return {"id": point.id, "title": point.title, "subject": point.subject,
                "curriculum_version": None, "evidence": point_evidence(db, point_id, history),
                "news": related_news(db, point_id, history, page)}


@router.get("/api/news/{news_id}/knowledge")
def get_news_points(news_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        news = db.get(News, news_id)
        if news is None:
            raise HTTPException(404, "新闻不存在")
        return {"news_id": news_id, "items": news_points(db, news)}


@router.get("/api/knowledge-news-links/{link_id}")
def news_snapshot(link_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        link = db.get(NewsKnowledgeLink, link_id)
        if link is None or link.status == 'rejected':
            raise HTTPException(404, "新闻关联不存在")
        news = db.get(News, link.news_id)
        if news is None:
            raise HTTPException(404, "新闻不存在")
        return news_link_dict(link, news)


@router.put("/api/knowledge-news-links/{link_id}/review")
def review_news_link(link_id: int, status: Literal["confirmed", "rejected", "candidate"] = Body(..., embed=True),
                     note: str = Body('', max_length=500),
                     expected_status: Literal['candidate', 'confirmed', 'rejected'] | None = Body(None),
                     x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    with SessionLocal() as db:
        return apply_review(db, 'news', link_id, status, note, expected_status)


@router.get("/knowledge/news-links/{link_id}", response_class=HTMLResponse)
def news_snapshot_page(link_id: int) -> HTMLResponse:
    row = news_snapshot(link_id)
    snap, evidence = row['snapshot'], row['evidence']
    base = escape(get_settings().base_path, quote=True)
    body = f'<a href="{base}/knowledge/{row["point_id"]}">← 返回考点</a><h1>{escape(snap["title"])}</h1>'
    state = '当前新闻一致' if row['is_current'] else '历史快照 · 当前新闻已更正'
    body += f'<p class="meta">{escape(source_label(snap["source"]))} · {escape(snap["published_at"])} · {state}</p>'
    field = '标题' if evidence['field'] == 'title' else '正文'
    state = '已确认关联' if row['status'] == 'confirmed' else '候选关联 · 待确认'
    body += f'<p class="learning-notice">{state} · 命中{field}。{escape(evidence["reason"])}。关联用于组织阅读。</p>'
    body += f'<blockquote>{escape(evidence["text"])}</blockquote><p><a href="{base}/news/{row["news_id"]}">查看当前新闻 →</a></p>'
    if urlparse(snap['url']).scheme in {'http', 'https'}:
        body += f'<p><a href="{escape(snap["url"], quote=True)}" target="_blank" rel="noopener noreferrer">匹配时的原站链接 ↗</a></p>'
    body += '<details><summary>查看匹配时的完整正文</summary>'
    body += ''.join(f'<p>{escape(line)}</p>' for line in snap['content'].splitlines())
    return layout('新闻关联依据', body + '</details>')


@router.put("/api/knowledge-links/{link_id}/review")
def review_link(link_id: int, status: Literal["confirmed", "rejected", "candidate"] = Body(..., embed=True),
                note: str = Body('', max_length=500),
                expected_status: Literal['candidate', 'confirmed', 'rejected'] | None = Body(None),
                x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    with SessionLocal() as db:
        return apply_review(db, 'material', link_id, status, note, expected_status)


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page() -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    body = f'<a href="{base}/learning">← 公众号研读</a><h1>按考点研读</h1><p>把知识点当目录：选一个你想弄清楚的问题，查看老师怎么说、原文在哪里，以及有哪些相关新闻。</p>'
    points = list_points()['items']
    multi = sum(point['account_count'] > 1 for point in points)
    body += f'<p class="learning-notice">当前从已有资料匹配出 {len(points)} 个知识点，其中 {multi} 个有多个账号的材料。样本仍少，这不是完整考试大纲，也不能据此判断重点或押题。</p>'
    comparisons = [point for point in points if point['account_count'] > 1]
    if comparisons:
        body += '<section class="learning-entry"><h2>先看两位老师讲同一知识点</h2><p>点开后，分别看问答和原文提法，核对相同之处与表述差别。这里保留两份来源，不合成一个标准答案；关系仍待确认。</p><ul>'
        body += ''.join(f'<li><a href="{base}/knowledge/{escape(point["id"], quote=True)}">{escape(point["title"])}</a> · {point["account_count"]} 个来源</li>' for point in comparisons)
        body += '</ul></section>'
    body += '<p>例如想复习“国有经济的主导作用”，点开这个目录，再看其中的原文问题和答案。一个目录只有一个来源时，就先读这一份；没有材料的章节目前不会自动补成答案。</p>'
    body += f'<details><summary>关联是否已经核对？</summary><p>目前按明确表述模板建立候选关联，需人工确认。不同来源分别呈现，不自动拼成统一结论；教材版本尚未指定。</p><p><a href="{base}/review">维护关联与审核记录 →</a></p></details>'
    for point in points:
        body += f'<article class="learning-entry"><div class="meta">{escape(point["subject"])}</div><h2><a href="{base}/knowledge/{point["id"]}">{escape(point["title"])}</a></h2><p>{point["evidence_count"]} 条当前资料依据 · {point["account_count"]} 个账号 · {point["news_count"]} 条新闻关联</p></article>'
    if not points:
        body += '<p>尚未发现匹配的考点材料。</p>'
    return layout("按考点研读", body)


@router.get("/knowledge/{point_id}", response_class=HTMLResponse)
def knowledge_detail(point_id: str, history: bool = False, page: int = Query(1, ge=1)) -> HTMLResponse:
    point = get_point(point_id, history, page)
    base = escape(get_settings().base_path, quote=True)
    body = f'<a href="{base}/knowledge">← 考点索引</a><h1>{escape(point["title"])}</h1><p class="learning-notice">关联用于组织阅读。候选关系及原文答案尚待核对，作者重点标记不等于系统押题。</p>'
    accounts = {entry['account'] for entry in point['evidence'] if entry['is_current']}
    if len(accounts) > 1:
        body += f'<p>这个知识点已有 {len(accounts)} 个来源。下面分别保留老师的原文：先回想问题，再对照各自表述和段落依据；不会用其中一份覆盖另一份。</p>'
    body += f'<p><a href="#theory">理论资料与自测</a> · <a href="#related-news">相关新闻 {point["news"]["total"]} 条 ↓</a></p>'
    body += f'<p><a href="?history={"false" if history else "true"}">{"只看当前版本" if history else "包含历史版本"}</a></p><h2 id="theory">理论资料与自测</h2>'
    for entry in point['evidence']:
        item = entry['item']
        state = '已确认关联' if entry['status'] == 'confirmed' else '候选关联 · 待确认'
        if not entry['is_current']:
            state += ' · 历史版本'
        body += f'<article class="learning-entry knowledge-entry"><div class="meta">{escape(entry["account"])} · 版本 {entry["revision"]} · {state}</div><h2 class="source-title">{escape(entry["title"])}</h2>'
        if entry['kind'] == 'questions':
            body += f'<details class="learning-question"><summary>{escape(item["question"])}</summary><p class="knowledge-text">{escape(item["answer"])}</p>'
            if item.get('needs_review'):
                body += '<p>原文存在多表述或信息不足，需对照教材核对。</p>'
            if item.get('starred_by_author'):
                body += '<p>原作者重点标记</p>'
        else:
            body += '<p class="knowledge-text">' + escape(item['text']) + '</p>'
            body += f'<details class="source-evidence"><summary>核对原文依据 · {len(entry["evidence"])} 段</summary>'
        for line in entry['evidence']:
            url = f'{base}/learning/{entry["material_id"]}?revision={entry["revision"]}#e-{escape(line["id"])}'
            body += f'<p class="evidence-text"><small><a href="{url}">版本 {entry["revision"]} / {escape(line["id"])}</a></small> {escape(line["text"])}</p>'
        body += '</details>'
        body += add_button(entry['material_id'], entry['revision'], entry['kind'], entry['item_index'])
        body += '</article>'
    if not point['evidence']:
        body += '<p>当前版本暂无可展示关联，可查看历史版本。</p>'
    body += f'<h2 id="related-news">联系新闻阅读 · {point["news"]["total"]} 条</h2><p>同句明确概念组合匹配，仅表示阅读关联，不能据此推断命题重点或新闻印证了全部理论。按刊载记录计数，可能包含转载，不代表独立事件数。</p>'
    for row in point['news']['items']:
        snap, evidence = row['snapshot'], row['evidence']
        state = '已确认关联' if row['status'] == 'confirmed' else '候选关联 · 待确认'
        state += '' if row['is_current'] else ' · 历史快照'
        field = '标题' if evidence['field'] == 'title' else '正文'
        body += f'<article class="learning-entry"><div class="meta">{escape(source_label(snap["source"]))} · {escape(snap["published_at"][:10])} · {state}</div>'
        body += f'<h3><a href="{base}/news/{row["news_id"]}">{escape(snap["title"])}</a></h3><p>命中{field} · {escape(evidence["reason"])}</p><blockquote>{escape(evidence["text"])}</blockquote>'
        body += f'<a href="{base}/knowledge/news-links/{row["id"]}">匹配时的新闻快照与依据 →</a></article>'
    if not point['news']['items']:
        body += '<p>暂无符合明确匹配条件的新闻。</p>'
    if page > 1:
        body += f'<a href="?history={str(history).lower()}&amp;page={page - 1}">上一页</a> '
    if page * 20 < point['news']['total']:
        body += f'<a href="?history={str(history).lower()}&amp;page={page + 1}">下一页</a>'
    return layout(point['title'], body)
