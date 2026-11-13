"""Read-only WeRSS bridge and evidence-preserving extraction; no model calls."""
from __future__ import annotations

from contextlib import closing

import hashlib
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.config import get_settings
from app.database import SessionLocal
from app.models import LearningMaterial, MaterialRevision
from app.revisions import append_revision
from app.material_status import now_iso, write_status

ACCOUNTS = {"MP_WXS_3243013434": "肖秀荣教授", "MP_WXS_3080503014": "考研政治徐涛"}
EXTRACTOR_VERSION = "rules-2"
LOCK = threading.Lock()


class ContentError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def extract_material(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#js_content") or soup
    audio = [{"title": a.get("name", ""), "transcribed": False}
             for a in root.select("mp-common-mpaudio, audio, mpvoice")]
    for node in root.select("script,style,template,mp-common-product"):
        node.decompose()
    lines = [re.sub(r"\s+", " ", str(line)).strip() for line in root.stripped_strings]
    text = "\n".join(lines)
    if "当前环境异常" in text or "完成验证后即可继续访问" in text:
        raise ContentError("verification_required")
    if len(text) < 100:
        raise ContentError("insufficient_text")
    evidence = [{"id": f"L{i + 1}", "text": line} for i, line in enumerate(lines)]
    points: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    flags = ["规则摘录，未核对教材；不是 AI 总结或命题预测。",
             "仅覆盖已取得的文字；音频、图片内文字及图书章节未完整验证。"]
    section = ""
    in_focus = False
    in_quiz = False
    current: dict[str, Any] | None = None
    for i, line in enumerate(lines):
        ref = f"L{i + 1}"
        if "重温重要提法" in line:
            in_focus = True
            continue
        if line.startswith("选择题练习"):
            in_focus = False
        if in_focus:
            if line.startswith("专题"):
                section = line
            else:
                points.append({"text": line, "section": section, "evidence": [ref]})
        if "点击空白处查看答案" in line:
            in_quiz = True
            continue
        if in_quiz and ("后台回复" in line or "下滑查看答案" in line):
            in_quiz = False
        if in_quiz:
            match = re.match(r"^[🌟⭐\s]*(\d+)[.．、]\s*(.*)", line)
            if match:
                current = {"number": int(match[1]), "question": match[2], "answer": "",
                           "starred_by_author": "🌟" in line or "⭐" in line, "evidence": [ref]}
                questions.append(current)
            elif current and not line.startswith("【🌟"):
                # Some saved pages put the number and question on separate lines.
                if not current["question"]:
                    current["question"] = line
                else:
                    current["answer"] += line
                current["evidence"].append(ref)
    for q in questions:
        q["needs_review"] = not q["answer"] or "/" in q["answer"] or (
            "四个自信" in q["question"] and "最大优势" in q["question"])
        if q["needs_review"]:
            flags.append(f"自测第{q['number']}条存在多种表述或信息不足，请对照原文和教材核对。")
    if not points and not questions:
        flags.append("未识别到支持的考点区或自测区，保留原文等待人工整理。")
    topic = next((line for line in lines if "D" in line and "背诵知识点" in line), "")
    pages = [line for line in lines if re.search(r"P\d+", line)]
    return {"method": EXTRACTOR_VERSION, "topic": topic, "pages": pages,
            "points": points, "questions": questions, "evidence": evidence,
            "audio": audio, "review_notes": flags, "review_status": "unreviewed",
            "publication_time_verified": False, "published_at": None,
            "coverage": {"text": "extracted", "images": "not_verified", "audio": "not_transcribed",
                         "article_list": "latest_only_not_complete"}}


def _import_materials() -> dict[str, int]:
    """Import only the two selected accounts; never write to WeRSS's database."""
    configured = get_settings().werss_database_path
    if not configured:
        raise ValueError("请配置 WERSS_DATABASE_PATH")
    path = Path(configured).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
        rows = source.execute(
            "SELECT id,mp_id,title,url,content_html FROM articles "
            "WHERE mp_id IN (?,?) AND has_content=1 ORDER BY create_time DESC LIMIT 200",
            tuple(ACCOUNTS),
        ).fetchall()
    result = {"added": 0, "updated": 0, "skipped": 0, "rejected": 0,
              "verification_required": 0, "insufficient_text": 0, "invalid_record": 0}
    with SessionLocal() as db:
        for source_id, account_id, title, url, html in rows:
            if not isinstance(title, str) or not isinstance(url, str) or not isinstance(html, str):
                result["rejected"] += 1
                result["invalid_record"] += 1
                continue
            parsed = urlparse(url or "")
            if parsed.scheme != "https" or parsed.netloc != "mp.weixin.qq.com":
                result["rejected"] += 1
                result["invalid_record"] += 1
                continue
            fingerprint = hashlib.sha256((EXTRACTOR_VERSION + (html or "") + title + url).encode()).hexdigest()
            material = db.query(LearningMaterial).filter_by(source_id=source_id).first()
            if material and material.fingerprint == fingerprint:
                result["skipped"] += 1
                continue
            try:
                payload = extract_material(html or "")
            except ContentError as exc:
                result["rejected"] += 1
                result[exc.reason] += 1
                continue
            if material is None:
                material = LearningMaterial(source_id=source_id)
                db.add(material)
                result["added"] += 1
            else:
                if not db.query(MaterialRevision.id).filter_by(material_id=material.id).first():
                    append_revision(db, material)
                result["updated"] += 1
            material.account = ACCOUNTS[account_id]
            material.title, material.url = title, url
            material.fingerprint, material.payload = fingerprint, payload
            material.imported_at = datetime.now(timezone.utc).replace(tzinfo=None)
            append_revision(db, material, html)
        db.commit()
    return result



def sync_materials() -> dict[str, int]:
    if not LOCK.acquire(blocking=False):
        raise ValueError("已有公众号导入任务在运行")
    try:
        write_status(state="running", started_at=now_iso(), finished_at=None, error=None)
        try:
            result = _import_materials()
        except (OSError, sqlite3.Error, ValueError):
            write_status(state="failed", finished_at=now_iso(), error="source_unavailable")
            raise
        except Exception:
            write_status(state="failed", finished_at=now_iso(), error="import_failed")
            raise
        stamp = now_iso()
        state = "partial" if result["rejected"] else "updated" if result["added"] + result["updated"] else "no_new_materials"
        changes: dict[str, Any] = dict(state=state, finished_at=stamp, result=result, error=None)
        if not result["rejected"]:
            changes["last_success_at"] = stamp
        if result["added"] + result["updated"]:
            changes["last_changed_at"] = stamp
        write_status(**changes)
        return result
    finally:
        LOCK.release()
