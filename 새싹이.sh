#!/usr/bin/env bash
# 새싹이.sh — 웹 UI 백엔드 런처 (CR-38)
#
# Electron 앱은 제거됐다. 이 스크립트는 백엔드만 띄우고, 사용자는 브라우저로 접속한다.
# 바인딩 주소·포트·인증은 conf.yaml의 app.web 섹션을 따른다.
#   - 로컬 전용(기본): host 127.0.0.1, auth_enabled false
#   - 사내망 공개    : host 0.0.0.0  + auth_enabled true + auth_password
#     (열어놓고 인증을 끄면 백엔드가 기동을 거부한다 — 무인증 노출 방지)
#
# 사용: ./새싹이.sh [--no-build]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PIDFILE="$ROOT/data/run/backend.pid"
LOG="$ROOT/data/logs/backend.log"
mkdir -p "$(dirname "$PIDFILE")" "$(dirname "$LOG")"

SKIP_BUILD=0
[ "${1:-}" = "--no-build" ] && SKIP_BUILD=1

# ── conf.yaml 부트스트랩 ──────────────────────────────────────────────────────
if [ ! -f "$ROOT/conf.yaml" ]; then
    if [ -f "$ROOT/conf.example.yaml" ]; then
        cp "$ROOT/conf.example.yaml" "$ROOT/conf.yaml"
        echo "conf.yaml 생성됨 (conf.example.yaml 복사)."
    else
        echo "[오류] conf.yaml / conf.example.yaml 둘 다 없음." >&2
        exit 1
    fi
fi

# ── 이전 인스턴스 정리 ────────────────────────────────────────────────────────
# pidfile 기준으로 종료한다. pgrep/pkill -f는 자기 자신의 명령줄까지 매칭해
# 런처 스크립트를 스스로 죽이는 사고가 난다.
if [ -f "$PIDFILE" ]; then
    OLD="$(cat "$PIDFILE")"
    if kill -0 "$OLD" 2>/dev/null; then
        echo "이전 백엔드(PID $OLD) 종료..."
        kill "$OLD" 2>/dev/null || true
        for _ in $(seq 1 20); do kill -0 "$OLD" 2>/dev/null || break; sleep 0.5; done
    fi
    rm -f "$PIDFILE"
fi

# ── 프론트엔드 빌드 (dist 없거나 소스가 더 새로울 때) ─────────────────────────
if [ "$SKIP_BUILD" -eq 0 ] && ! node "$ROOT/web/scripts/check-rebuild.mjs" 2>/dev/null; then
    echo "프론트엔드 빌드 중..."
    (
        cd "$ROOT/web"
        if [ -d "$ROOT/assets/npm_cache" ]; then
            npm install --prefer-offline --cache "$ROOT/assets/npm_cache"
        else
            npm install
        fi
        npm run build
    )
fi

# ── Ollama 확인·기동 ─────────────────────────────────────────────────────────
if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "Ollama 시작 중..."
    setsid nohup ollama serve >"$ROOT/data/logs/ollama.log" 2>&1 </dev/null &
    for _ in $(seq 1 30); do
        sleep 1
        curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
    done
    curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 \
        || echo "[경고] Ollama가 응답하지 않습니다. 계속 진행합니다..."
fi

export SAESSAGI_ROOT="$ROOT"
export SAESSAGI_CONFIG_PATH="$ROOT/conf.yaml"
export PYTHONPATH="$ROOT:$ROOT/src:$ROOT/vendor"

# ── 백엔드 기동 ──────────────────────────────────────────────────────────────
# uv run이 아니라 venv 인터프리터를 직접 부른다 — uv run은 실행 전 환경을 uv.lock에
# 맞춰 동기화하면서 락파일에 없는 melotts를 제거하고, TTS 초기화 실패가 LLM 대화까지
# 죽인다 (E-65).
setsid nohup "$ROOT/.venv/bin/python" -m app.main >"$LOG" 2>&1 </dev/null &
echo $! > "$PIDFILE"
echo "백엔드 기동 (PID $(cat "$PIDFILE")) — 로그: $LOG"

# ── 준비 대기 ────────────────────────────────────────────────────────────────
PORT="$(sed -n '/^  web:/,/^  [a-z]/p' conf.yaml | sed -n 's/^ *port: *\([0-9]*\).*/\1/p' | head -1)"
PORT="${PORT:-12393}"

for _ in $(seq 1 180); do
    sleep 1
    if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then break; fi
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null \
       || curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
        HOST="$(sed -n '/^  web:/,/^  [a-z]/p' conf.yaml | sed -n 's/^ *host: *\(.*\)/\1/p' | head -1 | tr -d '"' | tr -d "'")"
        echo ""
        echo "새싹이 준비 완료. 브라우저에서 접속하세요:"
        if [ "$HOST" = "0.0.0.0" ]; then
            IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
            echo "  http://${IP:-<서버IP>}:$PORT"
        else
            echo "  http://127.0.0.1:$PORT"
        fi
        echo ""
        echo "종료: kill \$(cat $PIDFILE)"
        exit 0
    fi
done

echo "[오류] 백엔드가 준비되지 않았습니다. 로그 마지막 30줄:" >&2
tail -n 30 "$LOG" >&2
exit 1
