"""Read-only WeRSS bridge and evidence-preserving extraction; no model calls."""
from __future__ import annotations

from contextlib import closing

import hashlib
import json
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
from app.models import LearningMaterial, MaterialRevision, AppState
from app.revisions import append_revision
from app.material_sources import accounts
from app.material_status import now_iso, write_status, parse_scan
from app.material_originals import observe

ACCOUNTS = {"MP_WXS_3243013434": "肖秀荣教授", "MP_WXS_3080503014": "考研政治徐涛"}
EXTRACTOR_VERSION = "rules-3"
LOCK = threading.Lock()
SCAN_KEY = 'learning_import_scan_v1'
BATCH_SIZE = 200


class ContentError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ImportBusy(ValueError):
    pass


def extract_material(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#js_content") or soup
    audio = [{"title": a.get("name", ""), "transcribed": False}
             for a in root.select("mp-common-mpaudio, audio, mpvoice")]
    for node in root.select("script,style,template,mp-common-product"):
        node.decompose()
    # Formatting spans must not turn one sentence into many independent claims.
    for node in root.select('span,strong,b,em,i,font,u,a,sup,sub'):
        node.unwrap()
    root.smooth()
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
    negative_context = False
    current: dict[str, Any] | None = None
    for i, line in enumerate(lines):
        ref = f"L{i + 1}"
        if line in {'重温重要提法', '点拨', '命题分析'}:
            in_focus = True
            negative_context = False
            continue
        if line.startswith("选择题练习"):
            in_focus = False
        if in_focus:
            if line.startswith("专题"):
                section = line
                negative_context = False
            elif '经典干扰项' in line:
                negative_context = True
            else:
                if line.startswith('➤'):
                    negative_context = False
                if (negative_context or len(line) < 10 or line.startswith(('✘', '×', '❌')) or
                        '干扰项' in line or '考查频率' in line):
                    continue
                points.append({"text": line, "section": section, "evidence": [ref]})
        if '点击空白处查看答案' in line or '点击空格横线查看答案' in line:
            in_quiz = True
            continue
        if in_quiz and any(marker in line for marker in ('后台回复', '下滑查看答案', '参考答案', '【解析】', '今天的带背内容', '往期带背内容')):
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
    if root.select('svg[data-lazy-bgimg],img'):
        flags.append('文章含图片或图片式交互题，本次未做 OCR；文字条目不代表覆盖了所有题目。')
    topic = next((line for line in lines if "D" in line and "背诵知识点" in line), "")
    pages = [line for line in lines if re.search(r"P\d+", line)]
    return {"method": EXTRACTOR_VERSION, "topic": topic, "pages": pages,
            "points": points, "questions": questions, "evidence": evidence,
            "audio": audio, "review_notes": flags, "review_status": "unreviewed",
            "publication_time_verified": False, "published_at": None,
            "coverage": {"text": "extracted", "images": "not_verified", "audio": "not_transcribed",
                         "article_list": "latest_only_not_complete"}}


def _import_materials(only_source_id: str | None = None) -> dict[str, int]:
    """Import only enabled source accounts; never write to WeRSS's database."""
    active_accounts = accounts()
    configured = get_settings().werss_database_path
    if not configured:
        raise ValueError("请配置 WERSS_DATABASE_PATH")
    path = Path(configured).resolve(strict=True)
    result = {"added": 0, "updated": 0, "skipped": 0, "rejected": 0,
              "verification_required": 0, "insufficient_text": 0, "invalid_record": 0,
              "scanned": 0, "remaining": 0}
    if not active_accounts:
        return result
    placeholders = ','.join('?' for _ in active_accounts)
    with SessionLocal() as db:
        checkpoint = db.get(AppState, SCAN_KEY)
        scan = parse_scan(checkpoint.value if checkpoint else None)
        source_key = hashlib.sha256(str(path).encode()).hexdigest()
        account_key = hashlib.sha256(json.dumps(active_accounts, sort_keys=True).encode()).hexdigest()
        if scan.get('source_key') != source_key or scan.get('account_key') != account_key:
            scan = {'source_key': source_key, 'account_key': account_key}
        after = scan.get('after')
        # Keyset traversal is independent of source timestamps. New/edited rows
        # behind the cursor are revisited next cycle, not permanently excluded.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as source:
            source.execute('BEGIN')
            def batch(cursor: str | None) -> list[Any]:
                return source.execute(
                    'SELECT CAST(id AS TEXT),mp_id,title,url,content_html FROM articles '
                    f'WHERE mp_id IN ({placeholders}) AND has_content=1 '
                    'AND (? IS NULL OR CAST(id AS TEXT)>?) '
                    'ORDER BY CAST(id AS TEXT) COLLATE BINARY LIMIT ?',
                    (*active_accounts, cursor, cursor, BATCH_SIZE + 1)).fetchall()
            peek = (batch(after) if only_source_id is None else source.execute(
                'SELECT CAST(id AS TEXT),mp_id,title,url,content_html FROM articles '
                f'WHERE mp_id IN ({placeholders}) AND has_content=1 AND CAST(id AS TEXT)=?',
                (*active_accounts, only_source_id)).fetchall())
            if only_source_id is None and after is not None and not peek:
                scan = {'source_key': source_key, 'account_key': account_key}
                peek = batch(None)
            rows = peek[:BATCH_SIZE]
            last = rows[-1][0] if rows else None
            if len(peek) > BATCH_SIZE:
                result['remaining'] = source.execute(
                    f'SELECT COUNT(*) FROM articles WHERE mp_id IN ({placeholders}) AND has_content=1 AND CAST(id AS TEXT)>?',
                    (*active_accounts, last)).fetchone()[0]
        result['scanned'] = len(rows)
        for source_id, account_id, title, url, html in rows:
            if not source_id or not isinstance(title, str) or not isinstance(url, str) or not isinstance(html, str):
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
            if material is None:
                material = db.query(LearningMaterial).filter_by(url=url, account=active_accounts[account_id]).first()
                if material is not None:
                    material.source_id = source_id
            if material and material.fingerprint == fingerprint:
                result["skipped"] += 1
                continue
            try:
                payload = extract_material(html or "")
            except ContentError as exc:
                result["rejected"] += 1
                result[exc.reason] += 1
                continue
            if material is not None and material.url==url:
                latest=db.query(MaterialRevision).filter_by(material_id=material.id).order_by(MaterialRevision.revision.desc()).first()
                if latest and latest.source_html and 'js_content' in latest.source_html and 'js_content' not in html:
                    normalize=lambda value:re.sub(r'\s+','',value)
                    previous=normalize(''.join(p['text'] for p in material.payload.get('evidence',[])))
                    incoming=normalize(''.join(p['text'] for p in payload.get('evidence',[])))
                    observe(db, material.id, fingerprint, bool(incoming and incoming in previous))
                    # Even reordered/changed fragments must not replace a saved
                    # original. Differences queue a fresh original-page check.
                    result['skipped']+=1
                    continue
            if material is None:
                material = LearningMaterial(source_id=source_id)
                db.add(material)
                result["added"] += 1
            else:
                if not db.query(MaterialRevision.id).filter_by(material_id=material.id).first():
                    append_revision(db, material)
                result["updated"] += 1
            material.account = active_accounts[account_id]
            material.title, material.url = title, url
            material.fingerprint, material.payload = fingerprint, payload
            material.imported_at = datetime.now(timezone.utc).replace(tzinfo=None)
            append_revision(db, material, html)
        if only_source_id is None:
            scan['cycle_scanned'] = scan.get('cycle_scanned', 0) + result['scanned']
            scan['cycle_rejected'] = scan.get('cycle_rejected', 0) + result['rejected']
            scan['after'] = last if result['remaining'] else None
            scan['remaining'] = result['remaining']
            if not result['remaining']:
                scan.update(last_cycle_finished_at=now_iso(), last_cycle_scanned=scan['cycle_scanned'],
                            last_cycle_rejected=scan['cycle_rejected'], cycle_scanned=0, cycle_rejected=0)
            if checkpoint is None:
                db.add(AppState(key=SCAN_KEY, value=json.dumps(scan)))
            else:
                checkpoint.value = json.dumps(scan)
        # Material versions, associations and cursor advance commit together.
        db.commit()
    return result


def import_recovered(source_id: str) -> dict[str, int]:
    if not LOCK.acquire(blocking=False):
        raise ImportBusy('资料导入任务正在运行')
    try:
        return _import_materials(only_source_id=source_id)
    finally:
        LOCK.release()



def sync_materials() -> dict[str, int]:
    if not LOCK.acquire(blocking=False):
        raise ImportBusy("已有公众号导入任务在运行")
    try:
        write_status(state="running", started_at=now_iso(), finished_at=None, error=None, error_detail=None, result={})
        try:
            result = _import_materials()
        except (OSError, sqlite3.Error, ValueError) as exc:
            detail = ('not_configured' if not get_settings().werss_database_path else
                      'source_missing' if isinstance(exc, FileNotFoundError) else
                      'source_busy' if isinstance(exc, sqlite3.OperationalError) and
                      getattr(exc, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else
                      'source_schema' if isinstance(exc, sqlite3.OperationalError) and
                      ('no such table' in str(exc) or 'no such column' in str(exc)) else 'source_unreadable')
            write_status(state="failed", finished_at=now_iso(), error="source_unavailable", error_detail=detail)
            raise
        except Exception:
            write_status(state="failed", finished_at=now_iso(), error="import_failed")
            raise
        stamp = now_iso()
        state = ("partial" if result["rejected"] else "catching_up" if result['remaining'] else
                 "updated" if result["added"] + result["updated"] else "no_new_materials")
        changes: dict[str, Any] = dict(state=state, finished_at=stamp, result=result, error=None)
        if not result["rejected"]:
            changes["last_success_at"] = stamp
        if result["added"] + result["updated"]:
            changes["last_changed_at"] = stamp
        write_status(**changes)
        return result
    finally:
        LOCK.release()
