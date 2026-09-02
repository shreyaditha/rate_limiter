# Local full-stack runner (no Docker required).
# Starts Redis + 3 mock services + gateway, then prints demo curls.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
if (-not (Test-Path (Join-Path $Root "gateway"))) {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
    $Root = Split-Path -Parent $Root
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$RedisExe = Join-Path $Root ".tools\redis\redis-server.exe"
$RedisCli = Join-Path $Root ".tools\redis\redis-cli.exe"
$PidDir = Join-Path $Root ".run"
$LogDir = Join-Path $PidDir "logs"

New-Item -ItemType Directory -Force -Path $PidDir, $LogDir | Out-Null

function Test-Port([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $Port)
        $c.Close()
        return $true
    } catch {
        return $false
    }
}

function Wait-Http([string]$Url, [int]$Seconds = 30) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Start-LoggedProcess([string]$Name, [string]$FilePath, [string[]]$ArgumentList, [string]$WorkingDirectory) {
    $out = Join-Path $LogDir "$Name.out.log"
    $err = Join-Path $LogDir "$Name.err.log"
    $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $out -RedirectStandardError $err `
        -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $PidDir "$Name.pid") -Value $p.Id
    Write-Host "started $Name (pid $($p.Id))"
    return $p
}

if (-not (Test-Path $Python)) {
    Write-Host "Creating venv and installing deps..."
    python -m venv (Join-Path $Root ".venv")
    & (Join-Path $Root ".venv\Scripts\python.exe") -m pip install --upgrade pip
    & (Join-Path $Root ".venv\Scripts\python.exe") -m pip install --only-binary=:all: -r (Join-Path $Root "requirements.txt")
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
}

if (-not (Test-Path $RedisExe)) {
    throw "Redis not found at $RedisExe. Run scripts\bootstrap-redis.ps1 first."
}

# Stop leftover stack if any
$ErrorActionPreference = "Continue"
& (Join-Path $PSScriptRoot "stop-local.ps1") -Quiet
$ErrorActionPreference = "Stop"

# Start Redis if not already running.
if (-not (Test-Port 6379)) {
    # Ensure a local redis.conf exists (portable Redis under .tools).
    $RedisConf = Join-Path $Root ".tools\redis\rate-limiter.conf"
    if (-not (Test-Path $RedisConf)) {
        @"
bind 127.0.0.1
port 6379
save ""
appendonly no
dbfilename dump.rdb
dir ./
"@ | Set-Content -Path $RedisConf -Encoding ASCII
    }

    Start-LoggedProcess -Name "redis" -FilePath $RedisExe -ArgumentList @(
        $RedisConf
    ) -WorkingDirectory (Join-Path $Root ".tools\redis") | Out-Null

    Start-Sleep -Seconds 1
    & $RedisCli ping | Out-Null
} else {
    Write-Host "redis already listening on 6379"
}

$env:JWT_SECRET = "change-me-in-production-use-a-long-random-string"
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
$env:RATE_LIMIT_REQUESTS = "10"
$env:RATE_LIMIT_WINDOW_SECONDS = "60"
$env:RATE_LIMIT_FAIL_MODE = "closed"
$env:ORDERS_UPSTREAM = "http://127.0.0.1:8001"
$env:INVENTORY_UPSTREAM = "http://127.0.0.1:8002"
$env:USERS_UPSTREAM = "http://127.0.0.1:8003"

Start-LoggedProcess -Name "orders" -FilePath $Python -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001"
) -WorkingDirectory (Join-Path $Root "services\orders") | Out-Null

Start-LoggedProcess -Name "inventory" -FilePath $Python -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8002"
) -WorkingDirectory (Join-Path $Root "services\inventory") | Out-Null

Start-LoggedProcess -Name "users" -FilePath $Python -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8003"
) -WorkingDirectory (Join-Path $Root "services\users") | Out-Null

Start-LoggedProcess -Name "gateway" -FilePath $Python -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
) -WorkingDirectory (Join-Path $Root "gateway") | Out-Null

Write-Host "Waiting for services..."
$ok = @(
    (Wait-Http "http://127.0.0.1:8001/health"),
    (Wait-Http "http://127.0.0.1:8002/health"),
    (Wait-Http "http://127.0.0.1:8003/health"),
    (Wait-Http "http://127.0.0.1:8000/health")
)
if ($ok -contains $false) {
    Write-Host "One or more services failed to start. Check logs in $LogDir"
    Get-ChildItem $LogDir | ForEach-Object { Write-Host "---- $($_.Name) ----"; Get-Content $_.FullName -Tail 40 }
    exit 1
}

Write-Host ""
Write-Host "FULL STACK IS UP"
Write-Host "  Gateway:   http://127.0.0.1:8000/docs"
Write-Host "  Orders:    http://127.0.0.1:8001"
Write-Host "  Inventory: http://127.0.0.1:8002"
Write-Host "  Users:     http://127.0.0.1:8003"
Write-Host "  Redis:     127.0.0.1:6379"
Write-Host ""
Write-Host "Stop with:  .\scripts\stop-local.ps1"
Write-Host "Demo with:  .\scripts\demo.ps1"
