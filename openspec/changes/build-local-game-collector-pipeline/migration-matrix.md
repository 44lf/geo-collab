# Collector migration and GEO ownership matrix

This matrix is the implementation boundary for Phase 1. It records the proven source code that
may move into the Collector and the normalization, identity, database, and object-storage behavior
that must remain owned by GEO.

## Repository ownership

| Concern | Collector repository | GEO repository |
|---|---|---|
| Source HTTP and parsing | Execute the four approved adapters and preserve bounded raw evidence | Stop issuing source requests after external cutover |
| Scheduling | Persist due times, bounded catch-up, pacing, and immutable claimed jobs | Own approved configuration and select eligible refresh/discovery targets |
| Canonical acquisition result | Emit the versioned intermediate `CanonicalGameResult` | Convert validated results to `game_library.types.Game` |
| Identity and merge | Preserve source identity and Gateway target identity without deciding merges | Normalize names, anchor refresh targets, resolve discovery identity, and merge |
| Bundle transport | Package, hash, spool, upload, retry, and retain until a matching receipt | Authenticate nodes, authorize uploads, validate, claim, import, and issue receipts |
| Database | No connection and no credentials | Own all MySQL models, transactions, item receipts, and business state |
| Object storage | No business-MinIO credentials; only short-lived Inbox upload authorization | Own Inbox verification and all business-MinIO category/image writes |
| Logs and evidence | Rotated JSONL plus bounded raw Bundle evidence | Operator events, transfer/import state, sanitized management views |

## Source adapter migration

| Source | Authoritative behavior to port | Fixtures/tests to port or add | Collector policy | GEO-owned code that must not move |
|---|---|---|---|---|
| Baidu | `server/app/modules/game_library/sources/baidu.py`: category `game_filter` and exact-name `game_query` parsing | Port the response shapes and fake HTTP coverage from `server/tests/test_game_sources.py` | Put HTTP, timeout, and finite transient retry behind the common source transport; preserve exact-name matching | Due-game selection, name normalization, target merge, DB/category/image writes |
| NineGame | `server/app/modules/game_library/sources/ninegame.py`: search-result page only; do not add the captcha-blocked detail page | Port `_CARD` and parsing/matcher coverage from `server/tests/test_game_ninegame.py` | Preserve strict matcher and miss semantics; classify network/HTTP errors in the common runtime | `ingest_service` outcome application and all persistence |
| YingYongBao | Port acquisition semantics from `planb/model.py`, `planb/yingyongbao.py`, and `planb/ingest.py` | Add frozen `__NEXT_DATA__` list/detail HTML fixtures; existing `test_game_discovery.py` remains GEO import coverage | Use the common TLS-validating transport, bounded details/media, and conservative pacing; do not copy the existing TLS-verification bypass | Do not port `planb/db_ingest.py`; GEO retains normalized-name resolution, horizontal-image handling, category reuse, SQLAlchemy, and MinIO |
| TapTap | Port category/detail/name-search and XSRF flow from `C:/Users/Administrator/Desktop/game-search/scripts/game_search/sources/taptap.py` | Add frozen category, detail, and search JSON fixtures plus 403/405/429 halt and timeout/5xx retry tests | Any 403, 405, or 429 halts the remaining TapTap run; do not copy desktop registry fallback that swallows an error and silently switches source | Refresh target identity, merge policy, `upsert_game`, image storage, and production scheduler state |

The proven PowerShell Spike remains an operational compatibility and diagnostic asset. Its
TapTap block behavior and Bundle v1 fixtures are evidence for the Python runtime, but its
one-shot upload scripts, fixed bundle identity, and latest-only Consumer behavior are not the new
runtime architecture.

## Scheduling, logging, and Bundle migration

| Existing behavior | Reuse/port | Do not copy |
|---|---|---|
| `game_library/scheduler.py` time-window, bounded gap, quota, and attempt-cap pure behavior | Port pure calculations and outcome vocabulary with clock/random injection | Web-process daemon threads, SQLAlchemy sessions, or in-process locks |
| `planb/discovery_scheduler.py` configured discovery window and bounded run | Port the approved schedule/job shape | GEO database reads from the Collector |
| `game_library/backfill_bundle.py` path, count, byte, MIME, and SHA-256 validation | Generalize into Bundle schema v2 and keep v1 compatibility in GEO | TapTap-only source restriction or directory-copy transport |
| Current run summary/event fields | Preserve the useful outcome and correlation vocabulary | Free-text-only logs, raw bodies, Cookie/XSRF, credentials, or pre-signed URLs |

## Bundle v2 acquisition contract

The Collector emits evidence, not a second production database model. Each immutable job and
Bundle carries:

- `schema_version`, `collector_id`, `job_id`, `run_id`, `bundle_id`, `transport_id`,
  `destination`, `policy_version`, schedule occurrence, mode (`refresh` or `discovery`), source
  adapter version, and Collector version;
- bounded job targets or discovery paths exactly as issued by the Gateway;
- one result per attempted item with stable `item_key`, outcome (`success`, `miss`, `blocked`, or
  `error`), source, source game ID, source URL, optional GEO `target_game_id` and `category_id`,
  request attempt/status summaries, and an evidence path;
- the acquisition fields needed to construct `game_library.types.Game`: `name`, `score`, `tags`,
  `platforms`, `comment_count`, `icon_url`, `screenshot_urls`, `android_package`, `description`,
  and bounded `raw`;
- media references that preserve role, source URL, declared MIME, byte count, and SHA-256;
- a complete safe relative-path inventory for normalized records, raw evidence, and media, plus
  archive byte count and SHA-256 in transfer metadata.

Collector-side “normalization” is limited to structural canonicalization: types, missing-value
handling, stable source/platform constants, URL/media inventory, and deterministic serialization.
It must not normalize a name for database identity, merge two sources, create a category, decide
soft deletion, or write a business object key.

## GEO normalization and storage mapping

After archive, manifest, source identity, job, target, file hash, and MIME validation, the
Consumer uses these authoritative paths:

### Known-target refresh

1. Load the Gateway-issued `target_game_id`.
2. Reject target/job/source mismatches; never fall back to creating another game.
3. Convert the validated result to `server.app.modules.game_library.types.Game`.
4. Call `server.app.modules.game_library.service.upsert_game` with:
   - `game_row` set to the existing target row;
   - `category_id` set to its existing `stock_category_id`;
   - validated `pre_downloaded` screenshot bytes;
   - validated `pre_downloaded_icon` bytes.
5. Commit the game update and unique `(transport_id, item_key)` receipt in the same MySQL
   transaction.

### Discovery

1. Preserve `(source, source_game_id)` as source identity.
2. Apply `game_library.normalization.normalize_game_name` only inside GEO.
3. Resolve the documented source-identity/normalized-name policy and isolate ambiguous conflicts.
4. Convert to `game_library.types.Game` and call `upsert_game`; reuse an existing category when
   resolved or let the GEO service create the companion category.
5. Commit the game update and item receipt together.

### Business MinIO

- The Consumer never inserts `StockImage` rows or writes business buckets directly.
- `upsert_game` delegates validated image bytes to
  `image_library.service.store_image_bytes(..., source_url=..., commit=False)`.
- Screenshot and icon deduplication follows the current `(category_id, source_url_hash)` rule,
  where `source_url_hash` is produced by `source_url_sha256`.
- The existing image service owns bucket selection, object upload, `StockImage`, and local proxy
  URL behavior. Inbox object keys and archive hashes are transport data and never become business
  image keys.

## Compatibility and cutover rules

- GEO Bundle v1 import remains available for the completed TapTap Spike while Bundle v2 is added.
- No Collector receives GEO MySQL or business-MinIO credentials.
- Miss, blocked, invalid, absent, or failed items never delete or deactivate an existing game.
- The in-process GEO scheduler remains the default until DEV proves the external path; both modes
  must not schedule the same workload.
- No production deployment, tag, scheduler cutover, or credential provisioning is part of this
  implementation change without separate authorization.
