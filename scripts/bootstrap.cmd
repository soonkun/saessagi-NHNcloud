@echo off
:: bootstrap.cmd — Wrapper to run bootstrap.ps1 from Windows CMD
:: Usage: double-click this file or run scripts\bootstrap.cmd in CMD

:: UTF-8 codepage
chcp 65001 > nul

:: Move to project root (parent of scripts\)
cd /d "%~dp0.."

:: Trust corporate SSL proxy certificates via Windows cert store
set UV_NATIVE_TLS=1

echo.
echo === AI Assistant Bootstrap ===
echo.

where powershell >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PowerShell not found.
    pause
    exit /b 1
)

powershell -ExecutionPolicy Bypass -File "scripts\bootstrap.ps1"

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Bootstrap failed. Check the messages above.
    pause
    exit /b %errorlevel%
)

pause
