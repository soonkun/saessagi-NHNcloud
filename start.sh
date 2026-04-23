#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="$ROOT/upstream/Open-LLM-VTuber"

# Ensure frontend submodule is initialized
if [ ! -f "$UPSTREAM/frontend/index.html" ]; then
    echo "Initializing frontend submodule..."
    git -C "$UPSTREAM" submodule update --init --recursive
fi

# Create symlinks so upstream's relative paths resolve from project root
for dir in frontend live2d-models backgrounds avatars web_tool characters; do
    if [ ! -e "$ROOT/$dir" ]; then
        ln -s "$UPSTREAM/$dir" "$ROOT/$dir"
    fi
done

export PYTHONPATH="$ROOT/src:$UPSTREAM/src:$UPSTREAM"

echo ""
echo "Starting AI Assistant server..."
echo "Open http://127.0.0.1:12393 in your browser."
echo "Press Ctrl+C to stop."
echo ""

cd "$ROOT"
uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
