@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 새싹이 AI 비서 — Windows 설치 스크립트
:: USB에서 실행: 탐색기에서 install.bat 더블클릭

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   새싹이 AI 비서 설치 (Windows)          ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ── 경로 설정 ──────────────────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
set "USB_ROOT=%SCRIPT_DIR%.."
if not defined SAESSAGI_INSTALL_DIR (
    set "INSTALL_DIR=%USERPROFILE%\saessagi"
) else (
    set "INSTALL_DIR=%SAESSAGI_INSTALL_DIR%"
)

echo [install] 설치 경로: %INSTALL_DIR%

:: ── 1. 관리자 권한 확인 ────────────────────────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo [install] 관리자 권한으로 실행하는 것을 권장합니다.
    echo [install] 계속 진행합니다 (일부 기능이 제한될 수 있음)
)

:: ── 2. Python 3.12 확인 / 설치 ────────────────────────────────────────────
echo [install] Python 확인 중...
python --version 2>nul | findstr "3.12" >nul
if errorlevel 1 (
    echo [install] Python 3.12가 없습니다. USB에서 설치합니다...
    set "PY_INSTALLER=%USB_ROOT%\windows\python-3.12-amd64.exe"
    if exist "!PY_INSTALLER!" (
        echo [install] Python 설치 중...
        "!PY_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
        timeout /t 15 /nobreak >nul
        echo [install] Python 설치 완료
    ) else (
        echo [install] 오류: USB에 Python 설치 파일이 없습니다.
        echo [install] python.org에서 Python 3.12를 설치 후 다시 실행하세요.
        pause
        exit /b 1
    )
    :: PATH 갱신
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
)
for /f "tokens=*" %%i in ('python -c "import sys; print(sys.executable)"') do set "PYTHON_EXE=%%i"
echo [install] ✓ Python: %PYTHON_EXE%

:: ── 3. Ollama 확인 / 설치 ─────────────────────────────────────────────────
echo [install] Ollama 확인 중...
where ollama >nul 2>&1
if errorlevel 1 (
    set "OLLAMA_SETUP=%USB_ROOT%\ollama\OllamaSetup.exe"
    if exist "!OLLAMA_SETUP!" (
        echo [install] Ollama 설치 중...
        "!OLLAMA_SETUP!" /S
        timeout /t 20 /nobreak >nul
        set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
        echo [install] ✓ Ollama 설치 완료
    ) else (
        echo [install] 경고: USB에 Ollama가 없습니다. 나중에 수동 설치 필요.
    )
) else (
    echo [install] ✓ Ollama 이미 설치됨
)

:: ── 4. 소스 코드 복사 ─────────────────────────────────────────────────────
echo [install] 소스 코드 복사 중...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
robocopy "%USB_ROOT%\shared\ai-assistant" "%INSTALL_DIR%" /E /NFL /NDL /NJH /NJS ^
    /XD .venv __pycache__ .git data cache /XF *.pyc >nul
echo [install] ✓ 소스 코드 복사 완료

:: ── 5. 모델 파일 복사 ─────────────────────────────────────────────────────
if exist "%USB_ROOT%\shared\models" (
    echo [install] AI 모델 복사 중... (수 분 소요)
    if not exist "%INSTALL_DIR%\assets\models" mkdir "%INSTALL_DIR%\assets\models"
    robocopy "%USB_ROOT%\shared\models" "%INSTALL_DIR%\assets\models" /E /NFL /NDL /NJH /NJS >nul
    echo [install] ✓ 모델 복사 완료
) else (
    echo [install] 경고: USB에 모델 파일이 없습니다.
)

:: ── 6. 가상환경 생성 ──────────────────────────────────────────────────────
cd /d "%INSTALL_DIR%"
echo [install] 가상환경 생성 중...
if exist ".venv" rmdir /s /q ".venv"
python -m venv .venv
set "PIP=%INSTALL_DIR%\.venv\Scripts\pip.exe"
set "PYEXE=%INSTALL_DIR%\.venv\Scripts\python.exe"
"%PIP%" install --upgrade pip --quiet
echo [install] ✓ 가상환경 생성 완료

:: ── 7. 패키지 설치 ────────────────────────────────────────────────────────
echo [install] 패키지 설치 중... (수 분~십수 분 소요)

:: GPU 감지 (RTX 등 CUDA 가능 여부)
set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
nvidia-smi >nul 2>&1
if not errorlevel 1 (
    echo [install] NVIDIA GPU 감지 — CUDA 버전 설치
    set "TORCH_INDEX=https://download.pytorch.org/whl/cu121"
    set "WHEELS_DIR=%USB_ROOT%\windows\wheels\cuda"
) else (
    echo [install] CPU 전용 모드
    set "WHEELS_DIR=%USB_ROOT%\windows\wheels\cpu"
)

:: 로컬 wheels → 없으면 PyPI
if exist "%WHEELS_DIR%" (
    "%PIP%" install --find-links "%WHEELS_DIR%" ^
        --requirement "%INSTALL_DIR%\deploy\requirements.txt" ^
        --quiet
) else (
    echo [install] 경고: 로컬 wheels 없음 — 인터넷에서 다운로드 시도
    "%PIP%" install ^
        --requirement "%INSTALL_DIR%\deploy\requirements.txt" ^
        --extra-index-url "%TORCH_INDEX%" ^
        --quiet
)

:: MeloTTS
"%PYEXE%" -c "import melo" 2>nul
if errorlevel 1 (
    echo [install] MeloTTS 설치 중...
    for %%f in ("%USB_ROOT%\windows\wheels\melo*.whl") do (
        "%PIP%" install --no-deps "%%f" --quiet
        goto melo_done
    )
    "%PIP%" install melo --no-deps --quiet
    :melo_done
)

:: Windows 전용 한국어 MeCab (eunjeon)
echo [install] eunjeon (Windows 한국어 형태소 분석기) 설치 중...
"%PIP%" install eunjeon --quiet 2>nul || (
    echo [install] 경고: eunjeon 설치 실패. MeloTTS 한국어 합성이 제한될 수 있음.
)

echo [install] ✓ 패키지 설치 완료

:: ── 8. post_install ────────────────────────────────────────────────────────
echo [install] 후처리 적용 중...
"%PYEXE%" "%INSTALL_DIR%\deploy\post_install.py"
echo [install] ✓ 후처리 완료

:: ── 9. Ollama 모델 로드 ───────────────────────────────────────────────────
if exist "%USB_ROOT%\ollama\models" (
    echo [install] Ollama 모델 복사 중...
    set "OLLAMA_MODEL_DIR=%USERPROFILE%\.ollama\models"
    if not exist "!OLLAMA_MODEL_DIR!" mkdir "!OLLAMA_MODEL_DIR!"
    robocopy "%USB_ROOT%\ollama\models" "!OLLAMA_MODEL_DIR!" /E /NFL /NDL /NJH /NJS >nul
    echo [install] ✓ Ollama 모델 복사 완료
) else (
    echo [install] 경고: Ollama 모델이 없습니다. 나중에 'ollama pull gemma4:e2b' 실행 필요.
)

:: ── 10. 바탕화면 바로가기 생성 ────────────────────────────────────────────
echo [install] 바탕화면 바로가기 생성 중...
set "SHORTCUT=%USERPROFILE%\Desktop\새싹이 AI비서.lnk"
powershell -NoProfile -Command ^
    "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
    "$s.TargetPath='%INSTALL_DIR%\deploy\start.bat';" ^
    "$s.WorkingDirectory='%INSTALL_DIR%';" ^
    "$s.Description='새싹이 AI 비서 서버 시작';" ^
    "$s.Save()"
echo [install] ✓ 바탕화면 바로가기 생성 완료

:: ── 완료 ─────────────────────────────────────────────────────────────────
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   새싹이 AI 비서 설치 완료               ║
echo  ╠══════════════════════════════════════════╣
echo  ║  설치 경로: %INSTALL_DIR%
echo  ║  실행: 바탕화면 "새싹이 AI비서" 더블클릭
echo  ╚══════════════════════════════════════════╝
echo.
pause
