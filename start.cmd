@echo off
chcp 65001 > nul

:: Project root = folder where this file lives
set ROOT=%~dp0

:: Ensure frontend submodule is initialized
if not exist "%ROOT%upstream\Open-LLM-VTuber\frontend\index.html" (
    echo Initializing frontend submodule...
    git -C "%ROOT%upstream\Open-LLM-VTuber" submodule update --init --recursive
)

:: Config path (absolute) -- server reads this from env var
set SAESSAGI_CONFIG_PATH=%ROOT%conf.yaml

:: PYTHONPATH: project src + upstream src
set PYTHONPATH=%ROOT%src;%ROOT%upstream\Open-LLM-VTuber\src;%ROOT%upstream\Open-LLM-VTuber

echo.
echo Starting AI Assistant server...
echo Open http://127.0.0.1:12393 in your browser.
echo Press Ctrl+C to stop.
echo.

:: Run from upstream dir so relative paths (frontend/, live2d-models/ etc.) resolve correctly
cd /d "%ROOT%upstream\Open-LLM-VTuber"
"%ROOT%.venv\Scripts\python.exe" -m uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393

pause
