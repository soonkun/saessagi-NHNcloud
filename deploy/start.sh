#!/usr/bin/env bash
# 새싹이 AI 비서 서버 시작 (macOS / Linux)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT"

if [ ! -d ".venv" ]; then
    echo "[start] .venv가 없습니다. install.sh를 먼저 실행하세요."
    exit 1
fi

export PYTHONPATH="src:upstream/Open-LLM-VTuber/src:upstream/Open-LLM-VTuber"

echo "[start] Ollama 실행 확인 중..."
if ! pgrep -x ollama >/dev/null 2>&1; then
    echo "[start] Ollama 시작..."
    ollama serve &>/dev/null &
    sleep 3
fi

echo "[start] 새싹이 서버 시작: http://127.0.0.1:12393"
.venv/bin/python -m uvicorn app.main:create_app \
    --factory \
    --host 127.0.0.1 \
    --port 12393 \
    --workers 1
