$route = Get-Content -Raw web/src/routes.tsx
$aiRedirectPattern = '(?s)\{\s*path:\s*["'']/?ai["'']\s*,\s*element:\s*<Navigate\s+to=["'']/agents["'']\s+replace\s*/>\s*\}'
if ($route -notmatch $aiRedirectPattern) { throw "missing /ai redirect" }

$navFiles = @("web/src/App.tsx", "web/src/components/MobileNav.tsx")
foreach ($file in $navFiles) {
  if ((Get-Content -Raw $file) -match '["'']ai["'']') {
    throw "ai navigation remains in $file"
  }
}

$modal = Get-Content -Raw web/src/features/pipelines/question-pools/QuestionPoolManagerModal.tsx
if ($modal -notmatch 'resetTransientState') {
  throw "question source modal does not reset transient state"
}
if ($modal -notmatch 'openCycleRef\.current\s*!==\s*cycle') {
  throw "question source modal does not reject stale open-cycle results"
}
if ($modal -notmatch '(?:function\s+handleClose|const\s+handleClose)' -or $modal -notmatch 'activeCycleRef\.current\s*=\s*null') {
  throw "question source modal does not synchronously invalidate work when closing"
}
if ($modal -notmatch 'createPendingRef\.current') {
  throw "question source create submission has no synchronous duplicate guard"
}
$pendingDisabledCount = ([regex]::Matches($modal, 'disabled=\{createPending\}')).Count
if ($pendingDisabledCount -lt 5) {
  throw "question source create controls are not disabled while pending"
}

$pipelineEditor = Get-Content -Raw web/src/features/pipelines/PipelineEditor.tsx
if ($pipelineEditor -notmatch 'questionPoolRefreshFailure') {
  throw "question source sync does not separate refresh failures from mutation failures"
}

$styles = Get-Content -Raw web/src/styles.css
$mobileModalHeightPattern = "(?s)@media\s*\(max-width:\s*768px\).*?\.schemePanel[^{]*\{[^}]*max-height:\s*calc\(\s*100dvh[^;]*safe-area-inset-bottom"
if ($styles -notmatch $mobileModalHeightPattern) {
  throw "mobile question source modal does not use safe-area-aware dynamic viewport height"
}
