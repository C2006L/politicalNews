from html import escape
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import HTMLResponse
from sqlalchemy import func

from app.config import get_settings
from app.database import SessionLocal
from app.models import MaterialRevision
from app.routers.learning import find_material, layout

router = APIRouter(tags=['个人复习'])


@router.get('/api/practice/versions')
def latest_versions() -> dict[str, Any]:
    with SessionLocal() as db:
        return {'versions': {str(material): version for material, version in db.query(
            MaterialRevision.material_id, func.max(MaterialRevision.revision)).group_by(MaterialRevision.material_id).all()}}


@router.get('/api/practice/card/{material_id}/{revision}/{kind}/{index}')
def get_card(material_id: int, revision: int, kind: Literal['questions', 'points'],
             index: int = Path(..., ge=0)) -> dict[str, Any]:
    material = find_material(material_id, revision)
    items = material.get(kind, [])
    if index >= len(items):
        raise HTTPException(404, '该版本中没有这张复习卡')
    item = items[index]
    refs = set(item.get('evidence', []))
    evidence = [line for line in material['evidence'] if line['id'] in refs]
    if not refs or len(evidence) != len(refs):
        raise HTTPException(422, '原文依据不足，暂不能加入复习')
    section = re.match(r'专题[一二三四五六七八九十百零\d]+', item.get('section', ''))
    cue = item.get('text', '').split('：', 1)[0] if '：' in item.get('text', '') else ''
    if len(cue) > 50:
        cue = ''
    recall = f'回想原文提法：{cue or "本条重点"}（{section.group() if section else "原文"} · 第 {index + 1} 条）'
    return {'key': f'material:{material_id}:r{revision}:{kind}:{index}', 'material_id': material_id,
            'revision': revision, 'kind': kind, 'index': index, 'title': material['title'],
            'account': material['account'], 'question': item['question'] if kind == 'questions' else
            recall,
            'answer': item['answer'] if kind == 'questions' else item['text'],
            'needs_review': item.get('needs_review', False), 'evidence': evidence,
            'starred_by_author': item.get('starred_by_author', False),
            'url': f'/learning/{material_id}?revision={revision}#e-{evidence[0]["id"]}'}


@router.get('/practice', response_class=HTMLResponse)
def practice_page() -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    return layout('我的复习', f'''<div class="eyebrow">RECALL & RETURN</div><h1>先回想，再看答案。</h1>
<p class="learning-notice">复习清单与回想记录保存在当前浏览器；与收藏、已读分开。清除本网站的浏览器数据会清除这些记录。按自己的回想情况安排下次复习，不推断考试成绩或永久掌握。</p>
<section data-practice-page><p data-practice-message role="status" aria-live="polite"></p>
<p data-practice-summary>正在读取复习清单…</p>
<label>范围 <select data-practice-filter><option value="due">现在该复习的</option><option value="all">全部复习卡</option></select></label>
<p>没想起：10 分钟后；不熟悉：明天；想起来了：依次间隔 1、3、7、14、30 天。下次日期在记录后显示。</p>
<p><a href="{base}/learning">从公众号资料加入复习 →</a> · <a href="{base}/knowledge">按考点学习 →</a></p>
<div data-practice-list></div><p><button data-practice-more type="button" hidden>再显示 20 张</button></p>
<details><summary>回想记录</summary><div data-practice-history></div><button data-practice-more-history type="button" hidden>更多记录</button></details>
<section data-practice-backup><h2>保留自己的复习记录</h2>
<p>导出本浏览器的复习卡、原文快照、安排及回想历史。备份只在本机处理，不上传；不包含收藏、已读、管理口令或公众号登录。文件上限 25 MB、5000 张卡及 50000 条历史。</p>
<button type="button" data-practice-export>导出复习备份</button>
<p><label>选择 JSON 备份 <input type="file" accept=".json,application/json" data-practice-file></label></p>
<p data-practice-preview role="status" aria-live="polite"></p><button type="button" data-practice-restore disabled>确认合并恢复</button>
<p>先校验预览，再合并：本机同编号卡片及进度优先保留，缺少的卡片才补入；历史去重。恢复不重建跨备份撤销关系。换资料库后同编号可能不是同一原文，请核对恢复卡片的标题和依据。</p></section>
</section>''')
