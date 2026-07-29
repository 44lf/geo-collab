# Phase 1 preliminary readiness report

Decision: **NO-GO for DEV cutover and production** until tasks 10.2-10.4 produce live evidence.

## Implemented path

The Python Collector now owns the four proven source adapters and local schedule execution. GEO
retains target selection and business normalization. The transport path is:

```text
local Agent
  -> immutable job/result checkpoints
  -> deterministic Bundle v2 + SHA-256
  -> durable local spool
  -> authenticated GEO Gateway
  -> short-lived fixed-key Inbox upload
  -> MySQL transfer queue
  -> lease-based Consumer
  -> GEO normalization/upsert/category/tag/image services
  -> transfer and item receipts
```

The Collector never receives production MySQL or business-MinIO credentials. Credential material
is read from the OS secret store or a container secret file. Technical logs and operator events
share correlation IDs and are sanitized before persistence.

## Normalization ownership

Source adapters emit a shared acquisition contract, but the Collector does not decide production
merge or write behavior. Successful normalized records are converted by
`CollectorImportService` into GEO's canonical `types.Game`, then reuse existing normalized-name,
source-identity, `upsert_game`, category/tag and image paths. Refresh imports remain anchored to
the Gateway-issued target/category. Discovery first checks source identity, then normalized name;
ambiguous matches become isolated conflicts. Miss, blocked and error items never delete or
deactivate a game.

## Reliability evidence

Automated tests cover:

- job manifest persistence before source access;
- atomic claim publication, incomplete-directory quarantine and restart without re-collection;
- full per-item result and cumulative request-count checkpoints;
- restart-safe immediate halt after a persisted 403/405/429 blocked result;
- persisted run completion timestamps/summary before remote registration;
- immutable run/bundle/transport identities and idempotent replay;
- deterministic archive inventory, size, MIME and SHA-256 validation;
- half-written spool quarantine, disk/high-water backpressure, receipt-gated processed cleanup and
  no pending-data deletion;
- finite retry/backoff, upload-authorization renewal and terminal identity failures;
- Gateway redirect rejection so Collector Bearer credentials cannot follow a cross-origin 30x;
- bounded Inbox download, server-side Bundle/job limits and normalized source/name identity checks;
- Consumer lease reclaim and owner/attempt/expiry fencing for retry, processed and dead-letter
  writes;
- per-item receipt transaction coupling, partial resume, global attempt ceiling and dead-letter;
- matching receipt plus archive SHA-256 before local cleanup eligibility;
- local JSONL retention, event replay, heartbeats, Web history and secret redaction;
- native and container launch contracts, OS secret store, health/readiness/metrics and clean
  SIGINT/SIGTERM handling.

Exact commands and counts are recorded in `verification.md`.

## Outstanding risks and required evidence

1. Gateway/Inbox/Consumer migrations and credentials have not been deployed to DEV.
2. No real four-source schedule has traversed the full path into geo_dev/business MinIO.
3. Fault injection has not yet been run against live DEV dependencies.
4. External scheduler mode has not been enabled, so mutual exclusion is only test-proven.
5. Repeated low-frequency cycles and backlog/log observation have not occurred.
6. Docker Compose syntax is verified, but the image was not built because Docker daemon was
   unavailable.
7. Production endpoint, node identity, Inbox policy, Consumer process placement, retention values
   and alert thresholds still require operations/security approval.

## DEV rollback

Before DEV cutover, record the current `GAME_INGEST_EXECUTION_MODE`. If the external path fails:

1. stop the local Agent and Consumer;
2. set GEO DEV back to `in_process`;
3. retain all Collector jobs/spool and server transfer/receipt rows for diagnosis;
4. do not delete Inbox objects or dead-letter evidence;
5. redeploy the previous GEO artifact only if the migration/API change itself is faulty;
6. verify the legacy in-process scheduler is the sole active scheduler before resuming.

No production rollback is specified yet because no production rollout is authorized.
