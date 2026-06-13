@echo off
chcp 65001 > nul
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "UPSTREAM=%ROOT%\upstream\Open-LLM-VTuber"

:: conf.yaml auto-copy if missing
if not exist "%ROOT%\conf.yaml" (
    if exist "%ROOT%\conf.example.yaml" (
        copy "%ROOT%\conf.example.yaml" "%ROOT%\conf.yaml" > nul
    ) else (
        echo [Error] conf.example.yaml not found.
        pause
        exit /b 1
    )
)

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
    set "ELECTRON_BUILD=1"
    npm run build
    cd /d "%ROOT%"
)

:: Copy conf.yaml to upstream dir
copy /Y "%ROOT%\conf.yaml" "%UPSTREAM%\conf.yaml" > nul 2>&1

:: Copy character avatar to upstream avatars dir
copy /Y "%ROOT%\assets\character\saessagi\neutral.png" "%UPSTREAM%\avatars\saessagi.png" > nul 2>&1

:: Set environment
set "SAESSAGI_ROOT=%ROOT%"
set "SAESSAGI_CONFIG_PATH=%ROOT%\conf.yaml"
set "PYTHONPATH=%ROOT%;%ROOT%\src;%UPSTREAM%\src;%UPSTREAM%"

echo.
echo Starting Saessagi...
echo.

:: Launch backend in a separate window
start "Saessagi Backend" cmd /c "cd /d "%UPSTREAM%" && uv run --project "%ROOT%" uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393 & pause"

:: Wait for backend to be ready
echo Waiting for backend...
:WAIT_LOOP
timeout /t 1 /nobreak > nul
curl -sf http://127.0.0.1:12393/ > nul 2>&1
if errorlevel 1 goto WAIT_LOOP

echo Backend ready. Launching app...

:: Launch Electron app
cd /d "%ROOT%\frontend"
start "" npm run start
