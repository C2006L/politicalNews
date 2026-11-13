"""Append-only snapshots. Caller owns the transaction with the current material."""
from copy import deepcopy

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import LearningMaterial, MaterialRevision


def append_revision(db: Session, material: LearningMaterial, html: str | None = None) -> int:
    db.flush()
    previous = db.query(func.max(MaterialRevision.revision)).filter_by(material_id=material.id).scalar() or 0
    number = previous + 1
    revision = MaterialRevision(material_id=material.id, revision=number,
                            snapshot={"title": material.title, "account": material.account,
                                      "url": material.url, "fingerprint": material.fingerprint,
                                      "payload": deepcopy(material.payload),
                                      "imported_at": material.imported_at.isoformat() + "Z"},
                            source_html=html, origin="import" if html is not None else "legacy_snapshot")
    db.add(revision)
    db.flush()
    from app.knowledge import link_revision
    link_revision(db, revision)
    return int(number)
