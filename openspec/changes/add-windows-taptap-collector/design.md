## Context

The production game library currently registers Baidu as its automatic ingest source. TapTap
collection code was retired after the production cloud egress repeatedly received HTTP 405.
A 2026-07-28 Spike on an approved company Windows host proved that its public egress can complete
XSRF bootstrap, exact-name search, detail retrieval, icon download, and screenshot download with
HTTP 200 responses. Host and IP evidence remain in the restricted Spike artifacts rather than
the repository.

The Windows host has no Python installation. A PyInstaller probe could open TCP connections but
failed its HTTPS requests, while the built-in Windows .NET HTTP stack completed the entire flow.
The production importer must run inside the GEO application container so its existing MySQL and
MinIO configuration remains authoritative.

The Windows implementation is maintained in the separate
`https://hlgit.5518game.com/geo/geo-taptap-collector` repository. This GEO change owns only the
consumer contract, target export, production validation, and import behavior.

## Goals / Non-Goals

**Goals:**

- Collect an explicit 10–20 game manifest on Windows without installing Python.
- Produce repeatable stability evidence and a portable, integrity-protected bundle.
- Import valid bundles through existing game-library merge and image-storage behavior.
- Isolate failures per game, resume interrupted runs, stop quickly on platform blocking, and keep
  the current production read path independent of Windows availability.

**Non-Goals:**

- Re-register TapTap in the production scheduler or replace the Baidu source.
- Evade access controls, rotate proxies, spoof fingerprints, or bypass rate limits.
- Expose production MySQL/MinIO to the Windows host.
- Add a public ingestion API or make article generation call the Windows host synchronously.
- Infer removals or soft-delete games from collector misses.

## Decisions

### Use a native PowerShell 5.1 collector

The Windows collector SHALL use the built-in .NET HTTP stack and an ASCII-only PowerShell source
file. Game names are read from UTF-8 JSON at runtime. This matches the successful Spike and avoids
installing Python or depending on the failed PyInstaller TLS behavior.

Alternative considered: install Python and reuse the historical source module. Rejected for the
first release because it adds machine configuration and the packaged Python HTTPS path did not
work on the target host.

The Collector repository owns `collector.ps1`, Windows launchers, examples, and its fixture-driven
tests. GEO does not vendor the collector implementation.

### Keep collection input explicit and bounded

The collector reads a manifest containing at most 20 target rows with `target_game_id`, exact
`name`, `category_id`, and `icon_local`. It performs XSRF bootstrap, exact brand matching, detail
retrieval, and bounded image downloads. It waits between games, records latency/status evidence,
and stops the run on HTTP 403, 405, or 429.

Alternative considered: category crawling and pagination. Rejected because the immediate goal is
stability validation and targeted backfill, not broad discovery.

### Produce a versioned directory bundle before introducing transport services

The collector writes:

```text
bundle/
  manifest.json
  report.json
  games/<target_game_id>/game.json
  games/<target_game_id>/images/*
```

Every image record includes source URL, relative path, media type, byte length, and SHA-256.
`manifest.json` includes `schema_version`, `bundle_id`, collector version, host/evidence metadata,
and a file inventory. A completed directory can be zipped for RDP drive transfer or SCP.

Alternative considered: immediate HTTPS push API. Deferred because a file bundle proves the
contract with less security and operational surface. Object-storage upload can be added later
without changing the bundle schema.

### Resume with an append-safe checkpoint

Each target is written into its own directory and becomes complete only after its metadata and
assets have been flushed and its checkpoint entry is marked successful. A resumed run skips
successful targets, retries ordinary failures, and preserves the prior evidence. A block response
halts the run rather than retrying aggressively.

### Validate before touching the database

The production CLI has two phases:

1. Bundle validation: schema/version, path containment, file inventory, size limits, SHA-256,
   supported MIME signatures, required game fields, and duplicate target detection.
2. Import: reconstruct `types.Game`, resolve the optional target row/category, and call
   `upsert_game(pre_downloaded=..., pre_downloaded_icon=...)`.

`--dry-run` performs phase 1 and prints the planned writes without opening a write transaction.
Formal import commits once per game and rolls back only the failed game.

### Preserve production ownership and source semantics

The Windows host never receives database credentials. Production remains the source of truth and
retains a complete local copy of metadata and image bytes. Import only adds or merges evidence;
missing or failed collection entries never deactivate a game. TapTap remains identified as the
origin in `Game.sources`.

## Risks / Trade-offs

- **TapTap may later block the company egress** → Halt on 403/405/429, keep runs low frequency,
  retain Baidu and existing production data, and require stability evidence before scheduling.
- **Private web APIs may change** → Version the collector and bundle schema; capture per-stage
  status without storing cookies; fail a target rather than guessing new response fields.
- **PowerShell 5.1 encoding can corrupt literals** → Keep script source ASCII-only and decode
  UTF-8 manifest data explicitly.
- **Large or malicious bundles could exhaust resources** → Enforce entry count, per-file and total
  byte limits, canonical path containment, MIME magic checks, and hashes before database work.
- **Manual bundle transfer is operationally slower** → Accept for the first validated release;
  later add presigned object-storage upload while keeping production pull/import semantics.
- **Single successful Spike does not prove durability** → Require repeated 10–20 game runs over
  24–48 hours before enabling Windows Task Scheduler.

## Migration Plan

1. Land collector, bundle schema, importer, and automated tests without changing the source
   registry or scheduler.
2. Export a 10–20 game target manifest from production using a read-only command.
3. Run the collector manually on the Windows host over 24–48 hours.
4. Validate the resulting bundle locally and in the production container using `--dry-run`.
5. Import the small bundle during a non-deployment window and verify MySQL/MinIO/front-end reads.
6. Only after evidence and authorization review, consider Windows Task Scheduler and
   object-storage transfer.

Rollback is operational: stop Windows collection, stop importing bundles, and continue with the
existing Baidu-backed production data. No schema downgrade is required.

## Open Questions

- Which exact 20 production game rows should form the first manifest?
- Has the organization obtained the written authorization required for ongoing TapTap collection?
- After the manual stability gate, should transport remain SCP/RDP bundle copy or move to a
  presigned OSS/MinIO upload?
