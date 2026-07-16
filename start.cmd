@echo off
chcp 65001 > nul
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

:: conf.yaml auto-copy if missing (API 키 포함이라 git 미추적)
if not exist "%ROOT%\conf.yaml" (
    if exist "%ROOT%\conf.example.yaml" (
        copy "%ROOT%\conf.example.yaml" "%ROOT%\conf.yaml" > nul
        echo conf.yaml created from conf.example.yaml. Put your API key in it if using OpenAI.
    ) else (
        echo [Error] conf.example.yaml not found.
        pause
        exit /b 1
    )
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
    set "ELECTRON_BUILD=1"
    npm run build
    cd /d "%ROOT%"
)

:: Set environment (CR-17: vendor/ 벤더링 — upstream 클론 불필요)
set "SAESSAGI_ROOT=%ROOT%"
set "SAESSAGI_CONFIG_PATH=%ROOT%\conf.yaml"
set "PYTHONPATH=%ROOT%;%ROOT%\src;%ROOT%\vendor"

echo.
echo Starting AI Assistant server (backend only)...
echo Use 새싹이.cmd to launch the full Electron app.
echo Press Ctrl+C to stop.
echo.

cd /d "%ROOT%"
uv run --project "%ROOT%" uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
pause
