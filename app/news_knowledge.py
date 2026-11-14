"""Explainable news-to-concept candidates, with immutable matching text snapshots."""
from __future__ import annotations

import hashlib
import json
import re
from html import escape
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.knowledge import CATALOG
from app.models import KnowledgePoint, News, NewsKnowledgeLink

# All expressions in a tuple must occur in the SAME title/sentence. These are
# reading suggestions, never statements that an event proves a textbook answer.
RULES = {
    "law-goal": (r"全面依法治国", r"法治中国|法治国家|宏伟目标"),
    "law-system": (r"全面依法治国", r"法治体系"),
    "law-justice": (r"公正司法", r"公平正义|司法公正|司法改革|生命线"),
    "law-right-duty": (r"法律", r"权利", r"义务"),
    "economy-state": (r"国有经济", r"主导作用"),
    "economy-private": (r"民营经济|民营企业", r"中国式现代化|生力军"),
    "economy-government-market": (r"政府和市场|政府与市场|有效市场.*有为政府|有为政府.*有效市场", r"关系|作用|结合"),
    "development-innovation": (r"创新发展|科技创新", r"发展动力"),
    "development-coordination": (r"协调发展", r"不平衡|发展差距"),
    "development-sharing": (r"共享发展", r"公平正义"),
    "productivity-innovation": (r"新质生产力", r"科技创新", r"核心要素|核心动力|驱动|推动|支撑|引领|发展新质生产力|新质生产力发展"),
    "economy-real": (r"实体经济", r"现代化产业体系"),
}
METHOD = "news-sentence-v1"


def news_snapshot(news: News) -> dict[str, str]:
    return {"title": news.title, "content": news.content or "", "summary": news.summary or "",
            "url": news.link, "source": news.source, "published_at": news.published_at.isoformat()}


def fingerprint(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def candidates(snapshot: dict[str, str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for field in ("title", "content"):
        for sentence in re.finditer(r"[^。！？!?\n]+[。！？!?]?", snapshot[field]):
            text = sentence.group()
            if len(text) > 800:
                continue
            for point_id, patterns in RULES.items():
                matches = [re.search(pattern, text) for pattern in patterns]
                if point_id not in found and all(matches):
                    terms = [match.group() for match in matches if match is not None]
                    found[point_id] = {"field": field, "start": sentence.start(), "end": sentence.end(),
                                       "text": text, "matched_terms": terms,
                                       "reason": "同一句中出现：" + "、".join(terms)}
    return found


def link_news(db: Session, news: News) -> int:
    db.flush()
    snap = news_snapshot(news)
    digest = fingerprint(snap)
    added = 0
    for point_id, evidence in candidates(snap).items():
        exists = db.query(NewsKnowledgeLink.id).filter_by(point_id=point_id, news_id=news.id, fingerprint=digest).first()
        if exists:
            continue
        if db.get(KnowledgePoint, point_id) is None:
            _, title, subject, _ = next(row for row in CATALOG if row[0] == point_id)
            db.add(KnowledgePoint(id=point_id, title=title, subject=subject))
            db.flush()
        db.add(NewsKnowledgeLink(point_id=point_id, news_id=news.id, fingerprint=digest,
                                snapshot=snap, evidence=evidence, status="candidate", method=METHOD))
        added += 1
    db.flush()
    return added


def rebuild_news_links() -> dict[str, int]:
    with SessionLocal() as db:
        added = sum(link_news(db, news) for news in db.query(News).yield_per(200))
        db.commit()
    return {"added": added}


def news_link_dict(link: NewsKnowledgeLink, news: News) -> dict[str, Any]:
    return {"id": link.id, "point_id": link.point_id, "news_id": link.news_id,
            "fingerprint": link.fingerprint, "snapshot": link.snapshot, "evidence": link.evidence,
            "status": link.status, "method": link.method,
            "is_current": link.fingerprint == fingerprint(news_snapshot(news))}


def related_news(db: Session, point_id: str, history: bool = False, page: int = 1) -> dict[str, Any]:
    rows = db.query(NewsKnowledgeLink, News).join(News, News.id == NewsKnowledgeLink.news_id).filter(
        NewsKnowledgeLink.point_id == point_id, NewsKnowledgeLink.status != "rejected").order_by(
            News.published_at.desc(), NewsKnowledgeLink.id.desc()).all()
    items = [news_link_dict(link, news) for link, news in rows]
    items = [item for item in items if history or item["is_current"]]
    return {"total": len(items), "page": page, "items": items[(page - 1) * 20:page * 20]}


def news_points(db: Session, news: News) -> list[dict[str, Any]]:
    rows = db.query(NewsKnowledgeLink, KnowledgePoint).join(KnowledgePoint).filter(
        NewsKnowledgeLink.news_id == news.id, NewsKnowledgeLink.status != "rejected",
        NewsKnowledgeLink.fingerprint == fingerprint(news_snapshot(news))).all()
    return [{"id": point.id, "title": point.title, "link_id": link.id, "status": link.status,
             "evidence": link.evidence} for link, point in rows]


def render_news_points(news: News, base: str, details: bool = False) -> str:
    with SessionLocal() as db:
        points = news_points(db, news)
    if not points:
        return ''
    links = ' · '.join(f'<a href="{escape(base, quote=True)}/knowledge/{point["id"]}">{escape(point["title"])}</a>'
                       for point in points)
    body = f'<section class="news-knowledge"><p>关联考点 · {links}</p>'
    if details:
        body += '<p>以下关联供联系理论阅读，候选关系需核对，不代表命题预测。</p>'
        for point in points:
            evidence = point['evidence']
            state = '已确认关联' if point['status'] == 'confirmed' else '候选关联 · 待确认'
            field = '标题' if evidence['field'] == 'title' else '正文'
            body += f'<p>{state} · 命中{field} · {escape(evidence["reason"])}</p><blockquote>{escape(evidence["text"])}</blockquote>'
            body += f'<a href="{escape(base, quote=True)}/knowledge/news-links/{point["link_id"]}">查看匹配时的新闻快照与依据 →</a>'
    return body + '</section>'
