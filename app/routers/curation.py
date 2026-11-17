"""Visible content classification and version-bound manual corrections."""
from datetime import datetime, timezone
from html import escape
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.editorial import CATEGORIES, document, fingerprint, decision_map, select_document
from app.models import News, LearningMaterial, ContentDecision
from app.review import require_token
from app.routers.learning import layout
from app.news_data import source_label

router = APIRouter(tags=['时政精选'])
Kind = Literal['news', 'material']
Category = Literal['policy', 'report', 'analysis', 'textbook', 'exam_advice', 'promotion', 'uncertain']


def content(db: Session, kind: str, identity: int) -> Any:
    row = db.get(News if kind == 'news' else LearningMaterial, identity)
    if row is None:
        raise HTTPException(404, '原文不存在')
    return row


def inventory(kind: str, item: int | None, category: str | None, scope: str, page: int) -> dict[str, Any]:
    with SessionLocal() as db:
        query: Any = db.query(News if kind == 'news' else LearningMaterial)
        if item is not None:
            query = query.filter_by(id=item)
        query = query.order_by(News.published_at.desc(), News.id.desc()) if kind == 'news' else query.order_by(
            LearningMaterial.imported_at.desc(), LearningMaterial.id.desc())
        overrides = decision_map(db)
        entries = []
        counts = dict.fromkeys(CATEGORIES, 0)
        for row in query:
            result = select_document(row, overrides)
            counts[result.category] += 1
            if category and result.category != category or scope == 'featured' and not result.selected:
                continue
            data = document(row)
            entries.append({'id': row.id, 'kind': kind, 'title': row.title, 'source': source_label(data['source']) if kind == 'news' else data['source'],
                            'excerpt': data['text'][:350], 'fingerprint': fingerprint(row),
                            'url': f'/{"news" if kind == "news" else "learning"}/{row.id}',
                            **result.as_dict()})
        return {'total': len(entries), 'page': page, 'counts': counts, 'kind': kind,
                'items': entries[(page - 1) * 20:page * 20]}


@router.get('/api/curation')
def list_curation(kind: Kind = 'news', item: int | None = Query(None, ge=1), category: str | None = Query(None, max_length=30),
                  scope: Literal['all', 'featured'] = 'all', page: int = Query(1, ge=1)) -> dict[str, Any]:
    if category and category not in CATEGORIES:
        raise HTTPException(422, '未知内容类别')
    return inventory(kind, item, category, scope, page)


class Correction(BaseModel):
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_decision: int = Field(ge=0)
    category: Category | None = None
    selection: Literal['auto', 'include', 'exclude'] = 'auto'
    note: str = Field(default='', max_length=500)


@router.put('/api/curation/{kind}/{identity}')
def correct(kind: Kind, identity: int, change: Correction, request: Request,
            x_sync_token: str | None = Header(None), x_requested_with: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    origin = request.headers.get('origin')
    if x_requested_with != 'study-ui' or origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '请从本站分类页面操作')
    with SessionLocal() as db:
        # Serialize the version check and append; avoid silently overwriting a
        # correction saved by another tab or applying it to changed source text.
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = content(db, kind, identity)
        current_fingerprint = fingerprint(row)
        if current_fingerprint != change.fingerprint:
            raise HTTPException(409, '原文已变化，请重新查看分类依据')
        previous = db.query(ContentDecision).filter_by(kind=kind, content_id=identity,
                    fingerprint=current_fingerprint).order_by(ContentDecision.id.desc()).first()
        if (previous.id if previous else 0) != change.expected_decision:
            raise HTTPException(409, '分类已被其他操作调整，请重新加载')
        note = change.note.strip()
        if previous and (previous.category, previous.selection, previous.note) == (change.category, change.selection, note):
            return {'changed': False, 'decision_id': previous.id}
        entry = ContentDecision(kind=kind, content_id=identity, fingerprint=current_fingerprint,
                                category=change.category, selection=change.selection, note=note,
                                created_at=datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(entry)
        db.commit()
        return {'changed': True, 'decision_id': entry.id}


@router.get('/api/curation/{kind}/{identity}/history')
def history(kind: Kind, identity: int, page: int = Query(1, ge=1),
            x_sync_token: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    with SessionLocal() as db:
        current = fingerprint(content(db, kind, identity))
        query = db.query(ContentDecision).filter_by(kind=kind, content_id=identity)
        return {'total': query.count(), 'page': page, 'items': [
            {'id': row.id, 'category': row.category, 'selection': row.selection, 'note': row.note,
             'is_current': row.fingerprint == current, 'created_at': row.created_at.isoformat() + 'Z'}
            for row in query.order_by(ContentDecision.id.desc()).offset((page - 1) * 20).limit(20)]}


@router.get('/curation')
def curation_page(kind: Kind = 'news', item: int | None = Query(None, ge=1), category: str | None = Query(None, max_length=30),
                  scope: Literal['all', 'featured'] = 'all', page: int = Query(1, ge=1)) -> Any:
    base = escape(get_settings().base_path, quote=True)
    if category and category not in CATEGORIES:
        raise HTTPException(422, '未知内容类别')
    options = ''.join(f'<option value="{key}" {"selected" if category == key else ""}>{label}</option>' for key, label in CATEGORIES.items())
    return layout('时政分类与精选', f'''<a href="{base}/today">← 返回时政精选</a>
<div class="eyebrow">THE EDITOR'S DESK</div><h1>为什么选这篇？</h1>
<p>区分时政报道、政策和解读，背诵、备考建议与推广保留在全部资料。规则会误判，可查看依据并调整；分类不是新闻事实核验。</p>
<p>这里显示全库内容是否符合精选规则；首页再按资料日期、筛选与每天最多 10 组展示。人工纳入会优先排序。</p>
<form method="get" class="review-filters"><label>来源 <select name="kind"><option value="news">新闻</option><option value="material" {"selected" if kind == "material" else ""}>公众号</option></select></label>
<label>类别 <select name="category"><option value="">全部类别</option>{options}</select></label>
<label>范围 <select name="scope"><option value="all">全部内容</option><option value="featured" {"selected" if scope == "featured" else ""}>入选内容</option></select></label><button>查看</button></form>
<section data-curation data-base="{base}" data-kind="{kind}" data-item="{item or ''}" data-category="{category or ''}" data-scope="{scope}" data-page="{page}">
<details><summary>需要纠正分类时</summary><p>填写现有维护口令后，可调整全库分类或恢复规则判断。说明会展示在入选依据中；原文更正后旧调整保留记录，但不自动用于新内容。</p>
<p data-curation-access>正在检查本机维护设置…</p><label>维护口令 <input type="password" data-curation-token autocomplete="off" maxlength="128"></label>
<button type="button" data-curation-setup hidden>首次设置本机维护口令</button>
<p>与其他维护功能共用口令，不保存在浏览器；浏览和查看分类不需要填写。</p></details>
<p data-curation-message role="status" aria-live="polite"></p><p data-curation-count></p><div data-curation-list></div>
<p><button type="button" data-curation-prev>上一页</button> <span data-curation-page></span> <button type="button" data-curation-next>下一页</button></p>
</section><script src="{base}/static/curation.js" defer></script>''', active='today')
