param([int]$Port=8000,[switch]$Pair)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$config=Join-Path (Get-Location) '.runtime/external-test.json'
if (-not (Test-Path $config)) {throw 'Missing ignored .runtime/external-test.json; see docs/DEPLOYMENT.ko.md'}
$settings=Get-Content $config -Raw | ConvertFrom-Json
foreach ($name in @('auth_secret','edge_secret')) {
  if ($settings.$name -notmatch '^[A-Fa-f0-9]{96}$') {throw "External-test $name must be a random 48-byte hex secret (96 hex characters). Refusing to launch."}
}
if ($settings.auth_secret -eq $settings.edge_secret) {throw 'Use distinct auth and edge secrets. Refusing to launch.'}
$env:LC_AUTH_SECRET=$settings.auth_secret
$env:LC_EDGE_SECRET=$settings.edge_secret
$env:LC_DATABASE_PATH=Join-Path (Get-Location) 'data/external-test.sqlite3'
$env:LC_COOKIE_SECURE='1'
$env:LC_ALLOWED_ORIGINS='https://language-conversation.pages.dev'
$env:LC_AI_PROVIDER='ollama'
$env:LC_MODEL='gemma3:4b'
$python=Join-Path (Get-Location) '.venv/Scripts/python.exe'
if ($Pair) { & $python -m apps.api.pairing } else { & $python -m uvicorn apps.api.main:app --host 127.0.0.1 --port $Port --no-proxy-headers }
