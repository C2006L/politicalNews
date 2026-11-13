import socket

import pytest

from app import database, fetch_news, news_data, sync_service
from app.models import News


def test_all_modules_share_test_session_factory():
    assert fetch_news.SessionLocal is database.SessionLocal
    assert news_data.SessionLocal is database.SessionLocal
    assert sync_service.SessionLocal is database.SessionLocal
    assert "political-news-tests-" in database.engine.url.database


def test_client_reads_fixture_database(client, db_session):
    from datetime import datetime
    row = News(title="隔离库测试材料", link="https://example.com/isolated",
               published="2026-11-12", published_at=datetime(2026, 11, 12),
               year=2026, month=11)
    db_session.add(row)
    db_session.commit()
    response = client.get("/api/news?year=2026")
    assert "隔离库测试材料" in response.text


def test_external_socket_is_blocked():
    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="External network"):
            connection.connect(("192.0.2.1", 80))


def test_no_real_scheduler_on_default_test_client(client):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    with patch("app.main.setup_scheduler", side_effect=AssertionError("scheduler started")):
        with TestClient(app) as test_client:
            assert test_client.get("/health").status_code == 200
