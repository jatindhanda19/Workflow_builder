<#
.SYNOPSIS
    Starts Flowwright on Windows: the API and the web app, each in its own window.

.DESCRIPTION
    API      http://127.0.0.1:8001   (reloads when Python code changes)
    Web app  http://127.0.0.1:5173   (open this in your browser)

    Close the two windows, or press Ctrl+C in each, to stop.

    Usage, from the repository root (after .\scripts\setup.ps1):
        .\scripts\start.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend\react-app"

if (-not (Test-Path $python)) { throw "No .venv found. Run .\scripts\setup.ps1 first." }
if (-not (Test-Path (Join-Path $root ".env"))) { throw "No .env found. Run .\scripts\setup.ps1 first." }
if (-not (Test-Path (Join-Path $frontend "node_modules"))) { throw "Frontend not installed. Run .\scripts\setup.ps1 first." }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm not found. Install Node.js 20+ from https://nodejs.org" }

# The PowerShell running this script, by full path: it does not depend on PATH (which may lack C:\Windows\System32).
$shell = (Get-Process -Id $PID).Path

Start-Process $shell -WorkingDirectory $root -ArgumentList @(
    "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'Flowwright API'; & '$python' -m uvicorn app.main:app --port 8001 --reload"
)
Start-Process $shell -WorkingDirectory $frontend -ArgumentList @(
    "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'Flowwright web'; npm run dev"
)

Write-Host "Flowwright is starting in two new windows:" -ForegroundColor Green
Write-Host "  API      http://127.0.0.1:8001   (health check: http://127.0.0.1:8001/health)"
Write-Host "  Web app  http://127.0.0.1:5173   <- open this"
