## 1. Bundle Contract and Validation

- [x] 1.1 Add failing tests for schema version, required fields, path containment, byte limits,
  MIME signatures, SHA-256 integrity, and duplicate targets
- [x] 1.2 Implement the dependency-free bundle schema/validation module until the contract tests pass

## 2. Windows Collector

- [x] 2.1 In the external Collector repository, add fixture-driven failing tests for manifest
  bounds, exact-title matching, block-response
  classification, checkpoint resume behavior, and report sanitization
- [x] 2.2 In the external Collector repository, add the ASCII-only PowerShell 5.1 collector,
  UTF-8 sample manifest, and launchers for
  bounded metadata/image collection
- [x] 2.3 In the external Collector repository, implement per-game checkpointing, pacing,
  stop-on-403/405/429, bundle inventory, hashes,
  and stability report generation

## 3. Production Importer

- [x] 3.1 Add failing service tests for dry-run planning, existing-row/category targeting,
  pre-downloaded images, per-game rollback/continue, idempotency, and no-delete behavior
- [x] 3.2 Implement the production `remote_game_bundle_import.py` CLI using validated bundles and
  `upsert_game(pre_downloaded=..., pre_downloaded_icon=...)`
- [x] 3.3 Add a read-only target-manifest export CLI for selecting the first 10–20 production games

## 4. Verification and Handoff

- [x] 4.1 Run focused unit tests, Ruff, formatting checks, and mypy for all changed Python files
- [x] 4.2 Run a one-game Windows collection against the validated host and validate its bundle with
  the production dry-run path
- [x] 4.3 Document the 24–48 hour stability procedure, authorization gate, manual transfer,
  production import, verification, and rollback commands
