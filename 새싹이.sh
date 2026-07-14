#!/usr/bin/env bash
# 새싹이.sh — Linux/WSL 런처 (새싹이.cmd 의 리눅스 버전)
# 사용: ./새싹이.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="$ROOT/upstream/Open-LLM-VTuber"

# ── conf.yaml 부트스트랩 (API 키 포함이라 git 미추적) ─────────────────────────
if [ ! -f "$ROOT/conf.yaml" ]; then
    if [ -f "$ROOT/conf.example.yaml" ]; then
        cp "$ROOT/conf.example.yaml" "$ROOT/conf.yaml"
        echo "conf.yaml 생성됨(conf.example.yaml 복사). 'api_key'/'llm_api_key'에 OpenAI 키를 넣으세요."
    else
        echo "[오류] conf.yaml / conf.example.yaml 둘 다 없음." >&2
        exit 1
    fi
fi

# ── upstream 확인 (gitignore된 외부 클론 — bootstrap.py 가 담당) ──────────────
if [ ! -d "$UPSTREAM/.git" ]; then
    echo "[오류] upstream/Open-LLM-VTuber 가 없습니다. 먼저 실행하세요:" >&2
    echo "  python3 scripts/bootstrap.py" >&2
    exit 1
fi

if [ ! -f "$UPSTREAM/frontend/index.html" ]; then
    echo "frontend 서브모듈 초기화 중..."
    git -C "$UPSTREAM" submodule update --init --recursive
fi

# ── 프론트엔드 빌드 (dist 없거나 소스가 더 새로울 때) ─────────────────────────
# check-rebuild.mjs: 재빌드 필요 시 exit 1, 최신이면 exit 0
if ! node "$ROOT/web/scripts/check-rebuild.mjs"; then
    echo "프론트엔드 빌드 중..."
    (
        cd "$ROOT/web"
        if [ -d "$ROOT/assets/npm_cache" ]; then
            npm install --prefer-offline --cache "$ROOT/assets/npm_cache"
        else
            npm install
        fi
        ELECTRON_BUILD=1 npm run build
    )
    # 사고 1 방지: Electron(file://)용 빌드는 상대 경로(./assets)여야 한다
    if grep -q 'src="/assets' "$ROOT/web/dist/index.html"; then
        echo "[오류] web/dist/index.html 의 script 경로가 절대 경로(/assets)입니다." >&2
        echo "ELECTRON_BUILD=1 이 적용되지 않은 잘못된 빌드 — Electron에서 흰 화면이 뜹니다." >&2
        exit 1
    fi
fi

# ── upstream 이 CWD에서 읽는 파일 배치 ────────────────────────────────────────
ln -sf "$ROOT/conf.yaml" "$UPSTREAM/conf.yaml" 2>/dev/null || true
cp -f "$ROOT/assets/character/saessagi/neutral.png" "$UPSTREAM/avatars/saessagi.png" 2>/dev/null || true

# ── 환경 변수 ─────────────────────────────────────────────────────────────────
export SAESSAGI_ROOT="$ROOT"
export SAESSAGI_CONFIG_PATH="$ROOT/conf.yaml"
export PYTHONPATH="$ROOT:$ROOT/src:$UPSTREAM/src:$UPSTREAM"

echo ""
echo "새싹이 시작 중..."
echo ""

# ── Ollama 확인·기동 (백엔드가 시작 시 접속 확인, 없으면 중단됨) ──────────────
if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "Ollama 시작 중..."
    nohup ollama serve >/dev/null 2>&1 &
    for _ in $(seq 1 30); do
        sleep 1
        if curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
            break
        fi
    done
    if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        echo "[경고] Ollama가 30초 내에 응답하지 않음. 일단 계속 진행합니다..."
    fi
fi
echo "Ollama 준비 완료."

# ── 이전 세션 잔여 백엔드 정리 (포트 12393 점유 시 재기동 실패) ───────────────
STALE_PID="$(lsof -ti tcp:12393 -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$STALE_PID" ]; then
    echo "포트 12393 점유 중인 이전 백엔드(PID $STALE_PID) 종료..."
    kill "$STALE_PID" 2>/dev/null || true
    sleep 1
fi

# ── 백엔드 기동 (로그: data/logs/backend.log) ─────────────────────────────────
LOG="$ROOT/data/logs/backend.log"
mkdir -p "$(dirname "$LOG")"
(
    cd "$UPSTREAM"
    exec uv run --project "$ROOT" uvicorn "app.main:create_app" --factory \
        --host 127.0.0.1 --port 12393
) >"$LOG" 2>&1 &
BACKEND_PID=$!
trap 'echo ""; echo "백엔드 종료 중..."; kill "$BACKEND_PID" 2>/dev/null || true' EXIT

echo "백엔드 대기 중... (로그: $LOG)"
BE_READY=0
for _ in $(seq 1 180); do
    sleep 1
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        break
    fi
    if curl -sf http://127.0.0.1:12393/ >/dev/null 2>&1; then
        BE_READY=1
        break
    fi
done
if [ "$BE_READY" -ne 1 ]; then
    echo "[오류] 백엔드가 준비되지 않았습니다. 로그 마지막 30줄:" >&2
    tail -n 30 "$LOG" >&2
    exit 1
fi

echo "백엔드 준비 완료. 앱 실행..."

# ── Electron 실행 (창을 닫으면 백엔드도 함께 종료) ────────────────────────────
cd "$ROOT/frontend"
npm run start
