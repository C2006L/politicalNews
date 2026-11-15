"""Conservative, version-bound candidate associations. No semantic auto-merge."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import KnowledgePoint, KnowledgeLink, MaterialRevision

# Exact question forms or anchored statement prefixes; never an unscoped keyword search.
CATALOG = [
    ("law-goal", "全面依法治国的宏伟目标", "思法", r"^全面依法治国的宏伟目标[：:]"),
    ("law-system", "全面依法治国的总抓手", "思法", r"^全面依法治国的总抓手[：:]"),
    ("law-justice", "公正司法的定位", "思法", r"^公正是法治的生命线"),
    ("law-right-duty", "法律权利与义务的关系", "思法", r"^法律权利的实现必须以相应法律义务"),
    ("economy-state", "国有经济的主导作用", "新思想", r"^在国民经济中起主导作用的是什么[？?]?$"),
    ("economy-private", "民营经济与中国式现代化", "新思想", r"^推进中国式现代化的生力军是什么[？?]?$"),
    ("economy-government-market", "政府与市场的关系", "新思想", r"^构建高水平社会主义市场经济体制的关键是什么[？?]?$"),
    ("development-innovation", "创新发展与发展动力", "新思想", r"^新发展理念中解决发展动力问题的是什么[？?]?$"),
    ("development-coordination", "协调发展与发展不平衡", "新思想", r"^新发展理念中解决发展不平衡问题的是什么[？?]?$"),
    ("development-sharing", "共享发展与公平正义", "新思想", r"^新发展理念中注重解决社会公平正义问题的是什么[？?]?$"),
    ("productivity-innovation", "新质生产力的核心要素", "新思想", r"^新质生产力的核心要素是什么[？?]?$"),
    ("economy-real", "现代化产业体系的根基", "新思想", r"^现代化产业体系的根基是什么[？?]?$"),
    ("modernization-dream-essence", "中国梦的本质", "新思想",
     r"^(?:中国梦的本质是什么[？?]?|实现中华民族伟大复兴的中国梦，本质是国家富强、民族振兴、人民幸福[。.]?)$"),
    ("modernization-supports", "中国式现代化的基础性、战略性支撑", "新思想",
     r"^(?:中国式现代化的基础性、战略性支撑是什么[？?]?|教育、科技、人才是中国式现代化的基础性、战略性支撑。)$"),
    ("development-primary-task", "全面建设社会主义现代化国家的首要任务", "新思想",
     r"^(?:全面建设社会主义现代化国家的首要任务是什么[？?]?|高质量发展是全面建设社会主义现代化国家的首要任务。)$"),
    ("development-productivity-role", "高质量发展与新质生产力", "新思想",
     r"^(?:推动高质量发展的内在要求和重要着力点是什么[？?]?|推动高质量发展的内在要求和重要着力点：发展新质生产力。)$"),
    ("economy-ownership-status", "公有制与非公有制经济的地位", "新思想",
     r"^(?:公有制经济和非公有制经济都是什么的重要组成部分[？?]?$|公有制经济与非公有制经济都是社会主义市场经济的重要组成部分，这里一定要注意)"),
]


def link_revision(db: Session, revision: MaterialRevision) -> int:
    payload = revision.snapshot.get("payload", {})
    valid_refs = {line["id"] for line in payload.get("evidence", [])}
    added = 0
    for point_id, title, subject, pattern in CATALOG:
        for kind in ("points", "questions"):
            for index, item in enumerate(payload.get(kind, [])):
                content = item.get("question" if kind == "questions" else "text", "")
                normalized = re.sub(r'^[➤•]\s*[①②③④⑤⑥⑦⑧⑨⑩]?\s*', '', content.strip())
                normalized = re.sub(r'\s*[（(]P\d+(?:[-–—]\d+)?[）)]\s*$', '', normalized)
                if not re.search(pattern, normalized):
                    continue
                refs = item.get("evidence", [])
                if not refs or not all(ref in valid_refs for ref in refs):
                    continue
                if db.get(KnowledgePoint, point_id) is None:
                    db.add(KnowledgePoint(id=point_id, title=title, subject=subject))
                    db.flush()
                exists = db.query(KnowledgeLink).filter_by(point_id=point_id, revision_id=revision.id,
                                                           kind=kind, item_index=index).first()
                if exists:
                    continue
                db.add(KnowledgeLink(point_id=point_id, revision_id=revision.id, kind=kind,
                                     item_index=index, evidence_ids=refs, status="candidate", method="catalog-v2"))
                added += 1
    return added


def rebuild_links() -> dict[str, int]:
    with SessionLocal() as db:
        added = sum(link_revision(db, revision) for revision in db.query(MaterialRevision).all())
        db.commit()
    return {"added": added}


def material_points(material_id: int, number: int | None) -> list[dict[str, str]]:
    with SessionLocal() as db:
        revision = db.query(MaterialRevision).filter_by(material_id=material_id, revision=number).first()
        if revision is None:
            return []
        points = db.query(KnowledgePoint).join(KnowledgeLink, KnowledgeLink.point_id == KnowledgePoint.id).filter(
            KnowledgeLink.revision_id == revision.id, KnowledgeLink.status != "rejected").all()
        return [{"id": p.id, "title": p.title} for p in points]


def point_evidence(db: Session, point_id: str, history: bool = False) -> list[dict[str, Any]]:
    links = db.query(KnowledgeLink).filter(KnowledgeLink.point_id == point_id,
                                         KnowledgeLink.status != "rejected").order_by(KnowledgeLink.id).all()
    rows = []
    for link in links:
        revision = db.get(MaterialRevision, link.revision_id)
        if revision is None:
            continue
        latest = db.query(MaterialRevision.revision).filter_by(material_id=revision.material_id).order_by(MaterialRevision.revision.desc()).first()
        is_current = bool(latest and latest[0] == revision.revision)
        if not history and not is_current:
            continue
        snap = revision.snapshot
        item = snap["payload"][link.kind][link.item_index]
        rows.append({"id": link.id, "material_id": revision.material_id, "revision": revision.revision,
                     "item_index": link.item_index,
                     "account": snap["account"], "title": snap["title"], "original_url": snap["url"],
                     "status": link.status, "method": link.method, "kind": link.kind, "item": item, "is_current": is_current,
                     "evidence": [line for line in snap["payload"]["evidence"] if line["id"] in link.evidence_ids]})
    return rows
