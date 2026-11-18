"""Opt-in local Docker operator, with durable receipts and no public mutation API.

One registered source per tick. An uncertain Docker completion blocks further
dispatch until that exact adapter run has a durable completion receipt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.material_sources import sources
from app.models import AppState
from app.werss_history import progress, execution_receipt

KEY = 'local_history_worker_v1'
LOCK = threading.Lock()
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'werss_history_collect.py'


def enabled() -> bool:
    return os.getenv('LOCAL_WERSS_HISTORY_WORKER_ENABLED') == '1' and bool(get_settings().werss_database_path)


def read() -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(AppState, KEY)
        try:
            value = json.loads(row.value) if row else {}
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}


def _write(value: dict[str, Any]) -> None:
    with SessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        row = db.get(AppState, KEY)
        if row:
            if json.loads(row.value).get('run_id') != value.get('run_id'):
                return
            row.value = json.dumps(value)
        else:
            db.add(AppState(key=KEY, value=json.dumps(value)))
        db.commit()


def _finished(receipt: dict, observed: dict) -> bool:
    if receipt.get('run_id') and receipt['run_id'] == observed.get('run_id') and observed.get('run_finished_at') and not observed.get('running'):
        return True
    operator=execution_receipt(str(receipt.get('run_id') or ''))
    return bool(operator.get('finished_at') and operator.get('source')==receipt.get('source'))


def status() -> dict[str, Any]:
    value = read()
    # Receipts are an allowlist, never subprocess output or upstream messages.
    return {'enabled': enabled(), **{k: value[k] for k in
            ('state', 'source', 'started_at', 'finished_at', 'error') if k in value}}


def eligible(entries: list[dict], observed: dict, attempts: dict, now: float) -> list[dict]:
    ready = []
    for source in entries:
        identity = source.get('mp_id')
        if not source.get('enabled') or not isinstance(identity, str) or not re.fullmatch(r'MP_WXS_\d+', identity):
            continue
        item = observed.get(identity, {})
        if item.get('running') or item.get('status') == 'verification_required':
            continue
        wait = 3600 if item.get('complete') else 300
        due = max(float(item.get('retry_after') or 0), float(item.get('checked_at') or 0) + wait,
                  float(attempts.get(identity, 0)) + wait)
        if due <= now:
            ready.append(source)
    # Rotate fairly; the configured study direction never authorizes other ids.
    return sorted(ready, key=lambda s: (attempts.get(s['mp_id'], 0),
                                        observed.get(s['mp_id'], {}).get('checked_at') or 0, s['key']))


def _claim(source: str, now: float) -> dict | None:
    """Serialize separate local app processes, including across service restarts."""
    with SessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        row = db.get(AppState, KEY)
        value = json.loads(row.value) if row else {}
        if value.get('state') in {'running', 'awaiting_confirmation'}:
            return None
        attempts = value.get('attempts', {})
        if now - float(attempts.get(source, 0)) < 300:
            return None
        attempts[source] = now
        receipt = dict(state='running', source=source, run_id=uuid.uuid4().hex,
                       started_at=now, attempts=attempts)
        if row:
            row.value = json.dumps(receipt)
        else:
            db.add(AppState(key=KEY, value=json.dumps(receipt)))
        db.commit()
        return receipt


def run() -> dict[str, Any]:
    if not enabled():
        return {'state': 'disabled'}
    if not LOCK.acquire(blocking=False):
        return {'state': 'busy'}
    try:
        before = read()
        observed = progress()
        if before.get('state') in {'running', 'awaiting_confirmation'}:
            if not _finished(before, observed.get(str(before.get('source') or ''), {})):
                return {'state': 'awaiting_confirmation'}
            _write({**before, 'state': 'observed_complete', 'finished_at': time.time()})
            before = read()
        docker = shutil.which('docker')
        if not docker:
            _write({**before, 'state': 'docker_unavailable', 'error': 'docker_cli_missing', 'finished_at': time.time()})
            return {'state': 'docker_unavailable'}
        candidates = eligible(sources(), observed, before.get('attempts', {}), time.time())
        if not candidates:
            return {'state': 'waiting'}
        receipt = _claim(candidates[0]['mp_id'], time.time())
        if not receipt:
            return {'state': 'busy'}
        command = [docker, 'exec', '-i', '-w', '/app', '-e',
                   'POLITICAL_HISTORY_RUN_ID=' + receipt['run_id'], 'political-news-werss',
                   '/app/env_x86_64/bin/python3', '-', receipt['source']]
        try:
            # No shell, arbitrary command, session token, or returned raw logs.
            result = subprocess.run(command, input=SCRIPT.read_text(encoding='utf-8'), encoding='utf-8',
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            after = progress().get(receipt['source'], {})
            if _finished(receipt, after):
                receipt.update(state='observed_complete', finished_at=time.time())
            else:
                # Even a nonzero exit can follow a completed container write.
                receipt.update(state='awaiting_confirmation', error='completion_unconfirmed')
            if result.returncode and receipt['state'] == 'observed_complete':
                receipt['error'] = 'operator_exit_nonzero'
        except subprocess.TimeoutExpired:
            receipt.update(state='awaiting_confirmation', error='docker_timeout')
        except OSError:
            receipt.update(state='awaiting_confirmation', error='docker_dispatch_unconfirmed')
        _write(receipt)
        return status()
    finally:
        LOCK.release()
