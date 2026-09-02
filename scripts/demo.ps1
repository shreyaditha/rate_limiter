# End-to-end demo against a running local stack (Windows PowerShell 5+ compatible).
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:8000"

Add-Type -AssemblyName System.Net.Http
$client = [System.Net.Http.HttpClient]::new()

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Url,
        [hashtable]$Headers = @{},
        [string]$Body
    )
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), $Url)
    foreach ($k in $Headers.Keys) {
        [void]$req.Headers.TryAddWithoutValidation($k, [string]$Headers[$k])
    }
    if ($PSBoundParameters.ContainsKey("Body")) {
        $req.Content = [System.Net.Http.StringContent]::new($Body, [System.Text.Encoding]::UTF8, "application/json")
    }
    $resp = $client.SendAsync($req).GetAwaiter().GetResult()
    $text = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $headerMap = @{}
    foreach ($h in $resp.Headers) { $headerMap[$h.Key] = ($h.Value -join ",") }
    if ($resp.Content -and $resp.Content.Headers) {
        foreach ($h in $resp.Content.Headers) { $headerMap[$h.Key] = ($h.Value -join ",") }
    }
    return @{ Status = [int]$resp.StatusCode; Body = $text; Headers = $headerMap }
}

Write-Host "=== 1) Health ==="
$h = Invoke-Api GET "$base/health"
Write-Host "status=$($h.Status) body=$($h.Body)"
if ($h.Status -ne 200) { throw "health failed" }
$health = $h.Body | ConvertFrom-Json
if ($health.redis -ne "up") { throw "redis not up" }

Write-Host "`n=== 2) Login as alice (admin) ==="
$login = Invoke-Api POST "$base/auth/login" -Body '{"username":"alice","password":"alicepass"}'
Write-Host "status=$($login.Status) body=$($login.Body)"
$token = ($login.Body | ConvertFrom-Json).access_token

Write-Host "`n=== 3) Authenticated GET /orders ==="
$orders = Invoke-Api GET "$base/orders" -Headers @{ Authorization = "Bearer $token" }
Write-Host "status=$($orders.Status) body=$($orders.Body)"
if ($orders.Status -ne 200) { throw "orders failed" }

Write-Host "`n=== 4) RBAC: bob cannot GET /users (expect 403) ==="
$bobLogin = Invoke-Api POST "$base/auth/login" -Body '{"username":"bob","password":"bobpass"}'
$bobToken = ($bobLogin.Body | ConvertFrom-Json).access_token
$rbac = Invoke-Api GET "$base/users" -Headers @{ Authorization = "Bearer $bobToken" }
Write-Host "status=$($rbac.Status) body=$($rbac.Body)"
if ($rbac.Status -ne 403) { throw "expected 403" }

Write-Host "`n=== 5) Rate limit: 11 GETs as alice via X-API-Key (11th expect 429) ==="
$last = $null
1..11 | ForEach-Object {
    $r = Invoke-Api GET "$base/orders" -Headers @{ "X-API-Key" = "alice-admin-key" }
    $remaining = $r.Headers["X-RateLimit-Remaining"]
    $snippet = if ($r.Body.Length -gt 100) { $r.Body.Substring(0, 100) + "..." } else { $r.Body }
    Write-Host ("request {0,2}: status={1} remaining={2} body={3}" -f $_, $r.Status, $remaining, $snippet)
    $last = $r
}
if ($last.Status -ne 429) { throw "expected 429 on 11th request" }

$client.Dispose()
Write-Host "`nALL DEMO CHECKS PASSED."
