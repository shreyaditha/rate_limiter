param([switch]$Quiet)

$Root = Split-Path -Parent $PSScriptRoot
$PidDir = Join-Path $Root ".run"
$RedisCli = Join-Path $Root ".tools\redis\redis-cli.exe"

function Stop-PidFile([string]$Name) {
    $file = Join-Path $PidDir "$Name.pid"
    if (-not (Test-Path $file)) { return }
    $pidValue = (Get-Content $file -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($pidValue) {
        Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
        if (-not $Quiet) { Write-Host "stopped $Name (pid $pidValue)" }
    }
    Remove-Item $file -Force -ErrorAction SilentlyContinue
}

foreach ($name in @("gateway", "orders", "inventory", "users", "redis")) {
    Stop-PidFile $name
}

# Also clear anything still bound to our ports.
foreach ($port in 8000, 8001, 8002, 8003, 6379) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        if (-not $Quiet) { Write-Host "freed port $port (pid $($c.OwningProcess))" }
    }
}

if (Test-Path $RedisCli) {
    cmd /c "`"$RedisCli`" shutdown nosave >nul 2>&1"
}

if (-not $Quiet) { Write-Host "local stack stopped" }
