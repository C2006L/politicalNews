"""Describe observed material coverage without claiming a complete source history."""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.database import SessionLocal
from app.materials import ACCOUNTS
from app.models import LearningMaterial


def summarize(rows: Iterable[LearningMaterial]) -> dict[str, Any]:
    accounts = []
    all_rows = list(rows)
    for name in ACCOUNTS.values():
        selected = [row for row in all_rows if row.account == name]
        numbers: dict[int, list[int]] = {}
        unstructured = audio = images_unknown = 0
        for row in selected:
            pattern = r'背诵手册\s*D\s*(\d{1,3})(?!\d)' if name == '肖秀荣教授' else r'(?:徐涛|选择题)带背\s*D\s*(\d{1,3})(?!\d)'
            match = re.search(pattern, row.title, flags=re.I)
            if match and 1 <= int(match[1]) <= 366:
                numbers.setdefault(int(match[1]), []).append(row.id)
            payload = row.payload or {}
            unstructured += not (payload.get('questions') or payload.get('points'))
            audio += bool(payload.get('audio'))
            images_unknown += payload.get('coverage', {}).get('images') != 'verified'
        observed = sorted(numbers)
        gaps = [n for n in range(observed[0], observed[-1] + 1) if n not in numbers] if observed else []
        accounts.append({'name': name, 'materials': len(selected), 'unstructured': unstructured,
                         'audio_not_transcribed': audio, 'images_not_verified': images_unknown,
                         'observed_periods': observed, 'possible_internal_gaps': gaps,
                         'duplicate_periods': [n for n in observed if len(numbers[n]) > 1],
                         'items': [{'period': n, 'material_ids': numbers[n]} for n in observed],
                         'last_organized_at': max((row.imported_at.isoformat() + 'Z' for row in selected), default=None)})
    return {'scope': 'local_imported_materials', 'complete_history_verified': False, 'accounts': accounts,
            'note': '只按本地标题识别已见期数，年度与合集边界尚未核实。区间缺号仅供核对，可能未收录、改名或跨年度；不据此断言漏采，不推断区间外缺失。'}


def coverage_report() -> dict[str, Any]:
    with SessionLocal() as db:
        return summarize(db.query(LearningMaterial).filter(LearningMaterial.account.in_(list(ACCOUNTS.values()))).all())
