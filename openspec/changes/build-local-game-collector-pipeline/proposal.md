## Why

GEO already has working Baidu, NineGame, YingYongBao, and TapTap acquisition code, but the
scheduled collection path is coupled to the GEO web process and the proven external TapTap path is
still operated through Spike-quality scripts. We need a first production-shaped collection
pipeline that can run on an operator's local computer now, move to a server later, and deliver
bounded game bundles reliably without exposing GEO databases or business object storage to the
collector.

## What Changes

- Build a cross-platform Python Collector Agent in the dedicated collector repository, initially
  runnable on a local computer, with the existing four source adapters, GEO scheduling policies,
  durable local spool, manual and scheduled execution, restart recovery, and conservative
  source-specific retry/blocking behavior.
- Keep GEO as the control plane for business configuration and target selection. The Collector
  obtains versioned job manifests through a new authenticated Gateway and never connects directly
  to DEV or production MySQL.
- Add a Collector Gateway that authenticates collector nodes, serves configuration and bounded
  jobs, creates idempotent transfers, issues short-lived Inbox upload URLs, accepts completion and
  telemetry events, and exposes transfer status.
- Replace operator-driven bundle movement with a durable, at-least-once Bundle transfer protocol:
  immutable transport identity, local spool, archive and file hashes, upload completion checks,
  persistent receipts, retries, backlog handling, and dead-letter isolation.
- Add a standalone GEO Collector Consumer that claims ready transfers oldest-first, validates and
  safely extracts bundles, imports each game through existing GEO normalization, game upsert, and
  image services, and records bundle/item receipts before acknowledging completion.
- Add Collector node, run, event, transfer, backlog, and error visibility to GEO's authenticated
  management surface. Technical logs use structured correlation fields and remain compatible with
  OpenTelemetry/Loki/SLS without making a complete log-platform rollout a Phase 1 prerequisite.
- Add an external-execution feature gate so the existing in-process GEO game-ingest schedulers are
  disabled only after the new end-to-end path has passed DEV acceptance.
- Deliver Phase 1 with local-computer execution and Docker-/Kubernetes-ready application
  boundaries, but do not require a Linux server, Kubernetes cluster, full centralized logging
  stack, or production deployment.

## Capabilities

### New Capabilities

- `local-game-collector-runtime`: Cross-platform multi-source collection, scheduling, durable
  local jobs/spool, recovery, and bounded Bundle production.
- `collector-control-gateway`: Collector identity, configuration and job delivery, idempotent
  transfer creation/completion, short-lived upload authorization, heartbeat, and run-event intake.
- `reliable-game-bundle-transport`: Immutable Bundle delivery, integrity metadata, retry/resume,
  acknowledgements, backlog protection, and dead-letter behavior.
- `collector-inbox-consumer`: Oldest-first claiming, validation, per-item idempotent GEO import,
  persistent receipts, retry classification, and processed/failed acknowledgement.
- `collector-observability`: Correlated node, run, event, transfer, backlog, and error visibility
  for operators, with structured technical-log integration boundaries.

### Modified Capabilities

None. The existing Windows TapTap Spike change remains complete and is superseded operationally
by these new capabilities rather than rewritten.

## Impact

- The dedicated collector repository will gain a Python package/runtime, migrated source adapters
  and tests, scheduler, spool, Gateway client, Bundle builder, telemetry, local launch packaging,
  and a Linux container image for future deployment.
- GEO will gain authenticated Collector Gateway endpoints, Consumer/service modules, database
  models and Alembic migrations for nodes/jobs/runs/events/transfers/receipts, management APIs and
  UI, and an external-execution gate for current game-ingest scheduler startup.
- Existing GEO game normalization, `upsert_game`, image storage, category relationships, and
  MySQL/MinIO ownership remain authoritative. Collector nodes receive no GEO database or business
  MinIO credentials.
- DEV and production use separate Gateway credentials, transfer state, Inbox buckets/prefixes, and
  destinations. Production rollout remains a separate authorized operation after DEV acceptance.
