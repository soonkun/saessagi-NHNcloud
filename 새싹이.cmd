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

:: Ensure Ollama (LLM server) is running. The backend needs it on startup, or it
:: aborts with "Ollama unreachable". If Ollama is down, start it and wait (max ~30s).
curl -sf http://127.0.0.1:11434/api/version > nul 2>&1
if not errorlevel 1 goto OLLAMA_READY
echo Ollama is not running. Starting Ollama...
start "Ollama" /min cmd /c "ollama serve"
set "_OLLAMA_TRIES=0"
:WAIT_OLLAMA
timeout /t 1 /nobreak > nul
curl -sf http://127.0.0.1:11434/api/version > nul 2>&1
if not errorlevel 1 goto OLLAMA_READY
set /a _OLLAMA_TRIES+=1
if %_OLLAMA_TRIES% geq 30 (
    echo [Warning] Ollama did not respond within 30s. Continuing anyway...
    goto OLLAMA_READY
)
goto WAIT_OLLAMA
:OLLAMA_READY
echo Ollama ready.

:: Kill any leftover backend still holding port 12393 from a previous session.
:: (The backend window stays open after closing the app; relaunching otherwise
::  fails with WinError 10048 "port in use" and the new backend shuts down.)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "127.0.0.1:12393" ^| findstr "LISTENING"') do (
    echo Stopping stale backend on port 12393 ^(PID %%P^)...
    taskkill /PID %%P /F > nul 2>&1
)

:: Launch backend in a separate window
start "Saessagi Backend" cmd /c "cd /d "%UPSTREAM%" && uv run --project "%ROOT%" uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393 & pause"

:: Wait for backend to be ready (bounded so a dead backend won't hang forever).
echo Waiting for backend...
set "_BE_TRIES=0"
:WAIT_LOOP
timeout /t 1 /nobreak > nul
curl -sf http://127.0.0.1:12393/ > nul 2>&1
if not errorlevel 1 goto BACKEND_READY
set /a _BE_TRIES+=1
if %_BE_TRIES% geq 180 (
    echo.
    echo [Error] Backend did not become ready within 180s.
    echo Check the "Saessagi Backend" window for the error message.
    pause
    exit /b 1
)
goto WAIT_LOOP
:BACKEND_READY

echo Backend ready. Launching app...

:: Launch Electron app
cd /d "%ROOT%\frontend"
start "" npm run start
