param([switch]$LocalHttp,[int]$Port=8443)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (Test-Path '.env') {
  foreach ($line in Get-Content '.env') {
    if ($line -match '^\s*(LC_[A-Z0-9_]+)\s*=\s*(.*?)\s*$') {
      [Environment]::SetEnvironmentVariable($Matches[1],$Matches[2].Trim('"').Trim("'"),'Process')
    }
  }
}
New-Item -ItemType Directory -Force data | Out-Null
if (-not $env:LC_AUTH_SECRET) {
  if (-not (Test-Path data/auth-secret)) {
    $bytes=New-Object byte[] 48
    $rng=[System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {$rng.GetBytes($bytes)} finally {$rng.Dispose()}
    [BitConverter]::ToString($bytes).Replace('-','') | Set-Content -NoNewline data/auth-secret
  }
  $env:LC_AUTH_SECRET=(Get-Content data/auth-secret -Raw).Trim()
}
if (-not $env:LC_DATABASE_PATH) {$env:LC_DATABASE_PATH=(Join-Path (Get-Location) 'data/language-conversation.sqlite3')}
if (-not (Test-Path apps/web/dist/index.html)) {throw 'Build UI first: cd apps/web; npm ci; npm run build'}
$pythonCommand=if(Test-Path .venv/Scripts/python.exe){(Resolve-Path .venv/Scripts/python.exe).Path}else{throw 'Create the project .venv and install requirements.lock.txt first; see docs/INSTALL.ko.md'}
if ($LocalHttp) {
  if ($Port -eq 8443) {$Port=8000}
  $env:LC_COOKIE_SECURE='0'
  $env:LC_ALLOWED_ORIGINS="http://127.0.0.1:$Port,http://localhost:$Port"
  Write-Host "PC-only development: http://127.0.0.1:$Port. iPhone requires trusted HTTPS."
  & $pythonCommand -m uvicorn apps.api.main:app --host 127.0.0.1 --port $Port
} else {
  if (-not (Test-Path .cert/server.pem) -or -not (Test-Path .cert/server-key.pem)) {throw 'Create certificates first: python scripts/create_local_cert.py --host <LAN-IP>. Then trust CA on your own devices; see docs/INSTALL.ko.md'}
  if (-not $env:LC_ALLOWED_ORIGINS) {throw 'Set LC_ALLOWED_ORIGINS=https://<LAN-IP>:8443 in .env before LAN HTTPS start.'}
  $env:LC_COOKIE_SECURE='1'
  Write-Host 'LAN HTTPS only. Keep Ollama/STT loopback. Stop this server with Ctrl+C.'
  & $pythonCommand -m uvicorn apps.api.main:app --host 0.0.0.0 --port $Port --ssl-certfile .cert/server.pem --ssl-keyfile .cert/server-key.pem
}
