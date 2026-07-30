# Game Collector scheduler cutover and rollback

This runbook covers only the execution-ownership switch between GEO's existing in-process game
ingest schedulers and the external Collector pipeline. It does not authorize production
deployment, credential provisioning, or source traffic.

## Execution modes

`GEO_GAME_INGEST_EXECUTION_MODE` has exactly two values:

- `in_process` (default): GEO Web may start the existing refresh and YingYongBao discovery
  scheduler threads. Their existing environment and database enablement gates still apply.
- `external`: GEO Web starts neither game-ingest scheduler. Jobs may be issued only through the
  separately deployed Collector Gateway after that path is accepted.

Never run `in_process` and `external` schedulers for the same refresh/discovery workload at the
same time. The mode is a deployment-level setting and every GEO Web replica must use the same
value.

## DEV cutover prerequisites

Before changing DEV to `external`, record evidence that:

1. Collector configuration and immutable jobs are issued without exposing database or
   business-MinIO credentials.
2. One bounded four-source job reaches a durable local Bundle, Inbox object, `READY` transfer,
   Consumer import, MySQL receipt, `PROCESSED` status, and matching Collector acknowledgement.
3. Duplicate transfer creation/completion and duplicate Consumer delivery do not create duplicate
   logical games, tags, categories, images, or item receipts.
4. Collector restart, upload interruption, expired upload authorization, Gateway restart,
   Consumer crash after partial item commits, and a temporary DEV outage retain recoverable
   backlog.
5. The operator view correlates the run by collector, job, run, bundle, transport, and item
   identities without exposing secrets.
6. All existing in-process work has reached a terminal state or has an explicit operator decision.

Do not use a successful configuration/startup test as cutover evidence; acceptance requires the
actual DEV data path and fault injection.

## Outstanding-job drain

Before switching modes:

1. Stop creating new manual in-process refresh/discovery runs.
2. Let active in-process work finish. Record any failure or incomplete target; do not synthesize a
   Collector job for it while the old run is still active.
3. Inspect Collector transfer rows and local spool. Already collected Bundles must continue
   upload/receipt draining even when new source execution is paused.
4. Keep uploaded `READY` and retryable transfers available to the Consumer. A scheduler mode
   change never deletes Inbox objects, local Bundles, transfer rows, or receipts.
5. Move only deterministic permanent failures to dead-letter with their replay evidence.

## DEV cutover

1. Preserve a redacted snapshot of the prior environment and current ingest configuration.
2. Set `GEO_GAME_INGEST_EXECUTION_MODE=external` for every DEV GEO Web replica.
3. Restart the DEV Web processes using the normal deployment procedure.
4. Verify startup logs report that in-process game-ingest schedulers are disabled.
5. Verify no legacy refresh/discovery thread creates a new run during at least one configured poll
   interval.
6. Enable the already-tested external schedule and observe a bounded run end to end.

The Collector may continue draining an existing spool while new job claiming is disabled. Source
execution and transport draining are separate controls.

## Rollback

Rollback is execution-only and does not undo imported game data:

1. Stop or disable new external job claiming while leaving transfer status/receipt polling
   available.
2. Allow already uploaded transfers to finish, or explicitly quarantine them. Do not delete them.
3. Confirm there is no active external job for the workload being returned.
4. Set `GEO_GAME_INGEST_EXECUTION_MODE=in_process` consistently on all GEO Web replicas.
5. Restart through the normal deployment procedure and verify the existing scheduler enablement
   gates and windows.
6. Record the rollback reason and the identities of any pending local, Inbox, retry, or
   dead-letter Bundles.

Rollback must not soft-delete games, remove media, discard local spool, or mark an unprocessed
transfer successful.

## Production boundary

Production Gateway/Inbox/Consumer deployment, production Collector identity issuance, changing
the production execution mode, and tagging or deploying GEO all require a separate explicit
authorization. DEV acceptance does not imply production approval.
