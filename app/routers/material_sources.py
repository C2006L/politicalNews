from html import escape
import json
from typing import Any, Literal
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from app.config import get_settings
from app.database import SessionLocal
from app.models import AppState, LearningMaterial
from app.material_sources import KEY, GROUPS, sources
from app.review import require_token
from app.routers.learning import layout
from app.material_status import pipeline_status

router = APIRouter(tags=['公众号来源'])


@router.get('/api/material-sources')
def inventory() -> dict[str, Any]:
    entries = sources()
    with SessionLocal() as db:
        for source in entries:
            source['imported'] = db.query(LearningMaterial).filter_by(account=source['name']).count()
            source['state'] = '已绑定并启用' if source['enabled'] and source['mp_id'] else '已绑定，暂停导入' if source['mp_id'] else '待核对账号身份'
    return {'items': entries, 'note': '绑定名单只控制本站导入，不代替上游订阅，也不表示历史文章完整。'}


class Change(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    mp_id: str | None = Field(None, pattern=r'^MP_WXS_[0-9]{3,20}$')
    group: Literal['postgrad', 'civil', 'shared']
    region: str = Field(min_length=1, max_length=30)
    enabled: bool
    identity_checked: bool = False


@router.put('/api/material-sources/{key}')
def configure(key: str, change: Change, request: Request, x_sync_token: str | None = Header(None),
              x_requested_with: str | None = Header(None)) -> dict[str, Any]:
    require_token(x_sync_token)
    origin = request.headers.get('origin')
    if x_requested_with != 'study-ui' or origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '请从本机来源管理页面修改')
    current = next((row for row in sources() if row['key'] == key), None)
    if not current:
        raise HTTPException(404, '来源不存在')
    if change.enabled and not change.mp_id:
        raise HTTPException(422, '先核对并绑定准确账号ID，才能启用')
    if change.mp_id and (change.mp_id != current['mp_id'] or change.name != current['name']) and not change.identity_checked:
        raise HTTPException(422, '请先对照原文核对账号身份')
    if any(s['key'] != key and (s['name'] == change.name or change.mp_id and s['mp_id'] == change.mp_id) for s in sources()):
        raise HTTPException(409, '名称或账号ID已绑定其他来源')
    with SessionLocal() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(AppState, KEY)
        changes = json.loads(row.value) if row else {}
        changes[key] = change.model_dump(exclude={'identity_checked'})
        if row:
            row.value = json.dumps(changes, ensure_ascii=False)
        else:
            db.add(AppState(key=KEY, value=json.dumps(changes, ensure_ascii=False)))
        db.commit()
    return {'saved': True, 'note': '本站名单已更新；请另行确认上游订阅和正文获取。'}


@router.get('/material-sources')
def sources_page() -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    data = inventory()
    acquisition = pipeline_status()['acquisition']
    observed = {r['name']: r for r in acquisition['accounts']}
    body = '<div class="eyebrow">SOURCE DESK</div><h1>公众号来源</h1><p>先选来源，再按文章用途分流。山东来源标注地域，不限制全国内容。</p>'
    body += f'<p><a href="{base}/learning">返回公众号研读 →</a> · <a href="{base}/learning-import">检查采集与正文 →</a></p>'
    body += '<p class="learning-notice">待绑定来源不会参与导入。绑定ID需先对照公众号原文，本站配置不会自动创建WeRSS订阅；已导入数量也不代表完整历史。</p>'
    body += '<label>本机维护口令 <input type="password" data-source-token autocomplete="off"></label>'
    for row in data['items']:
        body += f'<article class="learning-entry"><h2>{escape(row["name"])}</h2><p>{GROUPS[row["group"]]} · {escape(row["region"])} · {row["state"]} · 本站已导入 {row["imported"]} 篇</p>'
        count = observed.get(row['name'])
        if count:
            body += f'<p>上游快照 {count["articles"]} 篇，有文字 {count["with_text"]} 篇；快照不证明实时在线。</p>'
        body += f'<details><summary>核对与配置</summary><form data-source-config data-key="{row["key"]}" data-base="{base}">'
        body += f'<label>准确账号名称 <input name="name" value="{escape(row["name"], quote=True)}" required maxlength="60"></label> <label>WeRSS账号ID <input name="mp_id" value="{escape(row["mp_id"] or "", quote=True)}" placeholder="MP_WXS_数字"></label>'
        body += '<label>默认分组 <select name="group">'+''.join(f'<option value="{k}" {"selected" if k == row["group"] else ""}>{v}</option>' for k,v in GROUPS.items())+'</select></label>'
        body += f'<label>地域 <input name="region" value="{escape(row["region"], quote=True)}" required maxlength="30"></label><p><label><input type="checkbox" name="enabled" {"checked" if row["enabled"] else ""}> 启用本站导入</label></p><p><label><input type="checkbox" name="identity_checked"> 已对照原文确认账号名称与ID</label></p><button>保存本站配置</button><p data-source-message role="status"></p></form></details></article>'
    return layout('公众号来源', body+f'<script src="{base}/static/material_sources.js" defer></script>')
