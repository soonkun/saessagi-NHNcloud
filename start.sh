#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="$ROOT/upstream/Open-LLM-VTuber"

# Ensure frontend submodule is initialized
if [ ! -f "$UPSTREAM/frontend/index.html" ]; then
    echo "Initializing frontend submodule..."
    git -C "$UPSTREAM" submodule update --init --recursive
fi

# upstream 코드가 CWD에서 conf.yaml을 직접 읽으므로 심볼릭 링크 배치
ln -sf "$ROOT/conf.yaml" "$UPSTREAM/conf.yaml" 2>/dev/null || true

# 캐릭터 아바타 이미지를 upstream avatars/ 에 복사 (서빙 경로 맞춤)
cp -f "$ROOT/assets/character/saessagi/neutral.png" "$UPSTREAM/avatars/saessagi.png" 2>/dev/null || true

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
