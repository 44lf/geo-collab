# DEV fault injection — 2026-07-29

## Scope and result

Task 10.3 passed using Windows-native Gateway, Collector, and Consumer processes against the
isolated `geo_collector_dev_test` database and the DEV private Inbox/business MinIO. Shared
`geo_dev` and production were not changed.

The fault transfer reused the already validated four-source Bundle content from task 10.2, changed
only its correlation IDs, rebuilt the archive, and recomputed its immutable identity. This avoided
another source crawl while exercising the complete transport and Consumer path:

- transport: `transport-fault103-20260729a`;
- bundle: `bundle-fault103-20260729a`;
- archive size: 613,019 bytes;
- archive SHA-256:
  `c1fde5020092e5cb7fb575552b2671a7095f725b0addb8780389de1628261c7f`.

The earlier live Agent restart checkpoint in `dev-test-2026-07-29.md` supplies the source-side
restart evidence: resuming claim `refresh-20260728T190000Z` reused the same Job and Run, retained
`request_count=4`, did not re-request a completed source, and reached a processed 4/4 receipt.

## Fault matrix

| Fault | Injection and observed intermediate state | Recovery and invariant |
| --- | --- | --- |
| Local Agent restart | Existing live checkpoint stopped after four durable source checkpoints and before packaging/remote registration. | Same claim, Job, Run, and four requests resumed; no source recrawl or duplicate logical data. |
| Network/Gateway interruption | Draining the new packaged transfer with no `8443` listener produced one retryable attempt. Local state stayed `packaged`; the archive hash still matched; the remote transfer count stayed zero. | After the Gateway returned, the same transport and archive continued without rebuilding the Bundle. |
| Expired upload authorization | The Gateway issued a real 60-second fixed-key grant. After 65 seconds, DEV MinIO rejected the expired upload by closing the connection (`WinError 10053`) rather than returning HTTP 403. Local state remained `transfer_created`, and the one remote declaration remained intact. | The next drain requested a fresh authorization, uploaded the retained archive, completed it, and reached `ready`. The focused 403-path test also proved same-call renewal without transfer recreation. |
| Duplicate complete | `complete` was called twice while `ready` and twice again after processing. | Both pre-processing calls returned `ready`; both terminal calls returned `processed`. The database retained one transfer, then one transfer receipt and four item receipts. |
| Gateway restart | The local Windows Gateway process was stopped and replaced while the transfer was `ready`. | A fresh process read the same `transport_id`, `ready` state, object key, and archive SHA-256. |
| DEV destination outage | A Consumer used an unreachable MinIO endpoint (`127.0.0.1:1`). It claimed the transfer, wrote no item/transfer receipts, released it to `retry_wait`, and retained both local and Inbox archives. | Real MinIO raised urllib3 `MaxRetryError`; this exposed and fixed an incorrect `consumer_unexpected_error` classification. Replay after commit `38edaf4` emitted `consumer_dependency_unavailable` with a bounded 30-second retry. |
| Consumer crash after partial commits | An isolated, transport-scoped test trigger delayed the second item receipt. After the Baidu business write and item receipt committed, Consumer PID `10228` was force-stopped. The transfer remained `processing` on attempt 3 with one item receipt and no transfer receipt. | The trigger was removed, the six-second lease expired, and `fault-recovery-consumer` reclaimed the same transfer on attempt 4. It skipped the committed Baidu item, processed the remaining three, and created exactly one processed 4/4 transfer receipt. |

## Integrity and idempotency evidence

- Local archive, Inbox object, transfer row, and processed receipt all reported the same 613,019
  bytes and SHA-256
  `c1fde5020092e5cb7fb575552b2671a7095f725b0addb8780389de1628261c7f`.
- The recovered transfer has four unique item keys: Baidu, Jiuyou, and TapTap succeeded;
  Yingyongbao remained a source miss and was skipped.
- The isolated business model stayed at one game, one stock image, and three unique source
  identities. Replay did not create a second logical game, image, or source identity.
- Local spool became `processed` only after receiving the matching processed receipt. Repeating
  the local drain remained `processed`.
- Final isolated backlog was zero for `ready`, `retry_wait`, and `processing`.
- The temporary database trigger was removed, and no Consumer, Agent, Gateway listener, Docker,
  WSLg, or remote-desktop process was left running.

## Defect found and fixed

The MinIO Python client reports exhausted connection retries as urllib3 `MaxRetryError`, which was
falling through to `consumer_unexpected_error`. `ClaimedTransferProcessor` now treats urllib3
transport errors as dependency outages. Commit `38edaf4` adds the fix and regression test; the
clean DEV runtime used equivalent cherry-pick `011e1db`.

## Automated fault gates

- Collector restart/transport/spool selection: `6 passed`.
- GEO Consumer partial-commit/MySQL/MinIO/lease/complete selection: `15 passed`.
- Focused Consumer pipeline after the classification fix: `8 passed`; Ruff lint and format
  checks passed.

Production was not contacted, deployed, tagged, or enabled.
