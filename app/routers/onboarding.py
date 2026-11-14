"""Explicit first-use acquisition with visible progress and retry."""
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.database import SessionLocal
from app.models import News, LearningMaterial
from app.routers.learning import layout
from app.sync_service import get_sync_status, start_background_sync
from app import sync_service

router = APIRouter(tags=['首次使用'])


@router.get('/api/getting-started')
def initial_state() -> dict[str, Any]:
    with SessionLocal() as db:
        count = db.query(News).count()
        materials = db.query(LearningMaterial).count()
    sync = get_sync_status()
    state = 'ready' if count else 'empty'
    if sync['in_progress']:
        state = 'running' if sync_service.SYNC_LOCK.locked() else 'interrupted'
    elif not count and sync['finished_at']:
        state = 'no_content'
    return {'state': state, 'news_count': count, 'material_count': materials,
            'token_required': bool(get_settings().sync_admin_token),
            'finished_at': sync['finished_at']}


@router.post('/api/getting-started/news', status_code=202)
def acquire_news(request: Request, x_sync_token: str | None = Header(None),
                 x_requested_with: str | None = Header(None)) -> dict[str, str]:
    # Header-only mutations: cross-site forms cannot trigger acquisition.
    if x_requested_with != 'study-ui':
        raise HTTPException(403, '请从资料获取页面操作')
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '不允许跨站操作')
    required = get_settings().sync_admin_token
    if required and not secrets.compare_digest(x_sync_token or '', required):
        raise HTTPException(403, '请填写正确的管理令牌')
    try:
        started = start_background_sync('获取近期新闻', months=1, max_pages=2, max_items=40)
    except Exception:
        raise HTTPException(503, '未能启动获取任务，请稍后重试') from None
    if not started:
        raise HTTPException(409, '已有获取任务在运行')
    return {'state': 'running'}


@router.get('/getting-started')
def getting_started() -> HTMLResponse:
    from html import escape
    base = escape(get_settings().base_path, quote=True)
    return layout('获取学习资料', f'''<div class="eyebrow">START READING</div>
<h1>先准备好学习资料</h1>
<p>先获取近期新闻，再开始阅读。公众号资料可以稍后接入。</p>
<section class="learning-entry" data-onboarding data-base="{base}">
<h2>获取近期新闻</h2><p data-setup-count>正在检查资料库…</p>
<p data-setup-state role="status" aria-live="polite"></p>
<label data-setup-auth hidden>管理令牌 <input data-setup-token type="password" autocomplete="off"></label>
<p><button type="button" data-setup-start disabled>获取近期新闻</button>
<a href="{base}/today" data-setup-read hidden>开始阅读 →</a></p>
<p>获取任务在后台运行，可离开本页；回来后会继续显示状态。没有取得内容时可以重试，不会把空结果当作准备完成。</p>
</section><section class="learning-entry"><h2>接入公众号资料</h2>
<p>肖秀荣、徐涛的公众号由 WeRSS 获取。新闻准备完成不代表公众号已连接；查看同步状态，确认是否取得正文。</p>
<p><a href="{base}/status">查看采集与公众号状态 →</a> · <a href="{base}/learning">查看已有公众号资料 →</a></p>
</section><script src="{base}/static/onboarding.js" defer></script>''')
