# MLR Web UI launcher: install/build frontend when needed, start backend, open browser.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$fe = Join-Path $root 'webui\frontend'
if (-not (Test-Path (Join-Path $fe 'node_modules'))) {
    Write-Host '[webui] installing frontend dependencies ...'
    Push-Location $fe
    npm install --no-audit --no-fund
    Pop-Location
}
if (-not (Test-Path (Join-Path $fe 'dist\index.html'))) {
    Write-Host '[webui] building frontend ...'
    Push-Location $fe
    npm run build
    Pop-Location
}
if (-not (Test-Path (Join-Path $root 'webui\data\scan_full.json'))) {
    Write-Host '[webui] generating scan snapshot ...'
    python webui\export_scan.py
}

$py = (Get-Command python).Source
Write-Host '[webui] starting backend at http://127.0.0.1:8200 ...'
Start-Process -FilePath $py `
    -ArgumentList '-m', 'uvicorn', 'webui.backend.main:app', '--host', '127.0.0.1', '--port', '8200' `
    -WorkingDirectory $root
Start-Sleep -Seconds 3
Start-Process 'http://127.0.0.1:8200'
Write-Host ''
Write-Host '  MLR Web UI  ->  http://127.0.0.1:8200'
Write-Host '  Backend runs in a separate window; close it to stop.'
Write-Host ''
