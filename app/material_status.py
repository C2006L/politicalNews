"""Persistent import diagnostics; never expose credentials, paths or exception text."""
from __future__ import annotations
from app.material_sources import accounts as get_accounts

import json
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.database import SessionLocal
from app.models import AppState

KEY = "learning_import_status"


def parse_scan(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict) or (value.get('after') is not None and not isinstance(value['after'], str)):
        return {}
    for key in ('remaining', 'cycle_scanned', 'cycle_rejected', 'last_cycle_scanned', 'last_cycle_rejected'):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            return {}
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_status() -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(AppState, KEY)
        return json.loads(row.value) if row else {"state": "not_run"}


def write_status(**changes: Any) -> None:
    with SessionLocal() as db:
        row = db.get(AppState, KEY)
        data = json.loads(row.value) if row else {}
        data.update(changes)
        if row is None:
            row = AppState(key=KEY)
            db.add(row)
        row.value = json.dumps(data, ensure_ascii=False)
        db.commit()


def recover_import() -> None:
    if read_status().get("state") == "running":
        write_status(state="interrupted", finished_at=now_iso(), error="interrupted")


def pipeline_status() -> dict[str, Any]:
    from app.materials import SCAN_KEY, LOCK
    configured = get_settings().werss_database_path
    acquisition: dict[str, Any] = {
        "state": "unknown", "authorization": "unknown", "last_check_verified": False,
        "coverage": "latest_only_not_complete", "accounts": [],
        "note": "本地文章库不能证明采集器当前在线或授权有效；没有新入库不等于公众号没有更新。",
    }
    acquisition['storage_mode'] = 'exported_snapshot' if get_settings().werss_source_is_snapshot else 'direct_read_only'
    acquisition['snapshot_age_seconds'] = None
    if not configured:
        acquisition["state"] = "not_configured"
    else:
        try:
            path = Path(configured).resolve(strict=True)
            if get_settings().werss_source_is_snapshot:
                acquisition['snapshot_age_seconds'] = max(0, int(datetime.now(timezone.utc).timestamp() - path.stat().st_mtime))
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                for account_id, name in get_accounts().items():
                    row = db.execute(
                        "SELECT COUNT(*),SUM(CASE WHEN has_content=1 AND length(content_html)>0 THEN 1 ELSE 0 END) "
                        "FROM articles WHERE mp_id=?", (account_id,),
                    ).fetchone()
                    acquisition["accounts"].append({"name": name, "articles": row[0],
                                                    "with_text": row[1] or 0,
                                                    "awaiting_text": row[0] - (row[1] or 0)})
            acquisition["database_readable"] = True
        except (OSError, sqlite3.Error):
            acquisition["state"] = "source_unavailable"
            acquisition["database_readable"] = False
    with SessionLocal() as db:
        row = db.get(AppState, SCAN_KEY)
        saved = parse_scan(row.value if row else None)
    try:
        source_key = hashlib.sha256(str(Path(configured).resolve()).encode()).hexdigest() if configured else None
    except (OSError, RuntimeError):
        source_key = None
    account_key = hashlib.sha256(json.dumps(get_accounts(), sort_keys=True).encode()).hexdigest()
    if source_key is None or saved.get('source_key') != source_key or saved.get('account_key') != account_key:
        saved = {}
    scan = {key: saved.get(key) for key in ('remaining', 'cycle_scanned', 'cycle_rejected',
            'last_cycle_finished_at', 'last_cycle_scanned', 'last_cycle_rejected')}
    imported = read_status()
    if imported.get('state') == 'running' and not LOCK.locked():
        imported = {**imported, 'state': 'interrupted', 'error': 'interrupted'}
    from app.upstream import status as upstream_status
    from app.body_recovery import status as recovery_status
    from app.source_coverage import coverage_report
    return {"acquisition": acquisition, "import": imported, 'scan': scan, 'upstream_check': upstream_status(),
            'body_recovery': recovery_status(), 'coverage_report': coverage_report()}
