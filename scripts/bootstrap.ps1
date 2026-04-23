# bootstrap.ps1 — Initial environment setup
# Usage: run via scripts\bootstrap.cmd (recommended) or:
#        powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

$ErrorActionPreference = "Stop"

# UTF-8 output (prevents garbled text in CMD)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Corporate SSL proxy: trust Windows certificate store
$env:UV_NATIVE_TLS = "1"

# Ensure we are in project root (in case script is run directly from scripts\)
if (Test-Path "$PSScriptRoot\..\pyproject.toml") {
    Set-Location "$PSScriptRoot\.."
}

Write-Host ""
Write-Host "=== AI Assistant Bootstrap ===" -ForegroundColor Cyan
Write-Host ""

# ── 0. uv ──────────────────────────────────────────────────────────────────
Write-Host "[0/6] Checking uv package manager..." -ForegroundColor Cyan
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  Installing uv (official installer)..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "  uv installed. Please restart your terminal and run this script again." -ForegroundColor Red
        exit 1
    }
    Write-Host "  -> uv installed" -ForegroundColor Green
} else {
    Write-Host "  uv already installed -- skipping" -ForegroundColor Gray
}
Write-Host ""

# ── 1. upstream clone ───────────────────────────────────────────────────────
if (-not (Test-Path "upstream\Open-LLM-VTuber\.git")) {
    Write-Host "[1/6] Cloning Open-LLM-VTuber..." -ForegroundColor Cyan
    if (Test-Path "upstream\Open-LLM-VTuber") {
        Remove-Item "upstream\Open-LLM-VTuber" -Recurse -Force
    }
    git clone https://github.com/Open-LLM-VTuber/Open-LLM-VTuber.git upstream\Open-LLM-VTuber
    Write-Host "  -> cloned to upstream\Open-LLM-VTuber" -ForegroundColor Green
} else {
    Write-Host "[1/6] upstream\Open-LLM-VTuber already exists -- skipping" -ForegroundColor Gray
}
Write-Host ""

# ── 2. Ollama / Gemma4 ─────────────────────────────────────────────────────
Write-Host "[2/6] Checking Ollama models..." -ForegroundColor Cyan
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $models = (ollama list 2>&1 | Out-String)
    if ($models -notmatch "gemma4") {
        Write-Host "  Downloading gemma4:e4b (~9 GB, this will take a while)..." -ForegroundColor Yellow
        ollama pull gemma4:e4b
        Write-Host "  -> gemma4:e4b done" -ForegroundColor Green
    } else {
        Write-Host "  gemma4:e4b already exists -- skipping" -ForegroundColor Gray
    }
} else {
    Write-Host "  Ollama not installed. Install it manually:" -ForegroundColor Yellow
    Write-Host "    https://ollama.com/download/windows" -ForegroundColor Yellow
    Write-Host "  Then run: ollama pull gemma4:e4b" -ForegroundColor Yellow
}
Write-Host ""

# ── 3. Python venv ─────────────────────────────────────────────────────────
Write-Host "[3/6] Creating Python virtual environment..." -ForegroundColor Cyan
if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "  .venv already exists -- skipping" -ForegroundColor Gray
} else {
    # Remove broken venv if present
    if (Test-Path ".venv") { Remove-Item ".venv" -Recurse -Force }

    # Prefer system Python to avoid downloading from GitHub (SSL proxy issue)
    $sysPython = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if ($sysPython -and (python --version 2>&1) -match "3\.(1[1-9]|[2-9]\d)") {
        Write-Host "  Using system Python: $sysPython" -ForegroundColor Gray
        uv venv --python $sysPython
    } else {
        Write-Host "  Downloading Python 3.11 via uv (needs internet)..." -ForegroundColor Yellow
        uv venv --python 3.11
    }
    Write-Host "  -> .venv created" -ForegroundColor Green
}
Write-Host ""

# ── 4. Python packages ─────────────────────────────────────────────────────
Write-Host "[4/6] Installing Python packages (uv sync)..." -ForegroundColor Cyan
if (Test-Path "pyproject.toml") {
    uv sync
    Write-Host "  -> packages installed" -ForegroundColor Green
} else {
    Write-Host "  pyproject.toml not found -- installing dev tools only" -ForegroundColor Yellow
    uv pip install ruff mypy pytest pytest-cov
    @"
[project]
name = "ai-assistant"
version = "0.0.1"
description = "Offline multimodal AI assistant for corporate intranet"
requires-python = ">=3.11"
dependencies = []

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
"@ | Set-Content -Path "pyproject.toml" -Encoding UTF8
    Write-Host "  -> pyproject.toml created (starter kit)" -ForegroundColor Green
}
if (-not (Test-Path "src"))   { New-Item -ItemType Directory -Path "src"   | Out-Null }
if (-not (Test-Path "tests")) { New-Item -ItemType Directory -Path "tests" | Out-Null }
Write-Host ""

# ── 5. BGE-M3 embedding model ──────────────────────────────────────────────
Write-Host "[5/6] BGE-M3 embedding model (~1.5 GB)..." -ForegroundColor Cyan
$bgeDir = "assets\models\bge-m3"
if (-not (Test-Path "$bgeDir\config.json")) {
    New-Item -ItemType Directory -Path $bgeDir -Force | Out-Null
    Write-Host "  Installing huggingface_hub into venv..." -ForegroundColor Yellow
    uv pip install --quiet huggingface_hub
    Write-Host "  Downloading BAAI/bge-m3 -> $bgeDir (this will take a while)..." -ForegroundColor Yellow
    $bgeDirFwd = $bgeDir -replace '\\', '/'
    $script = "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-m3', local_dir='$bgeDirFwd', local_dir_use_symlinks=False)"
    .\.venv\Scripts\python.exe -c $script
    Write-Host "  -> BGE-M3 downloaded" -ForegroundColor Green
} else {
    Write-Host "  BGE-M3 already exists ($bgeDir) -- skipping" -ForegroundColor Gray
}
Write-Host ""

# ── 6. git init (starter kit only) ────────────────────────────────────────
Write-Host "[6/6] Checking git..." -ForegroundColor Cyan
if (-not (Test-Path ".git")) {
    git init | Out-Null
    git add README.md REQUIREMENTS.md CLAUDE.md PROJECT_PLAN.md .gitignore .claude/ prompts/ docs/ scripts/ specs/.gitkeep reviews/.gitkeep upstream/.gitkeep assets/ pyproject.toml | Out-Null
    git commit -m "chore: initial starter kit" | Out-Null
    Write-Host "  -> git repository initialized" -ForegroundColor Green
} else {
    Write-Host "  .git already exists -- skipping" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Bootstrap complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

if (Test-Path "src\app\main.py") {
    Write-Host "To start the server:" -ForegroundColor Cyan
    Write-Host '  $env:PYTHONPATH = "src;upstream/Open-LLM-VTuber/src;upstream/Open-LLM-VTuber"'
    Write-Host '  uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393'
    Write-Host ""
    Write-Host "Open http://127.0.0.1:12393 in your browser." -ForegroundColor Green
} else {
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Run Claude Code in this folder: claude"
    Write-Host "  2. Paste the prompt from prompts/00_kickoff.md"
}
Write-Host ""
