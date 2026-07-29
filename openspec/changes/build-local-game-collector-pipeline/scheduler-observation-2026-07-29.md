# DEV external scheduler observation — 2026-07-29

## Scope and result

Task 10.4 passed with Windows-native Gateway, Agent, and Consumer processes against isolated
`geo_collector_dev_test` and DEV MinIO. The observation deliberately allowed one bounded scheduled
collection and then multiple idle scheduler cycles; it did not burst missed occurrences.

Gateway startup used:

- `GEO_GAME_INGEST_EXECUTION_MODE=external`;
- `GEO_GAME_INGEST_SCHEDULER_ENABLED=false`;
- `GEO_PIPELINE_SCHEDULER_ENABLED=false`;
- `GEO_RUN_STARTUP_RECOVERY=false`.

The startup log recorded:

```text
game ingest execution mode is external; in-process schedulers are disabled
```

No legacy refresh/discovery scheduler thread was started.

## Bounded observation configuration

Isolated configuration version 2 temporarily used:

- refresh window `17:11–17:21 Asia/Shanghai`;
- `batch_size=1`;
- `source_order=[baidu]`;
- source gaps `20–30` seconds;
- `max_shots=2`;
- discovery disabled.

The local Agent used a ten-second scheduler poll, `catch_up_max_age=1800` seconds, and
`max_runs_per_start=1`. A fresh schedule-state directory prevented stale local state from being
mistaken for current evidence.

## Scheduled run

- occurrence/claim: `refresh-20260729T091100Z`;
- Job: `job-fd69af22e05649c5a95742603b1a50c6`, completed;
- Run: `run-7c545c8286c66722bf8929a8`, succeeded;
- Bundle: `bundle-7c545c8286c66722bf8929a8`;
- Transfer: `transport-7c545c8286c66722bf8929a8`, processed on attempt 1;
- request count: 1;
- item receipt: 1/1 Baidu succeeded into isolated game `1`;
- archive SHA-256:
  `a62886d20dbbe61cb0357a275d5c86609c67d87142fbb2ea2bfd49f6fac4c76c`;
- final backlog: zero.

The persisted `refresh-daily` schedule advanced to
`2026-07-30T09:11:00Z`, so later polls could not create another occurrence on the observation day.

## Repeated idle cycles and correlation

Three scheduler samples showed the node idle with no current run and zero spool backlog:

| Sample | Node heartbeat UTC | Schedule state updated UTC | Version-2 Job count | Backlog |
| --- | --- | --- | ---: | ---: |
| post-run | `09:14:00` | `09:13:55.847169` | 1 | 0 |
| idle cycle 1 | `09:14:30` | `09:14:25.086556` | 1 | 0 |
| idle cycle 2 | `09:14:59` | `09:14:54.221187` | 1 | 0 |

Thus the long-running scheduler continued polling and heartbeating without duplicate collection.
Consumer JSON stdout independently showed repeated zero-backlog poll cycles.

The Agent's rotated local JSONL and Gateway event store both carried the same Job, Run, Bundle,
and Transfer identities. The ordered Gateway timeline contained `job_claimed`, `item_completed`,
`run_completed`, `bundle_packaged`, and `transport_advanced`; the Consumer emitted correlated
`transfer_processing_started`, `transfer_processed`, and `poll_cycle_completed` logs.

## Restoration

After observation:

- Agent and Consumer were stopped;
- isolated `game_ingest_config` was restored exactly to `03:00–06:00`, batch 1, gaps `20–30`,
  four-source order, `max_shots=2`, and discovery disabled;
- game `1.last_verified_at` was restored to its pre-observation value;
- restored configuration version 3 was generated and verified through Collector doctor;
- Gateway was stopped;
- no Gateway listener, Agent, Consumer, database fault trigger, WSLg, Docker, or remote-desktop
  process remained.

Shared `geo_dev` and production were not changed, tagged, deployed, or enabled.
