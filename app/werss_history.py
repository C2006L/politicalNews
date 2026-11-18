"""Read-only, credential-free progress from the deployed WeRSS history adapter."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import re
from typing import Any

from app.config import get_settings


def execution_receipt(run_id: str) -> dict[str, Any]:
    """Independent operator completion, including failures before adapter entry."""
    source = get_settings().werss_database_path
    if not source or not re.fullmatch(r'[0-9a-f]{32}',run_id):
        return {}
    path = Path(source).resolve().parent / 'political-news-history.sqlite3'
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            row=db.execute('SELECT mp_id,finished,state,code FROM operator_runs WHERE run_id=?',(run_id,)).fetchone()
        return dict(source=row[0],finished_at=row[1],state=row[2],code=row[3]) if row else {}
    except (OSError,sqlite3.Error):
        return {}


def progress() -> dict[str, dict[str, Any]]:
    source = get_settings().werss_database_path
    if not source:
        return {}
    path = Path(source).resolve().parent / 'political-news-history.sqlite3'
    if not path.is_file():
        return {}
    entries = {}
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            for mp_id, raw, lease, expires in db.execute('SELECT mp_id,payload,lease,expires FROM progress'):
                item = json.loads(raw)
                entries[mp_id] = {k: item.get(k) for k in (
                    'adapter_version', 'status', 'checked_at', 'window_start', 'complete',
                    'offset', 'list_code', 'latest_fallback', 'saved_this_batch', 'end_reason', 'retry_after',
                    'last_list_success_at', 'last_body_success_at', 'last_batch_success_at', 'last_window_scan_at',
                    'run_id', 'run_started_at', 'run_finished_at')}
                entries[mp_id].update(discovered=len(item.get('discovered_ids', [])),
                                      pending_bodies=len(item.get('body_pending', [])),
                                      running=bool(lease and expires and expires > time.time()))
                if item.get('list_code') == -2012:
                    entries[mp_id]['status'] = 'authorization_or_limit'
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        return {}
    return entries
