from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.modules.collector.import_service import (
    CollectorImportService,
    DiscoveryImportItem,
    ImportContractError,
    canonical_game_from_payload,
)
from server.app.modules.game_library.backfill_bundle import ValidatedFile, ValidatedTarget


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


def _write_asset(path: Path, data: bytes, *, media_type: str, role: str) -> ValidatedFile:
    path.write_bytes(data)
    return ValidatedFile(
        relative_path=path.name,
        path=path,
        media_type=media_type,
        role=role,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        source_url=f"https://img.example/{path.name}",
    )


def _target(tmp_path: Path, *, category_id: int | None = 9) -> ValidatedTarget:
    icon = _write_asset(
        tmp_path / "icon.png",
        b"\x89PNG\r\n\x1a\nfixture",
        media_type="image/png",
        role="icon",
    )
    shot = _write_asset(
        tmp_path / "shot.jpg",
        b"\xff\xd8\xfffixture",
        media_type="image/jpeg",
        role="screenshot",
    )
    return ValidatedTarget(
        target_game_id=42,
        name="原神",
        category_id=category_id,
        status="success",
        game_file=None,
        game_data={
            "game": {
                "source": "taptap",
                "game_id": "168332",
                "name": "原神",
                "score": 7.9,
                "tags": ["开放世界"],
                "platforms": ["android", "pc"],
                "comment_count": 100,
                "icon_url": "https://img.example/icon.png",
                "screenshot_urls": ["https://img.example/shot.jpg"],
                "android_package": "com.example.game",
                "description": "fixture",
                "raw": {"id": 168332},
            }
        },
        assets=(icon, shot),
    )


def test_canonical_game_conversion_preserves_source_evidence():
    game = canonical_game_from_payload(_target_payload())

    assert game.source == "taptap"
    assert game.game_id == "168332"
    assert game.name == "原神"
    assert game.platforms == ["android", "pc"]
    assert game.raw == {"id": 168332}


def test_canonical_game_conversion_accepts_direct_bundle_v2_record():
    game = canonical_game_from_payload(
        {
            "source": "baidu",
            "source_game_id": "source-v2",
            "name": "Direct V2",
            "score": 8.8,
            "tags": ["RPG"],
            "platforms": ["Android"],
            "raw": {"source": "fixture"},
        }
    )

    assert game.source == "baidu"
    assert game.game_id == "source-v2"
    assert game.name == "Direct V2"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": "x" * 201}, "name"),
        ({"icon_url": "file:///etc/passwd"}, "icon_url"),
        ({"tags": {}}, "tags"),
        ({"raw": []}, "raw"),
    ],
)
def test_canonical_game_conversion_rejects_out_of_contract_payload(changes, field):
    payload = {
        "source": "baidu",
        "source_game_id": "source-v2",
        "name": "Direct V2",
        "tags": [],
        "platforms": [],
        "raw": {},
    }
    payload.update(changes)

    with pytest.raises(ImportContractError, match=field):
        canonical_game_from_payload(payload)


def _target_payload() -> dict:
    return {
        "game": {
            "source": "taptap",
            "game_id": "168332",
            "name": "原神",
            "score": 7.9,
            "tags": ["开放世界"],
            "platforms": ["android", "pc"],
            "comment_count": 100,
            "icon_url": "https://img.example/icon.png",
            "screenshot_urls": ["https://img.example/shot.jpg"],
            "android_package": "com.example.game",
            "description": "fixture",
            "raw": {"id": 168332},
        }
    }


def test_refresh_import_anchors_existing_row_and_uses_validated_media(tmp_path):
    row = SimpleNamespace(stock_category_id=9)
    session = _FakeSession(row)
    calls = []
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda db, game, **kwargs: calls.append((db, game, kwargs)),
    )

    outcome = service.import_refresh_target(_target(tmp_path))

    assert outcome.status == "imported"
    assert outcome.error is None
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closes == 1
    assert session.get_calls[0][1] == 42
    _, game, kwargs = calls[0]
    assert game.name == "原神"
    assert kwargs["game_row"] is row
    assert kwargs["category_id"] == 9
    assert kwargs["pre_downloaded_icon"][1].startswith(b"\x89PNG")
    assert kwargs["pre_downloaded"][0][1].startswith(b"\xff\xd8\xff")


def test_refresh_import_rejects_category_drift_without_upsert(tmp_path):
    session = _FakeSession(SimpleNamespace(stock_category_id=77))
    calls = []
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda *_args, **_kwargs: calls.append("called"),
    )

    outcome = service.import_refresh_target(_target(tmp_path))

    assert outcome.status == "failed"
    assert "category changed" in (outcome.error or "")
    assert calls == []
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closes == 1


def test_non_success_target_does_not_open_database(tmp_path):
    target = _target(tmp_path)
    target = ValidatedTarget(
        target_game_id=target.target_game_id,
        name=target.name,
        category_id=target.category_id,
        status="miss",
        game_file=None,
        game_data=None,
        assets=(),
    )
    service = CollectorImportService(
        session_factory=lambda: (_ for _ in ()).throw(AssertionError("must not open DB")),
        upsert_func=lambda *_args, **_kwargs: None,
    )

    assert service.import_refresh_target(target).status == "skipped"


def test_refresh_import_in_existing_session_anchors_cross_source_and_deduplicates_media(
    tmp_path,
):
    row = SimpleNamespace(id=42, stock_category_id=9, sources=[{"source": "taptap"}])
    session = _FakeSession(row)
    calls = []
    target = _target(tmp_path)
    duplicate = _write_asset(
        tmp_path / "shot-duplicate.jpg",
        b"\xff\xd8\xffsame-source",
        media_type="image/jpeg",
        role="screenshot",
    )
    target = ValidatedTarget(
        target_game_id=target.target_game_id,
        name=target.name,
        category_id=target.category_id,
        status=target.status,
        game_file=target.game_file,
        game_data=target.game_data,
        assets=target.assets + (duplicate,),
    )
    duplicate = ValidatedFile(
        relative_path=duplicate.relative_path,
        path=duplicate.path,
        media_type=duplicate.media_type,
        role=duplicate.role,
        size=duplicate.size,
        sha256=duplicate.sha256,
        source_url="https://img.example/shot.jpg",
    )
    target = ValidatedTarget(
        target_game_id=target.target_game_id,
        name=target.name,
        category_id=target.category_id,
        status=target.status,
        game_file=target.game_file,
        game_data=target.game_data,
        assets=target.assets[:2] + (duplicate,),
    )
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda db, game, **kwargs: calls.append((db, game, kwargs)) or row,
    )

    outcome = service.import_refresh_target_in_session(session, target)

    assert outcome.status == "imported"
    assert outcome.game_id == 42
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.closes == 0
    _, game, kwargs = calls[0]
    assert game.source == "taptap"
    assert kwargs["game_row"] is row
    assert kwargs["category_id"] == 9
    assert [asset[0] for asset in kwargs["pre_downloaded"]] == ["https://img.example/shot.jpg"]


def _discovery_item(*, source="baidu", source_game_id="baidu-101"):
    return DiscoveryImportItem(
        item_key="discovery-101",
        source=source,
        source_game_id=source_game_id,
        status="success",
        game_data={
            "game": {
                "source": source,
                "source_game_id": source_game_id,
                "name": "《 原神 》",
                "score": 8.8,
                "tags": ["开放世界"],
                "platforms": ["android"],
                "comment_count": 10,
                "icon_url": "https://img.example/discovery-icon.png",
                "screenshot_urls": [],
                "description": "discovery fixture",
                "raw": {"source": source_game_id},
            }
        },
        assets=(),
    )


def test_discovery_source_identity_precedes_name_and_reuses_existing_category():
    identity_row = SimpleNamespace(id=101, stock_category_id=77)
    session = _FakeSession(None)
    calls = []
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda db, game, **kwargs: calls.append((db, game, kwargs)) or identity_row,
        source_identity_lookup=lambda db, source, source_game_id: [identity_row],
        normalized_name_lookup=lambda db, normalized: [identity_row],
    )

    outcome = service.import_discovery_item_in_session(session, _discovery_item())

    assert outcome.status == "imported"
    assert outcome.game_id == 101
    _, _, kwargs = calls[0]
    assert kwargs["game_row"] is identity_row
    assert kwargs["category_id"] == 77
    assert session.commits == 0


def test_discovery_uses_normalized_name_only_after_source_identity_miss():
    normalized_row = SimpleNamespace(id=202, stock_category_id=88)
    session = _FakeSession(None)
    calls = []
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda db, game, **kwargs: calls.append((db, game, kwargs)) or normalized_row,
        source_identity_lookup=lambda db, source, source_game_id: [],
        normalized_name_lookup=lambda db, normalized: [normalized_row],
    )

    outcome = service.import_discovery_item_in_session(session, _discovery_item())

    assert outcome.status == "imported"
    assert outcome.game_id == 202
    assert calls[0][2]["game_row"] is normalized_row
    assert calls[0][2]["category_id"] == 88


def test_discovery_isolates_ambiguous_identity_or_conflicting_name_without_upsert():
    identity_row = SimpleNamespace(id=303, stock_category_id=9)
    other_name_row = SimpleNamespace(id=304, stock_category_id=10)
    session = _FakeSession(None)
    calls = []
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda *_args, **_kwargs: calls.append("called"),
        source_identity_lookup=lambda db, source, source_game_id: [identity_row],
        normalized_name_lookup=lambda db, normalized: [other_name_row],
    )

    outcome = service.import_discovery_item_in_session(session, _discovery_item())

    assert outcome.status == "conflict"
    assert "identity" in (outcome.error or "")
    assert calls == []
    assert session.commits == 0
    assert session.rollbacks == 0


def test_discovery_isolates_multiple_rows_claiming_the_same_source_identity():
    first = SimpleNamespace(id=401, stock_category_id=9)
    second = SimpleNamespace(id=402, stock_category_id=9)
    session = _FakeSession(None)
    calls = []
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda *_args, **_kwargs: calls.append("called"),
        source_identity_lookup=lambda db, source, source_game_id: [first, second],
        normalized_name_lookup=lambda db, normalized: [],
    )

    outcome = service.import_discovery_item_in_session(session, _discovery_item())

    assert outcome.status == "conflict"
    assert "multiple games" in (outcome.error or "")
    assert calls == []


def test_discovery_creates_a_game_only_when_both_identity_and_name_are_absent():
    created_row = SimpleNamespace(id=505, stock_category_id=90)
    session = _FakeSession(None)
    calls = []
    service = CollectorImportService(
        session_factory=lambda: session,
        upsert_func=lambda db, game, **kwargs: calls.append((db, game, kwargs)) or created_row,
        source_identity_lookup=lambda db, source, source_game_id: [],
        normalized_name_lookup=lambda db, normalized: [],
    )

    outcome = service.import_discovery_item_in_session(session, _discovery_item())

    assert outcome.status == "imported"
    assert outcome.game_id == 505
    assert calls[0][2]["game_row"] is None
    assert calls[0][2]["category_id"] is None


def test_discovery_non_success_is_skipped_without_session_work():
    item = DiscoveryImportItem(
        item_key="discovery-miss",
        source="baidu",
        source_game_id=None,
        status="miss",
        game_data=None,
        assets=(),
    )
    service = CollectorImportService(
        session_factory=lambda: (_ for _ in ()).throw(AssertionError("must not open DB")),
        upsert_func=lambda *_args, **_kwargs: None,
    )

    assert service.import_discovery_item(item).status == "skipped"
