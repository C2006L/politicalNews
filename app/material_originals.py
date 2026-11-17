"""Track upstream fragment changes without replacing a saved original page."""
import json
from typing import Any
from sqlalchemy.orm import Session
from app.models import AppState

KEY = 'material_original_refresh_v1'


def read(db: Session) -> dict[str, Any]:
    row = db.get(AppState, KEY)
    return json.loads(row.value) if row else {}


def write(db: Session, entries: dict[str, Any]) -> None:
    row = db.get(AppState, KEY)
    value = json.dumps(entries)
    if row:
        row.value = value
    else:
        db.add(AppState(key=KEY, value=value))
        db.flush()


def observe(db: Session, material_id: int, fingerprint: str, same_body: bool) -> None:
    entries = read(db)
    entry = entries.setdefault(str(material_id), {})
    if entry.get('accepted_fragment') == fingerprint:
        return
    if same_body:
        entry.update(accepted_fragment=fingerprint, pending_fragment=None)
    else:
        entry['pending_fragment'] = fingerprint
    write(db, entries)


def accept(db: Session, material_id: int, previous_fragment: str | None = None) -> None:
    entries = read(db)
    entry = entries.setdefault(str(material_id), {})
    fingerprint = entry.get('pending_fragment') or previous_fragment
    if fingerprint:
        entry.update(accepted_fragment=fingerprint, pending_fragment=None)
        write(db, entries)
