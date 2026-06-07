#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="$ROOT/upstream/Open-LLM-VTuber"

# conf.yaml 부트스트랩 — API 키를 포함하므로 git 미추적(.gitignore).
# 없으면 템플릿(conf.example.yaml)에서 생성하고 키 입력을 안내한다.
if [ ! -f "$ROOT/conf.yaml" ]; then
    if [ -f "$ROOT/conf.example.yaml" ]; then
        cp "$ROOT/conf.example.yaml" "$ROOT/conf.yaml"
        echo "conf.yaml 생성됨(conf.example.yaml 복사). 'api_key'/'llm_api_key'에 OpenAI 키를 넣으세요."
    else
        echo "ERROR: conf.yaml / conf.example.yaml 둘 다 없음. 설정 파일을 준비하세요." >&2
        exit 1
    fi
fi

# Ensure frontend submodule is initialized
if [ ! -f "$UPSTREAM/frontend/index.html" ]; then
    echo "Initializing frontend submodule..."
    git -C "$UPSTREAM" submodule update --init --recursive
fi

# 프론트엔드 빌드 (dist 없을 때만)
if [ ! -f "$ROOT/web/dist/index.html" ]; then
    echo "프론트엔드 빌드 중..."
    cd "$ROOT/web"
    if [ -d "$ROOT/assets/npm_cache" ]; then
        npm install --prefer-offline --cache "$ROOT/assets/npm_cache"
    else
        npm install
    fi
    npm run build
    cd "$ROOT"
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
