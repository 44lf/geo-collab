## ADDED Requirements

### Requirement: Validate bundles before database work
The importer SHALL validate schema version, required metadata, canonical relative paths, entry
counts, byte limits, MIME signatures, and SHA-256 digests before performing database writes.

#### Scenario: Image hash mismatch
- **WHEN** an image's bytes do not match its declared SHA-256
- **THEN** the importer rejects the bundle before writing any game or image row

#### Scenario: Path traversal entry
- **WHEN** a bundle inventory path resolves outside the bundle root
- **THEN** the importer rejects the bundle as unsafe

#### Scenario: Target identity mismatch
- **WHEN** a successful entry's source, exact game name, or category differs between the manifest
  target and its game JSON
- **THEN** the importer rejects the bundle before opening a database session

### Requirement: Dry-run import planning
The importer SHALL provide a dry-run mode that validates the entire bundle and reports planned
game and image operations without committing database changes.

#### Scenario: Operator validates a new bundle
- **WHEN** the operator runs the importer with `--dry-run` on a valid bundle
- **THEN** the importer reports the targets, metadata merges, and image counts and commits nothing

### Requirement: Reuse existing game merge behavior
The importer SHALL reconstruct the existing game-library `Game` value and invoke
`upsert_game` with pre-downloaded image bytes and an optional existing target row/category.

#### Scenario: Existing production game target
- **WHEN** a valid bundle entry identifies an existing production game
- **THEN** the importer merges TapTap metadata and stores validated images through the existing
  game-library and image-library services

#### Scenario: Production target changed after export
- **WHEN** the target game no longer exists or its category changed after the manifest was exported
- **THEN** the importer rolls back that target and does not fall back to creating or name-matching
  another game

### Requirement: Idempotent per-game transactions
The importer SHALL commit each game independently, allow the same valid bundle to be imported
again without duplicate image records, and isolate a game-level import failure from other games.

#### Scenario: Bundle is imported twice
- **WHEN** an operator reruns a previously successful bundle
- **THEN** existing source identities and source-URL image hashes prevent duplicate logical data

#### Scenario: One game fails during import
- **WHEN** one target raises a database or storage error
- **THEN** the importer rolls back that target, records its failure, and continues with later
  validated targets

### Requirement: Import never deletes games
Missing, missed, blocked, or failed collector entries MUST NOT cause the importer to deactivate,
soft-delete, or remove any production game or image.

#### Scenario: Collector misses a requested game
- **WHEN** a bundle records a miss for a production target
- **THEN** the importer performs no deletion or deactivation for that target

### Requirement: Production credentials remain production-side
The collection and transfer workflow MUST NOT place production database or object-storage
credentials on the Windows collector.

#### Scenario: Collector bundle is inspected
- **WHEN** an operator inspects collector configuration and output
- **THEN** neither production database credentials nor production object-storage credentials are
  present
