# GEO slimming Release A: post-deploy observation gate

## Status and scope

This is a future production-observation runbook. Release A has **not** been deployed or observed by this document, and Release B is not unlocked. Do not use this runbook to write production data, run Release B migrations, or infer a deployment from local test results.

An observation day is one complete UTC calendar day after the Release A deployment timestamp. Seven qualifying days must be consecutive. If telemetry, a required smoke test, or an operator record is missing for any UTC day, stop the streak and restart at the next complete UTC day after the gap is resolved. Preserve the interrupted evidence; do not backfill it as observed.

## Before day 1

Record the deployment timestamp, release commit/image IDs, operator, dashboard/log-query links, and a read-only evidence location. Verify that no Release B migration has run. Create the first daily entry only after its full UTC day has elapsed.

For every actual observation day, append a level-2 heading matching this regex exactly:

```text
^## Observation \d{4}-\d{2}-\d{2} UTC$
```

Do not pre-create seven dated entries. Each completed daily entry must contain the following exact labels, populated with timestamped output or links (not a pass/fail assertion without evidence):

```text
scheme_counts:
scheme_410_count:
scheme_get_count:
shared_endpoint_health:
pipeline_smoke:
mcp_smoke:
goal_skill_smoke:
article_smoke:
runtime_error_scan:
```

## Daily evidence collection

Run these read-only SQL queries against the production reporting connection and attach raw output with UTC execution time:

```sql
SELECT action, COUNT(*), MAX(created_at)
FROM audit_logs
WHERE action LIKE 'generation_scheme.%'
  AND created_at >= UTC_TIMESTAMP() - INTERVAL 7 DAY
GROUP BY action;

SELECT status, COUNT(*) FROM generation_scheme_runs GROUP BY status;
SELECT status, COUNT(*) FROM generation_scheme_run_tasks GROUP BY status;
SELECT COUNT(*), MAX(updated_at) FROM generation_schemes;
```

Under `scheme_counts`, include all four query outputs. Confirm no new scheme mutation audit or row is introduced, no scheme run remains pending/running, and historical failed-run rows (including the known five pending tasks under failed run 14) are not rewritten.

Use nginx access logs for the same UTC day:

- `scheme_410_count`: count each of the five scheme write methods returning 410; retain query/filter and sampled request IDs. They must have no corresponding new rows or audit writes.
- `scheme_get_count`: count list/detail/history GET requests separately; identify manual/known diagnostic traffic. Non-manual traffic must decline to zero after `/ai` is hidden; any unexplained traffic is a gate failure.
- `shared_endpoint_health`: record status/count/latency samples for engine endpoints, question-pool endpoints, and Pipeline endpoints. Include 4xx/5xx counts and links to the exact nginx query.

Smoke work must use approved test content and normal authorization. Capture request IDs, response status, created IDs only where the normal product workflow creates them, and cleanup/reference records according to existing retention rules:

- `pipeline_smoke`: execute one manual Pipeline run and retain run status plus node output evidence.
- `mcp_smoke`: use MCP to read a question and save an approved test article; verify the saved article can be read back.
- `goal_skill_smoke`: generate/download the goal skill ZIP, verify its SHA against info, and perform the normal install compatibility check.
- Also run the weekly-report Loop flow and retain its report/event evidence.
- `article_smoke`: verify Article feed and detail, then execute the approved publish smoke path and retain publish result/record status.
- `runtime_error_scan`: scan app, worker, and scheduler logs for the UTC day; attach queries and counts for ERROR, traceback, unhandled exception, failed scheduler run, and retry exhaustion. Classify each hit and link remediation.

## Immediate rollback and escalation

Stop the observation streak, preserve evidence, and use the normal release rollback procedure if any of these occur:

- scheme mutation returns anything other than 410, or creates rows/audit/background work;
- unexpected scheme GET traffic persists after the UI redirect is hidden;
- engine, question-pool, Pipeline, MCP, goal skill, weekly report, Article, or publish smoke fails;
- any pending/running scheme run appears, historical data changes unexpectedly, or runtime errors indicate a Release A regression;
- a required day has incomplete evidence.

Record the rollback decision, release identifiers, incident/ticket link, affected UTC date, raw SQL/nginx/log output, and the next permitted restart date. Do not alter historical scheme records merely to make the gate pass.

## Evidence retention and final review

Store daily SQL output, nginx queries/results, smoke request IDs, screenshots or API responses, log searches, operator name, and links in the release evidence location with immutable timestamps. At the end of seven consecutive complete UTC days, a release owner reviews the seven entries together. Release B remains locked unless every entry has all required labels and evidence, all protected contracts are healthy, and no rollback condition occurred.

Release B decision: NO-GO
