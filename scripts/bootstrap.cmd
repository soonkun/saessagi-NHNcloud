@echo off
:: bootstrap.cmd — Windows entry point
:: Usage: double-click this file, or run from CMD: scripts\bootstrap.cmd

chcp 65001 > nul
cd /d "%~dp0.."

:: Corporate SSL proxy: trust Windows certificate store
set UV_NATIVE_TLS=1

:: uv is required -- it manages Python automatically (no manual Python install needed)
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] uv not found. Install uv first by running this in PowerShell:
    echo   powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo Then open a new CMD window and run scripts\bootstrap.cmd again.
    echo.
    pause
    exit /b 1
)

:: uv run --python 3.11 downloads Python 3.11 automatically if not installed
uv run --python 3.12 python scripts\bootstrap.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Bootstrap failed. Check the messages above.
    pause
    exit /b %errorlevel%
)

pause
