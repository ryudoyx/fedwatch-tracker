@echo off
rem Keep this file ASCII-only: cmd re-parses following lines after chcp.
chcp 65001 >nul
cd /d "%~dp0"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ================================================
echo   FedWatch tracker - first-time setup
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [x] Python not found.
    echo     Install from https://www.python.org/downloads/
    echo     IMPORTANT: tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment...
if not exist ".venv" python -m venv .venv

echo [2/3] Installing dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [x] Install failed. Check your network.
    pause
    exit /b 1
)

echo [3/3] Checking database...
if exist "data\fedwatch.sqlite" (
    echo     Database found, skipping bootstrap.
) else (
    echo     No database. Downloading history, takes about a minute...
    .venv\Scripts\python.exe -m fedwatch bootstrap
)

echo.
echo ================================================
echo   Done. From now on just double-click
echo   the dashboard start script.
echo ================================================
pause
