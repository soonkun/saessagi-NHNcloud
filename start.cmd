@echo off
chcp 65001 > nul
cd /d "%~dp0"

set PYTHONPATH=src;upstream/Open-LLM-VTuber/src;upstream/Open-LLM-VTuber

:: Ensure frontend submodule is initialized
if not exist "upstream\Open-LLM-VTuber\frontend\index.html" (
    echo Initializing frontend submodule...
    git -C upstream\Open-LLM-VTuber submodule update --init --recursive
)

echo.
echo Starting AI Assistant server...
echo Open http://127.0.0.1:12393 in your browser.
echo Press Ctrl+C to stop.
echo.

uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
pause
