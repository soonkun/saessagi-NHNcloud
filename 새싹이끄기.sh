#!/usr/bin/env bash
# 새싹이끄기.sh — 새싹이.sh가 켠 것을 전부 끈다.
#
#     ./새싹이끄기.sh          백엔드 + 외부 접속 주소
#     ./새싹이끄기.sh --전부   Ollama·Neo4j까지 (다른 작업이 쓰고 있을 수 있으니 기본 제외)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$ROOT/data/run"

ALL=0
[ "${1:-}" = "--전부" ] || [ "${1:-}" = "--all" ] && ALL=1

stop_pidfile() {
    local name="$1" file="$RUN_DIR/$2"
    if [ -f "$file" ] && kill -0 "$(cat "$file")" 2>/dev/null; then
        kill "$(cat "$file")" 2>/dev/null
        for _ in $(seq 1 20); do kill -0 "$(cat "$file")" 2>/dev/null || break; sleep 0.5; done
        echo "  $name 종료"
    else
        echo "  $name 이미 꺼짐"
    fi
    rm -f "$file"
}

stop_pidfile "외부 접속 주소" cloudflared.pid
stop_pidfile "백엔드" backend.pid

if [ "$ALL" -eq 1 ]; then
    # Ollama·Neo4j는 pidfile을 안 남기므로 포트로 찾는다.
    for spec in "Ollama:11434" "Neo4j:7687"; do
        name="${spec%%:*}"; port="${spec##*:}"
        pid="$(ss -tlnp 2>/dev/null | grep ":$port " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)"
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null && echo "  $name 종료 (PID $pid)"
        else
            echo "  $name 이미 꺼짐"
        fi
    done
fi

echo ""
echo "다시 켜기:  ./새싹이.sh"
