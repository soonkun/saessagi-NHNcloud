# bootstrap.ps1 — 초기 환경 설정
# 사용: PowerShell에서 .\scripts\bootstrap.ps1

$ErrorActionPreference = "Stop"

Write-Host "`n=== AI 비서 프로젝트 부트스트랩 ===" -ForegroundColor Cyan
Write-Host ""

# 0. uv 설치 확인 (pip 불필요 — 공식 PowerShell 인스톨러 사용)
Write-Host "[0/7] uv 패키지 매니저 확인..." -ForegroundColor Cyan
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  uv 설치 중 (공식 인스톨러)..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # PATH 갱신 (현재 세션에 반영)
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "  uv 설치 후 터미널을 재시작하고 이 스크립트를 다시 실행하세요." -ForegroundColor Red
        exit 1
    }
    Write-Host "  → uv 설치 완료" -ForegroundColor Green
} else {
    Write-Host "  uv 이미 설치됨 — 건너뜀" -ForegroundColor Gray
}
Write-Host ""

# 1. Open-LLM-VTuber clone
if (-not (Test-Path "upstream\Open-LLM-VTuber\.git")) {
    Write-Host "[1/7] Open-LLM-VTuber clone 중..." -ForegroundColor Cyan
    if (Test-Path "upstream\Open-LLM-VTuber") {
        Remove-Item "upstream\Open-LLM-VTuber" -Recurse -Force
    }
    git clone https://github.com/Open-LLM-VTuber/Open-LLM-VTuber.git upstream\Open-LLM-VTuber
    Write-Host "  → upstream\Open-LLM-VTuber 에 clone 완료" -ForegroundColor Green
} else {
    Write-Host "[1/7] upstream\Open-LLM-VTuber 이미 존재 — 건너뜀" -ForegroundColor Gray
}
Write-Host ""

# 2. Gemma 4 E4B 모델 풀 (LLM — Ollama 경유)
Write-Host "[2/7] Ollama 모델 확인·다운로드..." -ForegroundColor Cyan
$models = (ollama list 2>&1 | Out-String)
if ($models -notmatch "gemma4") {
    Write-Host "  gemma4:e4b 다운로드 중 (약 9GB, 시간이 걸립니다)..." -ForegroundColor Yellow
    ollama pull gemma4:e4b
    Write-Host "  → gemma4:e4b 완료" -ForegroundColor Green
} else {
    Write-Host "  gemma4:e4b 이미 존재 — 건너뜀" -ForegroundColor Gray
}
Write-Host ""

# 3. BGE-M3 임베딩 모델 다운로드 (HuggingFace — 문서 검색용, 약 1.5GB)
Write-Host "[3/7] BGE-M3 임베딩 모델 다운로드 (약 1.5GB)..." -ForegroundColor Cyan
$bgeDir = "assets\models\bge-m3"
if (-not (Test-Path "$bgeDir\config.json")) {
    if (-not (Test-Path $bgeDir)) {
        New-Item -ItemType Directory -Path $bgeDir -Force | Out-Null
    }
    # huggingface-cli 확인 — 없으면 uv tool로 설치 (pip 불필요)
    if (-not (Get-Command huggingface-cli -ErrorAction SilentlyContinue)) {
        Write-Host "  huggingface-cli 설치 중 (uv tool install)..." -ForegroundColor Yellow
        uv tool install huggingface_hub
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    }
    Write-Host "  BAAI/bge-m3 → $bgeDir" -ForegroundColor Yellow
    huggingface-cli download BAAI/bge-m3 `
        --local-dir $bgeDir `
        --local-dir-use-symlinks False
    Write-Host "  → BGE-M3 다운로드 완료" -ForegroundColor Green
} else {
    Write-Host "  BGE-M3 이미 존재 ($bgeDir) — 건너뜀" -ForegroundColor Gray
}
Write-Host ""

# 4. Python 가상환경 생성
Write-Host "[4/7] Python 가상환경 생성..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    uv venv --python 3.11
    Write-Host "  → .venv 생성 완료" -ForegroundColor Green
} else {
    Write-Host "  .venv 이미 존재 — 건너뜀" -ForegroundColor Gray
}
Write-Host ""

# 5. Python 패키지 설치
Write-Host "[5/7] Python 패키지 설치 (uv sync)..." -ForegroundColor Cyan
if (Test-Path "pyproject.toml") {
    uv sync
    Write-Host "  → 패키지 설치 완료" -ForegroundColor Green
} else {
    # 스타터킷 환경: pyproject.toml 없음 → 개발 도구만 설치
    Write-Host "  pyproject.toml 없음 — 개발 도구만 설치" -ForegroundColor Yellow
    .\.venv\Scripts\Activate.ps1
    uv pip install ruff mypy pytest pytest-cov
    deactivate
    # pyproject.toml 초기화 (스타터킷 전용)
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
    Write-Host "  → pyproject.toml 생성 (스타터킷)" -ForegroundColor Green
}
if (-not (Test-Path "src")) { New-Item -ItemType Directory -Path "src" | Out-Null }
if (-not (Test-Path "tests")) { New-Item -ItemType Directory -Path "tests" | Out-Null }
Write-Host ""

# 6. git 초기화 (스타터킷 전용 — 이미 .git이 있으면 건너뜀)
Write-Host "[6/7] git 확인..." -ForegroundColor Cyan
if (-not (Test-Path ".git")) {
    git init | Out-Null
    git add README.md REQUIREMENTS.md CLAUDE.md PROJECT_PLAN.md .gitignore .claude/ prompts/ docs/ scripts/ specs/.gitkeep reviews/.gitkeep upstream/.gitkeep assets/ pyproject.toml | Out-Null
    git commit -m "chore: initial starter kit" | Out-Null
    Write-Host "  → git 저장소 초기화·최초 커밋" -ForegroundColor Green
} else {
    Write-Host "  .git 이미 존재 — 건너뜀" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "부트스트랩 완료" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

if (Test-Path "src\app\main.py") {
    # 완성된 repo를 클론한 경우
    Write-Host "서버 실행 방법:" -ForegroundColor Cyan
    Write-Host '  $env:PYTHONPATH = "src;upstream/Open-LLM-VTuber/src;upstream/Open-LLM-VTuber"'
    Write-Host "  uv run uvicorn `"app.main:create_app`" --factory --host 127.0.0.1 --port 12393"
    Write-Host ""
    Write-Host "브라우저에서 http://127.0.0.1:12393 접속하면 새싹이가 반겨줍니다." -ForegroundColor Green
} else {
    # 스타터킷 — Claude Code로 개발 시작
    Write-Host "다음 단계:" -ForegroundColor Cyan
    Write-Host "  1. 이 폴더에서 Claude Code 실행: claude"
    Write-Host "  2. prompts/00_kickoff.md 의 프롬프트를 복사해 Claude Code에 붙여넣기"
}
Write-Host ""
