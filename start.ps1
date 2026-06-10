# ============================================================
#  DocExtract Pipeline — Windows Launcher
#  Usage: Right-click → "Run with PowerShell"
#         OR: powershell -ExecutionPolicy Bypass -File start.ps1
# ============================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  ⚡ DocExtract Pipeline  ·  MinerU + Kimi K2.5" -ForegroundColor Cyan
Write-Host "  ════════════════════════════════════════════════" -ForegroundColor DarkGray
Write-Host ""

# ── Check .env exists ─────────────────────────────────────
if (-not (Test-Path "$ROOT\.env")) {
    Write-Host "  ❌ .env not found! Create it with your keys." -ForegroundColor Red
    exit 1
}

# ── Install deps if needed ────────────────────────────────
Write-Host "  📦 Checking dependencies..." -ForegroundColor Yellow
pip install -q -r "$ROOT\requirements.txt"

# ── Check if backend is already up ───────────────────────
$backendUp = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $backendUp = $true }
} catch {}

if ($backendUp) {
    Write-Host "  ✅ Backend already running on :8000" -ForegroundColor Green
} else {
    Write-Host "  🚀 Starting FastAPI backend on port 8000..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "cd '$ROOT'; python -m uvicorn mineru_server:app --host 127.0.0.1 --port 8000"
    ) -WindowStyle Normal
    Write-Host "  ⏳ Waiting for backend to start..." -ForegroundColor Yellow
    Start-Sleep -Seconds 4

    # Verify
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5 -UseBasicParsing
        $health = $r.Content | ConvertFrom-Json
        $keys   = $health.kimi_keys
        $model  = $health.kimi_model
        Write-Host "  ✅ Backend ready  ·  Kimi keys: $keys  ·  Model: $model" -ForegroundColor Green
    } catch {
        Write-Host "  ⚠  Backend may still be starting..." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  🌐 Starting Streamlit UI on port 8501..." -ForegroundColor Cyan
Write-Host "  → Open:  http://localhost:8501" -ForegroundColor White
Write-Host "  → API:   http://localhost:8000/docs" -ForegroundColor White
Write-Host ""

Set-Location $ROOT
python -m streamlit run streamlit_app.py `
    --server.port 8501 `
    --server.address 127.0.0.1 `
    --server.headless false `
    --theme.base dark
