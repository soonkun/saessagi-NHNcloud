#!/usr/bin/env bash
# scripts/bundle_deps.sh
#
# 오프라인 번들 빌드 스크립트.
#
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# 주의: 이 스크립트는 빌드 머신(인터넷 연결 O)에서만 실행한다.
#       사내 PC (오프라인 배포 대상)에서는 절대 실행하지 말 것.
#       모델 파일과 wheel 파일은 assets/ 및 wheels/ 디렉토리에 저장되며,
#       이후 오프라인 설치 머신으로 복사한다.
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
#
# 사용법 (빌드 머신):
#   bash scripts/bundle_deps.sh
#
# 전제조건:
#   - pip, huggingface-cli (huggingface_hub) 설치됨
#   - 인터넷 연결 가능 상태

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

WHEELS_DIR="${PROJECT_ROOT}/assets/wheels"
MODELS_DIR="${PROJECT_ROOT}/assets/models"
NPM_CACHE_DIR="${PROJECT_ROOT}/assets/npm_cache"  # M_12 §12.2 frontend 오프라인 번들

mkdir -p "${WHEELS_DIR}"
mkdir -p "${MODELS_DIR}"
mkdir -p "${NPM_CACHE_DIR}"

echo "=== [bundle_deps.sh] Python 패키지 wheel 다운로드 ==="
echo "  대상 디렉토리: ${WHEELS_DIR}"

# M_02 ASREngine 의존성
pip download \
    "faster-whisper>=1.0,<2" \
    "ctranslate2>=4.4,<5" \
    --dest "${WHEELS_DIR}" \
    --no-deps

# 위에서 --no-deps를 썼으므로 전이 의존성도 별도 다운로드
pip download \
    "faster-whisper>=1.0,<2" \
    "ctranslate2>=4.4,<5" \
    --dest "${WHEELS_DIR}"

echo ""
echo "=== [bundle_deps.sh] Whisper 모델 아티팩트 다운로드 ==="
echo "  빌드 머신에서만 실행 — 사내 PC에서 실행 금지."

# RECOMMENDED 프로파일 모델: Whisper large-v3 int8
echo "  [1/2] Systran/faster-whisper-large-v3 → ${MODELS_DIR}/whisper-large-v3-int8"
huggingface-cli download Systran/faster-whisper-large-v3 \
    --local-dir "${MODELS_DIR}/whisper-large-v3-int8" \
    --local-dir-use-symlinks False

# MIN 프로파일 모델: Whisper medium int8
echo "  [2/2] Systran/faster-whisper-medium → ${MODELS_DIR}/whisper-medium-int8"
huggingface-cli download Systran/faster-whisper-medium \
    --local-dir "${MODELS_DIR}/whisper-medium-int8" \
    --local-dir-use-symlinks False

echo ""
echo "=== [bundle_deps.sh] M_04 TTSEngine 의존성 ==="
echo "  주의: melo 패키지명은 PyPI 확인 후 아래 주석 해제 필요"

# M_04 TTS 의존성
pip download \
    "soundfile>=0.12,<1" \
    "python-multipart>=0.0.9" \
    --dest "${WHEELS_DIR}"

# Coqui TTS (XTTS v2) — CPML 라이선스 주의, 법무 승인 후 활성화 (CR-02 참조)
# pip download "TTS>=0.22,<1" --dest "${WHEELS_DIR}"

# MeloTTS — PyPI 미등록. 설치 방법 확정 후 활성화 (docs/CHANGE_REQUESTS.md CR-01 참조)
# git clone https://github.com/myshell-ai/MeloTTS.git /tmp/MeloTTS
# pip wheel /tmp/MeloTTS --wheel-dir "${WHEELS_DIR}" --no-deps

echo ""
echo "=== [bundle_deps.sh] M_05b ToolRouter 의존성 ==="

# M_05b ToolRouter — JSON Schema 검증, Windows 화면 캡처, PNG 인코딩
pip download \
    "jsonschema>=4.21,<5" \
    "mss>=9.0,<10" \
    "Pillow>=10.2,<12" \
    --dest "${WHEELS_DIR}"

echo ""
echo "=== [bundle_deps.sh] M_04 TTS 모델 아티팩트 다운로드 ==="
echo "  빌드 머신에서만 실행 — 사내 PC에서 실행 금지."

echo "  [1/2] MeloTTS 한국어 모델 → ${MODELS_DIR}/melotts-ko"
# CR-01 확정 후 활성화
# huggingface-cli download myshell-ai/MeloTTS-Korean \
#     --local-dir "${MODELS_DIR}/melotts-ko" \
#     --local-dir-use-symlinks False

echo "  [2/2] XTTS v2 모델 (CR-02 법무 승인 후 활성화) → ${MODELS_DIR}/xtts_v2"
# huggingface-cli download coqui/XTTS-v2 \
#     --local-dir "${MODELS_DIR}/xtts_v2" \
#     --local-dir-use-symlinks False

echo ""
echo "=== [bundle_deps.sh] M_07 VectorSearch 의존성 ==="
echo "  BGE-M3 임베딩, LanceDB 벡터 저장소, PyArrow 스키마, NumPy 배열"

# M_07 VectorSearch — 벡터 검색 핵심 의존성
pip download \
    "sentence-transformers>=3.0,<5" \
    "lancedb>=0.10,<1" \
    "pyarrow>=15.0,<19" \
    "numpy>=1.26,<3" \
    --dest "${WHEELS_DIR}"

echo ""
echo "=== [bundle_deps.sh] M_07 BGE-M3 모델 다운로드 ==="
echo "  빌드 머신에서만 실행 — 사내 PC에서 실행 금지."

echo "  BAAI/bge-m3 → ${MODELS_DIR}/bge-m3"
huggingface-cli download BAAI/bge-m3 \
    --local-dir "${MODELS_DIR}/bge-m3" \
    --local-dir-use-symlinks False

echo ""
echo "=== [bundle_deps.sh] M_06 DocumentIngest 의존성 ==="
echo "  PDF/DOCX/PPTX/MD 파서 + XXE 방어"

# M_06 DocumentIngest — 문서 파서 핵심 의존성
pip download \
    "pypdfium2>=4.30,<5" \
    "python-docx>=1.1,<2" \
    "python-pptx>=1.0,<2" \
    "markdown-it-py>=3.0,<4" \
    "defusedxml>=0.7,<1" \
    --dest "${WHEELS_DIR}"

echo ""
echo "=== [bundle_deps.sh] M_10 IdleMonitor 의존성 ==="
echo "  pynput: 전역 키보드·마우스 훅 (Primary 백엔드)"
echo "  pywin32: Windows GetLastInputInfo 폴백 (Windows 전용 wheel)"

# pynput — 모든 플랫폼 (WSL/Linux에서도 import 가능해야 하므로 플랫폼 제한 없음)
pip download \
    "pynput>=1.7,<2" \
    --dest "${WHEELS_DIR}"

# pywin32 — Windows 전용. 빌드 머신이 Linux라면 --platform으로 Windows wheel 명시.
pip download \
    "pywin32>=306" \
    --platform win_amd64 \
    --python-version 3.12 \
    --only-binary=:all: \
    --dest "${WHEELS_DIR}"

echo ""
echo "=== [bundle_deps.sh] M_11 ProactiveDispatcher 의존성 ==="
echo "  APScheduler: cron + interval 스케줄러 (AsyncIOScheduler)"

# APScheduler — 순수 파이썬, 모든 플랫폼 공통
# 전이 의존성(tzlocal, pytz_deprecation_shim 등)은 pip download가 자동 해결
pip download \
    "APScheduler>=3.10,<4" \
    --dest "${WHEELS_DIR}"

echo ""
echo "=== [bundle_deps.sh] M_13 MeetingMinutes 의존성 ==="
echo "  lxml: XML 파싱·생성. C extension(libxml2 바인딩), 순수 로컬 처리."

# lxml — macOS ARM (arm64), Linux (x86_64/aarch64), Windows (amd64) 3종 wheel
pip download \
    "lxml>=5.0,<6" \
    --dest "${WHEELS_DIR}"

# Windows amd64 wheel (빌드 머신이 Linux/macOS인 경우)
pip download \
    "lxml>=5.0,<6" \
    --platform win_amd64 \
    --python-version 3.12 \
    --only-binary=:all: \
    --dest "${WHEELS_DIR}" || true  # Windows wheel 없어도 계속

# Linux x86_64 wheel
pip download \
    "lxml>=5.0,<6" \
    --platform manylinux2014_x86_64 \
    --python-version 3.12 \
    --only-binary=:all: \
    --dest "${WHEELS_DIR}" || true


# ==============================================================================
# M_12 Frontend npm 캐시 (§12.2 / §15.2)
# ------------------------------------------------------------------------------
# frontend/ 디렉토리의 node_modules를 오프라인에서 재구성할 수 있도록
# npm 캐시를 assets/npm_cache로 수집한다. Q-14 화이트리스트 정책에 따라
# electron·electron-builder·electron-rebuild·canvas만 postinstall 허용.
#
# 실행 조건: frontend/package-lock.json이 최신 상태여야 한다.
# 오프라인 설치 시 frontend/에서 `npm ci --offline --cache=<asset_path>` 실행.
# ==============================================================================

FRONTEND_DIR="${PROJECT_ROOT}/frontend"
if [ -f "${FRONTEND_DIR}/package-lock.json" ]; then
    echo ""
    echo "[npm] frontend/ 캐시 수집 → ${NPM_CACHE_DIR}"
    # npm cache는 tarball + metadata. --prefer-offline로 네트워크 최소화.
    (
        cd "${FRONTEND_DIR}"
        npm ci \
            --cache "${NPM_CACHE_DIR}" \
            --prefer-offline \
            --ignore-scripts \
            --no-audit \
            --no-fund
    )
    echo "[npm] 완료. 오프라인 PC에서 재현: cd frontend && npm ci --offline --cache=${NPM_CACHE_DIR}"
else
    echo "[npm] ${FRONTEND_DIR}/package-lock.json 부재 — M_12 frontend 빌드 전 생략"
fi

echo ""
echo "=== [bundle_deps.sh] 완료 ==="
echo "  wheels    : ${WHEELS_DIR}"
echo "  models    : ${MODELS_DIR}"
echo "  npm cache : ${NPM_CACHE_DIR}"
echo ""
echo "다음 단계: assets/ 디렉토리를 통째로 오프라인 배포 패키지에 포함시킨다."
echo "오프라인 설치 시:"
echo "  pip install --no-index --find-links=${WHEELS_DIR} faster-whisper ctranslate2 jsonschema mss Pillow"
echo "  cd frontend && npm ci --offline --cache=${NPM_CACHE_DIR}"
