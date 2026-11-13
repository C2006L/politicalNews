from datetime import datetime

from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="gov_cn")
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="时政")
    title: Mapped[str] = mapped_column(String, index=True, nullable=False)
    link: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published: Mapped[str] = mapped_column(String, nullable=False, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    month: Mapped[int] = mapped_column(Integer, index=True, nullable=False)


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class LearningMaterial(Base):
    __tablename__ = "learning_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    account: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MaterialRevision(Base):
    __tablename__ = "material_revisions"
    __table_args__ = (UniqueConstraint("material_id", "revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("learning_materials.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String, nullable=False)


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)


class KnowledgeLink(Base):
    __tablename__ = "knowledge_links"
    __table_args__ = (UniqueConstraint("point_id", "revision_id", "kind", "item_index"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[str] = mapped_column(ForeignKey("knowledge_points.id"), index=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("material_revisions.id"), index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, default="candidate", nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
