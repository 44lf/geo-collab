## 1. Contracts and Repository Foundation

- [x] 1.1 Record a GEO/Desktop/Spike-to-Collector migration matrix for Baidu, NineGame,
  YingYongBao, TapTap, scheduling, Bundle validation, and logging, including exact source fixtures
  and code that must remain GEO-owned.
- [x] 1.2 Add failing Collector contract tests for canonical game results, immutable job manifests,
  correlation identities, error categories, and supported source registration.
- [x] 1.3 Scaffold the cross-platform Python Collector package, development dependencies, lint/type
  gates, and test entry points until the foundation contract tests pass.
- [x] 1.4 Specify and test Bundle schema v2 for multi-source refresh/discovery jobs, raw/normalized
  evidence, full file inventory, component versions, archive integrity, and bounded limits while
  documenting legacy v1 compatibility.

## 2. Proven Source Adapter Migration

- [x] 2.1 Port Baidu fixtures and failing behavior tests from GEO, then migrate the Python adapter
  without changing its verified request/parsing semantics.
- [x] 2.2 Port NineGame fixtures and failing behavior tests from GEO, then migrate the Python
  adapter without changing its verified request/parsing semantics.
- [x] 2.3 Port YingYongBao list/detail/discovery fixtures and failing behavior tests from GEO, then
  migrate its dependency-light client, model, parser, and bounded detail behavior.
- [x] 2.4 Port the Python TapTap category/detail/name-search adapter and fixtures from the Desktop
  `game-search` skill, add failing 403/405/429 halt and transient-retry tests, and implement the
  common runtime policy.
- [x] 2.5 Add registry-level tests proving all four adapters emit the canonical result contract,
  preserve raw evidence, and reject unknown sources before network access.

## 3. Local Scheduling, Jobs, and Spool

- [x] 3.1 Add failing pure tests for GEO-compatible time windows, quotas, bounded random gaps,
  attempt caps, configuration versions, and manual/scheduled use of one execution path, then port
  the scheduling rules.
- [x] 3.2 Add failing tests for persisted `next_due_at`, startup/resume catch-up age/count limits,
  stale-occurrence skipping, and expired-configuration safe stop, then implement scheduler state.
- [x] 3.3 Add failing tests for immutable job persistence, per-target checkpoints, atomic state
  replacement, restart recovery, and the global Collector/data-directory lock, then implement the
  local job store.
- [x] 3.4 Add failing tests for spool state transitions, high-water backpressure, unacknowledged
  retention, processed grace-period cleanup, and failed-evidence retention, then implement the
  spool manager.
- [x] 3.5 Add failing tests for deterministic schema-v2 packaging and archive/file
  size/hash/MIME/path validation, then implement the Bundle builder.

## 4. GEO Gateway Data Model and Security

- [x] 4.1 Add failing model/migration tests for Collector identities, configuration versions, jobs,
  nodes, runs, events, transfers, transfer receipts, and unique per-item receipts.
- [x] 4.2 Create the Alembic migration and SQLAlchemy models with identity, idempotency, lease,
  status-transition, and correlation constraints until model tests pass.
- [x] 4.3 Add failing Gateway authentication/authorization tests for valid, revoked, cross-node,
  cross-destination, and secret-redaction cases, then implement revocable scoped Collector auth.
- [x] 4.4 Add failing API/service tests for versioned configuration delivery and bounded immutable
  refresh/discovery job claiming through GEO due-game and discovery configuration services, then
  implement the endpoints.
- [x] 4.5 Add failing API/service tests for idempotent transfer create, immutable-field conflict,
  short-lived fixed-key upload authorization, object completion verification, and terminal status
  polling, then implement transfer endpoints.
- [x] 4.6 Add failing API/service tests for bounded heartbeat/event batches, event ID deduplication,
  receive timestamps, stale-node calculation, and sanitized payloads, then implement telemetry
  endpoints.

## 5. Collector Gateway Client and Reliable Transfer

- [x] 5.1 Add failing Collector client tests for scoped authentication, configuration caching,
  immutable job persistence before source access, and idempotent job acknowledgement, then
  implement the Gateway control client.
- [x] 5.2 Add failing tests for transfer create, short-lived upload, remote object verification,
  complete/status calls, and the full local transfer state machine, then implement the transfer
  client.
- [x] 5.3 Add fault tests for interrupted upload, expired upload authorization, uploaded-object
  completion failure, duplicate create/complete, and local restart, then implement same-identity
  resume without re-collection.
- [x] 5.4 Add failing retry-classification tests for timeout/connection/5xx backoff and immediate
  auth/identity/hash/schema stop, then implement finite backoff, jitter, and deferred drain.
- [x] 5.5 Add failing receipt tests proving local cleanup requires matching processed
  `transport_id` and archive SHA-256 and that failed receipts preserve evidence, then implement
  acknowledgement polling and cleanup.

## 6. GEO Consumer and Import Service

- [x] 6.1 Add failing tests for oldest-first bounded transfer claiming, expiring leases, reclaim,
  concurrent Consumers, and duplicate ready delivery, then implement the claim repository.
- [x] 6.2 Add failing validation tests for archive limits, path traversal, symlinks, encrypted
  members, archive/file hashes, MIME signatures, schema v2, and job/target consistency, then
  implement layered validation and isolation.
- [x] 6.3 Extract the existing remote Bundle importer into a tested internal
  `CollectorImportService` that converts validated records and reuses GEO normalization,
  `upsert_game`, category/tag, and image services.
- [x] 6.4 Add failing refresh-import tests for target anchoring, category drift, cross-source
  merge, image URL-hash deduplication, and no deletion/deactivation on miss/blocked/failure, then
  implement the refresh path.
- [x] 6.5 Add failing discovery-import tests for source identity, normalized-name resolution,
  conflict isolation, new-game creation, and replay idempotency, then implement the discovery
  path.
- [x] 6.6 Add crash-injection tests proving each game write and item receipt commit together,
  partially committed Bundles resume remaining items, and processed transfer state survives status
  publication/polling failure.
- [x] 6.7 Add failing retry/dead-letter tests for temporary MySQL/business-MinIO failures,
  permanent validation/business failures, attempt ceilings, sanitized reasons, and replay
  evidence, then implement terminal classification.
- [x] 6.8 Add the standalone Consumer process entry point, graceful shutdown, health/readiness,
  bounded poll loop, and structured correlation logs with focused process tests.

## 7. External Scheduler Cutover

- [x] 7.1 Add failing configuration/startup tests for mutually exclusive `in_process` and
  `external` game-ingest execution modes, preserving current behavior by default.
- [x] 7.2 Implement the external-execution gate so GEO web startup does not launch the legacy
  refresh/discovery threads when external mode is explicitly enabled.
- [x] 7.3 Add integration tests proving Collector refresh jobs use GEO due-game selection and
  discovery jobs use approved discovery configuration without exposing a database session or
  credentials to the Agent.
- [x] 7.4 Document cutover, rollback, outstanding-job drain, and the prohibition on running both
  scheduler modes for the same workload.

## 8. Operator Visibility and Technical Logs

- [x] 8.1 Add failing management API tests for authenticated read-only node, run, event, transfer,
  backlog, receipt, and sanitized failure detail queries, then implement the APIs.
- [x] 8.2 Add the GEO Web Collector overview and run-detail timeline with tests/type checks for
  online/stale nodes, current stage, source outcomes, spool/backlog, transfer/import status, and
  correlation IDs.
- [x] 8.3 Add shared structured-log helpers and tests that enforce UTC timestamps, correlation
  fields, component versions, error fingerprints, and removal of credentials, Cookie/XSRF,
  pre-signed URLs, sensitive queries, and raw bodies.
- [x] 8.4 Add local JSONL rotation/buffering and an OpenTelemetry-compatible export boundary,
  proving that telemetry/backend failure does not fail or delete a Bundle.
- [x] 8.5 Define and test separate retention/pressure policies for events, technical logs,
  processed Bundles, raw evidence, and dead-letter evidence.

## 9. Local Packaging and Operations

- [x] 9.1 Add a native local-computer launcher/package with persistent data paths, OS-secret-store
  integration, foreground diagnostic mode, clean shutdown, and version output.
- [x] 9.2 Add a non-root Linux Dockerfile and local Compose example using the same Agent entry point
  and a persistent spool volume, without making Docker a Phase 1 runtime requirement.
- [x] 9.3 Add health/readiness/metrics contracts and platform-neutral SIGTERM/interrupt handling
  tests so the same artifacts remain future Kubernetes compatible.
- [x] 9.4 Write operator documentation for installation, identity provisioning, manual run,
  background scheduling, spool inspection, safe upgrade/rollback, credential rotation, and
  uninstall without deleting pending data.

## 10. End-to-End and Fault-Injection Acceptance

- [x] 10.1 Run focused Collector, Gateway, Consumer, migration, import, API, Web typecheck/build,
  lint, formatting, and secret-scan gates and capture exact evidence.
- [ ] 10.2 Deploy the new Gateway, Inbox policy, Consumer, and management view to DEV only, then
  run one bounded local four-source schedule through Bundle upload, geo_dev/business-MinIO import,
  processed receipt, local cleanup eligibility, and Web timeline.
  - Isolated DEV checkpoint passed on 2026-07-29, including processed receipt, independent
    archive/business-object hash verification, Web timeline, and restart/resume evidence. See
    `dev-test-2026-07-29.md`. This remains unchecked because shared `geo_dev` has an unreconciled
    Alembic revision and was not modified.
- [ ] 10.3 Inject local Agent restart, network interruption, expired upload authorization,
  duplicate complete, Gateway restart, Consumer crash after partial item commits, and DEV outage,
  proving no Bundle loss or duplicate logical data.
- [ ] 10.4 Enable external scheduler mode in DEV, verify the legacy in-process schedulers are not
  running, and observe repeated low-frequency scheduled cycles with backlog and log correlation.
- [ ] 10.5 Produce a Phase 1 acceptance and production-readiness report listing passed evidence,
  unresolved risks/open questions, rollback steps, and the separate approvals/infrastructure still
  required; do not deploy, tag, or enable production as part of this change.
