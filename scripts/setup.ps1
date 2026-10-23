<#
.SYNOPSIS
    One-time setup for Flowwright on Windows. Safe to run again: finished steps are skipped.

.DESCRIPTION
    1. Creates the Python virtual environment (.venv) and installs the backend and test dependencies.
    2. Creates .env from .env.example, with a freshly generated AUTH_SECRET_KEY.
    3. Installs the React app's dependencies.

    Usage, from the repository root:
        .\scripts\setup.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Step([string]$Description, [scriptblock]$Command) {
    Write-Host "==> $Description" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE) { throw "Failed: $Description (exit code $LASTEXITCODE)" }
}

# -- Python backend ----------------------------------------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found. Install Python 3.11 or newer from https://www.python.org/downloads/"
}
python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if ($LASTEXITCODE) { throw "Python 3.11 or newer is required (found: $(python --version))." }

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Invoke-Step "Creating the virtual environment (.venv)" { python -m venv .venv }
}
Invoke-Step "Installing backend dependencies" { & $python -m pip install --disable-pip-version-check -q -r requirements-dev.txt }

# -- Configuration -----------------------------------------------------------------------------------------
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "==> Creating .env with a new AUTH_SECRET_KEY" -ForegroundColor Cyan
    $secret = & $python -c "import secrets; print(secrets.token_urlsafe(48))"
    $lines = (Get-Content (Join-Path $root ".env.example")) -replace '^AUTH_SECRET_KEY=$', "AUTH_SECRET_KEY=$secret"
    [System.IO.File]::WriteAllLines($envFile, $lines)  # UTF-8 without a byte-order mark
} else {
    Write-Host "==> .env already exists: left unchanged" -ForegroundColor DarkGray
}

# -- React frontend ----------------------------------------------------------------------------------------
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm not found. Install Node.js 20 or newer (LTS) from https://nodejs.org, restart the terminal, run again."
}
Invoke-Step "Installing frontend dependencies" { npm install --prefix frontend/react-app --no-fund --no-audit }

# -- Done --------------------------------------------------------------------------------------------------
$groqKey = (Select-String -Path $envFile -Pattern '^GROQ_API_KEY=(.+)$' -ErrorAction SilentlyContinue)
Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
if (-not $groqKey) {
    Write-Host "Next: put your Groq API key in .env (GROQ_API_KEY=...). Get one at https://console.groq.com/keys" -ForegroundColor Yellow
}
Write-Host "Then start the app with:  .\scripts\start.ps1"
