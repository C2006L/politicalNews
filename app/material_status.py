"""Persistent import diagnostics; never expose credentials, paths or exception text."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.database import SessionLocal
from app.models import AppState

KEY = "learning_import_status"


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
    from app.materials import ACCOUNTS
    configured = get_settings().werss_database_path
    acquisition: dict[str, Any] = {
        "state": "unknown", "authorization": "unknown", "last_check_verified": False,
        "coverage": "latest_only_not_complete", "accounts": [],
        "note": "本地文章库不能证明采集器当前在线或授权有效；没有新入库不等于公众号没有更新。",
    }
    if not configured:
        acquisition["state"] = "not_configured"
    else:
        try:
            path = Path(configured).resolve(strict=True)
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                for account_id, name in ACCOUNTS.items():
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
    return {"acquisition": acquisition, "import": read_status()}
