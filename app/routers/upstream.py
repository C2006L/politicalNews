from typing import Any

from fastapi import APIRouter, Header, HTTPException

from app import upstream
from app.review import require_token

router = APIRouter(tags=['上游通道检查'])


@router.post('/api/upstream/check', status_code=202)
def check_channel(x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    if not upstream.configured():
        raise HTTPException(503, '尚未配置本地 WeRSS 检查接口')
    if upstream.LOCK.locked():
        raise HTTPException(409, '已有通道检查在运行')
    current = upstream.status()
    if current['retry_after_seconds']:
        raise HTTPException(429, '请至少间隔一分钟再检查')
    try:
        started = upstream.start_check()
    except Exception:
        raise HTTPException(503, '未能启动通道检查，请稍后重试') from None
    if not started:
        raise HTTPException(409, '已有通道检查在运行')
    return {'state': 'running'}
