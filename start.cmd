@echo off
chcp 65001 > nul
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "UPSTREAM=%ROOT%\upstream\Open-LLM-VTuber"

:: Ensure frontend submodule is initialized
if not exist "%UPSTREAM%\frontend\index.html" (
    echo Initializing frontend submodule...
    git -C "%UPSTREAM%" submodule update --init --recursive
)

:: Build frontend if dist is missing OR source is newer than the built bundle.
:: check-rebuild.mjs exits 1 when a rebuild is needed, 0 when up to date.
node "%ROOT%\web\scripts\check-rebuild.mjs"
if errorlevel 1 (
    echo Building frontend...
    cd /d "%ROOT%\web"
    if exist "%ROOT%\assets\npm_cache" (
        npm install --prefer-offline --cache "%ROOT%\assets\npm_cache"
    ) else (
        npm install
    )
    set "ELECTRON_BUILD=1" && npm run build
    cd /d "%ROOT%"
)

:: upstream 코드가 CWD에서 conf.yaml을 직접 읽으므로 복사 배치 (symlink는 관리자 권한 필요)
copy /Y "%ROOT%\conf.yaml" "%UPSTREAM%\conf.yaml" > nul 2>&1

:: 캐릭터 아바타 이미지를 upstream avatars\ 에 복사 (서빙 경로 맞춤)
copy /Y "%ROOT%\assets\character\saessagi\neutral.png" "%UPSTREAM%\avatars\saessagi.png" > nul 2>&1

:: Project root for resolving data/assets paths
set "SAESSAGI_ROOT=%ROOT%"
set "SAESSAGI_CONFIG_PATH=%ROOT%\conf.yaml"
set "PYTHONPATH=%ROOT%;%ROOT%\src;%UPSTREAM%\src;%UPSTREAM%"

echo.
echo Starting AI Assistant server...
echo Open http://127.0.0.1:12393 in your browser.
echo Press Ctrl+C to stop.
echo.

:: Run from upstream dir so frontend/, live2d-models/, model_dict.json resolve correctly
cd /d "%UPSTREAM%"
uv run --project "%ROOT%" uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
pause
