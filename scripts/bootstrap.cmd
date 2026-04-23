@echo off
:: bootstrap.cmd — Windows CMD에서 bootstrap.ps1을 실행하는 래퍼
:: 사용: 이 파일을 더블클릭하거나 cmd에서 scripts\bootstrap.cmd 실행

echo.
echo === AI 비서 프로젝트 부트스트랩 ===
echo.

:: PowerShell이 설치되어 있는지 확인
where powershell >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] PowerShell을 찾을 수 없습니다. Windows PowerShell을 설치하세요.
    pause
    exit /b 1
)

:: 스크립트 위치 기준으로 프로젝트 루트 계산
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."

:: PowerShell 실행 정책 우회(-ExecutionPolicy Bypass)로 bootstrap.ps1 실행
powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%bootstrap.ps1"

if %errorlevel% neq 0 (
    echo.
    echo [오류] 부트스트랩이 실패했습니다. 위 메시지를 확인하세요.
    pause
    exit /b %errorlevel%
)

pause
