"""Ordered SQLite migrations. Each upgrade is serialized and transactional."""
from datetime import datetime, timezone

from sqlalchemy import Engine, text, select

from app.models import Base

LATEST_VERSION = 3


def migrate(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        raise ValueError("Only SQLite is supported by the current migration and FTS implementation")
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
            if applied - set(range(1, LATEST_VERSION + 1)):
                raise ValueError("Database schema is newer than this application; restore the matching application version")
            if 1 not in applied:
                for name in ("news", "app_state", "learning_materials"):
                    Base.metadata.tables[name].create(conn, checkfirst=True)
                conn.execute(text("INSERT INTO schema_migrations VALUES (1,:at)"), {"at": datetime.now(timezone.utc).isoformat()})
            if 2 not in applied:
                Base.metadata.tables["material_revisions"].create(conn, checkfirst=True)
                for row in conn.execute(select(Base.metadata.tables["learning_materials"])).mappings():
                    snapshot = {"title": row["title"], "account": row["account"], "url": row["url"],
                                "payload": row["payload"], "imported_at": row["imported_at"].isoformat() + "Z",
                                "fingerprint": row["fingerprint"]}
                    conn.execute(Base.metadata.tables["material_revisions"].insert().values(
                        material_id=row["id"], revision=1, snapshot=snapshot,
                        source_html=None, origin="legacy_snapshot"))
                conn.execute(text("INSERT INTO schema_migrations VALUES (2,:at)"), {"at": datetime.now(timezone.utc).isoformat()})
            if 3 not in applied:
                for name in ("knowledge_points", "knowledge_links"):
                    Base.metadata.tables[name].create(conn, checkfirst=True)
                conn.execute(text("INSERT INTO schema_migrations VALUES (3,:at)"), {"at": datetime.now(timezone.utc).isoformat()})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
