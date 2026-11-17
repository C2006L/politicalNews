"""Local source registry; pending names are never invented upstream identities."""
import json
import re
from typing import Any
from app.database import SessionLocal
from app.models import AppState, LearningMaterial
from app.editorial import select_document

KEY = 'material_sources_v1'
GROUPS = {'postgrad': '考研资料', 'civil': '公考资料', 'shared': '公共时政与评论'}
DEFAULTS: list[dict[str, Any]] = [
    {'key': 'xiao', 'name': '肖秀荣教授', 'mp_id': 'MP_WXS_3243013434', 'group': 'postgrad', 'region': '全国', 'enabled': True},
    {'key': 'xu', 'name': '考研政治徐涛', 'mp_id': 'MP_WXS_3080503014', 'group': 'postgrad', 'region': '全国', 'enabled': True},
    {'key': 'tui', 'name': '腿姐', 'mp_id': None, 'group': 'postgrad', 'region': '全国', 'enabled': False},
    {'key': 'dengta', 'name': '灯塔党建在线', 'mp_id': None, 'group': 'civil', 'region': '山东', 'enabled': False},
    {'key': 'shandong', 'name': '山东发布', 'mp_id': None, 'group': 'civil', 'region': '山东', 'enabled': False},
    {'key': 'banyuetan', 'name': '半月谈', 'mp_id': None, 'group': 'shared', 'region': '全国', 'enabled': False},
    {'key': 'people_comment', 'name': '人民日报评论', 'mp_id': None, 'group': 'shared', 'region': '全国', 'enabled': False},
]


def sources() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        saved = db.get(AppState, KEY)
        changes = json.loads(saved.value) if saved else {}
    return [{**row, **changes.get(row['key'], {})} for row in DEFAULTS]


def accounts() -> dict[str, str]:
    return {r['mp_id']: r['name'] for r in sources() if r['enabled'] and r['mp_id']}


def route_material(row: LearningMaterial, overrides: dict | None = None) -> dict[str, Any]:
    source = next((s for s in sources() if s['name'] == row.account), None)
    decision = select_document(row, overrides)
    category = decision.category
    # Source preference is not article classification. Textbook drill never enters civil.
    if category == 'exam_advice' and (re.search(r'申论|国考|省考|公考|公务员', row.title) or source and source['group'] == 'civil'):
        directions = ['civil']
    elif category in {'textbook', 'exam_advice'}:
        directions = ['postgrad']
    elif category in {'policy', 'report', 'analysis'} and decision.selected:
        directions = ['postgrad', 'civil']
    else:
        directions = []
    return {'purpose': category, 'label': decision.as_dict()['label'], 'directions': directions,
            'group': source['group'] if source else 'shared', 'region': source['region'] if source else '待确认',
            'source_enabled': bool(source and source['enabled']), 'reason': decision.reasons[0],
            'brief_candidate': decision.selected and category in {'policy', 'report', 'analysis'}}
