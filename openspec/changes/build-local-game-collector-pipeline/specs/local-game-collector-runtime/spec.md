## ADDED Requirements

### Requirement: Unified Python source runtime
The Collector Agent SHALL execute the proven Baidu, NineGame, YingYongBao, and TapTap acquisition
adapters through one versioned Python source contract that emits canonical game records and
preserves bounded raw evidence.

#### Scenario: Four source adapters are available
- **WHEN** an operator or schedule requests a supported source
- **THEN** the Agent dispatches the request through the registered adapter and emits the same
  canonical identity, metadata, media-reference, result, and evidence fields

#### Scenario: Unknown source is requested
- **WHEN** a job names a source that is not registered by the running Collector version
- **THEN** the Agent rejects the job before issuing an external request and records a permanent
  configuration error

### Requirement: Versioned job manifests
The Agent MUST persist an immutable versioned job manifest before issuing any source request. A
manifest SHALL identify its job, policy version, mode, sources or source order, bounded targets or
discovery paths, media limits, schedule occurrence, and destination.

#### Scenario: Valid refresh job is claimed
- **WHEN** the Gateway returns a bounded refresh job
- **THEN** the Agent atomically stores the manifest and uses it as the sole input for that run

#### Scenario: Job exceeds a configured bound
- **WHEN** a manifest exceeds its target, discovery-path, request, or media limit
- **THEN** the Agent rejects it without contacting a source

### Requirement: Manual and scheduled execution
The Agent SHALL support operator-initiated runs and persisted configuration-driven schedules using
the same execution, pacing, spool, event, and transfer paths.

#### Scenario: Operator starts a run
- **WHEN** an operator invokes a supported `run-now` command
- **THEN** the Agent creates a normal job/run record and does not bypass pacing, locking, evidence,
  or transfer behavior

#### Scenario: Schedule becomes due
- **WHEN** a schedule occurrence is due while the Agent is online
- **THEN** the Agent claims one bounded job and executes it within the configured window and quota

### Requirement: Bounded offline catch-up
The Agent SHALL persist schedule progress and SHALL NOT burst all missed occurrences after a local
computer resumes from sleep, shutdown, or lost connectivity.

#### Scenario: Recent occurrence was missed
- **WHEN** the Agent starts and finds a missed occurrence younger than `catch_up_max_age`
- **THEN** it executes no more than the configured `max_runs_per_start` catch-up count

#### Scenario: Stale occurrence was missed
- **WHEN** a missed occurrence is older than `catch_up_max_age`
- **THEN** the Agent records it as skipped and does not issue the source requests

### Requirement: Conservative source failure policy
The Agent SHALL distinguish blocked, permanent, and retryable source outcomes. It MUST halt the
remaining TapTap run immediately on HTTP 403, 405, or 429 and MUST NOT use proxy rotation,
fingerprint spoofing, or access-control bypass.

#### Scenario: TapTap blocks a request
- **WHEN** any TapTap stage returns HTTP 403, 405, or 429
- **THEN** the Agent records the status/evidence, stops remaining TapTap targets, and schedules no
  automatic retry for that run

#### Scenario: Source request times out
- **WHEN** a source request fails with an ordinary timeout or eligible transient 5xx response
- **THEN** the Agent applies only the configured finite conservative retry policy and records every
  attempt

### Requirement: Durable local execution state
The Agent SHALL persist jobs, per-target results, Bundles, transfer state, and unacknowledged
telemetry outside the application release directory. State transitions MUST use atomic replacement
and one global execution lock.

#### Scenario: Local computer restarts during a run
- **WHEN** the Agent restarts after some targets have completed
- **THEN** it preserves completed evidence and resumes from the first incomplete safe stage

#### Scenario: A second Agent starts
- **WHEN** another process attempts to use the same Collector identity and data directory
- **THEN** only one process acquires the global lock and the other exits without running a source

### Requirement: Disk backpressure
The Agent MUST stop accepting new collection jobs at the configured spool high-water mark while
continuing transfer, receipt polling, telemetry draining, and acknowledged-data cleanup.

#### Scenario: Spool reaches high-water mark
- **WHEN** pending spool bytes or filesystem utilization reaches its configured threshold
- **THEN** the Agent pauses new source execution, exposes a degraded state, and retains every
  unprocessed Bundle
