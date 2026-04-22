#!/usr/bin/env bash
# 새싹이 AI 비서 — USB 번들 생성 스크립트 (macOS 개발 머신에서 실행)
#
# 사용법:
#   bash scripts/bundle_usb.sh [USB_마운트_경로]
#   예) bash scripts/bundle_usb.sh /Volumes/SAESSAGI
#   USB 경로 생략 시 ~/saessagi-bundle/ 에 생성
#
# 사전 요건:
#   - 이 프로젝트가 ~/saessagi (또는 현재 디렉토리)에 있을 것
#   - assets/models/ 에 AI 모델이 있을 것
#   - ollama 가 설치되어 있고 gemma4:e2b 모델이 당겨져 있을 것
#   - 인터넷 연결 (wheel 다운로드용, 약 1회만 필요)

set -euo pipefail

### ── 경로 설정 ───────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USB_ROOT="${1:-$HOME/saessagi-bundle}"

log()  { echo "▶ $*"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ⚠ $*"; }
err()  { echo "  ✗ $*" >&2; exit 1; }
hr()   { echo "──────────────────────────────────────────────────────────────"; }

hr
log "새싹이 USB 번들 생성"
log "프로젝트: $PROJECT_ROOT"
log "출력:     $USB_ROOT"
hr

cd "$PROJECT_ROOT"

### ── 디렉토리 구조 생성 ──────────────────────────────────────────────────────
log "[1/8] 디렉토리 구조 생성"
mkdir -p \
    "$USB_ROOT/shared/ai-assistant" \
    "$USB_ROOT/shared/models" \
    "$USB_ROOT/macos/wheels/arm64" \
    "$USB_ROOT/macos/wheels/x86_64" \
    "$USB_ROOT/windows/wheels/cpu" \
    "$USB_ROOT/windows/wheels/cuda" \
    "$USB_ROOT/ollama/models" \
    "$USB_ROOT/deploy"
ok "디렉토리 생성 완료"

### ── 소스 코드 복사 ──────────────────────────────────────────────────────────
log "[2/8] 소스 코드 복사"
rsync -a --delete \
    --exclude=".venv/" \
    --exclude=".git/" \
    --exclude="__pycache__/" \
    --exclude="*.pyc" \
    --exclude="data/" \
    --exclude="cache/" \
    --exclude="saessagi-bundle/" \
    "$PROJECT_ROOT/" "$USB_ROOT/shared/ai-assistant/"
ok "소스 코드 복사 완료"

### ── AI 모델 파일 복사 ───────────────────────────────────────────────────────
log "[3/8] AI 모델 파일 복사 (시간이 걸릴 수 있음)"
MODELS_SRC="$PROJECT_ROOT/assets/models"
if [ -d "$MODELS_SRC" ]; then
    rsync -a --info=progress2 "$MODELS_SRC/" "$USB_ROOT/shared/models/"
    ok "AI 모델 복사 완료 ($(du -sh "$USB_ROOT/shared/models" | cut -f1))"
else
    warn "assets/models/ 없음 — 나중에 수동으로 복사하세요"
fi

### ── Ollama 모델 내보내기 ─────────────────────────────────────────────────────
log "[4/8] Ollama 모델 복사"
OLLAMA_SRC="${OLLAMA_MODELS:-$HOME/.ollama/models}"
if [ -d "$OLLAMA_SRC" ]; then
    rsync -a --info=progress2 "$OLLAMA_SRC/" "$USB_ROOT/ollama/models/"
    ok "Ollama 모델 복사 완료 ($(du -sh "$USB_ROOT/ollama/models" | cut -f1))"
else
    warn "~/.ollama/models 없음 — 'ollama pull gemma4:e2b' 후 다시 실행하거나 수동 복사하세요"
fi

### ── 요구사항 파일 생성 ───────────────────────────────────────────────────────
log "[5/8] requirements.txt 생성"
uv export --no-dev --format requirements-txt --no-hashes 2>/dev/null \
    | grep -v "^-e " \
    > "$USB_ROOT/shared/ai-assistant/deploy/requirements.txt"
ok "requirements.txt 생성 완료"

### ── Python 패키지 Wheels 다운로드 ──────────────────────────────────────────
log "[6/8] Python wheels 다운로드 (플랫폼별, 수 분 소요)"

REQ="$USB_ROOT/shared/ai-assistant/deploy/requirements.txt"

# 공통 다운로드 함수
download_wheels() {
    local platform="$1"
    local dest="$2"
    local extra_args="${3:-}"

    log "  다운로드: $platform → $dest"
    pip download \
        --platform "$platform" \
        --python-version "312" \
        --only-binary ":all:" \
        $extra_args \
        -r "$REQ" \
        -d "$dest" \
        --quiet 2>&1 | grep -v "^Collecting\|^  Downloading\|^  Using cached" || true
}

# macOS ARM64
download_wheels "macosx_11_0_arm64" "$USB_ROOT/macos/wheels/arm64"
ok "macOS ARM64 wheels 완료"

# macOS x86_64
download_wheels "macosx_10_9_x86_64" "$USB_ROOT/macos/wheels/x86_64"
ok "macOS x86_64 wheels 완료"

# Windows AMD64 — CPU torch
download_wheels "win_amd64" "$USB_ROOT/windows/wheels/cpu"
ok "Windows CPU wheels 완료"

# Windows AMD64 — CUDA torch (별도 다운로드)
log "  CUDA torch 다운로드 (RTX4090 등 NVIDIA GPU용)"
pip download \
    --platform "win_amd64" \
    --python-version "312" \
    --only-binary ":all:" \
    --index-url "https://download.pytorch.org/whl/cu121" \
    "torch>=2.0.0" \
    -d "$USB_ROOT/windows/wheels/cuda" \
    --quiet 2>/dev/null || warn "CUDA torch 다운로드 실패 (인터넷 연결 확인)"

# MeloTTS wheel (PyPI의 melo 패키지)
log "  MeloTTS wheel 다운로드"
for dest in "$USB_ROOT/macos/wheels/arm64" "$USB_ROOT/macos/wheels/x86_64" "$USB_ROOT/windows/wheels/cpu" "$USB_ROOT/windows/wheels/cuda"; do
    pip download melo --only-binary ":all:" -d "$dest" --quiet 2>/dev/null || true
done

ok "Wheels 다운로드 완료"

### ── Ollama 바이너리 다운로드 안내 ──────────────────────────────────────────
log "[7/8] Ollama 설치 파일 확인"
OLLAMA_MAC="$USB_ROOT/ollama/Ollama-macos.dmg"
OLLAMA_WIN="$USB_ROOT/ollama/OllamaSetup.exe"

if [ ! -f "$OLLAMA_MAC" ]; then
    warn "macOS용 Ollama DMG 없음"
    warn "  수동 다운로드 후 아래 경로에 복사:"
    warn "  → $OLLAMA_MAC"
    warn "  URL: https://ollama.com/download/mac"
fi

if [ ! -f "$OLLAMA_WIN" ]; then
    warn "Windows용 Ollama 설치 파일 없음"
    warn "  수동 다운로드 후 아래 경로에 복사:"
    warn "  → $OLLAMA_WIN"
    warn "  URL: https://ollama.com/download/windows"
fi

### ── Python 설치 파일 안내 ───────────────────────────────────────────────────
PY_MAC="$USB_ROOT/macos/python-3.12-macos.pkg"
PY_WIN="$USB_ROOT/windows/python-3.12-amd64.exe"

if [ ! -f "$PY_MAC" ]; then
    warn "macOS Python 설치 파일 없음"
    warn "  → $PY_MAC"
    warn "  URL: https://www.python.org/downloads/ (macOS 64-bit)"
fi
if [ ! -f "$PY_WIN" ]; then
    warn "Windows Python 설치 파일 없음"
    warn "  → $PY_WIN"
    warn "  URL: https://www.python.org/downloads/ (Windows installer 64-bit)"
fi

### ── README 생성 ────────────────────────────────────────────────────────────
log "[8/8] README 생성"
cat > "$USB_ROOT/README.txt" << 'README'
새싹이 AI 비서 — USB 배포 패키지
=================================

■ Windows 설치
  deploy\install.bat 더블클릭 (또는 관리자 권한으로 실행)

■ macOS 설치
  터미널에서: bash deploy/install.sh

■ 서버 실행
  Windows: deploy\start.bat 더블클릭
  macOS:   bash deploy/start.sh

■ 접속
  브라우저에서 http://127.0.0.1:12393 열기

■ 주의사항
  - 서버 실행 전 Ollama가 먼저 켜져 있어야 합니다
  - 최초 실행 시 모델 로딩에 수십 초 소요됩니다
  - NVIDIA GPU 탑재 PC는 자동으로 CUDA 가속됩니다

■ 문의: IT 담당자에게 연락하세요
README

ok "README 생성 완료"

### ── 최종 요약 ───────────────────────────────────────────────────────────────
hr
echo ""
echo "  USB 번들 생성 완료!"
echo "  경로: $USB_ROOT"
echo "  크기: $(du -sh "$USB_ROOT" | cut -f1)"
echo ""
echo "  ─── 수동 추가 필요 항목 (없는 경우) ────────────────────"
[ ! -f "$OLLAMA_MAC" ] && echo "  • macOS Ollama DMG  → $OLLAMA_MAC"
[ ! -f "$OLLAMA_WIN" ] && echo "  • Windows OllamaSetup.exe → $OLLAMA_WIN"
[ ! -f "$PY_MAC" ]     && echo "  • macOS Python PKG  → $PY_MAC"
[ ! -f "$PY_WIN" ]     && echo "  • Windows Python EXE → $PY_WIN"
echo ""
echo "  USB에 복사 후 대상 PC에서 deploy/install.bat (또는 install.sh) 실행"
hr
