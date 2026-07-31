#!/bin/bash
# 백엔드가 죽으면 다시 띄운다 (E-86).
#
# 대화 처리 중 백엔드가 흔적 없이 SIGKILL로 사라지는 일이 반복됐다(원인 조사 중).
# 원인을 잡기 전까지 사용자가 "사이트에 연결할 수 없음"을 보지 않도록 하는 임시 안전망이다.
# 근본 해결이 아니므로, 되살릴 때마다 사유를 로그에 남겨 추적을 돕는다.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/data/logs"
WD_LOG="$LOG_DIR/watchdog.log"
PORT="$(grep -A 5 '^  web:' "$ROOT/conf.yaml" | grep -m1 'port:' | tr -dc '0-9')"
PORT="${PORT:-50002}"
INTERVAL=15

echo "$(date '+%F %T') watchdog 시작 (포트 $PORT, ${INTERVAL}초 간격)" >> "$WD_LOG"
while true; do
    if ! curl -sf -o /dev/null --max-time 10 "http://127.0.0.1:$PORT/login" \
       && ! curl -sf -o /dev/null --max-time 10 "http://127.0.0.1:$PORT/"; then
        # 두 번 연속 실패해야 재시작 — 일시적 지연으로 멀쩡한 서버를 죽이지 않는다.
        sleep 5
        if ! curl -sf -o /dev/null --max-time 10 "http://127.0.0.1:$PORT/login" \
           && ! curl -sf -o /dev/null --max-time 10 "http://127.0.0.1:$PORT/"; then
            echo "$(date '+%F %T') 백엔드 응답 없음 → 재시작" >> "$WD_LOG"
            # 죽기 직전 stderr 마지막 줄을 남겨 원인 추적을 돕는다
            tail -n 5 "$LOG_DIR/backend.log" 2>/dev/null | sed 's/^/    /' >> "$WD_LOG"
            "$ROOT/새싹이.sh" --no-build --local >> "$WD_LOG" 2>&1
            echo "$(date '+%F %T') 재시작 완료" >> "$WD_LOG"
        fi
    fi
    sleep "$INTERVAL"
done
