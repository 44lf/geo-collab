## ADDED Requirements

### Requirement: Immutable correlated identities
Every collection and delivery SHALL use a unique `collector_id`, `run_id`, `bundle_id`, and
`transport_id`. A transport identity MUST refer to one immutable destination, object key, byte
size, and archive SHA-256 for its lifetime.

#### Scenario: Bundle is packaged
- **WHEN** a completed bounded job is packaged
- **THEN** the manifest and transfer metadata record all correlation identities and component
  versions

### Requirement: Complete integrity inventory
A Bundle SHALL contain a versioned manifest with bounded entries and SHALL record every included
file's safe relative path, role, media type, byte size, SHA-256, source identity, and source URL
where applicable. The archive SHALL have its own byte size and SHA-256.

#### Scenario: Bundle is validated before upload
- **WHEN** packaging completes
- **THEN** the Agent re-reads the archive and inventory and refuses transfer if any declared size,
  hash, MIME signature, or path rule fails

### Requirement: Resume without re-collection
Retryable Gateway, network, or Inbox failures SHALL resume from the last durable transfer stage
using the same identities and object key. They MUST NOT trigger another source crawl.

#### Scenario: Network fails during archive upload
- **WHEN** an upload attempt is interrupted
- **THEN** the local archive remains intact and a later attempt reuses the same transfer and object
  identity

#### Scenario: Archive upload succeeds but completion fails
- **WHEN** the object exists but the Gateway completion call did not succeed
- **THEN** the Agent rechecks or repeats completion without rebuilding or re-uploading valid bytes

### Requirement: Retry classification
The transport SHALL retry only eligible timeouts, connection failures, and transient server
errors with finite exponential backoff and jitter. Authentication, authorization, identity,
schema, hash, and unsafe-content failures MUST stop automatic retry.

#### Scenario: Gateway returns authorization failure
- **WHEN** a transfer request returns HTTP 401 or 403
- **THEN** the Agent stops automatic transfer attempts for that credential and exposes a
  configuration/security error

#### Scenario: Gateway returns transient server failure
- **WHEN** an eligible transient failure occurs
- **THEN** the Agent retries within configured limits and then defers the same transfer for a later
  drain cycle

### Requirement: Acknowledgement-gated cleanup
The Agent SHALL retain a local Bundle until it receives a matching terminal receipt. Only a
`PROCESSED` receipt with the same transport identity and archive SHA-256 permits normal local
cleanup after the retention grace period.

#### Scenario: Processed receipt arrives
- **WHEN** the Agent observes a matching processed receipt
- **THEN** it marks local state processed and schedules acknowledged artifacts for retention-based
  deletion

#### Scenario: Failed receipt arrives
- **WHEN** the Agent observes a permanent failed/dead-letter receipt
- **THEN** it retains the evidence under failure retention and exposes the reason for operator
  action

### Requirement: Backlog survival
The transport system SHALL preserve every collected Bundle across Collector shutdown, Gateway
downtime, Consumer downtime, MySQL downtime, or business-MinIO downtime.

#### Scenario: Destination is unavailable for one day
- **WHEN** collection has completed but the destination path remains unavailable
- **THEN** all Bundles remain recoverable locally or in the Inbox and are delivered oldest-first
  after recovery
