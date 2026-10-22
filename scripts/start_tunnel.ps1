param([int]$Port=8000)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& .runtime/cloudflared.exe tunnel --no-autoupdate --url "http://127.0.0.1:$Port"
