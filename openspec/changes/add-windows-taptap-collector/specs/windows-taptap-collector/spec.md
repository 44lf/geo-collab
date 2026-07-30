## ADDED Requirements

### Requirement: Explicit bounded collection manifest
The collector SHALL read a UTF-8 JSON manifest containing between 1 and 20 explicit target games,
and each target MUST provide an exact game name and stable target identifier.

#### Scenario: Valid twenty-game manifest
- **WHEN** an operator starts collection with a valid manifest containing 20 unique targets
- **THEN** the collector accepts the manifest and processes only those targets

#### Scenario: Oversized manifest
- **WHEN** a manifest contains more than 20 targets
- **THEN** the collector rejects it before sending any TapTap request

### Requirement: Complete per-game TapTap acquisition
For each target, the collector SHALL perform XSRF bootstrap, exact-name brand search, detail
retrieval, icon acquisition when requested, and a configured bounded number of screenshot
downloads.

#### Scenario: Exact game is available
- **WHEN** TapTap returns an exact brand match and valid detail response
- **THEN** the collector records game identity, score, tags, platforms, description, icon metadata,
  and bounded screenshot metadata

#### Scenario: Search returns only fuzzy matches
- **WHEN** TapTap search returns related games but no title exactly matching the requested name
- **THEN** the collector records a miss and does not import a related game as the target

### Requirement: Conservative request control
The collector SHALL apply a configurable delay between target games and MUST halt the remaining
run when any TapTap stage returns HTTP 403, 405, or 429.

#### Scenario: Rate limit response
- **WHEN** a target request returns HTTP 429
- **THEN** the collector records the blocking response and sends no request for later targets

### Requirement: Per-game isolation and resumability
The collector SHALL persist completion state per target, skip successfully completed targets on
resume, and retry only incomplete or ordinarily failed targets.

#### Scenario: Run interrupted after five games
- **WHEN** the operator resumes a run whose first five targets are marked successful
- **THEN** the collector preserves those results and starts with the first incomplete target

### Requirement: Integrity-protected portable bundle
The collector SHALL produce a versioned bundle whose file inventory records each asset's relative
path, source URL, MIME type, byte count, and SHA-256 digest.

#### Scenario: Successful collection bundle
- **WHEN** collection finishes with one or more successful targets
- **THEN** the bundle contains a schema version, bundle ID, host/run evidence, per-game metadata,
  stability report, and integrity inventory

### Requirement: Sanitized stability evidence
The collector SHALL record public egress IP, per-stage HTTP status, latency, result counts, and
errors, and MUST NOT persist cookies or XSRF token values.

#### Scenario: Operator reviews a completed report
- **WHEN** a stability run completes
- **THEN** its report contains sufficient stage evidence to distinguish success, blocking, misses,
  and network failures without exposing session secrets
