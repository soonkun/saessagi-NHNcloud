#!/usr/bin/env bash
# 외부접속끄기.sh — 인터넷 주소를 닫는다. (새싹이 자체는 계속 켜져 있다)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CF_PID="$ROOT/data/run/cloudflared.pid"

if [ -f "$CF_PID" ] && kill -0 "$(cat "$CF_PID")" 2>/dev/null; then
    kill "$(cat "$CF_PID")" 2>/dev/null
    sleep 1
    rm -f "$CF_PID"
    echo "인터넷 주소를 닫았습니다. 이제 밖에서는 접속할 수 없습니다."
else
    echo "이미 닫혀 있습니다."
    rm -f "$CF_PID"
fi

echo "새싹이 자체를 끄려면:  kill \$(cat $ROOT/data/run/backend.pid)"
