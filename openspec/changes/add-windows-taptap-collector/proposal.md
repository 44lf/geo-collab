## Why

The production server's TapTap requests are blocked at its cloud egress, while a controlled
Spike proved that an approved company Windows server can retrieve TapTap metadata and images.
We need a decoupled collector and import path so production can receive fresh game data without
depending on the company server at request time or exposing production databases to it.

## What Changes

- Add a dependency-light Windows collector that accepts an explicit game manifest, retrieves
  TapTap search/detail metadata and image bytes, and produces a versioned result bundle.
- Add bounded request pacing, stop-on-block behavior, resumability, per-game error isolation, and
  a machine-readable stability report for 10–20 game validation runs.
- Add a production-side dry-run/import CLI that validates bundle hashes and sizes, reconstructs
  `Game` values, and reuses `upsert_game(pre_downloaded=...)`.
- Make imports idempotent, commit per game, and never infer soft deletion from missing or failed
  collector results.
- Keep the current Baidu automatic ingest registry unchanged; TapTap remains an external
  collector source rather than an in-process production scheduler source.

## Capabilities

### New Capabilities

- `windows-taptap-collector`: Authorized Windows collection, pacing, resumability, image
  acquisition, and stability evidence generation.
- `game-backfill-bundle-import`: Versioned bundle validation and idempotent production import into
  the existing game library and image store.

### Modified Capabilities

None.

## Impact

- Standalone collector code, Windows launchers, bundle documentation, and collector tests live in
  the dedicated `geo/geo-taptap-collector` GitLab repository.
- New production import CLI under `server/scripts/`.
- Reuse of `server.app.modules.game_library.types.Game` and
  `server.app.modules.game_library.service.upsert_game`.
- New unit tests for bundle schema, integrity checks, resumability, block handling, and import
  semantics.
- The collector repository owns bundle production; GEO owns strict schema-v1 consumption and
  production persistence.
- No database migration, no new public API, no direct Windows-to-MySQL connection, and no change
  to the current Baidu scheduler source registry.
