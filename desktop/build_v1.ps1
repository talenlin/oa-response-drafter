param(
  [string]$Version = '1.0.1',
  [string]$PythonRuntime = '',
  [string]$OutputDirectory = '',
  [switch]$SkipLibreOffice,
  [string]$OfficeOxideWheel = ''
)

$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Version must have the form major.minor.patch.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseRoot = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $projectRoot 'release' }
$editionSuffix = if ($SkipLibreOffice) { '-轻量版' } else { '' }
$portableName = "OA阅答-v$Version$editionSuffix-免安装版"
$portableRoot = Join-Path $releaseRoot $portableName
$appRoot = Join-Path $portableRoot 'app'
$nativeRoot = Join-Path $PSScriptRoot 'native'
$iconPath = Join-Path $projectRoot 'web\assets\oa-yueda-logo-v1.ico'
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not $OfficeOxideWheel) {
  $OfficeOxideWheel = Join-Path $PSScriptRoot 'vendor\office_oxide-0.1.15-cp310-abi3-win_amd64.whl'
}

if (-not $PythonRuntime) {
  $PythonRuntime = & py -3.12 -c 'import sys; print(sys.base_prefix)'
  if ($LASTEXITCODE) { throw 'Python 3.12 was not found. Install it or supply -PythonRuntime.' }
  $PythonRuntime = ([string]$PythonRuntime).Trim()
}
if (-not (Test-Path -LiteralPath $PythonRuntime)) { throw "Python runtime not found: $PythonRuntime" }
if (-not (Test-Path -LiteralPath $csc)) { throw 'C# compiler not found.' }

# Validate the exact pinned build input before replacing any release output.
$hasOfficeOxide = Test-Path -LiteralPath $OfficeOxideWheel -PathType Leaf
if ($hasOfficeOxide) {
  $expectedHash = '6af23b79a5e037e027eadc684bf48cf4b91238d7d8c8e86109cf7cfeb1ca4657'
  $actualHash = (Get-FileHash -LiteralPath $OfficeOxideWheel -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actualHash -ne $expectedHash) { throw 'OfficeOxide wheel SHA-256 verification failed. Run desktop\download_office_oxide.ps1 again.' }
} elseif ($SkipLibreOffice) {
  throw 'The small build requires OfficeOxide. Run desktop\download_office_oxide.ps1 first, or supply -OfficeOxideWheel with the verified Windows wheel.'
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
if ([IO.Path]::GetFullPath($portableRoot) -ne (Join-Path ([IO.Path]::GetFullPath($releaseRoot)) $portableName)) { throw 'Invalid release output path.' }
if (Test-Path -LiteralPath $portableRoot) { Remove-Item -LiteralPath $portableRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $appRoot | Out-Null

# Current application sources only; local user data and test caches are excluded.
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge') -Destination (Join-Path $appRoot 'bridge') -Recurse
Get-ChildItem (Join-Path $appRoot 'bridge') -Directory -Filter '__pycache__' -Recurse | Remove-Item -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'web') -Destination (Join-Path $appRoot 'web') -Recurse
New-Item -ItemType Directory -Force -Path (Join-Path $appRoot 'desktop') | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run_app.py') -Destination (Join-Path $appRoot 'desktop\run_app.py')

# Carry a private Python runtime so end users do not need Python or uv.
$runtimePython = Join-Path $appRoot 'runtime\python'
New-Item -ItemType Directory -Force -Path $runtimePython | Out-Null
& robocopy $PythonRuntime $runtimePython /E /XD (Join-Path $PythonRuntime 'Lib\site-packages') /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Unable to copy Python runtime (robocopy $LASTEXITCODE)." }
$sourcePackages = Join-Path $PythonRuntime 'Lib\site-packages'
$targetPackages = Join-Path $runtimePython 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $targetPackages | Out-Null
$packageItems = @('docx', 'lxml', 'typing_extensions.py', 'python_docx-*.dist-info', 'lxml-*.dist-info', 'typing_extensions-*.dist-info')
foreach ($pattern in $packageItems) {
  Get-ChildItem -LiteralPath $sourcePackages -Filter $pattern -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $targetPackages -Recurse
  }
}

# OfficeOxide is a small, permissively licensed Windows wheel used to convert
# legacy DOC files when the end user has no desktop Word installation. Install
# it into the copied runtime with the source runtime's pip, so pip itself is
# never shipped to end users.
if ($hasOfficeOxide) {
  & (Join-Path $PythonRuntime 'python.exe') -m pip install --no-index --no-deps --no-compile --target $targetPackages $OfficeOxideWheel
  if ($LASTEXITCODE) { throw "Unable to install the OfficeOxide compatibility component." }
} elseif ($SkipLibreOffice) {
  throw "The small build requires OfficeOxide. Run desktop\download_office_oxide.ps1 first, or supply -OfficeOxideWheel with the verified Windows wheel."
} else {
  Write-Warning 'OfficeOxide wheel is absent. This complete build will use Microsoft Word or bundled LibreOffice for legacy DOC files.'
}

# The complete edition includes its own legacy-DOC converter. Reuse only the
# third-party runtime from the previous build cache, never old OA阅答 code.
if (-not $SkipLibreOffice) {
  $converterCandidates = @(
    (Join-Path $projectRoot 'build\cache\libreoffice'),
    'C:\Program Files\LibreOffice'
  )
  $converterSource = $converterCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (-not $converterSource) { throw 'LibreOffice runtime was not found. Use -SkipLibreOffice only for a smaller build.' }
  $converterTarget = Join-Path $appRoot 'runtime\libreoffice'
  New-Item -ItemType Directory -Force -Path $converterTarget | Out-Null
  & robocopy $converterSource $converterTarget /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "Unable to copy document converter (robocopy $LASTEXITCODE)." }
}

$launcherOutput = Join-Path $portableRoot 'OA阅答.exe'
$uninstallerOutput = Join-Path $portableRoot '卸载OA阅答.exe'
& $csc /nologo /target:winexe /optimize+ "/win32icon:$iconPath" /reference:System.dll /reference:System.Windows.Forms.dll "/out:$launcherOutput" (Join-Path $nativeRoot 'Launcher.cs')
if ($LASTEXITCODE) { throw 'Launcher compilation failed.' }
& $csc /nologo /target:winexe /optimize+ "/win32icon:$iconPath" /reference:System.dll /reference:System.Windows.Forms.dll "/out:$uninstallerOutput" (Join-Path $nativeRoot 'Uninstaller.cs')
if ($LASTEXITCODE) { throw 'Uninstaller compilation failed.' }

Copy-Item -LiteralPath (Join-Path $PSScriptRoot '使用说明.txt') -Destination (Join-Path $portableRoot '使用说明.txt')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $portableRoot 'THIRD_PARTY_NOTICES.md')

# Publish registry provenance instead of the build machine's local wheel path.
if ($hasOfficeOxide) {
  $provenance = Join-Path $targetPackages 'office_oxide-0.1.15.dist-info\direct_url.json'
  $publicOrigin = @{
    url = 'https://files.pythonhosted.org/packages/8c/9a/6f167fe34c285e5c8f3f65d65d7b41b2a5688a083ca4312c3da9544b49de/office_oxide-0.1.15-cp310-abi3-win_amd64.whl'
    archive_info = @{ hashes = @{ sha256 = $expectedHash } }
  }
  $publicOrigin | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $provenance -Encoding UTF8
}
# Cached bytecode can embed absolute paths from the builder's user directory.
foreach ($cache in @(Get-ChildItem -LiteralPath $runtimePython -Directory -Filter '__pycache__' -Recurse)) {
  $cachePath = [IO.Path]::GetFullPath($cache.FullName)
  if (-not $cachePath.StartsWith([IO.Path]::GetFullPath($runtimePython) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid bytecode cache path.' }
  Remove-Item -LiteralPath $cachePath -Recurse -Force
}

$portableZip = Join-Path $releaseRoot "$portableName.zip"
if (Test-Path -LiteralPath $portableZip) { Remove-Item -LiteralPath $portableZip -Force }
Compress-Archive -Path (Join-Path $portableRoot '*') -DestinationPath $portableZip -CompressionLevel Optimal

$installerBase = Join-Path $releaseRoot 'OA阅答-installer-base.exe'
& $csc /nologo /target:winexe /optimize+ "/win32icon:$iconPath" /reference:System.dll /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll /reference:Microsoft.CSharp.dll "/out:$installerBase" (Join-Path $nativeRoot 'Installer.cs')
if ($LASTEXITCODE) { throw 'Installer compilation failed.' }

$installerPath = Join-Path $releaseRoot "OA阅答-v$Version$editionSuffix-安装版.exe"
if (Test-Path -LiteralPath $installerPath) { Remove-Item -LiteralPath $installerPath -Force }
$payloadLength = (Get-Item -LiteralPath $portableZip).Length
$installerBytes = [System.IO.File]::ReadAllBytes($installerBase)
$lengthBytes = [BitConverter]::GetBytes([Int64]$payloadLength)
$magicBytes = [Text.Encoding]::ASCII.GetBytes('OAYUEDA1')
$output = [System.IO.File]::Create($installerPath)
try {
  $output.Write($installerBytes, 0, $installerBytes.Length)
  $input = [System.IO.File]::OpenRead($portableZip)
  try { $input.CopyTo($output) } finally { $input.Dispose() }
  $output.Write($lengthBytes, 0, $lengthBytes.Length)
  $output.Write($magicBytes, 0, $magicBytes.Length)
} finally {
  $output.Dispose()
}
Remove-Item -LiteralPath $installerBase -Force

Get-Item -LiteralPath $portableZip, $installerPath | Select-Object Name, Length, LastWriteTime
