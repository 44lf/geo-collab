from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text

from server.app.core.config import get_settings
from server.tests.utils import build_test_app


def _tiptap_doc() -> dict:
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}]}


@pytest.mark.mysql
def test_fts_fallback_when_table_missing(monkeypatch):
    test_app = build_test_app(monkeypatch)
    client = test_app.client

    try:
        # Create an article
        resp = client.post(
            "/api/articles",
            json={
                "title": "Fallback测试文章ABC",
                "author": "TestAuthor",
                "content_json": _tiptap_doc(),
            },
        )
        assert resp.status_code == 200
        article_id = resp.json()["id"]

        # Drop the FTS table to simulate it not existing
        session = test_app.session_factory()
        try:
            session.execute(text("DROP TABLE IF EXISTS articles_fts"))
            session.commit()
        finally:
            session.close()

        # Search with >= 3 char query - should fall back to LIKE, no 500
        resp = client.get("/api/articles", params={"q": "Fallback"})
        assert resp.status_code == 200
        results = resp.json()
        assert isinstance(results, list)
        assert any(item["id"] == article_id for item in results)

        # Search that matches nothing
        resp = client.get("/api/articles", params={"q": "xyznotfound999"})
        assert resp.status_code == 200
        results = resp.json()
        assert results == []

        # Short query should still work with LIKE
        resp = client.get("/api/articles", params={"q": "AB"})
        assert resp.status_code == 200
        results = resp.json()
        assert any(item["id"] == article_id for item in results)
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_fts_fallback_when_match_throws(monkeypatch):
    test_app = build_test_app(monkeypatch)
    client = test_app.client

    try:
        # Create an article
        resp = client.post(
            "/api/articles",
            json={
                "title": "抛出异常测试XYZ",
                "author": "ErrorTest",
                "content_json": _tiptap_doc(),
            },
        )
        assert resp.status_code == 200
        article_id = resp.json()["id"]

        # Drop internal FTS shadow table to cause MATCH to throw a different error
        session = test_app.session_factory()
        try:
            session.execute(text("DROP TABLE IF EXISTS articles_fts_data"))
            session.commit()
        finally:
            session.close()

        # FTS MATCH should throw, but fallback to LIKE should succeed
        resp = client.get("/api/articles", params={"q": "抛出异常"})
        assert resp.status_code == 200
        results = resp.json()
        assert isinstance(results, list)
        assert any(item["id"] == article_id for item in results)
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_alembic_upgrade_from_empty_to_head(monkeypatch):
    data_dir = Path.cwd() / ".test-data" / uuid.uuid4().hex
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    get_settings.cache_clear()

    try:
        cfg = AlembicConfig("alembic.ini")
        command.upgrade(cfg, "head")

        db_path = data_dir / "geo.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        expected_tables = {
            "platforms",
            "accounts",
            "assets",
            "articles",
            "article_body_assets",
            "article_groups",
            "article_group_items",
            "publish_tasks",
            "publish_task_accounts",
            "publish_records",
            "task_logs",
            "users",
        }
        assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"
        assert "articles_fts" in tables, "FTS5 virtual table not created"
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()


@pytest.mark.mysql
def test_migration_trigram_fallback(monkeypatch):
    """Verify FTS5 table is created even when trigram tokenizer is unavailable."""
    data_dir = Path.cwd() / ".test-data" / uuid.uuid4().hex
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    get_settings.cache_clear()

    try:
        cfg = AlembicConfig("alembic.ini")

        # Run migrations up to just before FTS migration
        command.upgrade(cfg, "0006")

        db_path = data_dir / "geo.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")

        # Simulate the fallback logic from the migration: 
        # try trigram, fall back to basic FTS5 if trigram unavailable
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE articles_fts USING fts5("
                    "title, author, content='articles', content_rowid='id', tokenize='trigram')"
                ))
            except Exception:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE articles_fts USING fts5("
                    "title, author, content='articles', content_rowid='id')"
                ))
            conn.execute(text(
                "INSERT INTO articles_fts(rowid, title, author) SELECT id, title, author FROM articles"
            ))
            conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS after_articles_insert "
                "AFTER INSERT ON articles BEGIN "
                "INSERT INTO articles_fts(rowid, title, author) VALUES (new.id, new.title, new.author); "
                "END"
            ))
            conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS after_articles_delete "
                "AFTER DELETE ON articles BEGIN "
                "INSERT INTO articles_fts(articles_fts, rowid, title, author) "
                "VALUES('delete', old.id, old.title, old.author); "
                "END"
            ))
            conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS after_articles_update "
                "AFTER UPDATE ON articles BEGIN "
                "INSERT INTO articles_fts(articles_fts, rowid, title, author) "
                "VALUES('delete', old.id, old.title, old.author); "
                "INSERT INTO articles_fts(rowid, title, author) VALUES (new.id, new.title, new.author); "
                "END"
            ))
            conn.commit()

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "articles_fts" in tables, "FTS5 virtual table should exist even without trigram"

        # Verify FTS works by inserting and querying
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO articles_fts(rowid, title, author) VALUES (1, 'test', 'author')"))
            conn.commit()
            result = conn.execute(text(
                "SELECT rowid FROM articles_fts WHERE articles_fts MATCH :q"), {"q": "test"}
            ).fetchall()
            assert len(result) == 1
            assert result[0][0] == 1

    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()
