@echo off
:: bootstrap.cmd — Windows entry point
:: Usage: double-click this file, or run from CMD: scripts\bootstrap.cmd

chcp 65001 > nul
cd /d "%~dp0.."

:: Check Python is real (Microsoft Store stub prints "Python" and exits with error)
python -c "import sys; assert sys.version_info >= (3,11), 'Need 3.11+'" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Python 3.11+ not found.
    echo.
    echo The Microsoft Store Python stub does not count.
    echo Please install real Python 3.11 from:
    echo   https://www.python.org/downloads/
    echo.
    echo During installation, check "Add Python to PATH".
    echo.
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
