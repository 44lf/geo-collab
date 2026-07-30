## Context

GEO currently contains proven game acquisition paths for Baidu and NineGame, a separate
YingYongBao discovery path, database-backed scheduling/configuration, and run-summary logging. A
Python TapTap implementation also exists in the local `game-search` skill, while the dedicated
collector repository contains the subsequently proven PowerShell/MinIO Spike. The Spike proved
that collection and Bundle import are feasible, but its scripts overwrite logs, depend on manual
operation, do not manage backlog as a durable queue, and only consume the newest ready marker.

The immediate Collector carrier is now an operator's local computer rather than a dedicated
Windows or Linux server. The architecture must therefore tolerate sleep, shutdown, changing
networks, and delayed schedules. It must also remain portable to a later Linux container or
Kubernetes deployment without changing source behavior, Bundle contracts, Gateway APIs, or GEO
import behavior.

The Collector is outside the trusted GEO production network. It must never receive MySQL
credentials or business-MinIO credentials, and all source access must remain authorized,
low-frequency, bounded, and non-evasive. TapTap HTTP 403/405/429 responses halt the affected run.
No production deployment, tag, or rollout is authorized by this design change.

## Goals / Non-Goals

**Goals:**

- Move schedule execution and all four proven source adapters into one cross-platform Python
  Collector Agent while retaining GEO as the business configuration and target-selection control
  plane.
- Run Phase 1 on a local computer with both manual and background scheduling modes, durable local
  jobs/spool, bounded catch-up after offline periods, and restart-safe transmission.
- Introduce an authenticated Gateway, immutable Bundle transfer, short-lived Inbox upload
  authorization, persistent transfer/item receipts, and an independent GEO Consumer.
- Import only through existing GEO normalization, game upsert, category, tag, and image services.
- Make node/run/transfer/import state visible in GEO Web and establish structured log/telemetry
  contracts that can later feed OpenTelemetry/Loki/SLS.
- Produce deployment-neutral code and container artifacts so a future Linux/Kubernetes carrier is
  a packaging/operations change rather than a rewrite.
- Prove the complete path in DEV, including restart and network interruption recovery, before
  permitting an external-scheduler cutover or a separately authorized production rollout.

**Non-Goals:**

- Building a Kubernetes cluster, Kafka/RabbitMQ, multi-region scheduling, autoscaling, or a full
  centralized logging platform in Phase 1.
- Re-discovering or rewriting the already proven source protocols.
- Letting a Collector connect directly to DEV/production MySQL or GEO business-MinIO buckets.
- Proxy rotation, fingerprint spoofing, access-control bypass, or aggressive retry behavior.
- Replacing GEO game normalization, merge, image, or article-use business rules.
- Automatically deploying, tagging, or enabling the production path.

## Decisions

### 1. Treat the local computer as an Edge Collector Node

The Collector is a cross-platform Python package with one long-running `agent` entry point and
explicit `run-now` commands. Phase 1 can run natively or in a local Docker environment, but the
core contains no Windows-service, Docker, or Kubernetes business logic. Local directories are
split into immutable application releases and persistent `config`, `jobs`, `spool`, `state`, and
`logs`.

The local carrier is not assumed to be always online. The scheduler persists `next_due_at`,
claimed job manifests, and run completion. On startup/resume it performs at most the configured
number of catch-up runs whose age is below `catch_up_max_age`; older occurrences are recorded as
skipped rather than replayed in a burst. New collection pauses at a spool high-water mark while
transmission and acknowledgement processing continue.

Alternative considered: keep independent CMD/PowerShell scripts. Rejected because scripts do not
provide a single scheduler, state owner, upgrade boundary, or portable Linux path.

### 2. Migrate source execution, not GEO business ownership

The Collector repository receives:

- the Baidu and NineGame Python source adapters and their fixtures from GEO;
- the YingYongBao client/parser/discovery behavior and fixtures from GEO;
- the Python TapTap adapter from `game-search`, plus the operational stop/retry evidence from the
  PowerShell Spike;
- a shared, versioned source result model aligned with GEO `types.Game`;
- scheduling pure functions for time windows, batch quotas, bounded random gaps, and attempt caps.

GEO retains `GameIngestConfig`, target eligibility and due-game selection, name normalization,
cross-source identity/merge decisions, soft-delete policy, article-use state, and all DB/image
writes. This prevents collector code from becoming a second copy of production business logic.

Alternative considered: give the Collector read-only production DB access so it can select due
games itself. Rejected because it exposes the database boundary, couples the agent to migrations,
and makes a local workstation a production database client.

### 3. Split scheduling from target selection through the Gateway

The Collector owns timer execution. It periodically retrieves a versioned configuration through
the Gateway and, when a schedule is due, claims a bounded immutable job manifest.

- A `refresh` job is created from GEO's due-game selection and contains stable target identity,
  name, source order, image limits, and policy version.
- A `discovery` job contains source/category paths and bounded detail/image limits and does not
  depend on an existing game row.

The Collector persists the manifest before issuing source requests. Configuration includes a
maximum staleness duration; when an expired configuration cannot be refreshed, the Agent stops
new crawls but continues to send already-collected Bundles and telemetry.

The existing GEO scheduler remains the default until the external path passes DEV. A single
feature gate selects `in_process` or `external`; both modes must not schedule the same workload.

Alternative considered: use Kubernetes CronJobs or Windows Task Scheduler per source. Rejected
because schedules are dynamic, require persistent offline/catch-up state, and must coordinate one
shared spool and source pacing policy.

### 4. Use a Gateway control plane and direct pre-signed object upload

The Gateway is implemented in the GEO codebase but runs as a separately deployable process. It is
stateless apart from MySQL/MinIO and provides:

- collector identity and revocation;
- versioned configuration and bounded job claiming;
- idempotent transfer creation by collector/transport ID;
- short-lived, collector-scoped Inbox upload authorization;
- transfer completion and status/receipt queries;
- heartbeat and idempotent run-event batch intake.

Large Bundle bytes do not flow through the Gateway application process. The Collector uploads
directly to the Inbox with a pre-signed URL, then calls `complete`. The Gateway HEAD-checks object
identity and declared size before atomically moving the transfer row to `READY`. When the object
store's standard pre-signed PUT cannot sign checksum metadata headers, the Gateway streams the
private Inbox object once to recompute SHA-256 at completion; the Consumer independently verifies
the archive again before extraction. Reissuing an expired upload URL or repeating `complete` for
the same immutable transfer is safe.

Phase 1 authentication uses TLS plus a revocable per-collector credential stored through the local
OS secret store and hashed server-side. IP restrictions are additive, not the identity mechanism,
because a local computer can change networks. mTLS can be added later without changing the
transfer model.

Alternative considered: permanent MinIO credentials on the local computer. Rejected because
pre-signed uploads reduce credential scope and make revocation and audit clearer.

Alternative considered: proxy all archive bytes through Gateway. Rejected because it adds a
bandwidth/memory bottleneck and a new failure point for large files.

### 5. Make the local spool the source of truth until acknowledgement

Each run has a unique `run_id`; each Bundle has a `bundle_id`; each delivery has an immutable
`transport_id` scoped to a `collector_id` and destination. A spool directory stores the job
manifest, normalized/raw records, archive, ready metadata, event segments, and atomically replaced
state.

The delivery state machine is:

`COLLECTED -> PACKAGED -> TRANSFER_CREATED -> ARCHIVE_UPLOADED -> READY -> PROCESSED`

Retryable failures leave the state at the last completed transition. Retries reuse the same object
key and identities and never re-crawl. Local Bundle bytes are deleted only after a matching
processed receipt with the expected archive SHA-256 is observed and the configured local grace
period has elapsed.

The transport provides at-least-once delivery. Business one-time effect is achieved by immutable
identity, unique DB receipts, per-item checkpoints, GEO upserts, and image source-URL hash
deduplication rather than by claiming distributed exactly-once execution.

### 6. Use MySQL transfer and item receipts as the durable queue

Gateway transfer rows, rather than MinIO `ready/` listing order, are the authoritative queue.
Transfers are claimed oldest-first in bounded batches with `claimed_by` and `lease_until`.

The Consumer records:

- one unique transfer receipt keyed by `transport_id`;
- one unique item receipt keyed by `(transport_id, item_key)`;
- immutable archive identity and source/job metadata;
- attempts, classification, error summary, and completion timestamps.

Each game's GEO write and item-receipt completion occur in the same MySQL transaction. If the
Consumer crashes after some games commit, a retry skips completed items. After all items commit,
the transfer becomes `PROCESSED`; a crash after DB commit but before Collector status polling is
self-healing because the receipt remains queryable.

Alternative considered: use a message broker in Phase 1. Rejected because the transfer table is a
durable bounded queue at current volume and MinIO already owns large bytes. Broker integration can
be added later if throughput demonstrates a need.

### 7. Import only through GEO service contracts

The Consumer validates archive bytes, SHA-256, safe extraction limits, Bundle schema, per-file
hashes/MIME, source identity, and job/target consistency before calling a new internal
`CollectorImportService`.

That service converts validated records to GEO `types.Game` and delegates to existing
normalization, `upsert_game`, category/tag, and image services. Known-target refreshes anchor to
the Gateway-issued target; discovery imports use the documented GEO identity policy. A miss,
blocked source, invalid item, or transport failure never deactivates or deletes an existing game.

The current `remote_game_bundle_import.py` becomes a diagnostic CLI over the same service rather
than a separate implementation path.

### 8. Separate operator events, technical logs, and raw evidence

Operator-visible events are small structured records stored in MySQL and exposed through
authenticated GEO management APIs. All components carry `collector_id`, `run_id`,
`transport_id`, `bundle_id`, `source`, and stage so a run can be followed end to end.

Technical logs are structured JSON with the same correlation fields. Phase 1 retains rotated
local JSONL and supports buffered event/log-segment upload. The schema is OpenTelemetry-compatible;
an OpenTelemetry Collector and Loki/SLS export may be enabled later without changing domain code.
Raw HTTP payloads and downloaded media are Bundle evidence, not log lines.

Secrets, Cookie/XSRF values, pre-signed URLs, database/MinIO credentials, and sensitive query
parameters are removed before any log or event is persisted. Telemetry failure never changes
Bundle correctness; it queues locally with an independent bounded retention policy.

### 9. Design deployment boundaries for Compose first and Kubernetes later

Phase 1 deployables are `collector-agent`, `collector-gateway`, and `collector-consumer`. They use
environment/secret configuration, JSON stdout, non-root container images where applicable,
graceful termination, health/readiness endpoints, and no implicit local state outside mounted
data directories.

Future Kubernetes mapping is:

- Collector Agent: one stateful instance per authorized egress node with a PVC;
- Gateway: stateless multi-replica Deployment;
- Consumer: lease-coordinated Deployment;
- telemetry collector: DaemonSet or Deployment.

This compatibility is an implementation constraint, not a requirement to create Kubernetes or
Helm artifacts in Phase 1.

## Risks / Trade-offs

- **[Local computer sleeps or changes networks]** -> Persist schedules/jobs/spool, bound catch-up,
  use outbound-only TLS and identity credentials rather than fixed IP as the sole control.
- **[The external source behavior changes]** -> Keep per-source fixtures, version adapters and
  Bundle/normalizer metadata, halt blocked sources conservatively, and preserve raw evidence.
- **[Copied source code drifts from GEO]** -> Move source ownership to the Collector repository,
  preserve contract tests, and stop executing the retired GEO copies after cutover.
- **[Gateway or production is unavailable]** -> Collector retains immutable local spool, pauses at
  disk high-water, and resumes the same transfer without re-crawling.
- **[DB commit and acknowledgement are not atomic across systems]** -> Make DB transfer/item
  receipts authoritative and keep status polling idempotent.
- **[A local workstation credential is exposed]** -> Use a revocable collector identity, OS secret
  storage, short-lived upload authorization, narrow API scopes, and no production DB/business
  MinIO credentials.
- **[Technical logging scope delays the data path]** -> Require structured correlation and
  operator events in Phase 1, while treating full Loki/SLS rollout as optional.
- **[Existing and external schedulers both run]** -> Use a mutually exclusive execution-mode
  feature gate and cut over only after DEV evidence.
- **[Kubernetes work consumes Phase 1 capacity]** -> Keep applications Kubernetes-ready but defer
  cluster, Helm, HA, and autoscaling work.

## Migration Plan

1. Add failing contract tests in the Collector repository for the unified source result, job,
   Bundle, spool, and transfer behavior.
2. Move the four source adapters and fixture coverage into the Python Collector runtime without
   changing GEO's active scheduler.
3. Add scheduling, manual execution, bounded catch-up, local spool, restart recovery, and
   structured events; verify entirely offline with fixtures.
4. Add GEO Gateway models/migrations and authenticated config, job, transfer, status, heartbeat,
   and event endpoints.
5. Add the Consumer, validation, receipt/item checkpointing, import service reuse, retry, and
   dead-letter behavior.
6. Add minimal GEO management APIs/UI for node, run, transfer, backlog, and error visibility.
7. Deploy the Gateway/Consumer against DEV and run the local Agent through one real four-source
   bounded cycle.
8. Inject local restart, upload interruption, duplicate completion, and Consumer crash/retry;
   retain exact evidence for each acceptance scenario.
9. Enable external scheduling in DEV and confirm the in-process scheduler is disabled.
10. Produce a clean local-agent package/container artifact and operational runbook.
11. Treat production Gateway/Inbox/Consumer deployment and production scheduler cutover as a
    separate authorized rollout with rollback to `in_process` scheduling.

Rollback keeps the external scheduler gate off or restores `in_process`, stops Collector job
claiming, and leaves already-uploaded transfers/receipts intact for explicit completion or
quarantine. No rollback deletes Bundles or imported business data.

## Open Questions

- Which local packaging mode will be used for the first operator run: native self-contained Python
  or Docker Desktop with a persistent host volume?
- Does the production environment already have an approved public/VPN route for pre-signed Inbox
  uploads, or must the Gateway temporarily proxy bytes?
- Which technical-log backend, if any, is already approved: Alibaba SLS, Loki, or Phase 1 MinIO
  JSONL retention only?
- What final written authorization and rate limits apply to ongoing collection from each source?
