#!/usr/bin/env bash
# 새싹이.sh — 이것 하나만 실행하면 필요한 모든 것이 켜진다.
#
#     ./새싹이.sh
#
# 켜는 것 (conf.yaml 설정에 따라 자동 판단):
#   1. 프론트엔드 빌드 (소스가 빌드보다 새로울 때만)
#   2. Ollama
#   3. Neo4j            — app.graphrag.enabled 가 true일 때만
#   4. 백엔드           — RAG 폴더 감시·자동 시딩은 백엔드가 알아서 한다
#   5. 외부 접속 주소   — cloudflared가 있으면 자동 (--local 로 생략)
#
# 옵션:
#   --local      외부 접속 주소를 만들지 않는다 (서버 안에서만 사용)
#   --no-build   프론트엔드 재빌드를 건너뛴다 (빠른 재시작)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
RUN_DIR="$ROOT/data/run"
LOG_DIR="$ROOT/data/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

SKIP_BUILD=0
WANT_TUNNEL=1
for arg in "$@"; do
    case "$arg" in
        --no-build) SKIP_BUILD=1 ;;
        --local)    WANT_TUNNEL=0 ;;
        *) echo "알 수 없는 옵션: $arg (사용 가능: --local, --no-build)" >&2; exit 2 ;;
    esac
done

say()  { echo -e "\033[0;36m$*\033[0m"; }
ok()   { echo -e "  \033[0;32m✓\033[0m $*"; }
warn() { echo -e "  \033[1;33m!\033[0m $*"; }

# ── 0. conf.yaml ─────────────────────────────────────────────────────────────
if [ ! -f "$ROOT/conf.yaml" ]; then
    if [ -f "$ROOT/conf.example.yaml" ]; then
        cp "$ROOT/conf.example.yaml" "$ROOT/conf.yaml"
        ok "conf.yaml 생성 (conf.example.yaml 복사)"
    else
        echo "[오류] conf.yaml / conf.example.yaml 둘 다 없음." >&2
        exit 1
    fi
fi

# 설정 읽기는 YAML 파서로 한다 — sed로 다루면 따옴표·특수문자(#, :)에서 틀린다.
read_conf() {
    "$PY" - "$1" <<'PY' 2>/dev/null
import sys, yaml
keys = sys.argv[1].split(".")
try:
    node = yaml.safe_load(open("conf.yaml", encoding="utf-8")) or {}
    for k in keys:
        node = (node or {}).get(k)
    print("" if node is None else node)
except Exception:
    print("")
PY
}

PORT="$(read_conf app.web.port)";        PORT="${PORT:-12393}"
HOST="$(read_conf app.web.host)";        HOST="${HOST:-127.0.0.1}"
PASSWORD="$(read_conf app.web.auth_password)"
AUTH="$(read_conf app.web.auth_enabled)"
GRAPHRAG="$(read_conf app.graphrag.enabled)"
WATCH_ON="$(read_conf app.rag_watch.enabled)"
WATCH_ROOT="$(read_conf app.rag_watch.root)"

# ── 1. 이전 백엔드 정리 ──────────────────────────────────────────────────────
# pidfile 기준. pgrep/pkill -f는 자기 명령줄까지 매칭해 런처가 스스로를 죽인다.
if [ -f "$RUN_DIR/backend.pid" ]; then
    OLD="$(cat "$RUN_DIR/backend.pid")"
    if kill -0 "$OLD" 2>/dev/null; then
        kill "$OLD" 2>/dev/null
        for _ in $(seq 1 20); do kill -0 "$OLD" 2>/dev/null || break; sleep 0.5; done
        ok "이전 백엔드 종료 (PID $OLD)"
    fi
    rm -f "$RUN_DIR/backend.pid"
fi

# ── 2. 프론트엔드 빌드 ───────────────────────────────────────────────────────
if [ "$SKIP_BUILD" -eq 0 ] && ! node "$ROOT/web/scripts/check-rebuild.mjs" 2>/dev/null; then
    say "프론트엔드 빌드 중..."
    (
        cd "$ROOT/web"
        if [ -d "$ROOT/assets/npm_cache" ]; then
            npm install --prefer-offline --cache "$ROOT/assets/npm_cache"
        else
            npm install
        fi
        npm run build
    ) || { echo "[오류] 프론트엔드 빌드 실패" >&2; exit 1; }
    ok "프론트엔드 빌드 완료"
fi

# ── 3. Ollama ────────────────────────────────────────────────────────────────
if curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    ok "Ollama 실행 중"
else
    setsid nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 </dev/null &
    for _ in $(seq 1 30); do
        sleep 1
        curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
    done
    if curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        ok "Ollama 시작"
    else
        warn "Ollama가 응답하지 않습니다 — LLM 대화가 안 될 수 있습니다"
    fi
fi

# ── 4. Neo4j (지식그래프를 켠 경우에만) ──────────────────────────────────────
if [ "$GRAPHRAG" = "True" ] || [ "$GRAPHRAG" = "true" ]; then
    if curl -sf -o /dev/null http://127.0.0.1:7474 2>/dev/null; then
        ok "Neo4j 실행 중"
    else
        NEO="$ROOT/../opt/neo4j/bin/neo4j"
        JRE="$ROOT/../opt/jre21"
        if [ -x "$NEO" ]; then
            JAVA_HOME="$JRE" setsid nohup "$NEO" console >"$LOG_DIR/neo4j.log" 2>&1 </dev/null &
            for _ in $(seq 1 40); do
                sleep 2
                curl -sf -o /dev/null http://127.0.0.1:7474 2>/dev/null && break
            done
            if curl -sf -o /dev/null http://127.0.0.1:7474 2>/dev/null; then
                ok "Neo4j 시작"
            else
                warn "Neo4j가 응답하지 않습니다 — 그래프 기능만 비활성됩니다"
            fi
        else
            warn "graphrag.enabled=true인데 Neo4j가 없습니다 ($NEO) — 그래프 기능 비활성"
        fi
    fi
fi

# ── 5. 백엔드 ────────────────────────────────────────────────────────────────
export SAESSAGI_ROOT="$ROOT"
export SAESSAGI_CONFIG_PATH="$ROOT/conf.yaml"
export PYTHONPATH="$ROOT:$ROOT/src:$ROOT/vendor"

# uv run이 아니라 venv 인터프리터를 직접 부른다 — uv run은 실행 전 환경을 uv.lock에
# 맞춰 동기화하면서 락파일에 없는 melotts를 제거하고, TTS 초기화 실패가 LLM 대화까지
# 죽인다 (E-65).
setsid nohup "$PY" -m app.main >"$LOG_DIR/backend.log" 2>&1 </dev/null &
echo $! > "$RUN_DIR/backend.pid"

BE_READY=0
for _ in $(seq 1 180); do
    sleep 1
    kill -0 "$(cat "$RUN_DIR/backend.pid")" 2>/dev/null || break
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null \
       || curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
        BE_READY=1; break
    fi
done

if [ "$BE_READY" -ne 1 ]; then
    echo "" >&2
    echo "[오류] 백엔드가 준비되지 않았습니다. 로그 마지막 30줄:" >&2
    tail -n 30 "$LOG_DIR/backend.log" >&2
    exit 1
fi
ok "백엔드 준비 완료 (PID $(cat "$RUN_DIR/backend.pid"))"

if [ "$WATCH_ON" = "True" ] || [ "$WATCH_ON" = "true" ]; then
    # 자동 시딩은 백엔드가 첫 스캔 전에 알아서 한다 (CR-41) — 사람이 스크립트를 돌릴 필요 없음
    ok "RAG 폴더 감시: $WATCH_ROOT"
fi

# ── 6. 외부 접속 주소 ────────────────────────────────────────────────────────
URL=""
if [ "$WANT_TUNNEL" -eq 1 ]; then
    CF="$ROOT/../opt/bin/cloudflared"
    if [ -x "$CF" ]; then
        if [ -f "$RUN_DIR/cloudflared.pid" ] && kill -0 "$(cat "$RUN_DIR/cloudflared.pid")" 2>/dev/null; then
            kill "$(cat "$RUN_DIR/cloudflared.pid")" 2>/dev/null; sleep 2
        fi
        : > "$LOG_DIR/cloudflared.log"
        # QUIC(UDP/443)이 막힌 사내망에서 끊기므로 http2로 고정한다
        setsid nohup "$CF" tunnel --url "http://127.0.0.1:$PORT" \
            --protocol http2 --no-autoupdate >"$LOG_DIR/cloudflared.log" 2>&1 </dev/null &
        echo $! > "$RUN_DIR/cloudflared.pid"
        for _ in $(seq 1 40); do
            sleep 2
            URL="$(grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/cloudflared.log" | head -1)"
            [ -n "$URL" ] && break
        done
        if [ -n "$URL" ]; then
            for _ in $(seq 1 15); do
                sleep 2
                curl -sf -o /dev/null -m 10 "$URL/login" 2>/dev/null && break
            done
            ok "외부 접속 주소 준비"
        else
            warn "외부 주소를 받지 못했습니다 (인터넷 연결 확인) — 서버 안에서는 사용 가능"
        fi
    fi
fi

# ── 안내 ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  새싹이가 준비되었습니다                                       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
if [ -n "$URL" ]; then
    echo "  아무 컴퓨터에서 브라우저를 열고 아래 주소를 입력하세요."
    echo ""
    echo "      $URL"
elif [ "$HOST" = "0.0.0.0" ]; then
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo "      http://${IP:-<서버IP>}:$PORT"
else
    echo "      http://127.0.0.1:$PORT   (이 서버 안에서만)"
fi
if [ "$AUTH" = "True" ] || [ "$AUTH" = "true" ]; then
    echo ""
    echo "  비밀번호:  ${PASSWORD:-(conf.yaml의 app.web.auth_password 확인)}"
fi
echo ""
echo "  이 터미널 창은 닫아도 됩니다. 계속 켜져 있습니다."
echo "  끄기:  ./새싹이끄기.sh"
echo ""
