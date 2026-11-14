from html import escape
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request, HTTPException, Body
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.database import SessionLocal
from app.models import RelationReviewEvent
from app.review import require_token, queue_items, password_configured, set_initial_password
from app.routers.learning import layout

router = APIRouter(tags=['关联审核'])


def local_setup_allowed(request: Request) -> bool:
    origin = request.headers.get('origin')
    return bool(request.client and request.client.host in {'127.0.0.1', '::1'}
                and request.url.hostname in {'127.0.0.1', 'localhost', '::1'}
                and not request.headers.get('x-forwarded-for')
                and (not origin or origin.rstrip('/') == str(request.base_url).rstrip('/')))


@router.get('/api/review/access')
def review_access(request: Request) -> dict[str, Any]:
    configured = bool(get_settings().sync_admin_token) or password_configured()
    return {'configured': configured, 'can_setup': not configured and local_setup_allowed(request)}


@router.post('/api/review/access', status_code=201)
def initialize_access(request: Request, password: str = Body(..., embed=True, min_length=10, max_length=128),
                      x_requested_with: str | None = Header(None)) -> dict[str, bool]:
    if not local_setup_allowed(request) or x_requested_with != 'study-ui':
        raise HTTPException(403, '首次设置管理口令只能在本机页面操作')
    set_initial_password(password)
    return {'configured': True}


@router.get('/api/review/queue')
def review_queue(kind: Literal['all', 'material', 'news'] = 'all',
                 status: Literal['candidate', 'confirmed', 'rejected'] = 'candidate',
                 page: int = Query(1, ge=1), x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    with SessionLocal() as db:
        return queue_items(db, kind, status, page)


@router.get('/api/review/events')
def review_events(page: int = Query(1, ge=1), x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    with SessionLocal() as db:
        query = db.query(RelationReviewEvent)
        total = query.count()
        events = query.order_by(RelationReviewEvent.id.desc()).offset((page - 1) * 20).limit(20).all()
        return {'total': total, 'page': page, 'items': [
            {'id': event.id, 'kind': event.kind, 'link_id': event.link_id, 'point_id': event.point_id,
             'previous': event.previous, 'status': event.status, 'note': event.note,
             'created_at': event.created_at.isoformat() + 'Z'} for event in events]}


@router.get('/review', response_class=HTMLResponse)
def review_page() -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    return layout('关联审核', f'''<a href="{base}/knowledge">← 考点索引</a>
<div class="eyebrow">CHECK THE EVIDENCE</div><h1>核对关联，保留依据。</h1>
<p class="learning-notice">审核只确认资料或新闻与考点是否相关，不代表原文答案已核对教材。只处理当前版本；更正后的新关联需要重新核对。</p>
<section data-review data-base="{base}">
<form data-review-login><label>管理口令 <input type="password" data-review-token autocomplete="off" maxlength="128" required></label>
<button type="submit" data-review-submit disabled>正在检查管理设置…</button></form>
<p data-review-access-note>口令仅用于当前页面请求，不存入浏览器；离开页面后需重新填写。</p>
<p data-review-message role="status" aria-live="polite"></p>
<div class="review-filters"><label>内容 <select data-review-kind><option value="all">全部来源</option><option value="material">公众号资料</option><option value="news">新闻</option></select></label>
<label>关系状态 <select data-review-status><option value="candidate">待确认</option><option value="confirmed">已确认</option><option value="rejected">已排除</option></select></label></div>
<p data-review-count></p><div data-review-list></div>
<p><button data-review-prev type="button" disabled>上一页</button> <span data-review-page></span> <button data-review-next type="button" disabled>下一页</button></p>
<details><summary>审核操作记录</summary><p>记录从此功能启用后产生，不追溯补写更早的操作；当前为共享管理口令，不能区分不同操作人。</p>
<div data-review-events></div><p><button type="button" data-events-prev disabled>上一页记录</button> <span data-events-page></span> <button type="button" data-events-next disabled>下一页记录</button></p></details>
</section><script src="{base}/static/review.js" defer></script>''')
