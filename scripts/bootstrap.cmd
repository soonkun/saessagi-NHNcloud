@echo off
:: bootstrap.cmd — Windows entry point
:: Usage: double-click this file, or run from CMD: scripts\bootstrap.cmd

chcp 65001 > nul
cd /d "%~dp0.."

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

python scripts\bootstrap.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Bootstrap failed. Check the messages above.
    pause
    exit /b %errorlevel%
)

pause
