import shutil
import uuid
from pathlib import Path

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

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db: Session = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    return TestApp(client=TestClient(app), data_dir=data_dir, session_factory=TestingSessionLocal)
