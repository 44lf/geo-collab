# Phase 1 acceptance and production-readiness report

## Decision

- **Controlled DEV: GO.** The Windows-native Collector, Gateway, private Inbox, Consumer,
  GEO normalization/import, receipts, Web visibility, fault recovery, and external scheduler have
  passed bounded DEV acceptance.
- **Production: NO-GO.** Production was not contacted, tagged, deployed, configured, or enabled.
  A separate explicit rollout approval and the infrastructure/security gates below remain
  mandatory.

Confidence is high for the tested DEV path and medium for production portability. The main
uncertainty is no longer application transport behavior; it is the still-unselected production
endpoint, carrier, supervision, credentials, TLS, storage policy, monitoring, and operational
ownership.

## Accepted architecture and ownership

```text
local Windows Agent
  -> immutable job/result checkpoints
  -> deterministic Bundle v2 + SHA-256
  -> durable local spool
  -> authenticated HTTPS GEO Gateway
  -> short-lived fixed-key private Inbox upload
  -> MySQL transfer queue
  -> lease-based Consumer
  -> existing GEO normalization/upsert/category/tag/image services
  -> item and transfer receipts
```

The Collector owns schedule execution and the proven Baidu, NineGame, YingYongBao, and TapTap
source adapters. GEO remains the control plane and sole owner of target selection, normalized-name
and source-identity merge rules, `upsert_game`, categories, tags, business images, article-use
state, and all business writes.

The edge Collector receives neither MySQL nor business-MinIO credentials. Its long-lived
credential stays in the OS secret store, and its object-storage authority is one short-lived,
collector-scoped Inbox upload grant. Miss, blocked, and error items do not delete or deactivate
games.

## Passed evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Implementation and static gates | Collector `143 passed`; GEO Collector/ingest `168 passed`; Ruff and strict OpenSpec validation passed; scoped secret scans had no matches. | `verification.md` |
| Shared DEV E2E | One bounded four-source schedule traversed local checkpoints, Bundle upload, private Inbox, shared `geo_dev`, business MinIO, processed 4/4 receipt, Web timeline, and exact configuration restoration. | `dev-test-2026-07-29.md` |
| Integrity | Local archive, Inbox object, transfer row, Bundle inventory, processed receipt, and business image bytes were independently hashed and matched. | `dev-test-2026-07-29.md` |
| Idempotency | Replaying the same claim retained one Job, Run, Transfer, transfer receipt, and four item receipts; request count and business data did not duplicate. | `dev-test-2026-07-29.md` |
| Fault recovery | Agent restart, Gateway/network interruption, real upload-grant expiry, duplicate complete, Gateway restart, DEV dependency outage, partial Consumer commit plus process death, lease reclaim, and replay all retained the Bundle and logical identities. | `fault-injection-2026-07-29.md` |
| External scheduling | Gateway external mode disabled both legacy schedulers. One bounded scheduled run was followed by repeated idle cycles with a persisted next-due time, advancing heartbeats, correlated logs, one Job only, and zero backlog. | `scheduler-observation-2026-07-29.md` |
| Read-only operations view | Authenticated Web management showed node/run/transfer/backlog state, sanitized timeline, receipt counts, archive identity, attempts, and item outcomes. | `dev-test-2026-07-29.md` |

The fault exercise also found and fixed real MinIO connection exhaustion being misclassified as
an unexpected Consumer error. Commit `38edaf4` now reports urllib3 transport failures as
`consumer_dependency_unavailable`.

## Current safe state

- Shared `geo_dev` test configuration is restored.
- Isolated scheduler configuration is restored and recorded as configuration version 3.
- All tested transfers are terminal; ready/retry/processing backlog is zero.
- No temporary database trigger remains.
- No Agent, Consumer, local Gateway listener, Docker, WSLg, or remote-desktop process was left
  running.
- The retained spool archives, Inbox objects, database receipts, first dead-letter evidence, DEV
  backup, and pre-existing runtime stash remain available for diagnosis.

## Unresolved risks and open questions

1. `collector_nodes.last_success_at` is not populated by completed runs. Run and receipt evidence is
   correct, but the node summary can misleadingly show no last success.
2. The private Collector Inbox policy was verified. The existing GEO business-image bucket still
   follows its pre-existing public-read behavior and needs an explicit production policy decision.
3. Native Windows execution is verified. A new Docker image was not built in the final checkpoint,
   and no Linux/Kubernetes carrier is currently assigned.
4. The local Gateway uses a self-signed certificate. Production needs an approved DNS name,
   trusted TLS certificate, rotation owner, and Collector trust/bootstrap procedure.
5. The Agent and Consumer were run as controlled processes, not installed as production-supervised
   services. Service ownership, restart policy, resource limits, upgrade procedure, and host
   patching remain undecided.
6. Central log export is intentionally deferred. JSONL/stdout correlation works, but Loki, SLS, or
   OpenTelemetry retention, dashboards, alerts, and on-call routing are not configured.
7. Final written source authorization, allowed frequency, permitted fields/media, and incident
   contacts for Baidu, NineGame, YingYongBao, and TapTap remain an organizational gate.
8. The production database migration/backup window and compatibility check have not been
   performed. The guarded shared-DEV reconciliation does not authorize a production migration.
9. Production node identity, destination, credential expiry/rotation, egress allowlist, Inbox
   bucket lifecycle, dead-letter retention, backlog thresholds, and maximum archive quota need
   operations/security sign-off.
10. The local-computer carrier can sleep or change networks. Phase 1 tolerates this through bounded
    catch-up and durable spool, but the acceptable availability SLO and eventual dedicated carrier
    are still product/operations decisions.

## Required production approvals and infrastructure

Production rollout requires all of the following, in a separately authorized change:

1. product/data-owner approval for scope, source frequency, and normalized write behavior;
2. security approval for threat model, Collector credential lifecycle, TLS, egress, private Inbox
   policy, secret distribution, and log redaction;
3. DBA approval for a fresh backup, migration plan, lock budget, restore rehearsal, and maintenance
   window;
4. operations ownership of the production Gateway endpoint, Consumer placement, Collector carrier,
   process supervisor, upgrades, rollback, and on-call response;
5. production MySQL and business-MinIO credentials available only to Gateway/Consumer as designed;
6. a production Collector node/destination and short-lived Inbox upload configuration;
7. retention and alert thresholds for heartbeat staleness, spool pressure, retry backlog, lease
   expiry, dead letter, transfer age, and disk capacity;
8. a low-frequency canary plan with explicit stop conditions and human review before broader
   scheduling;
9. a real TLS certificate and DNS/network path verified from the selected Collector carrier;
10. explicit final authorization to tag, deploy, create production credentials, write production
    data, and enable the external scheduler.

## DEV rollback

If the controlled DEV external path fails:

1. stop the external Agent and Consumer;
2. set `GEO_GAME_INGEST_EXECUTION_MODE` back to `in_process`;
3. restore the recorded `game_ingest_config` values;
4. retain local jobs/spool/logs and all server transfer/item/receipt rows;
5. do not delete Inbox objects, unprocessed Bundles, or dead-letter evidence;
6. redeploy the previous GEO artifact only if the migration/API artifact itself is faulty;
7. verify exactly one legacy refresh/discovery scheduler is active before resuming;
8. compare backlog, last terminal receipts, business row counts, and MinIO hashes before declaring
   rollback complete.

## Future production rollback contract

Before any production enablement, the rollout plan must record the previous execution mode,
application artifact, database revision, configuration snapshot, credential state, and scheduler
owner. A production rollback must stop the external scheduler first, preserve all durable evidence,
restore the previous artifact/configuration only after database compatibility is confirmed, and
prove that exactly one scheduling path is active. Physical deletion of queue, receipt, Inbox, or
business data is never a rollback step.
