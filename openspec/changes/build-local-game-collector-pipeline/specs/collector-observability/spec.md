## ADDED Requirements

### Requirement: End-to-end correlation
Collector Agent, Gateway, Consumer, and GEO import events SHALL carry `collector_id`, `run_id`,
`transport_id`, `bundle_id`, source, component version, stage, event type, level, and UTC timestamp
where applicable.

#### Scenario: Operator opens a run
- **WHEN** an operator views one run in GEO
- **THEN** the system correlates its schedule, crawl, Bundle, upload, ready, import, receipt, and
  failure events without relying on free-text matching

### Requirement: Node and run visibility
Authenticated GEO management surfaces SHALL expose Collector node freshness, platform/version,
current run/stage, last success/error, enabled sources, spool/backlog counts, run outcomes, and
transfer/import state.

#### Scenario: Local computer is online
- **WHEN** current heartbeats and run events are available
- **THEN** GEO shows the node online with its effective current state

#### Scenario: Local computer sleeps or shuts down
- **WHEN** heartbeats become stale
- **THEN** GEO shows the node stale/offline while preserving the last known state and pending
  transfers

### Requirement: Idempotent operator events
Operator-visible events SHALL use unique event IDs and bounded structured payloads. Replayed event
batches MUST NOT create duplicate timeline entries or counters.

#### Scenario: Offline event buffer reconnects
- **WHEN** the Agent resends buffered event segments after connectivity returns
- **THEN** GEO stores each event once and rebuilds an ordered run timeline

### Requirement: Structured technical logs
All new Collector subsystem processes SHALL emit structured technical logs with correlation fields
to JSON stdout or JSONL and SHALL keep the schema compatible with a later OpenTelemetry exporter.

#### Scenario: Technical backend is not configured
- **WHEN** Loki/SLS/OpenTelemetry export is unavailable in Phase 1
- **THEN** rotated local/MinIO JSONL remains available without preventing business events,
  transfer, or import

### Requirement: Secret-safe evidence
Logs, events, errors, and Web responses MUST NOT contain Collector credentials, MinIO/database
secrets, Cookie or XSRF values, pre-signed URLs, sensitive query parameters, or raw response
bodies.

#### Scenario: Upstream request raises an exception
- **WHEN** an exception contains headers, URL query data, or response content
- **THEN** persisted telemetry contains only sanitized category/status/fingerprint information and
  raw evidence remains in the access-controlled Bundle

### Requirement: Configurable retention and pressure behavior
The system SHALL apply separate retention/size policies to operator events, technical logs, raw
Bundle evidence, processed Bundles, and dead-letter Bundles. Log pressure MUST NOT delete an
unprocessed Bundle.

#### Scenario: Local log quota is reached
- **WHEN** local technical logs reach their configured quota
- **THEN** acknowledged oldest technical logs are rotated or removed before any unprocessed
  Bundle evidence

### Requirement: Read-only Phase 1 management
Phase 1 GEO management APIs and UI SHALL be authenticated and read-only for Collector node, run,
event, transfer, backlog, and error inspection.

#### Scenario: Operator views a failed transfer
- **WHEN** an authorized operator opens the failure detail
- **THEN** GEO shows sanitized classification, attempts, timestamps, correlation IDs, and replay
  evidence reference without exposing credentials or raw secrets
