#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="$ROOT/upstream/Open-LLM-VTuber"

# Ensure frontend submodule is initialized
if [ ! -f "$UPSTREAM/frontend/index.html" ]; then
    echo "Initializing frontend submodule..."
    git -C "$UPSTREAM" submodule update --init --recursive
fi

# Project root for resolving data/assets paths (PathsConfig reads this)
export SAESSAGI_ROOT="$ROOT"
export SAESSAGI_CONFIG_PATH="$ROOT/conf.yaml"
export PYTHONPATH="$ROOT:$ROOT/src:$UPSTREAM/src:$UPSTREAM"

echo ""
echo "Starting AI Assistant server..."
echo "Open http://127.0.0.1:12393 in your browser."
echo "Press Ctrl+C to stop."
echo ""

# Run from upstream dir so frontend/, live2d-models/, model_dict.json resolve correctly
cd "$UPSTREAM"
uv run --project "$ROOT" uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
