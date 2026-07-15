#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

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

# 프론트엔드 빌드 (dist 없을 때만)
if [ ! -f "$ROOT/web/dist/index.html" ]; then
    echo "프론트엔드 빌드 중..."
    cd "$ROOT/web"
    if [ -d "$ROOT/assets/npm_cache" ]; then
        npm install --prefer-offline --cache "$ROOT/assets/npm_cache"
    else
        npm install
    fi
    ELECTRON_BUILD=1 npm run build
    cd "$ROOT"
fi

# Project root for resolving data/assets paths (PathsConfig reads this)
# CR-17: vendor/ 벤더링 — upstream 클론 불필요
export SAESSAGI_ROOT="$ROOT"
export SAESSAGI_CONFIG_PATH="$ROOT/conf.yaml"
export PYTHONPATH="$ROOT:$ROOT/src:$ROOT/vendor"

echo ""
echo "Starting AI Assistant server..."
echo "Open http://127.0.0.1:12393 in your browser."
echo "Press Ctrl+C to stop."
echo ""

# 프로젝트 루트에서 실행 (model_dict.json·conf.yaml·characters/ 가 루트에 있음)
cd "$ROOT"
uv run --project "$ROOT" uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
