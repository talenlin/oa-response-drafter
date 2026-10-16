param(
  [string]$Destination = (Join-Path $PSScriptRoot 'vendor\office_oxide-0.1.15-cp310-abi3-win_amd64.whl')
)

$ErrorActionPreference = 'Stop'
$url = 'https://files.pythonhosted.org/packages/8c/9a/6f167fe34c285e5c8f3f65d65d7b41b2a5688a083ca4312c3da9544b49de/office_oxide-0.1.15-cp310-abi3-win_amd64.whl'
$expectedHash = '6af23b79a5e037e027eadc684bf48cf4b91238d7d8c8e86109cf7cfeb1ca4657'

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
Invoke-WebRequest -Uri $url -OutFile $Destination -TimeoutSec 60
$actualHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
  Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
  throw "OfficeOxide download hash verification failed. Expected $expectedHash; received $actualHash."
}
Write-Host "OfficeOxide 0.1.15 is ready: $Destination"
