#!/bin/bash
# 백엔드가 죽으면 다시 띄운다 (E-86).
#
# 대화 처리 중 백엔드가 흔적 없이 사라지는 일이 반복됐다. 원인(E-88 cuDNN segfault)은
# 잡았지만, 다른 이유로 죽었을 때도 사용자가 "사이트에 연결할 수 없음"을 보지 않도록
# 그대로 둔다. 되살릴 때마다 사유를 로그에 남겨 추적을 돕는다.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/data/logs"
WD_LOG="$LOG_DIR/watchdog.log"
LOCK="$ROOT/data/run/launcher.lock"
PORT="$(grep -A 5 '^  web:' "$ROOT/conf.yaml" | grep -m1 'port:' | tr -dc '0-9')"
PORT="${PORT:-50002}"
INTERVAL=15

mkdir -p "$ROOT/data/run"

# 포트를 잡고 있는 프로세스가 있는지 본다.
# 기동 중인 백엔드(모델 로딩에 30~60초)는 아직 HTTP에 응답하지 않지만 포트는 이미
# 잡고 있다. 이때 재시작을 걸면 두 번째 프로세스가 bind에 실패해 죽으면서
# **backend.log를 덮어써 진짜 사인(死因)을 지운다.** 실제로 그 일이 있었다.
port_taken() {
    ss -tln 2>/dev/null | awk -v p=":$PORT\$" '$4 ~ p {found=1} END {exit !found}'
}

alive() {
    curl -sf -o /dev/null --max-time 10 "http://127.0.0.1:$PORT/login" \
        || curl -sf -o /dev/null --max-time 10 "http://127.0.0.1:$PORT/"
}

echo "$(date '+%F %T') watchdog 시작 (포트 $PORT, ${INTERVAL}초 간격)" >> "$WD_LOG"
while true; do
    if ! alive; then
        # 두 번 연속 실패해야 재시작 — 일시적 지연으로 멀쩡한 서버를 죽이지 않는다.
        sleep 5
        if ! alive; then
            if port_taken; then
                echo "$(date '+%F %T') 응답은 없지만 포트를 잡고 있음 (기동 중으로 보고 대기)" >> "$WD_LOG"
            else
                echo "$(date '+%F %T') 백엔드 응답 없음 → 재시작" >> "$WD_LOG"
                # 죽기 직전 stderr 마지막 줄을 남겨 원인 추적을 돕는다
                tail -n 5 "$LOG_DIR/backend.log" 2>/dev/null | sed 's/^/    /' >> "$WD_LOG"
                # 사람이 런처를 돌리는 중이면 건너뛴다 — 둘이 동시에 띄우면 뒤엣것이
                # bind에 실패하고, 그 과정에서 로그가 덮인다. -n: 못 잡으면 즉시 포기.
                # SAESSAGI_LAUNCH_LOCKED: 런처가 안에서 같은 락을 다시 잡아 교착되는 것을 막는다.
                if SAESSAGI_LAUNCH_LOCKED=1 flock -n "$LOCK" \
                        "$ROOT/새싹이.sh" --no-build --local >> "$WD_LOG" 2>&1; then
                    echo "$(date '+%F %T') 재시작 완료" >> "$WD_LOG"
                else
                    echo "$(date '+%F %T') 다른 곳에서 기동 중 — 이번 재시작 건너뜀" >> "$WD_LOG"
                fi
            fi
        fi
    fi
    sleep "$INTERVAL"
done
