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

# 두 곳에서 동시에 띄우지 않는다 (E-89).
# 사람이 이 스크립트를 돌리는 동안 워치독이 "응답 없음"으로 판단해 또 띄우면, 나중 것이
# bind에 실패해 죽으면서 backend.log를 덮어써 **먼저 뜬 프로세스의 진짜 사인이 지워진다.**
#
# 락은 **직접 연 fd 9**로 잡는다. `flock <파일> <명령>` 형태를 쓰면 flock이 연 fd를
# 백그라운드로 띄운 백엔드가 그대로 물려받아, 백엔드가 사는 내내 락이 풀리지 않는다
# (실제로 그래서 다음 재시작이 10분간 멈췄다). fd 번호를 알고 있어야 자식에게
# 물려주지 않도록 닫아 줄 수 있다 — 아래 백그라운드 실행마다 `9>&-`가 붙는 이유다.
exec 9>"$RUN_DIR/launcher.lock"
if [ -n "${SAESSAGI_LOCK_NOWAIT:-}" ]; then
    # 워치독 경로: 사람이 띄우는 중이면 끼어들지 않고 물러난다.
    if ! flock -n 9; then
        echo "다른 곳에서 새싹이를 켜는 중입니다 — 이번 실행은 건너뜁니다." >&2
        exit 75
    fi
else
    if ! flock -w 600 9; then
        echo "[오류] 다른 실행이 10분 넘게 락을 잡고 있습니다." >&2
        exit 1
    fi
fi

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
    setsid nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 </dev/null 9>&- &
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
            JAVA_HOME="$JRE" setsid nohup "$NEO" console >"$LOG_DIR/neo4j.log" 2>&1 </dev/null 9>&- &
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
# --fork를 붙여 항상 새 프로세스로 떼어낸다. 없으면 setsid가 그대로 exec해 버려
# 런처를 띄운 셸의 프로세스 그룹에 남고, 그 셸이 끝날 때 함께 종료된다
# (실제로 백엔드가 조용히 죽어 "사이트에 연결할 수 없음"이 됐다).
# 이전 실행의 stderr를 보존한다 (E-86).
# `>`로 덮어쓰면 백엔드가 왜 죽었는지 적힌 마지막 출력이 재시작과 동시에 사라진다.
# 파이썬 로그(app-*.log)는 loguru가 append하지만, C 레벨 abort·OOM 메시지는 stderr에만
# 남으므로 이 파일이 유일한 단서다. 실제로 원인 추적이 두 번 막혔다.
if [ -s "$LOG_DIR/backend.log" ]; then
    mv "$LOG_DIR/backend.log" "$LOG_DIR/backend.prev-$(date +%m%d-%H%M%S).log"
    # 오래된 것은 정리 — 최근 10개만 남긴다.
    ls -1t "$LOG_DIR"/backend.prev-*.log 2>/dev/null | tail -n +11 | xargs -r rm -f
fi
setsid --fork nohup "$PY" -m app.main >"$LOG_DIR/backend.log" 2>&1 </dev/null 9>&- &

BE_READY=0
for _ in $(seq 1 180); do
    sleep 1
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null \
       || curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
        BE_READY=1; break
    fi
done

# 실제로 포트를 잡고 있는 프로세스를 pidfile에 적는다.
# `$!`는 setsid 래퍼의 PID라 실제 서버와 다를 수 있고, 그러면 끄기 스크립트가 엉뚱한
# PID에 신호를 보내 백엔드가 안 꺼지거나 이미 죽은 것을 살아있다고 오판한다.
BE_PID="$(ss -tlnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $0}' \
          | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
[ -n "$BE_PID" ] && echo "$BE_PID" > "$RUN_DIR/backend.pid"

if [ "$BE_READY" -ne 1 ]; then
    echo "" >&2
    echo "[오류] 백엔드가 준비되지 않았습니다. 로그 마지막 30줄:" >&2
    tail -n 30 "$LOG_DIR/backend.log" >&2
    exit 1
fi
ok "백엔드 준비 완료 (PID ${BE_PID:-?})"

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
            --protocol http2 --no-autoupdate >"$LOG_DIR/cloudflared.log" 2>&1 </dev/null 9>&- &
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
