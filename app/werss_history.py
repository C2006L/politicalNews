"""Read-only, credential-free progress from the deployed WeRSS history adapter."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

from app.config import get_settings


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
            for mp_id, raw in db.execute('SELECT mp_id,payload FROM progress'):
                item = json.loads(raw)
                entries[mp_id] = {k: item.get(k) for k in (
                    'adapter_version', 'status', 'checked_at', 'window_start', 'complete',
                    'offset', 'list_code', 'latest_fallback', 'saved_this_batch', 'end_reason', 'retry_after')}
                entries[mp_id].update(discovered=len(item.get('discovered_ids', [])),
                                      pending_bodies=len(item.get('body_pending', [])))
                if item.get('list_code') == -2012:
                    entries[mp_id]['status'] = 'authorization_or_limit'
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        return {}
    return entries
