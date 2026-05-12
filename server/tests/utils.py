import shutil
import threading
import uuid
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from server.app.core.config import get_settings
from server.app.db.base import Base
from server.app.db.session import get_db
from server.app.main import create_app


class TestApp:
    def __init__(self, client: TestClient, data_dir: Path, session_factory: sessionmaker[Session]):
        self.client = client
        self.data_dir = data_dir
        self.session_factory = session_factory

    def cleanup(self) -> None:
        shutil.rmtree(self.data_dir, ignore_errors=True)
        get_settings.cache_clear()


def build_test_app(monkeypatch) -> TestApp:
    data_dir = Path.cwd() / ".test-data" / uuid.uuid4().hex
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    get_settings.cache_clear()

    # 清理全局任务锁和取消标志（避免跨测试污染）
    from server.app.services import tasks as _tasks_mod
    _tasks_mod._task_locks.clear()
    _tasks_mod._account_locks.clear()
    _tasks_mod._account_locks_lock = threading.Lock()
    _tasks_mod._task_cancel.clear()

    from server.app.services import browser_sessions as _bs_mod
    _bs_mod._reset_globals()

    db_path = data_dir / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5("
                "title, author, content='articles', content_rowid='id', tokenize='trigram')"
            )
        )
        conn.execute(sa.text("INSERT OR IGNORE INTO articles_fts(rowid, title, author) SELECT id, title, author FROM articles"))
        conn.execute(
            sa.text(
                "CREATE TRIGGER IF NOT EXISTS after_articles_insert "
                "AFTER INSERT ON articles BEGIN "
                "INSERT INTO articles_fts(rowid, title, author) VALUES (new.id, new.title, new.author); "
                "END"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TRIGGER IF NOT EXISTS after_articles_delete "
                "AFTER DELETE ON articles BEGIN "
                "INSERT INTO articles_fts(articles_fts, rowid, title, author) "
                "VALUES('delete', old.id, old.title, old.author); "
                "END"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TRIGGER IF NOT EXISTS after_articles_update "
                "AFTER UPDATE ON articles BEGIN "
                "INSERT INTO articles_fts(articles_fts, rowid, title, author) "
                "VALUES('delete', old.id, old.title, old.author); "
                "INSERT INTO articles_fts(rowid, title, author) VALUES (new.id, new.title, new.author); "
                "END"
            )
        )
        conn.commit()
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db: Session = TestingSessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # 让后台任务线程也使用测试数据库
    monkeypatch.setattr("server.app.api.routes.tasks.bg_session_factory", TestingSessionLocal)

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    return TestApp(client=TestClient(app), data_dir=data_dir, session_factory=TestingSessionLocal)
