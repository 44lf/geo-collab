$route = Get-Content -Raw web/src/routes.tsx
$aiRedirectPattern = '(?s)\{\s*path:\s*["'']/?ai["'']\s*,\s*element:\s*<Navigate\s+to=["'']/agents["'']\s+replace\s*/>\s*\}'
if ($route -notmatch $aiRedirectPattern) { throw "missing /ai redirect" }

$navFiles = @("web/src/App.tsx", "web/src/components/MobileNav.tsx")
foreach ($file in $navFiles) {
  if ((Get-Content -Raw $file) -match '["'']ai["'']') {
    throw "ai navigation remains in $file"
  }
}
