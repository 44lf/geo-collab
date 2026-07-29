## ADDED Requirements

### Requirement: Collector identity and isolation
The Gateway SHALL authenticate every Collector request over TLS with a revocable per-collector
identity and SHALL bind jobs, transfers, events, and status queries to that identity.

#### Scenario: Authorized Collector connects
- **WHEN** a valid enabled Collector credential is presented
- **THEN** the Gateway authorizes only resources belonging to that Collector and its configured
  destination

#### Scenario: Credential is invalid or revoked
- **WHEN** a request uses an invalid, disabled, or revoked Collector credential
- **THEN** the Gateway rejects it without revealing another Collector's state or issuing upload
  authorization

### Requirement: Versioned configuration delivery
The Gateway SHALL expose the currently approved versioned schedule/source configuration and a
maximum staleness duration without disclosing GEO database credentials or internal service
secrets.

#### Scenario: Collector configuration changes
- **WHEN** an operator changes approved ingest configuration in GEO
- **THEN** the next Collector configuration response carries a new version and complete effective
  settings

#### Scenario: Configuration is unchanged
- **WHEN** the Collector presents the current version
- **THEN** the Gateway may return an unchanged response without creating a new job

### Requirement: Bounded idempotent job claiming
The Gateway SHALL create and return immutable bounded `refresh` or `discovery` job manifests using
GEO business target-selection services and SHALL make repeated claim acknowledgement safe.

#### Scenario: Refresh job is requested
- **WHEN** an eligible Collector requests work for a due refresh schedule
- **THEN** the Gateway selects due games through GEO services and returns one bounded immutable
  manifest without granting database access

#### Scenario: Discovery job is requested
- **WHEN** an eligible discovery schedule is due
- **THEN** the Gateway returns configured source/category paths and request/media limits without
  requiring existing game IDs

### Requirement: Idempotent transfer creation
The Gateway SHALL create at most one transfer record for a Collector-scoped `transport_id`,
immutable archive identity, job, and destination.

#### Scenario: Transfer is created
- **WHEN** a Collector submits a new valid transport identity, size, SHA-256, and Bundle metadata
- **THEN** the Gateway persists the transfer and returns short-lived upload authorization for its
  fixed Inbox object key

#### Scenario: Transfer creation is repeated
- **WHEN** the Collector repeats the same request with identical immutable fields
- **THEN** the Gateway returns the existing transfer state or renewed upload authorization without
  creating a duplicate

#### Scenario: Identity is reused with different content
- **WHEN** a Collector reuses a transport identity with a different hash, size, job, or destination
- **THEN** the Gateway rejects the request as a permanent identity conflict

### Requirement: Upload completion boundary
The Gateway SHALL move a transfer to `READY` only after validating that the expected Inbox object
exists and matches the declared object key, size, and integrity metadata.

#### Scenario: Upload completion succeeds
- **WHEN** the Collector completes an uploaded object that matches the transfer declaration
- **THEN** the Gateway atomically marks the transfer `READY` and makes it claimable by the Consumer

#### Scenario: Completion is retried
- **WHEN** the Collector repeats completion for an already ready or processed identical transfer
- **THEN** the Gateway returns the current state without creating another ready item

### Requirement: Heartbeat and run-event intake
The Gateway SHALL accept idempotent batched node heartbeats and structured run events with bounded
payload size and server receive timestamps.

#### Scenario: Event batch is repeated
- **WHEN** an Agent retries a batch containing previously accepted event IDs
- **THEN** the Gateway stores each event at most once and acknowledges the accepted cursor

#### Scenario: Heartbeat becomes stale
- **WHEN** no accepted heartbeat arrives within the configured stale interval
- **THEN** the node is exposed as stale/offline without manufacturing a new failure event

### Requirement: Collector status query
The Gateway SHALL allow a Collector to query only its own job/transfer processed or failed receipt
state needed for safe local cleanup.

#### Scenario: Processed transfer is polled
- **WHEN** the owning Collector queries a processed transport identity
- **THEN** the Gateway returns the terminal state and immutable archive SHA-256
