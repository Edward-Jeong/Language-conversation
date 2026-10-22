param()
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not $env:LC_DATABASE_PATH) {$env:LC_DATABASE_PATH=(Join-Path (Get-Location) 'data/language-conversation.sqlite3')}
if (Test-Path data/auth-secret) {$env:LC_AUTH_SECRET=(Get-Content data/auth-secret -Raw).Trim()}
if (Test-Path '.env') {
  foreach($line in Get-Content '.env') {
    if($line -match '^\s*(LC_[A-Z0-9_]+)\s*=\s*(.*?)\s*$') {[Environment]::SetEnvironmentVariable($Matches[1],$Matches[2].Trim('"').Trim("'"),'Process')}
  }
}
$pythonCommand=if(Test-Path .venv/Scripts/python.exe){(Resolve-Path .venv/Scripts/python.exe).Path}else{throw 'Create the project .venv and install requirements.lock.txt first; see docs/INSTALL.ko.md'}
& $pythonCommand -m apps.api.pairing
