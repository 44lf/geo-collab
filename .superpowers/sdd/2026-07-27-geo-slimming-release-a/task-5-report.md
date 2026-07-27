# Release A Task 5 Report

## Status

Implemented the complete shared question-source manager under `/agents`, added a contextual
manager/sync entry to `question_source` pipeline nodes, retired the visible AI navigation entry,
and made `/ai` an explicit replace redirect to `/agents`.

Implementation commit: `0111054` (`feat: move question source management into agents`).

The Task 3 active/compat DTO behavior and Task 4 API clients were not changed. The old
AI-generation source remains in the TypeScript include and now consumes the required named
`PoolManagerModal` compatibility wrapper.

## TDD Evidence

### RED

Created `scripts/check-release-a-frontend.ps1` before changing production routing/navigation.

Command:

```powershell
powershell -NoProfile -File scripts/check-release-a-frontend.ps1
```

Observed result:

```text
Exit code: 1
missing /ai redirect
```

The failure was expected and was caused by the old `/ai` workspace route still being mounted.

### GREEN

After the route/navigation implementation, the same command completed with exit code 0 and no
output:

```powershell
powershell -NoProfile -File scripts/check-release-a-frontend.ps1
```

The script asserts both:

- `/ai` is a `<Navigate to="/agents" replace />` route.
- `App.tsx` and `MobileNav.tsx` contain no exact `"ai"` navigation key.

## Implementation

### Question-source management

`web/src/features/pipelines/question-pools/QuestionPoolManagerModal.tsx` now owns the full shared
question-source UI and loads pools through the Task 4 `api/question-pools.ts` client. It supports:

- Creating a source with an optional Feishu `app_token` / `table_id` binding.
- Renaming and rebinding an existing source.
- Clearing a binding by saving empty strings, matching the backend PATCH contract.
- Manual Feishu synchronization with added/updated/reactivated/deactivated feedback.
- Toggling `auto_sync_enabled`.
- Deleting only when `useAuth().user.role === "admin"`; operators do not receive a delete control.
- Refreshing its own list and notifying the owning entry point after mutations.

Edits call:

```ts
updateQuestionPool(id, {
  name,
  feishu_app_token,
  feishu_table_id,
  auto_sync_enabled,
})
```

The old `web/src/features/ai-generation/PoolManagerModal.tsx` is retained as a named compatibility
wrapper with the existing `GenerateTab.tsx` props (`pools`, `onClose`, `onChanged`). It delegates to
the new manager and deliberately keeps the unused `_pools` name from the brief.

### `/agents` entry points

- `AgentManagementWorkspace` exposes `问题源管理` beside `新建智能体`.
- A selected `question_source` node exposes `管理问题源` and `立即同步`.
- Node-level sync refreshes both the pool list and the selected pool's question-type cache so the
  editor reflects newly synchronized questions without leaving the page.
- Empty-source copy points users to the local `管理问题源` action instead of the retired AI page.

### Routing and navigation

- `/ai` now renders `<Navigate to="/agents" replace />`.
- The old lazy AI workspace route import and route adapter were removed from `routes.tsx`.
- The `"ai"` `NavKey`, desktop `navItems` entry, tab title, known-nav entry, and mobile bottom entry
  were removed.
- The legacy source files were not deleted and remain compilable for Release A.

## Responsive and Permission Behavior

- Desktop: the manager is capped at 760 px and keeps pool metadata/actions in a compact card row.
- Mobile (`max-width: 768px`): the existing modal safe-area rules make it near-full-width above the
  bottom bar; pool cards stack vertically, Feishu fields collapse to one column, and actions wrap.
- Removing the mobile AI item leaves three primary destinations plus `更多`, all sharing the
  existing flexible bottom-bar sizing.
- All authenticated users can create, rename/rebind, sync, and toggle auto-sync.
- Only admins see the delete action. The backend remains the final admin authorization boundary.

## Verification

Fresh final runs:

| Gate | Result |
| --- | --- |
| `powershell -NoProfile -File scripts/check-release-a-frontend.ps1` | PASS |
| `pnpm --filter @geo/web lint` | PASS, 0 errors; 13 pre-existing warnings |
| `pnpm --filter @geo/web typecheck` | PASS |
| Targeted Prettier check for the new modal and compatibility wrapper | PASS |
| `pnpm --filter @geo/web build` | PASS; Vite transformed 1860 modules |
| `git diff --check` | PASS |

The requested full `pnpm --filter @geo/web format:check` was run and remains a repository baseline
failure: Prettier reports 91 files, including many untouched files. The first run reported 92 files;
formatting the new modal removed it from the failure list. The new modal and compatibility wrapper
pass a targeted `prettier --check`.

Lint has 13 warnings and no errors. The warnings are existing Fast Refresh export and Hook
dependency warnings in unrelated files; none point to Task 5 files.

## Files

Created:

- `scripts/check-release-a-frontend.ps1`
- `web/src/features/pipelines/question-pools/QuestionPoolManagerModal.tsx`

Modified:

- `web/src/features/pipelines/AgentManagementWorkspace.tsx`
- `web/src/features/pipelines/PipelineEditor.tsx`
- `web/src/features/ai-generation/PoolManagerModal.tsx`
- `web/src/routes.tsx`
- `web/src/App.tsx`
- `web/src/components/MobileNav.tsx`
- `web/src/types.ts`
- `web/src/styles.css`

## Self-review

- No Task 3/4 API or DTO file was edited.
- The only behavioral CSS change is the mobile safe-area-aware panel height; the later Task 5
  scoped Prettier pass also made mechanical layout/casing changes without changing selectors or
  declarations.
- The old AI-generation implementation was not deleted; only its public route/import was retired.
- The compatibility wrapper matches the current `GenerateTab` call signature and typecheck/build
  prove it remains compilable.
- The route/navigation static assertion passes and an exact-key scan finds no AI nav key in desktop
  or mobile navigation.
- Mutation failures remain visible through toasts; a post-mutation entry-point refresh failure is
  reported separately so it is not mislabeled as a failed server mutation.

## Concerns

- Full-repository Prettier remains red on 84 files after the Task 5 scoped formatting pass.
  Reformatting the remaining frontend was intentionally kept out of this task.
- No live Feishu credentials or authenticated backend were available for a destructive interactive
  sync/delete smoke test. API contracts are covered by existing clients plus TypeScript/build gates.

## Review Fix Round (2026-07-27)

### Race and interaction fixes

- The manager now creates an incrementing open-cycle token. `reload` and every async mutation only
  update modal state or show a toast while both `openCycleRef` and `activeCycleRef` match the
  captured token. Closing synchronously invalidates the token and clears the transient pool,
  create, edit, and busy state before the parent applies `open={false}`; reopening therefore
  cannot display an old edit/create state or accept an earlier response.
- `createPendingRef` is set synchronously before `createQuestionPool`, and the create form controls
  are disabled while pending. A second click in the same open cycle therefore cannot send a second
  POST. A response from an older cycle cannot clear the newer cycle's pending guard.
- In `PipelineEditor`, the sync mutation is isolated from the post-success pool/type refresh. A
  successful sync is acknowledged first; if either refresh fails the user sees
  `同步成功，但刷新问题源失败` (with the underlying message when available), not the misleading
  mutation-failure toast.
- The mobile `.schemePanel` cap is `100dvh - 12px - 72px - env(safe-area-inset-bottom)`, matching
  the modal overlay's top gap, persistent bottom navigation, and iOS safe area while preserving
  the panel's existing flex/scroll body.

### Verification

| Gate | Result |
| --- | --- |
| `powershell -NoProfile -File scripts/check-release-a-frontend.ps1` | Blocked locally by the machine's `RemoteSigned` execution policy for this unsigned workspace script. |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-release-a-frontend.ps1` | PASS. This is the same requested assertion with only a process-local execution-policy override. |
| Scoped Prettier for all 10 Task 5 paths | PASS after running Prettier write/check on exactly the nine supported Task 5 source files. `scripts/*.ps1` has no Prettier parser and was explicitly ignored with `--ignore-unknown`. The formatting diff was reviewed as mechanical only; no file outside the Task 5 list was formatted. |
| `pnpm --filter @geo/web lint` | PASS: 0 errors, 13 pre-existing warnings. |
| `pnpm --filter @geo/web typecheck` | PASS. |
| `pnpm --filter @geo/web build` | PASS; Vite transformed 1860 modules. |
| `pnpm --filter @geo/web format:check` | Expected remaining baseline failure: 84 files after the scoped Task 5 formatting pass. |
| `git diff --check` | PASS. |
