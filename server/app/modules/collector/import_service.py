"""GEO-owned import boundary for validated external Collector results."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func

from server.app.modules.game_library import backfill_bundle, types
from server.app.modules.game_library.models import Game as GameRow
from server.app.modules.game_library.normalization import normalize_game_name

SessionFactory = Callable[[], Any]
UpsertFunc = Callable[..., Any]
GameLookup = Callable[[Any, str, str], list[GameRow]]
NormalizedNameLookup = Callable[[Any, str], list[GameRow]]


@dataclass(frozen=True)
class ImportOutcome:
    status: str
    error: str | None = None
    game_id: int | None = None


class ImportContractError(RuntimeError):
    """A validated item cannot safely be applied through GEO's business contracts."""


_SOURCES = {"baidu", "ninegame", "yingyongbao", "taptap"}


def _required_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ImportContractError(f"canonical payload field {field} is invalid")
    return value


def _optional_text(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or len(value) > maximum:
        raise ImportContractError(f"canonical payload field {field} is invalid")
    return value


def _string_list(
    value: object,
    *,
    field: str,
    max_items: int,
    max_item_length: int,
) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ImportContractError(f"canonical payload field {field} is invalid")
    result: list[str] = []
    for item in value:
        result.append(_required_text(item, field=field, maximum=max_item_length))
    return result


def _http_url(value: object, *, field: str) -> str | None:
    text = _optional_text(value, field=field, maximum=2048)
    if text is None:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise ImportContractError(f"canonical payload field {field} is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImportContractError(f"canonical payload field {field} is invalid")
    return text


@dataclass(frozen=True)
class DiscoveryImportItem:
    """Validated schema-v2 discovery item prepared by the Consumer coordinator.

    This is deliberately a small bridge type: archive extraction and item receipts stay in the
    Consumer coordinator, while this service receives only validated game data and media.
    """

    item_key: str
    source: str
    source_game_id: str | None
    status: str
    game_data: dict[str, Any] | None
    assets: tuple[backfill_bundle.ValidatedFile, ...]


def canonical_game_from_payload(payload: dict[str, Any]) -> types.Game:
    """Convert a validated acquisition payload to GEO's canonical game type."""

    raw = payload.get("game", payload)
    if not isinstance(raw, dict):
        raise ImportContractError("canonical payload game must be an object")
    source = _required_text(raw.get("source"), field="source", maximum=32)
    if source not in _SOURCES:
        raise ImportContractError("canonical payload source is unsupported")
    source_game_id = _required_text(
        raw.get("source_game_id", raw.get("game_id")),
        field="source_game_id",
        maximum=255,
    )
    name = _required_text(raw.get("name"), field="name", maximum=200)
    score = raw.get("score")
    if score is not None and (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not 0 <= float(score) <= 10
    ):
        raise ImportContractError("canonical payload field score is invalid")
    comment_count = raw.get("comment_count")
    if comment_count is not None and (
        isinstance(comment_count, bool)
        or not isinstance(comment_count, int)
        or not 0 <= comment_count <= 2_147_483_647
    ):
        raise ImportContractError("canonical payload field comment_count is invalid")
    tags_value = raw.get("tags")
    tags = _string_list(
        [] if tags_value is None else tags_value,
        field="tags",
        max_items=100,
        max_item_length=100,
    )
    platforms_value = raw.get("platforms")
    platforms = _string_list(
        [] if platforms_value is None else platforms_value,
        field="platforms",
        max_items=16,
        max_item_length=32,
    )
    screenshot_values_raw = raw.get("screenshot_urls")
    screenshot_values = _string_list(
        [] if screenshot_values_raw is None else screenshot_values_raw,
        field="screenshot_urls",
        max_items=20,
        max_item_length=2048,
    )
    screenshot_urls = [_http_url(value, field="screenshot_urls") for value in screenshot_values]
    if any(value is None for value in screenshot_urls):
        raise ImportContractError("canonical payload field screenshot_urls is invalid")
    raw_evidence_value = raw.get("raw")
    raw_evidence = {} if raw_evidence_value is None else raw_evidence_value
    if not isinstance(raw_evidence, dict):
        raise ImportContractError("canonical payload field raw is invalid")
    try:
        raw_size = len(
            json.dumps(raw_evidence, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ImportContractError("canonical payload field raw is invalid") from exc
    if raw_size > 1024 * 1024:
        raise ImportContractError("canonical payload field raw exceeds byte limit")
    return types.Game(
        source=source,
        game_id=source_game_id,
        name=name,
        score=float(score) if score is not None else None,
        tags=tags,
        platforms=platforms,
        comment_count=comment_count,
        icon_url=_http_url(raw.get("icon_url"), field="icon_url"),
        screenshot_urls=[str(value) for value in screenshot_urls],
        android_package=_optional_text(
            raw.get("android_package"),
            field="android_package",
            maximum=255,
        ),
        description=_optional_text(
            raw.get("description"),
            field="description",
            maximum=200_000,
        ),
        raw=raw_evidence,
    )


def validated_asset_bytes(
    target: backfill_bundle.ValidatedTarget,
) -> tuple[
    tuple[str, bytes, str] | None,
    list[tuple[str, bytes, str]],
]:
    """Read only media already accepted by the Bundle validator."""

    return _validated_asset_bytes(target.assets)


def _validated_asset_bytes(
    assets: tuple[backfill_bundle.ValidatedFile, ...],
) -> tuple[
    tuple[str, bytes, str] | None,
    list[tuple[str, bytes, str]],
]:
    icon = None
    screenshots = []
    seen_screenshot_urls: set[str] = set()
    for asset in assets:
        item = (asset.source_url or "", asset.path.read_bytes(), asset.media_type)
        if asset.role == "icon" and icon is None:
            icon = item
        elif asset.role == "screenshot" and (asset.source_url or "") not in seen_screenshot_urls:
            screenshots.append(item)
            seen_screenshot_urls.add(asset.source_url or "")
    return icon, screenshots


class CollectorImportService:
    """Import validated refresh targets through GEO's existing business services.

    Bundle validation and transfer/item receipt orchestration are deliberately outside this
    class. The service owns the business-write boundary: anchor to the Gateway-issued game row,
    reject category drift, pass validated media bytes to ``upsert_game``, and commit or roll back
    one game at a time.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        upsert_func: UpsertFunc | None = None,
        source_identity_lookup: GameLookup | None = None,
        normalized_name_lookup: NormalizedNameLookup | None = None,
    ) -> None:
        if session_factory is None:
            from server.app.db.session import SessionLocal

            session_factory = SessionLocal
        if upsert_func is None:
            from server.app.modules.game_library.service import upsert_game

            upsert_func = upsert_game
        self._session_factory = session_factory
        self._upsert_func = upsert_func
        self._source_identity_lookup = source_identity_lookup or _find_source_identity
        self._normalized_name_lookup = normalized_name_lookup or _find_normalized_name

    def import_refresh_target(
        self,
        target: backfill_bundle.ValidatedTarget,
    ) -> ImportOutcome:
        """Import one successful, validated known-target refresh."""

        if target.status != "success":
            return ImportOutcome(status="skipped")

        db = self._session_factory()
        try:
            outcome = self.import_refresh_target_in_session(db, target)
            db.commit()
            return outcome
        except Exception as exc:
            db.rollback()
            return ImportOutcome(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            db.close()

    def import_refresh_target_in_session(
        self,
        db: Any,
        target: backfill_bundle.ValidatedTarget,
    ) -> ImportOutcome:
        """Apply one refresh without committing, rolling back, or closing ``db``.

        Consumer receipt orchestration calls this method and commits the game write plus its item
        receipt in one outer transaction. A refresh is always anchored to the issued target row;
        it never falls back to normalized-name or source-identity lookup.
        """
        if target.status != "success":
            return ImportOutcome(status="skipped")
        if target.game_data is None:
            raise ImportContractError("validated success target has no game data")
        game = canonical_game_from_payload(target.game_data)
        game_row = db.get(GameRow, target.target_game_id)
        if game_row is None:
            raise ImportContractError(f"production game {target.target_game_id} not found")
        if target.category_id is not None and game_row.stock_category_id != target.category_id:
            raise ImportContractError(
                f"production game {target.target_game_id} category changed: "
                f"bundle={target.category_id} current={game_row.stock_category_id}"
            )
        return self._upsert_validated_game(
            db,
            game,
            target.assets,
            game_row=game_row,
            category_id=game_row.stock_category_id,
        )

    def import_discovery_item(self, item: DiscoveryImportItem) -> ImportOutcome:
        """Standalone discovery helper retained for diagnostics and one-off imports."""
        if item.status != "success":
            return ImportOutcome(status="skipped")
        db = self._session_factory()
        try:
            outcome = self.import_discovery_item_in_session(db, item)
            if outcome.status == "imported":
                db.commit()
            return outcome
        except Exception as exc:
            db.rollback()
            return ImportOutcome(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            db.close()

    def import_discovery_item_in_session(
        self,
        db: Any,
        item: DiscoveryImportItem,
    ) -> ImportOutcome:
        """Resolve and apply one discovery item without ending the caller transaction.

        Source identity is checked first. A single normalized-name match is considered only when
        the source identity is absent. Any multiple match, or an identity/name disagreement, is
        returned as an isolated conflict rather than silently merging distinct games.
        """
        if item.status != "success":
            return ImportOutcome(status="skipped")
        if item.game_data is None or not item.source_game_id:
            raise ImportContractError("successful discovery item requires game data and source ID")
        game = canonical_game_from_payload(item.game_data)
        if game.source != item.source or game.game_id != item.source_game_id:
            raise ImportContractError(
                "discovery item source identity disagrees with normalized game"
            )
        normalized_name = normalize_game_name(game.name)
        if not normalized_name:
            raise ImportContractError("discovery game name cannot be normalized")

        identity_rows = self._source_identity_lookup(db, item.source, item.source_game_id)
        name_rows = self._normalized_name_lookup(db, normalized_name)
        resolved, conflict = _resolve_discovery_game(identity_rows, name_rows)
        if conflict is not None:
            return ImportOutcome(status="conflict", error=conflict)
        category_id = resolved.stock_category_id if resolved is not None else None
        return self._upsert_validated_game(
            db,
            game,
            item.assets,
            game_row=resolved,
            category_id=category_id,
        )

    def _upsert_validated_game(
        self,
        db: Any,
        game: types.Game,
        assets: tuple[backfill_bundle.ValidatedFile, ...],
        *,
        game_row: GameRow | None,
        category_id: int | None,
    ) -> ImportOutcome:
        icon, screenshots = _validated_asset_bytes(assets)
        row = self._upsert_func(
            db,
            game,
            max_screenshots=len(screenshots),
            pre_downloaded=screenshots,
            category_id=category_id,
            game_row=game_row,
            pre_downloaded_icon=icon,
        )
        return ImportOutcome(status="imported", game_id=getattr(row, "id", None))


def _find_source_identity(db: Any, source: str, source_game_id: str) -> list[GameRow]:
    """Use the persisted source list as the first discovery identity key."""
    source_entry = func.json_object(
        "source",
        source,
        "source_game_id",
        source_game_id,
    )
    return list(
        db.query(GameRow).filter(func.json_contains(GameRow.sources, source_entry) == 1).all()
    )


def _find_normalized_name(db: Any, normalized_name: str) -> list[GameRow]:
    return list(db.query(GameRow).filter(GameRow.name_normalized == normalized_name).all())


def _resolve_discovery_game(
    identity_rows: list[GameRow],
    name_rows: list[GameRow],
) -> tuple[GameRow | None, str | None]:
    if len(identity_rows) > 1:
        return None, "multiple games claim this source identity"
    if len(name_rows) > 1:
        return None, "multiple games share this normalized name"
    identity_row = identity_rows[0] if identity_rows else None
    name_row = name_rows[0] if name_rows else None
    if identity_row is not None and name_row is not None and identity_row.id != name_row.id:
        return None, "source identity conflicts with normalized-name match"
    return identity_row or name_row, None
