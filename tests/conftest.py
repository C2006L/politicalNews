"""Offline tests share the application's temporary database, never the user's DB."""
import os
import socket
import sys
import tempfile
import warnings
from types import SimpleNamespace
from pathlib import Path

import pytest

# These must be set before any app import creates engines or reads configuration.
_test_root = tempfile.TemporaryDirectory(prefix="political-news-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(_test_root.name) / "tests.db").as_posix()
os.environ["AUTO_SYNC_ON_STARTUP"] = "0"
os.environ["BOOTSTRAP_RECENT_NEWS_ON_STARTUP"] = "0"
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["WERSS_DATABASE_PATH"] = ""
os.environ["EXPOSE_API_DOCS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from sqlalchemy import text
from app.database import SessionLocal, engine, init_db
from app.main import app
from app.models import Base


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    def allowed(address):
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}
    def no_network(sock, address):
        if allowed(address):
            return connect(sock, address)
        raise AssertionError("External network access is disabled in ordinary tests.")
    def no_network_ex(sock, address):
        if allowed(address):
            return connect_ex(sock, address)
        raise AssertionError("External network access is disabled in ordinary tests.")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network_ex)
    monkeypatch.setenv("AUTO_SYNC_ON_STARTUP", "0")
    monkeypatch.setenv("BOOTSTRAP_RECENT_NEWS_ON_STARTUP", "0")
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("WERSS_DATABASE_PATH", "")
    monkeypatch.setenv("EXPOSE_API_DOCS", "1")
    import app.sync_service as sync
    def no_worker(*args, **kwargs):
        raise AssertionError("Real collection workers are disabled in tests.")
    monkeypatch.setattr(sync, "threading", SimpleNamespace(Thread=no_worker))
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.execute(text("DELETE FROM news_fts"))
    yield


@pytest.fixture
def test_db():
    return SessionLocal


@pytest.fixture
def db_session(test_db):
    with test_db() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def pytest_unconfigure(config):
    engine.dispose()
    _test_root.cleanup()
