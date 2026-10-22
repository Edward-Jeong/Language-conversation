param()
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$artifacts=@(
 @{Name='whisper-bin-x64-v1.7.6.zip';Url='https://github.com/ggml-org/whisper.cpp/releases/download/v1.7.6/whisper-bin-x64.zip';Sha='0D2ECA299C248F965BD0341BCB219DB4B433C7F0C0CE2200D4DF85765E8156A9'},
 @{Name='ggml-base.bin';Url='https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.bin';Sha='60ED5BC3DD14EEA856493D334349B405782DDCAF0028D4B5DF4088345FBA2EFE'}
)
Write-Host 'Downloads: official whisper.cpp v1.7.6 x64 CPU binary ~4MB; multilingual base weights ~148MB. No account, inference fee, or system settings changed.'
New-Item -ItemType Directory -Force .runtime | Out-Null
foreach($artifact in $artifacts) {
 $dest=Join-Path '.runtime' $artifact.Name
 if (-not (Test-Path $dest)) {Invoke-WebRequest $artifact.Url -OutFile $dest}
 if ((Get-FileHash $dest -Algorithm SHA256).Hash -ne $artifact.Sha) {throw "Checksum mismatch: $dest. File preserved for inspection; not executed."}
}
if (-not (Test-Path .runtime/whisper-v1.7.6)) {Expand-Archive .runtime/whisper-bin-x64-v1.7.6.zip .runtime/whisper-v1.7.6}
Write-Host 'Verified. Run scripts/start_stt.ps1 in a separate terminal.'
