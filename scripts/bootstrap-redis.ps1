# Downloads portable Redis for Windows into .tools/redis (no admin required).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $Root ".tools\redis"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$zip = Join-Path $dest "Redis-x64-5.0.14.1.zip"
$url = "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip"

if (-not (Test-Path (Join-Path $dest "redis-server.exe"))) {
    Write-Host "Downloading Redis..."
    Invoke-WebRequest -Uri $url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $dest -Force
}

Write-Host "Redis ready at $dest"
& (Join-Path $dest "redis-server.exe") --version
