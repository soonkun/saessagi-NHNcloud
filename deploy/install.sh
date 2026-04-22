#!/usr/bin/env bash
# 새싹이 AI 비서 — macOS 설치 스크립트
# USB에서 실행: bash /Volumes/SAESSAGI/deploy/install.sh
set -euo pipefail

### ── 설정 ────────────────────────────────────────────────────────────────────
INSTALL_DIR="${SAESSAGI_INSTALL_DIR:-$HOME/saessagi}"
PYTHON_MIN="3.12"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USB_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"  # deploy/../ = USB 루트

log()  { echo "[install] $*"; }
ok()   { echo "[install] ✓ $*"; }
err()  { echo "[install] ✗ $*" >&2; exit 1; }

### ── 1. 시스템 확인 ──────────────────────────────────────────────────────────
log "=== 새싹이 AI 비서 설치 (macOS) ==="
log "설치 경로: $INSTALL_DIR"

ARCH="$(uname -m)"
log "플랫폼: macOS $ARCH"

### ── 2. Python 3.12 확인 ─────────────────────────────────────────────────────
PYTHON=""
for cmd in python3.12 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
        if [[ "$VER" == "3.12" || "$VER" > "3.12" ]]; then
            PYTHON="$cmd"
            ok "Python $VER 발견: $(command -v $cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    log "Python 3.12가 없습니다. USB에서 설치합니다..."
    PKG="$USB_ROOT/macos/python-3.12-macos.pkg"
    if [ -f "$PKG" ]; then
        sudo installer -pkg "$PKG" -target /
        PYTHON=python3.12
    else
        err "USB에 Python 설치 파일이 없습니다: $PKG\n공식 사이트(python.org)에서 Python 3.12를 설치 후 다시 실행하세요."
    fi
fi

### ── 3. Ollama 확인 / 설치 ──────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    log "Ollama가 없습니다. USB에서 설치합니다..."
    DMG="$USB_ROOT/ollama/Ollama-macos.dmg"
    if [ -f "$DMG" ]; then
        hdiutil attach "$DMG" -quiet
        MOUNTED=$(ls /Volumes | grep -i ollama | head -1)
        cp -r "/Volumes/$MOUNTED/Ollama.app" /Applications/
        hdiutil detach "/Volumes/$MOUNTED" -quiet
        # PATH에 ollama 추가
        sudo ln -sf /Applications/Ollama.app/Contents/Resources/ollama /usr/local/bin/ollama
        ok "Ollama 설치 완료"
    else
        log "경고: USB에 Ollama가 없습니다. 나중에 수동 설치 필요."
    fi
else
    ok "Ollama 이미 설치됨: $(ollama --version 2>/dev/null || echo '버전 불명')"
fi

### ── 4. 소스 코드 복사 ───────────────────────────────────────────────────────
log "소스 코드 복사: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude=".venv" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".git" \
    --exclude="data/" \
    --exclude="cache/" \
    "$USB_ROOT/shared/ai-assistant/" "$INSTALL_DIR/"
ok "소스 코드 복사 완료"

### ── 5. 모델 파일 복사 ───────────────────────────────────────────────────────
MODELS_SRC="$USB_ROOT/shared/models"
if [ -d "$MODELS_SRC" ]; then
    log "AI 모델 복사 중... (수 분 소요)"
    mkdir -p "$INSTALL_DIR/assets/models"
    rsync -a --ignore-existing "$MODELS_SRC/" "$INSTALL_DIR/assets/models/"
    ok "모델 복사 완료"
else
    log "경고: USB에 모델 파일이 없습니다 ($MODELS_SRC)"
fi

### ── 6. 가상환경 생성 ───────────────────────────────────────────────────────
cd "$INSTALL_DIR"
log "가상환경 생성 중..."
"$PYTHON" -m venv .venv
ok "가상환경 생성 완료"

PIP=".venv/bin/pip"
PYEXE=".venv/bin/python"

$PIP install --upgrade pip --quiet

### ── 7. 패키지 설치 ─────────────────────────────────────────────────────────
log "패키지 설치 중... (수 분 소요)"

# 로컬 wheels 디렉토리 선택 (arm64 우선, x86_64 폴백)
if [ "$ARCH" = "arm64" ]; then
    WHEELS_DIR="$USB_ROOT/macos/wheels/arm64"
else
    WHEELS_DIR="$USB_ROOT/macos/wheels/x86_64"
fi

FIND_LINKS=""
if [ -d "$WHEELS_DIR" ]; then
    FIND_LINKS="--find-links $WHEELS_DIR"
fi

# 메인 패키지 설치 (로컬 우선, 없으면 PyPI)
$PIP install $FIND_LINKS \
    --requirement deploy/requirements.txt \
    --quiet 2>&1 | grep -E "error|Error|WARNING" || true

ok "패키지 설치 완료"

### ── 8. MeloTTS 설치 (PyPI에 없음 → 소스 또는 로컬) ────────────────────────
if ! $PYEXE -c "import melo" 2>/dev/null; then
    log "MeloTTS 설치 중..."
    MELO_WHL=$(ls "$USB_ROOT/macos/wheels/"melo*.whl 2>/dev/null | head -1)
    if [ -n "$MELO_WHL" ]; then
        $PIP install --no-deps "$MELO_WHL" --quiet
    else
        $PIP install melo --no-deps --quiet
    fi
    ok "MeloTTS 설치 완료"
fi

### ── 9. post_install (mecab_shim + MeCab Tagger 패치) ───────────────────────
log "후처리 적용 중..."
$PYEXE deploy/post_install.py
ok "후처리 완료"

### ── 10. Ollama 모델 로드 ────────────────────────────────────────────────────
OLLAMA_MODELS_SRC="$USB_ROOT/ollama/models"
if [ -d "$OLLAMA_MODELS_SRC" ] && command -v ollama &>/dev/null; then
    log "Ollama 모델 복사 중..."
    OLLAMA_MODEL_DIR="${OLLAMA_MODELS:-$HOME/.ollama/models}"
    mkdir -p "$OLLAMA_MODEL_DIR"
    rsync -a --ignore-existing "$OLLAMA_MODELS_SRC/" "$OLLAMA_MODEL_DIR/"
    ok "Ollama 모델 복사 완료"
else
    log "경고: Ollama 모델이 없습니다. 'ollama pull gemma4:e2b'를 나중에 실행하세요."
fi

### ── 11. 실행 스크립트 권한 ─────────────────────────────────────────────────
chmod +x "$INSTALL_DIR/deploy/start.sh"

### ── 완료 ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   새싹이 AI 비서 설치 완료               ║"
echo "╠══════════════════════════════════════════╣"
echo "║  설치 경로: $INSTALL_DIR"
echo "║  실행 방법: $INSTALL_DIR/deploy/start.sh"
echo "╚══════════════════════════════════════════╝"
