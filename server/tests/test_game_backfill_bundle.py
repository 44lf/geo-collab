import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from server.app.modules.game_library import backfill_bundle
from server.app.modules.game_library.models import Game as GameRow
from server.app.modules.image_library import service as image_service
from server.app.modules.image_library import store as minio_store
from server.app.modules.image_library.models import StockCategory, StockImage
from server.scripts import remote_game_bundle_import
from server.tests.utils import build_test_engine, reset_test_database

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 24


def _write_file(root: Path, relative: str, data: bytes) -> dict:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": relative,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _valid_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    root.mkdir()
    game = {
        "target_game_id": 42,
        "category_id": 9,
        "game": {
            "source": "taptap",
            "game_id": "168332",
            "name": "原神",
            "score": 7.9,
            "tags": ["开放世界"],
            "platforms": ["android", "ios", "pc"],
            "comment_count": 100,
            "icon_url": "https://img.example/icon.png",
            "screenshot_urls": ["https://img.example/shot.jpg"],
            "android_package": "com.miHoYo.Yuanshen",
            "description": "desc",
            "raw": {},
        },
    }
    game_bytes = json.dumps(game, ensure_ascii=False).encode()
    game_file = _write_file(root, "games/42/game.json", game_bytes)
    icon_file = _write_file(root, "games/42/images/icon.png", PNG_BYTES)
    shot_file = _write_file(root, "games/42/images/shot-01.jpg", JPEG_BYTES)
    game_file.update({"media_type": "application/json", "role": "game"})
    icon_file.update(
        {
            "media_type": "image/png",
            "role": "icon",
            "source_url": "https://img.example/icon.png",
        }
    )
    shot_file.update(
        {
            "media_type": "image/jpeg",
            "role": "screenshot",
            "source_url": "https://img.example/shot.jpg",
        }
    )
    manifest = {
        "schema_version": 1,
        "bundle_id": "bundle-20260728-001",
        "collector_version": "1.0.0",
        "source": "taptap",
        "started_at": "2026-07-28T01:00:00Z",
        "finished_at": "2026-07-28T01:10:00Z",
        "targets": [
            {
                "target_game_id": 42,
                "name": "原神",
                "category_id": 9,
                "status": "success",
                "game_file": game_file["path"],
                "asset_files": [icon_file["path"], shot_file["path"]],
            }
        ],
        "files": [game_file, icon_file, shot_file],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def _rewrite_game_payload(root: Path, mutate) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    game_entry = manifest["files"][0]
    game_path = root / game_entry["path"]
    payload = json.loads(game_path.read_text(encoding="utf-8"))
    mutate(payload)
    game_bytes = json.dumps(payload, ensure_ascii=False).encode()
    game_path.write_bytes(game_bytes)
    game_entry["bytes"] = len(game_bytes)
    game_entry["sha256"] = hashlib.sha256(game_bytes).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_validate_bundle_accepts_valid_contract(tmp_path):
    bundle = backfill_bundle.validate_bundle(_valid_bundle(tmp_path))

    assert bundle.bundle_id == "bundle-20260728-001"
    assert len(bundle.targets) == 1
    assert bundle.targets[0].game_data["game"]["name"] == "原神"
    assert [asset.role for asset in bundle.targets[0].assets] == ["icon", "screenshot"]


def test_validate_bundle_rejects_unknown_schema_version(tmp_path):
    root = _valid_bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backfill_bundle.BundleValidationError, match="schema_version"):
        backfill_bundle.validate_bundle(root)


def test_validate_bundle_rejects_missing_required_field(tmp_path):
    root = _valid_bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["bundle_id"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backfill_bundle.BundleValidationError, match="bundle_id"):
        backfill_bundle.validate_bundle(root)


def test_validate_bundle_rejects_path_traversal(tmp_path):
    root = _valid_bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backfill_bundle.BundleValidationError, match="unsafe path"):
        backfill_bundle.validate_bundle(root)


def test_validate_bundle_rejects_declared_oversized_file(tmp_path, monkeypatch):
    root = _valid_bundle(tmp_path)
    monkeypatch.setattr(backfill_bundle, "MAX_FILE_BYTES", 16)

    with pytest.raises(backfill_bundle.BundleValidationError, match="file too large"):
        backfill_bundle.validate_bundle(root)


def test_validate_bundle_rejects_total_size_limit(tmp_path, monkeypatch):
    root = _valid_bundle(tmp_path)
    monkeypatch.setattr(backfill_bundle, "MAX_FILE_BYTES", 10_000)
    monkeypatch.setattr(backfill_bundle, "MAX_TOTAL_BYTES", 50)

    with pytest.raises(backfill_bundle.BundleValidationError, match="bundle too large"):
        backfill_bundle.validate_bundle(root)


def test_validate_bundle_rejects_mime_signature_mismatch(tmp_path):
    root = _valid_bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][1]["media_type"] = "image/jpeg"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backfill_bundle.BundleValidationError, match="MIME signature"):
        backfill_bundle.validate_bundle(root)


def test_validate_bundle_rejects_sha256_mismatch(tmp_path):
    root = _valid_bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][1]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backfill_bundle.BundleValidationError, match="SHA-256"):
        backfill_bundle.validate_bundle(root)


def test_validate_bundle_rejects_duplicate_target_ids(tmp_path):
    root = _valid_bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["targets"].append(dict(manifest["targets"][0]))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backfill_bundle.BundleValidationError, match="duplicate target_game_id"):
        backfill_bundle.validate_bundle(root)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload["game"].update(name="related game"), "name mismatch"),
        (lambda payload: payload["game"].update(source="baidu"), "source mismatch"),
        (lambda payload: payload.update(category_id=99), "category_id mismatch"),
    ],
)
def test_validate_bundle_rejects_target_identity_mismatch(tmp_path, mutate, message):
    root = _valid_bundle(tmp_path)
    _rewrite_game_payload(root, mutate)

    with pytest.raises(backfill_bundle.BundleValidationError, match=message):
        backfill_bundle.validate_bundle(root)


def _add_second_success_target(root: Path, target_id: int = 43, name: str = "崩坏：星穹铁道"):
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    game = {
        "target_game_id": target_id,
        "category_id": 10,
        "game": {
            "source": "taptap",
            "game_id": "230625",
            "name": name,
            "score": 8.1,
            "tags": ["角色扮演"],
            "platforms": ["android", "ios", "pc"],
            "comment_count": 50,
            "icon_url": None,
            "screenshot_urls": [],
            "android_package": None,
            "description": "second",
            "raw": {},
        },
    }
    game_bytes = json.dumps(game, ensure_ascii=False).encode()
    entry = _write_file(root, f"games/{target_id}/game.json", game_bytes)
    entry.update({"media_type": "application/json", "role": "game"})
    manifest["files"].append(entry)
    manifest["targets"].append(
        {
            "target_game_id": target_id,
            "name": name,
            "category_id": 10,
            "status": "success",
            "game_file": entry["path"],
            "asset_files": [],
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


class _FakeSession:
    def __init__(self, row):
        self.row = row
        self.get_calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def get(self, model, row_id):
        self.get_calls.append((model, row_id))
        return self.row

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1

    def delete(self, _row):
        raise AssertionError("bundle import must never delete rows")


def test_import_dry_run_plans_without_opening_database_session(tmp_path):
    root = _valid_bundle(tmp_path)

    def forbidden_session_factory():
        raise AssertionError("dry-run must not open a database session")

    result = remote_game_bundle_import.run_import(
        root,
        dry_run=True,
        session_factory=forbidden_session_factory,
    )

    assert result["bundle_id"] == "bundle-20260728-001"
    assert result["planned"] == 1
    assert result["imported"] == 0
    assert result["targets"][0]["images"] == 2


def test_import_targets_existing_row_category_and_pre_downloaded_images(tmp_path):
    root = _valid_bundle(tmp_path)
    existing_row = SimpleNamespace(stock_category_id=9)
    session = _FakeSession(existing_row)
    calls = []

    def fake_upsert(db, game, **kwargs):
        calls.append((db, game, kwargs))
        return existing_row

    result = remote_game_bundle_import.run_import(
        root,
        dry_run=False,
        session_factory=lambda: session,
        upsert_func=fake_upsert,
    )

    assert result["imported"] == 1
    assert result["failed"] == 0
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closes == 1
    assert session.get_calls[0][1] == 42
    _, game, kwargs = calls[0]
    assert game.source == "taptap"
    assert game.name == "原神"
    assert kwargs["category_id"] == 9
    assert kwargs["game_row"] is existing_row
    assert kwargs["pre_downloaded_icon"][1] == PNG_BYTES
    assert kwargs["pre_downloaded"] == [("https://img.example/shot.jpg", JPEG_BYTES, "image/jpeg")]


def test_import_rolls_back_failed_game_and_continues_next(tmp_path):
    root = _valid_bundle(tmp_path)
    _add_second_success_target(root)
    sessions = [
        _FakeSession(SimpleNamespace(stock_category_id=9)),
        _FakeSession(SimpleNamespace(stock_category_id=10)),
    ]
    calls = []

    def session_factory():
        return sessions[len(calls)]

    def fake_upsert(_db, game, **_kwargs):
        calls.append(game.name)
        if game.name == "原神":
            raise RuntimeError("first failed")
        return object()

    result = remote_game_bundle_import.run_import(
        root,
        dry_run=False,
        session_factory=session_factory,
        upsert_func=fake_upsert,
    )

    assert calls == ["原神", "崩坏：星穹铁道"]
    assert result["imported"] == 1
    assert result["failed"] == 1
    assert sessions[0].rollbacks == 1 and sessions[0].commits == 0
    assert sessions[1].commits == 1 and sessions[1].rollbacks == 0
    assert all(session.closes == 1 for session in sessions)


def test_import_ignores_miss_and_never_deletes(tmp_path):
    root = _valid_bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["targets"].append(
        {
            "target_game_id": 99,
            "name": "missing",
            "category_id": 11,
            "status": "miss",
            "asset_files": [],
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    session = _FakeSession(SimpleNamespace(stock_category_id=9))
    calls = []

    result = remote_game_bundle_import.run_import(
        root,
        dry_run=False,
        session_factory=lambda: session,
        upsert_func=lambda _db, game, **_kwargs: calls.append(game.name),
    )

    assert calls == ["原神"]
    assert result["skipped"] == 1
    assert result["imported"] == 1


def test_import_rejects_missing_production_target_without_upsert(tmp_path):
    root = _valid_bundle(tmp_path)
    session = _FakeSession(None)
    calls = []

    result = remote_game_bundle_import.run_import(
        root,
        dry_run=False,
        session_factory=lambda: session,
        upsert_func=lambda *_args, **_kwargs: calls.append("called"),
    )

    assert calls == []
    assert result["imported"] == 0
    assert result["failed"] == 1
    assert "production game 42 not found" in result["targets"][0]["error"]
    assert session.rollbacks == 1


def test_import_rejects_category_drift_without_upsert(tmp_path):
    root = _valid_bundle(tmp_path)
    session = _FakeSession(SimpleNamespace(stock_category_id=77))
    calls = []

    result = remote_game_bundle_import.run_import(
        root,
        dry_run=False,
        session_factory=lambda: session,
        upsert_func=lambda *_args, **_kwargs: calls.append("called"),
    )

    assert calls == []
    assert result["imported"] == 0
    assert result["failed"] == 1
    assert "category changed" in result["targets"][0]["error"]
    assert session.rollbacks == 1


@pytest.mark.mysql
def test_import_is_idempotent_against_mysql_and_image_store_contract(tmp_path, monkeypatch):
    root = _valid_bundle(tmp_path)
    _rewrite_game_payload(root, lambda payload: payload.update(category_id=None))
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["targets"][0]["category_id"] = None
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    engine = build_test_engine()
    session_factory = sessionmaker(bind=engine)
    uploads = []
    monkeypatch.setattr(image_service, "slugify_bucket", lambda name: f"test-{len(name)}")
    monkeypatch.setattr(minio_store, "ensure_bucket", lambda _bucket: None)
    monkeypatch.setattr(
        minio_store,
        "upload_image",
        lambda bucket, key, data, content_type: uploads.append(
            (bucket, key, len(data), content_type)
        ),
    )

    try:
        with session_factory() as db:
            db.add(
                GameRow(
                    id=42,
                    name="原神",
                    name_normalized="原神",
                    sources=[],
                    platforms=[],
                    screenshot_urls=[],
                )
            )
            db.commit()

        first = remote_game_bundle_import.run_import(
            root,
            dry_run=False,
            session_factory=session_factory,
        )
        second = remote_game_bundle_import.run_import(
            root,
            dry_run=False,
            session_factory=session_factory,
        )

        assert first["imported"] == 1 and first["failed"] == 0, first["targets"][0]
        assert second["imported"] == 1 and second["failed"] == 0, second["targets"][0]
        with session_factory() as db:
            game = db.get(GameRow, 42)
            assert game is not None
            assert game.sources == [{"source": "taptap", "source_game_id": "168332"}]
            assert game.icon_url.startswith("/api/stock-images/")
            assert db.query(StockCategory).count() == 2
            assert db.query(StockImage).count() == 2
        assert len(uploads) == 2
    finally:
        reset_test_database(engine)
        engine.dispose()
