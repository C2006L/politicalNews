"""Relation review and its audit event share one transaction."""
from datetime import datetime, timezone
import secrets
import hashlib
import json
import base64
import binascii
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import update, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import (KnowledgeLink, NewsKnowledgeLink, KnowledgePoint, MaterialRevision,
                        News, RelationReviewEvent, AppState)
from app.news_knowledge import fingerprint, news_snapshot


PASSWORD_KEY = 'review_password_v1'


def password_configured() -> bool:
    with SessionLocal() as db:
        return db.get(AppState, PASSWORD_KEY) is not None


def set_initial_password(password: str) -> None:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000).hex()
    with SessionLocal() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        if get_settings().sync_admin_token or db.get(AppState, PASSWORD_KEY) is not None:
            raise HTTPException(409, '管理口令已经配置，不能重复初始化')
        db.add(AppState(key=PASSWORD_KEY, value=json.dumps({'salt': salt.hex(), 'digest': digest})))
        db.commit()


def require_token(token: str | None) -> None:
    if token and token.startswith('base64:'):
        try:
            token = base64.b64decode(token[7:], validate=True).decode('utf-8')
        except (ValueError, binascii.Error):
            raise HTTPException(403, '请填写正确的管理口令') from None
    required = get_settings().sync_admin_token
    if required:
        valid = secrets.compare_digest((token or '').encode(), required.encode())
    else:
        with SessionLocal() as db:
            saved = db.get(AppState, PASSWORD_KEY)
            config = json.loads(saved.value) if saved else None
        valid = False
        if config and token and len(token) <= 128:
            digest = hashlib.pbkdf2_hmac('sha256', token.encode(), bytes.fromhex(config['salt']), 600000).hex()
            valid = secrets.compare_digest(digest, config['digest'])
    if not valid:
        raise HTTPException(403, '请填写正确的管理口令')


def apply_review(db: Session, kind: str, link_id: int, status: str, note: str = '',
                 expected_status: str | None = None) -> dict[str, Any]:
    model = KnowledgeLink if kind == 'material' else NewsKnowledgeLink
    link = cast(KnowledgeLink | NewsKnowledgeLink | None, db.get(model, link_id))
    if link is None:
        raise HTTPException(404, '关联不存在')
    previous = link.status
    if expected_status is not None and previous != expected_status:
        raise HTTPException(409, '关联已被其他操作修改，请刷新后重试')
    if previous == status:
        return {'id': link_id, 'status': status, 'changed': False}
    changed = db.execute(update(model).where(model.id == link_id, model.status == previous).values(status=status).returning(model.id)).scalar_one_or_none()
    if changed is None:
        raise HTTPException(409, '关联已更新，请刷新后重试')
    db.add(RelationReviewEvent(kind=kind, link_id=link_id, point_id=link.point_id,
                              previous=previous, status=status, note=note.strip(),
                              created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    db.commit()
    return {'id': link_id, 'status': status, 'changed': True}


def queue_items(db: Session, kind: str, status: str, page: int) -> dict[str, Any]:
    rows = []
    if kind in ('all', 'material'):
        latest = {material_id: number for material_id, number in db.query(
            MaterialRevision.material_id, func.max(MaterialRevision.revision)).group_by(MaterialRevision.material_id).all()}
        for link, revision, point in db.query(KnowledgeLink, MaterialRevision, KnowledgePoint).join(
                MaterialRevision, MaterialRevision.id == KnowledgeLink.revision_id).join(
                KnowledgePoint, KnowledgePoint.id == KnowledgeLink.point_id).filter(KnowledgeLink.status == status).all():
            if latest.get(revision.material_id) != revision.revision:
                continue
            snap = revision.snapshot
            item = snap['payload'][link.kind][link.item_index]
            rows.append({'id': link.id, 'kind': 'material', 'point_id': point.id, 'point_title': point.title,
                         'status': link.status, 'title': snap['title'], 'source': snap['account'],
                         'revision': revision.revision, 'question': item.get('question'),
                         'answer': item.get('answer'), 'needs_review': item.get('needs_review', False),
                         'context': '\n'.join(line['text'] for line in snap['payload']['evidence']),
                         'evidence': [line for line in snap['payload']['evidence'] if line['id'] in link.evidence_ids],
                         'url': f'/learning/{revision.material_id}?revision={revision.revision}'})
    if kind in ('all', 'news'):
        for news_link, news, point in db.query(NewsKnowledgeLink, News, KnowledgePoint).join(
                News, News.id == NewsKnowledgeLink.news_id).join(
                KnowledgePoint, KnowledgePoint.id == NewsKnowledgeLink.point_id).filter(NewsKnowledgeLink.status == status).all():
            if news_link.fingerprint != fingerprint(news_snapshot(news)):
                continue
            rows.append({'id': news_link.id, 'kind': 'news', 'point_id': point.id, 'point_title': point.title,
                         'status': news_link.status, 'title': news_link.snapshot['title'],
                         'source': news_link.snapshot['source'], 'question': None, 'answer': None,
                         'evidence': [{'id': news_link.evidence['field'], 'text': news_link.evidence['text']}],
                         'reason': news_link.evidence['reason'], 'url': f'/knowledge/news-links/{news_link.id}'})
            rows[-1]['context'] = news_link.snapshot['content']
    rows.sort(key=lambda row: (row['kind'], row['id']))
    return {'total': len(rows), 'page': page, 'items': rows[(page - 1) * 20:page * 20]}
