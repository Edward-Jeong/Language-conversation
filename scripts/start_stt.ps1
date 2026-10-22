param()
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$binary='.runtime/whisper-v1.7.6/Release/whisper-server.exe'
if (-not (Test-Path $binary) -or -not (Test-Path .runtime/ggml-base.bin)) {throw 'Download the fixed runtime/model with scripts/install_stt.ps1 first.'}
& $binary -m .runtime/ggml-base.bin --host 127.0.0.1 --port 8080 -t 6 -l auto
