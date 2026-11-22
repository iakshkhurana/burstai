# Start (or restart) the BurstAI checking engine on this PC and connect it to the public site.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_engine.ps1
#
# What it does:
#   1. stops any engine / tunnel started earlier,
#   2. starts the engine on 127.0.0.1:8000 (never exposed to the network directly),
#   3. opens a Cloudflare quick tunnel to it,
#   4. tells the site (BURSTAI_SITE_URL) the tunnel's new address, authenticated with BURSTAI_ENGINE_TOKEN.
# Step 4 means the tunnel URL can change on every restart without redeploying the Worker.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- read .env
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw ".env not found. Copy .env.example to .env and set BURSTAI_ENGINE_TOKEN and BURSTAI_SITE_URL." }
$cfg = @{}
Get-Content $envFile | ForEach-Object { if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$') { $cfg[$Matches[1]] = $Matches[2] } }
$token = $cfg["BURSTAI_ENGINE_TOKEN"]; $site = ($cfg["BURSTAI_SITE_URL"] + "").TrimEnd("/")
if (-not $token) { throw "BURSTAI_ENGINE_TOKEN is empty in .env" }
if (-not $site) { throw "BURSTAI_SITE_URL is empty in .env (for example https://burstai.online)" }

$cloudflared = @("C:\Program Files (x86)\cloudflared\cloudflared.exe", "C:\Program Files\cloudflared\cloudflared.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cloudflared) { $cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source }
if (-not $cloudflared) { throw "cloudflared is not installed. Run: winget install Cloudflare.cloudflared" }

# --- stop previous instances
$old = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
if ($old) { Stop-Process -Id $old -Force -Confirm:$false }
Get-CimInstance Win32_Process -Filter "name='cloudflared.exe'" | Where-Object { $_.CommandLine -like "*localhost:8000*" -or $_.CommandLine -like "*127.0.0.1:8000*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
Start-Sleep -Seconds 1

# --- start engine + tunnel (hidden, detached from this window)
Remove-Item "$root\tunnel.err.log", "$root\tunnel.log" -ErrorAction SilentlyContinue
Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
  -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput "$root\server.log" -RedirectStandardError "$root\server.err.log" | Out-Null
Start-Process -FilePath $cloudflared -ArgumentList "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate" `
  -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput "$root\tunnel.log" -RedirectStandardError "$root\tunnel.err.log" | Out-Null

# --- wait for the tunnel address
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
  Start-Sleep -Seconds 1
  $m = Select-String -Path "$root\tunnel.err.log", "$root\tunnel.log" -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($m) { $url = $m.Matches[0].Value }
}
if (-not $url) { throw "The tunnel did not come up. See tunnel.err.log" }
Write-Host "Tunnel: $url"

# --- wait until the engine answers through the tunnel, then register it with the site
$ok = $false
for ($i = 0; $i -lt 40 -and -not $ok; $i++) {
  try { $h = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 10; if ($h.version) { $ok = $true } } catch { Start-Sleep -Seconds 2 }
}
if (-not $ok) { throw "Engine did not answer through the tunnel. See server.err.log" }
$body = @{ url = $url } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri "$site/api/engine/register" -Headers @{ "x-engine-token" = $token } -ContentType "application/json" -Body $body -TimeoutSec 30
Write-Host "Registered with $site -> $($r.url)"
Write-Host "Engine is live. Language models finish loading in the background (about a minute)."
