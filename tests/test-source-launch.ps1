$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$stdoutPath = Join-Path $workspace "output\source-launch-test.stdout.log"
$stderrPath = Join-Path $workspace "output\source-launch-test.stderr.log"
$launcherSource = Get-ChildItem -LiteralPath $workspace -Filter "*.cmd" |
    Where-Object { Select-String -LiteralPath $_.FullName -SimpleMatch "bridge\bridge.py" -Quiet } |
    Select-Object -First 1
$launcherName = "source-launch-test.cmd"
$launcherPath = Join-Path $workspace $launcherName

if (-not $launcherSource) {
    throw "Source launcher was not found."
}

Copy-Item -LiteralPath $launcherSource.FullName -Destination $launcherPath -Force

if (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue) {
    throw "Port 8787 must be free before running this test."
}

$env:OA_LAUNCH_NO_BROWSER = "1"

New-Item -ItemType Directory -Path (Split-Path -Parent $stdoutPath) -Force | Out-Null
Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue

$process = Start-Process `
    -FilePath "cmd.exe" `
    -ArgumentList "/d", "/c", $launcherName `
    -WorkingDirectory $workspace `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

$ready = $false
try {
    for ($attempt = 0; $attempt -lt 24; $attempt += 1) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:8787/api/health" -TimeoutSec 1
            if ($health.status -eq "ready") {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
} finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    $listener = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
        if ($serverProcess.CommandLine -like "*bridge\bridge.py*") {
            Stop-Process -Id $listener.OwningProcess -Force
        }
    }
    Remove-Item -LiteralPath $launcherPath -Force -ErrorAction SilentlyContinue
}

if (-not $ready) {
    Write-Output "RED: CMD did not start the app on port 8787."
    Get-Content -LiteralPath $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
    exit 1
}

Write-Output "GREEN: app health endpoint is ready."
