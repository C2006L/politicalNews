from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from app import body_recovery
from app.review import require_token

router = APIRouter(tags=['正文恢复'])


def error(exc: body_recovery.RecoveryError) -> HTTPException:
    return HTTPException(exc.status_code, exc.code)


@router.get('/api/body-recovery/status')
def recovery_status(page: int = Query(1, ge=1)) -> dict[str, Any]:
    return body_recovery.status(page)


@router.post('/api/body-recovery/start', status_code=202)
def recover_bodies(x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    try:
        ids = body_recovery.enqueue()
    except body_recovery.RecoveryError as exc:
        raise error(exc) from None
    try:
        started = body_recovery.start() if ids else False
    except Exception:
        started = False
    return {'accepted': len(ids), 'worker_started': started}


@router.post('/api/body-recovery/check', status_code=202)
def check_bodies(x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    if not body_recovery.configured():
        raise HTTPException(503, 'not_configured')
    try:
        started = body_recovery.start()
    except Exception:
        raise HTTPException(503, 'worker_unavailable') from None
    if not started:
        raise HTTPException(429, '请稍后再次检查恢复结果')
    return {'state': 'checking'}


@router.post('/api/body-recovery/{identity}/retry', status_code=202)
def retry_body(identity: UUID, x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    try:
        ids = body_recovery.enqueue(str(identity))
    except body_recovery.RecoveryError as exc:
        raise error(exc) from None
    try:
        started = body_recovery.start() if ids else False
    except Exception:
        started = False
    return {'accepted': len(ids), 'worker_started': started}
