# Focused verification evidence

Date: 2026-07-29 (Asia/Shanghai)

Scope: local Python Collector, GEO Collector Gateway/Consumer/import/migration APIs, read-only
management UI, local packaging and Compose syntax. No DEV or production deployment was performed.

## Collector repository

Working directory: `E:\geo-taptap-collector`

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q --basetemp E:\geo\.test-tmp\collector-acceptance-final
python -m ruff check --no-cache src tests
python -m ruff format --check src tests
$env:MYPY_CACHE_DIR='E:\geo\.test-tmp\collector-acceptance-final-mypy'
python -m mypy
```

Result:

- pytest: `141 passed in 2.05s`
- Ruff lint: passed
- Ruff format: 47 files already formatted
- strict mypy: 27 source files passed
- launcher/CLI tests invoke both SIGINT and SIGTERM handlers, prove the scheduled loop exits,
  prove the CLI `finally` path calls `application.close()`, and prove close flushes buffered
  telemetry.

Additional packaging/launcher evidence:

```powershell
docker compose -f compose.example.yaml config --quiet
$env:PYTHONPATH='src'
python -m geo_game_collector --version
python -m geo_game_collector --config <test-config> configure `
  --gateway-url https://gateway.test/ `
  --collector-id collector-evidence `
  --data-dir <test-data>
python -m geo_game_collector --config <test-config> health
python -m pip install -e . --no-deps
geo-game-collector --version
```

Result:

- Compose configuration parsed successfully.
- version: `geo-game-collector 0.1.0`
- public configuration was written without a credential.
- health returned `ok: true`, writable data directory, and zeroed spool metrics.
- editable package build/install succeeded and the installed console entry point returned 0.1.0.
- Docker daemon was not available, so an image build/runtime smoke test is not claimed.

## GEO server and Web

Working directory: `E:\geo`

```powershell
$collectorTests = Get-ChildItem server\tests -Filter 'test_collector_*.py' |
  ForEach-Object { $_.FullName }
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q $collectorTests server\tests\test_game_ingest_execution_mode.py `
  --basetemp E:\geo\.test-tmp\geo-collector-final
python -m ruff check --no-cache `
  server/app/modules/collector `
  server/alembic/versions/0073_collector_gateway_models.py `
  server/app/modules/game_library/execution_mode.py `
  server/app/core/config.py server/app/main.py `
  server/scripts/remote_game_bundle_import.py `
  server/tests/test_collector_*.py `
  server/tests/test_game_ingest_execution_mode.py
python -m ruff format --check `
  server/app/modules/collector `
  server/alembic/versions/0073_collector_gateway_models.py `
  server/app/modules/game_library/execution_mode.py `
  server/app/core/config.py server/app/main.py `
  server/scripts/remote_game_bundle_import.py `
  server/tests/test_collector_*.py `
  server/tests/test_game_ingest_execution_mode.py
$env:MYPY_CACHE_DIR='E:\geo\.test-tmp\geo-collector-final-mypy'
python -m mypy `
  server/app/modules/collector `
  server/app/modules/game_library/execution_mode.py

Push-Location web
pnpm test:collector
pnpm exec prettier --check package.json src/App.tsx `
  src/components/MobileMorePage.tsx src/routes.tsx src/types.ts `
  src/api/collector-management.ts `
  src/features/collector/CollectorManagementWorkspace.tsx `
  src/features/collector/collectorManagementViewModel.ts `
  tests/collector-management-view-model.test.ts
pnpm typecheck
pnpm lint
pnpm build
Pop-Location
```

Result:

- pytest: `151 passed in 22.43s`
- Ruff lint/format: passed; 43 files formatted
- mypy: 20 source files passed
- Web Collector view-model tests: `3 passed`
- Web changed-file Prettier check: passed
- Web typecheck: passed
- Web lint: 0 errors; 13 pre-existing repository warnings outside the Collector feature
- Web production build: passed; 1,863 modules transformed

The pytest selection contains model/migration, auth, job/control, transfer, Inbox, bundle
validation, Consumer leases/retries/dead-letter/crash recovery, GEO normalization/import,
management API, and external scheduler gate tests.

## Secret scan

Production source paths, Web Collector files, Dockerfile and Compose example were scanned with the
following reproducible command:

```powershell
$secretTargets = @(
  'server/app/modules/collector',
  'web/src/api/collector-management.ts',
  'web/src/features/collector',
  'E:/geo-taptap-collector/src',
  'E:/geo-taptap-collector/Dockerfile',
  'E:/geo-taptap-collector/compose.example.yaml'
)
$secretPattern = '(?i)-----BEGIN (?:RSA|EC|OPENSSH|PRIVATE) KEY-----|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._~-]{20,}|mysql(?:\+pymysql)?://[^/\s:]+:[^@\s]+@|X-Amz-(?:Signature|Credential)=[A-Za-z0-9%]+'
$secretMatches = rg -n --pcre2 $secretPattern $secretTargets
if ($LASTEXITCODE -eq 1) {
  Write-Output 'SECRET_SCAN_NO_MATCHES'
} elseif ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
} else {
  $secretMatches
  exit 1
}
```

Result: `SECRET_SCAN_NO_MATCHES`.

Test fixtures and operator documentation intentionally contain obvious words such as `secret` to
prove redaction and show placeholder commands; they were excluded from the literal-secret gate.

## Remaining acceptance evidence

The following are deliberately not claimed:

- DEV deployment and a real four-source Bundle through Gateway, Inbox, Consumer, business MinIO,
  MySQL receipt and Web timeline;
- live network interruption/auth-expiry/Gateway restart/Consumer crash fault injection in DEV;
- external scheduler cutover and repeated observation cycles;
- Docker image build, because the local Docker daemon was unavailable;
- any production tag, deployment, credential, database write or scheduler change.
