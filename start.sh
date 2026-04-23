#!/usr/bin/env bash
# start.sh — macOS / Linux server launcher

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

# Ensure frontend submodule is initialized
if [ ! -f "$ROOT/upstream/Open-LLM-VTuber/frontend/index.html" ]; then
    echo "Initializing frontend submodule..."
    git -C "$ROOT/upstream/Open-LLM-VTuber" submodule update --init --recursive
fi

export SAESSAGI_CONFIG_PATH="$ROOT/conf.yaml"
export PYTHONPATH="$ROOT/src:$ROOT/upstream/Open-LLM-VTuber/src:$ROOT/upstream/Open-LLM-VTuber"

echo ""
echo "Starting AI Assistant server..."
echo "Open http://127.0.0.1:12393 in your browser."
echo "Press Ctrl+C to stop."
echo ""

# Run from upstream dir so relative paths (frontend/, live2d-models/ etc.) resolve correctly
cd "$ROOT/upstream/Open-LLM-VTuber"
"$ROOT/.venv/bin/python" -m uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
