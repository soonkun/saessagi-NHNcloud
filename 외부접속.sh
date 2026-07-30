#!/usr/bin/env bash
# 외부접속.sh — 새싹이를 켜고, 밖에서 접속할 인터넷 주소를 만들어 알려준다.
#
# 컴퓨터를 잘 몰라도 이것만 실행하면 된다:
#     ./외부접속.sh
#
# 하는 일:
#   1) 새싹이 백엔드를 켠다 (이미 켜져 있으면 그대로 둔다)
#   2) cloudflare 무료 터널로 인터넷 주소를 하나 받아온다
#   3) 그 주소와 비밀번호를 화면에 크게 보여준다
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

CF_BIN="/NHNHOME/WORKSPACE/26rda001_A/SAS/soonkun/opt/bin/cloudflared"
CF_LOG="$ROOT/data/logs/cloudflared.log"
CF_PID="$ROOT/data/run/cloudflared.pid"
mkdir -p "$(dirname "$CF_LOG")" "$(dirname "$CF_PID")"

PORT="$(sed -n '/^  web:/,/^  [a-z]/p' conf.yaml 2>/dev/null | sed -n 's/^ *port: *\([0-9]*\).*/\1/p' | head -1)"
PORT="${PORT:-12393}"

# ── 1. 새싹이 켜기 ────────────────────────────────────────────────────────────
if curl -sf -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null \
   || curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
    echo "새싹이는 이미 켜져 있습니다."
else
    echo "새싹이를 켜는 중... (1~2분 걸립니다)"
    ./새싹이.sh --no-build >/dev/null 2>&1
    if ! curl -sf -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null; then
        echo ""
        echo "[오류] 새싹이가 켜지지 않았습니다. 아래 로그를 확인하세요:"
        echo "       $ROOT/data/logs/backend.log"
        exit 1
    fi
fi

# ── 2. 이전 터널 정리 ────────────────────────────────────────────────────────
if [ -f "$CF_PID" ] && kill -0 "$(cat "$CF_PID")" 2>/dev/null; then
    kill "$(cat "$CF_PID")" 2>/dev/null
    sleep 2
fi
rm -f "$CF_PID"

# ── 3. 인터넷 주소 받아오기 ──────────────────────────────────────────────────
echo "인터넷 주소를 받아오는 중..."
: > "$CF_LOG"
setsid nohup "$CF_BIN" tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate \
    >"$CF_LOG" 2>&1 </dev/null &
echo $! > "$CF_PID"

URL=""
for _ in $(seq 1 40); do
    sleep 2
    URL="$(grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CF_LOG" | head -1)"
    [ -n "$URL" ] && break
done

if [ -z "$URL" ]; then
    echo ""
    echo "[오류] 주소를 받지 못했습니다. 인터넷 연결을 확인하고 다시 실행해 보세요."
    echo "       로그: $CF_LOG"
    exit 1
fi

# 주소가 실제로 응답하는지 확인 (터널이 자리잡는 데 몇 초 걸린다)
for _ in $(seq 1 15); do
    sleep 2
    curl -sf -o /dev/null -m 10 "$URL/login" 2>/dev/null && break
done

# 비밀번호는 YAML 파서로 읽는다 — 따옴표 유무·특수문자(#, : 등)를 sed로 다루면 틀린다.
PW="$("$ROOT/.venv/bin/python" -c "
import yaml,sys
try:
    cfg = yaml.safe_load(open('$ROOT/conf.yaml', encoding='utf-8')) or {}
    print(((cfg.get('app') or {}).get('web') or {}).get('auth_password') or '')
except Exception:
    pass
" 2>/dev/null)"

cat <<EOS

╔══════════════════════════════════════════════════════════════╗
║  새싹이가 준비되었습니다                                       ║
╚══════════════════════════════════════════════════════════════╝

  아무 컴퓨터에서나 인터넷 브라우저(크롬, 엣지 등)를 열고
  아래 주소를 주소창에 그대로 입력하세요.

      $URL

  비밀번호:  ${PW:-(conf.yaml 확인)}

  ※ 이 터미널 창은 닫아도 됩니다. 계속 켜져 있습니다.
  ※ 끄고 싶을 때:  ./외부접속끄기.sh

EOS
