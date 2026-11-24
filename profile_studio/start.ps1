# Customer Profile Studio - builds the UI if needed and starts the server.
# Usage (from anywhere):  powershell -ExecutionPolicy Bypass -File "<path>\profile_studio\start.ps1"
# Then open http://localhost:8600
param([int]$Port = 8600, [switch]$Rebuild)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $here "frontend"
$backend = Join-Path $here "backend"

if ($Rebuild -or -not (Test-Path (Join-Path $frontend "dist\index.html"))) {
    Write-Host "Building the UI..."
    Push-Location $frontend
    if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
    npm run build
    Pop-Location
}

Write-Host "Customer Profile Studio: http://localhost:$Port"
Push-Location $backend
python -m uvicorn app:app --host 127.0.0.1 --port $Port
Pop-Location
