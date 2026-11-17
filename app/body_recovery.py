"""Bounded, persistent recovery of known missing bodies. Upstream DB is read-only."""
from __future__ import annotations
from app.material_sources import accounts as get_accounts

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4, UUID

from sqlalchemy import func

from app.config import get_settings
from app.database import SessionLocal
from app.materials import import_recovered, ImportBusy
from app.models import BodyRecovery
from app.upstream import service_request, ProbeFailure

LOCK = threading.Lock()
LAST_RUN = 0.0
ACTIVE = ('pending', 'submitting', 'queued', 'running', 'verifying', 'unknown')
RETRY_SECONDS = 600


class RecoveryError(Exception):
    def __init__(self, code: str, status_code: int = 503):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def configured() -> bool:
    settings = get_settings()
    return bool(settings.werss_api_url and settings.werss_recovery_token_file and settings.werss_database_path)


def context_key() -> str:
    settings = get_settings()
    try:
        path = str(Path(settings.werss_database_path).resolve())
    except (OSError, RuntimeError, ValueError):
        path = settings.werss_database_path
    return hashlib.sha256((settings.werss_api_url + '\n' + path).encode()).hexdigest()


def source_rows(source_id: str | None = None, missing_only: bool = False) -> list[dict[str, Any]]:
    settings = get_settings()
    active_accounts = get_accounts()
    if not active_accounts:
        return []
    placeholders = ','.join('?' for _ in active_accounts)
    if not settings.werss_database_path:
        raise RecoveryError('source_unavailable')
    try:
        path = Path(settings.werss_database_path).resolve(strict=True)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            rows = db.execute('SELECT CAST(id AS TEXT),mp_id,title,url,content_html,has_content FROM articles '
                              f'WHERE mp_id IN ({placeholders}) AND (? IS NULL OR CAST(id AS TEXT)=?) '
                              "AND (?=0 OR COALESCE(has_content,0)!=1 OR content_html IS NULL OR trim(content_html)='')",
                              (*active_accounts, source_id, source_id, int(missing_only))).fetchall()
    except (OSError, sqlite3.Error, ValueError, RuntimeError):
        raise RecoveryError('source_unavailable') from None
    return [dict(source_id=row[0], account_id=row[1], title=row[2], url=row[3], html=row[4], has_content=row[5]) for row in rows]


def eligible(row: dict[str, Any]) -> bool:
    identity = row['source_id']
    try:
        parsed = urlparse(row['url']) if isinstance(row['url'], str) else None
    except ValueError:
        return False
    return bool(isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.~-]{0,249}', identity)
                and row['account_id'] in get_accounts() and isinstance(row['title'], str)
                and parsed and parsed.scheme == 'https' and parsed.netloc == 'mp.weixin.qq.com')


def missing(row: dict[str, Any]) -> bool:
    return row['has_content'] != 1 or not isinstance(row['html'], str) or not row['html'].strip()


def retry_after(attempt: BodyRecovery) -> int:
    return max(0, min(RETRY_SECONDS, math.ceil(RETRY_SECONDS - (now() - (attempt.submitted_at or attempt.created_at)).total_seconds())))


def enqueue(retry_id: str | None = None) -> list[str]:
    if not configured():
        raise RecoveryError('not_configured')
    if LOCK.locked():
        raise RecoveryError('worker_busy', 409)
    key = context_key()
    available = [row for row in source_rows(missing_only=True) if eligible(row) and missing(row)]
    with SessionLocal() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        attempts = db.query(BodyRecovery).filter_by(source_key=key).order_by(BodyRecovery.created_at.desc()).all()
        latest: dict[str, BodyRecovery] = {}
        for attempt in attempts:
            latest.setdefault(attempt.source_id, attempt)
        if retry_id:
            old = db.get(BodyRecovery, retry_id)
            if old is None or old.source_key != key:
                raise RecoveryError('attempt_missing', 404)
            if latest.get(old.source_id) is not old:
                raise RecoveryError('not_latest_attempt', 409)
            if old.state not in ('failed', 'invalid_content', 'unknown') or retry_after(old):
                raise RecoveryError('not_retryable', 409)
            available = [row for row in available if row['source_id'] == old.source_id and row['account_id'] == old.account_id]
            if not available:
                raise RecoveryError('source_no_longer_missing', 409)
            old.state, old.updated_at = 'superseded', now()
        active = {attempt.source_id for attempt in latest.values() if attempt.state in ACTIVE}
        capacity = 3 - len(active)
        if capacity <= 0:
            raise RecoveryError('queue_full', 409)
        available.sort(key=lambda row: (latest[row['source_id']].created_at if row['source_id'] in latest else datetime.min, row['source_id']))
        created = []
        for row in available:
            old = latest.get(row['source_id'])
            if row['source_id'] in active or (old and retry_after(old)):
                continue
            if old and old.state == 'unknown' and not retry_id:
                continue
            identity = str(uuid4())
            db.add(BodyRecovery(id=identity, source_key=key, source_id=row['source_id'], account_id=row['account_id'],
                                title=row['title'], state='pending', created_at=now(), updated_at=now()))
            created.append(identity)
            if len(created) >= capacity:
                break
        db.commit()
    return created


def update(identity: str, **changes: Any) -> None:
    with SessionLocal() as db:
        row = db.get(BodyRecovery, identity)
        if row:
            for key, value in changes.items():
                setattr(row, key, value)
            row.updated_at = now()
            db.commit()


def recover_interrupted() -> None:
    with SessionLocal() as db:
        for row in db.query(BodyRecovery).filter_by(state='submitting').all():
            row.state, row.error, row.updated_at = 'unknown', 'submission_unconfirmed', now()
        db.commit()


def process(identity: str) -> None:
    with SessionLocal() as db:
        attempt = db.get(BodyRecovery, identity)
        if attempt is None or attempt.source_key != context_key() or attempt.state not in ACTIVE:
            return
        source_id, account_id, state, task_id = attempt.source_id, attempt.account_id, attempt.state, attempt.task_id
        age = (now() - (attempt.submitted_at or attempt.created_at)).total_seconds()
    try:
        rows = source_rows(source_id)
        if not rows or not eligible(rows[0]) or rows[0]['account_id'] != account_id:
            update(identity, state='failed', error='source_identity_changed')
            return
        if not missing(rows[0]):
            try:
                result = import_recovered(source_id)
            except ImportBusy:
                update(identity, state='verifying', error='import_busy')
                return
            if result['rejected']:
                update(identity, state='invalid_content', error='content_rejected')
            elif result['added'] + result['updated'] + result['skipped']:
                update(identity, state='completed', error=None)
            else:
                update(identity, state='verifying', error='body_not_visible')
            return
        if state == 'pending':
            # Persist the uncertain boundary BEFORE the non-idempotent HTTP call.
            update(identity, state='submitting', submitted_at=now(), error=None)
            payload = service_request(f'/articles/{source_id}/refresh', recovery=True)
            data = payload.get('data')
            if type(payload.get('code')) is not int or payload.get('code') != 0 or not isinstance(data, dict) or data.get('article_id') != source_id:
                update(identity, state='unknown', error='submission_unconfirmed')
                return
            task = data.get('task_id')
            try:
                valid = isinstance(task, str) and str(UUID(task)) == task
            except ValueError:
                valid = False
            if not valid:
                update(identity, state='unknown', error='submission_unconfirmed')
                return
            update(identity, state='queued', task_id=task, error=None)
            return
        if state == 'submitting':
            update(identity, state='unknown', error='submission_unconfirmed')
            return
        if state == 'unknown':
            return
        if age >= RETRY_SECONDS:
            update(identity, state='unknown', error='task_timeout')
            return
        if state == 'verifying' or not task_id:
            return
        payload = service_request(f'/articles/refresh/tasks/{task_id}', method='GET', recovery=True)
        data = payload.get('data')
        if type(payload.get('code')) is not int or payload.get('code') != 0 or not isinstance(data, dict) or data.get('article_id') != source_id or data.get('task_id') != task_id:
            update(identity, state='unknown', error='task_unconfirmed')
            return
        remote = data.get('status')
        if remote == 'failed':
            update(identity, state='failed', error='upstream_failed')
        elif remote == 'success':
            update(identity, state='verifying', error='body_not_visible')
        elif remote in ('pending', 'running'):
            update(identity, state='running', error=None)
        else:
            update(identity, state='unknown', error='task_unconfirmed')
    except ProbeFailure as exc:
        if state == 'pending':
            # The server may have accepted a request even when the response was lost.
            rejected = exc.category in {'invalid_configuration','service_credential_missing','service_auth_required','resource_missing','incompatible_service'}
            update(identity, state='failed' if rejected else 'unknown', error=exc.category)
        elif exc.category == 'resource_missing':
            update(identity, state='unknown', error='task_unconfirmed')
        else:
            update(identity, error=exc.category)
    except RecoveryError as exc:
        update(identity, error=exc.code)
    except Exception:
        # Do not repeat a potentially accepted submission after storage/transport errors.
        update(identity, state='unknown', error='local_observation_failed')


def run(_lock_owned: bool = False) -> None:
    global LAST_RUN
    if not _lock_owned and not LOCK.acquire(blocking=False):
        return
    try:
        LAST_RUN = time.monotonic()
        if not configured():
            return
        with SessionLocal() as db:
            rows = db.query(BodyRecovery).filter(BodyRecovery.source_key == context_key(), BodyRecovery.state.in_(ACTIVE)).order_by(BodyRecovery.created_at).all()
            identities = [row.id for row in rows if row.state != 'pending']
        for identity in identities:
            process(identity)
        with SessionLocal() as db:
            current = db.query(BodyRecovery).filter(BodyRecovery.source_key == context_key(), BodyRecovery.state.in_(ACTIVE)).all()
            occupied = any(row.state not in ('pending', 'unknown') or (row.state == 'unknown' and retry_after(row)) for row in current)
            recent = db.query(func.max(BodyRecovery.submitted_at)).filter_by(source_key=context_key()).scalar()
            if occupied or (recent and (now() - recent).total_seconds() < 30):
                return
            pending = min((row for row in current if row.state == 'pending'), key=lambda row: row.created_at, default=None)
            pending_id = pending.id if pending else None
        if pending_id:
            process(pending_id)
    finally:
        LOCK.release()


def start() -> bool:
    if time.monotonic() - LAST_RUN < 10 or not LOCK.acquire(blocking=False):
        return False
    try:
        threading.Thread(target=run, kwargs={'_lock_owned': True}, daemon=True).start()
        return True
    except Exception:
        LOCK.release()
        raise


def status(page: int = 1) -> dict[str, Any]:
    source_error = None
    try:
        missing_count = sum(eligible(row) and missing(row) for row in source_rows(missing_only=True))
    except RecoveryError as exc:
        missing_count, source_error = None, exc.code
    with SessionLocal() as db:
        query = db.query(BodyRecovery).filter_by(source_key=context_key())
        total = query.count()
        active = query.filter(BodyRecovery.state.in_(ACTIVE)).count()
        rows = query.order_by(BodyRecovery.created_at.desc()).offset((page - 1) * 20).limit(20).all()
        items = [{'id': row.id, 'title': row.title, 'account': get_accounts().get(row.account_id, '未知来源'),
                  'state': row.state, 'error': row.error, 'created_at': row.created_at.isoformat() + 'Z',
                  'updated_at': row.updated_at.isoformat() + 'Z', 'retry_after_seconds': retry_after(row),
                  'can_retry': row.state in ('failed', 'invalid_content', 'unknown') and not retry_after(row)} for row in rows]
    return {'configured': configured(), 'missing_count': missing_count, 'source_error': source_error,
            'automatic': get_settings().scheduler_enabled and configured(),
            'active_count': active, 'total': total, 'page': page, 'items': items,
            'worker_running': LOCK.locked(), 'check_after_seconds': max(0, math.ceil(10 - (time.monotonic() - LAST_RUN)))}
