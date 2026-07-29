## ADDED Requirements

### Requirement: Oldest-first bounded claiming
The Consumer SHALL claim `READY` transfers oldest-first in configurable bounded batches using a
persistent owner and expiring lease.

#### Scenario: Multiple transfers are ready
- **WHEN** a Consumer requests a batch
- **THEN** it claims no more than the configured limit ordered by ready time and stable identity

#### Scenario: Consumer crashes while processing
- **WHEN** a processing lease expires without terminal completion
- **THEN** another Consumer can reclaim the same transfer without creating a second receipt

### Requirement: Layered Bundle validation
The Consumer MUST validate transfer identity, archive size/SHA-256, safe extraction, Bundle schema,
file-count/byte limits, per-file paths/sizes/hashes/MIME signatures, source identity, and job/target
consistency before any business write.

#### Scenario: Unsafe or corrupt archive arrives
- **WHEN** an archive has traversal, symlink, encrypted member, size expansion, hash, or MIME
  violations
- **THEN** the Consumer performs no business import and records a permanent isolated failure

### Requirement: GEO service-layer import
The Consumer SHALL convert validated records to GEO's canonical game type and SHALL perform
normalization, game merge/upsert, tags, category relationships, and image storage only through GEO
service-layer contracts.

#### Scenario: Known target refresh is imported
- **WHEN** a validated refresh item references the expected existing target
- **THEN** the Consumer anchors the merge to that target and stores validated media through GEO
  image services

#### Scenario: Discovery item is imported
- **WHEN** a validated discovery item has no existing target
- **THEN** the Consumer applies the documented GEO source-identity and normalized-name policy
  before creating or merging a game

### Requirement: Per-item transactional receipts
Each item import and its completed item receipt MUST commit in the same MySQL transaction, with a
unique `(transport_id, item_key)` constraint.

#### Scenario: Consumer crashes after some items commit
- **WHEN** a multi-item Bundle is retried after partial committed progress
- **THEN** completed items are skipped and only incomplete eligible items are retried

#### Scenario: Same transfer is delivered twice
- **WHEN** duplicate ready/completion delivery reaches the Consumer
- **THEN** unique transfer/item receipts and GEO idempotency prevent duplicate logical games,
  tags, categories, or images

### Requirement: Terminal receipt consistency
The Consumer SHALL mark the transfer `PROCESSED` only after every eligible item reaches a terminal
successful/skipped state and SHALL make that database receipt queryable before any best-effort
Inbox cleanup.

#### Scenario: Database commit precedes status polling
- **WHEN** the Consumer commits processed state and then stops before the Agent polls
- **THEN** the next status query returns the durable processed receipt without re-importing items

### Requirement: Retry and dead-letter isolation
The Consumer SHALL distinguish retryable destination failures from permanent validation/business
failures, retain retryable transfers as backlog, and preserve permanent failure evidence and
replay metadata in dead-letter state.

#### Scenario: MySQL or business MinIO is temporarily unavailable
- **WHEN** import fails with an eligible destination availability error
- **THEN** the Consumer releases or expires the lease for bounded retry without deleting the
  archive or transfer

#### Scenario: Deterministic item data is invalid
- **WHEN** a validated container has a permanent item-level business contract violation
- **THEN** the item and transfer record the sanitized reason and replay evidence for operator
  review

### Requirement: Collector failure does not delete games
A missed, blocked, invalid, failed, or absent Collector item MUST NOT deactivate, soft-delete, or
delete an existing GEO game or its media.

#### Scenario: Source returns no exact match
- **WHEN** a refresh target is recorded as a miss
- **THEN** the Consumer records the outcome without removing or deactivating the target game
