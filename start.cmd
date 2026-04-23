@echo off
chcp 65001 > nul
cd /d "%~dp0"

set ROOT=%~dp0
set UPSTREAM=%ROOT%upstream\Open-LLM-VTuber

:: Ensure frontend submodule is initialized
if not exist "%UPSTREAM%\frontend\index.html" (
    echo Initializing frontend submodule...
    git -C "%UPSTREAM%" submodule update --init --recursive
)

:: Create directory junctions so upstream's relative paths resolve from project root
:: (mklink /J does not require admin rights)
if not exist "%ROOT%frontend"      mklink /J "%ROOT%frontend"      "%UPSTREAM%\frontend"
if not exist "%ROOT%live2d-models" mklink /J "%ROOT%live2d-models" "%UPSTREAM%\live2d-models"
if not exist "%ROOT%backgrounds"   mklink /J "%ROOT%backgrounds"   "%UPSTREAM%\backgrounds"
if not exist "%ROOT%avatars"       mklink /J "%ROOT%avatars"       "%UPSTREAM%\avatars"
if not exist "%ROOT%web_tool"      mklink /J "%ROOT%web_tool"      "%UPSTREAM%\web_tool"
if not exist "%ROOT%characters"    mklink /J "%ROOT%characters"    "%UPSTREAM%\characters"

set PYTHONPATH=%ROOT%src;%UPSTREAM%\src;%UPSTREAM%

echo.
echo Starting AI Assistant server...
echo Open http://127.0.0.1:12393 in your browser.
echo Press Ctrl+C to stop.
echo.

uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393
pause
