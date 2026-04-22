@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 새싹이 AI 비서 서버 시작 (Windows)

set "ROOT=%~dp0.."
cd /d "%ROOT%"

if not exist ".venv\Scripts\python.exe" (
    echo [start] .venv가 없습니다. install.bat를 먼저 실행하세요.
    pause
    exit /b 1
)

set "PYTHONPATH=src;upstream\Open-LLM-VTuber\src;upstream\Open-LLM-VTuber"

:: Ollama 실행 확인
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
    echo [start] Ollama 시작 중...
    start /min "" ollama serve
    timeout /t 3 /nobreak >nul
)

echo [start] 새싹이 서버 시작: http://127.0.0.1:12393
.venv\Scripts\python.exe -m uvicorn app.main:create_app ^
    --factory ^
    --host 127.0.0.1 ^
    --port 12393 ^
    --workers 1

pause
