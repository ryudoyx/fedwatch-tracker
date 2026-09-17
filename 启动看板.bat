@echo off
rem Keep this file ASCII-only: cmd re-parses following lines after chcp.
chcp 65001 >nul
cd /d "%~dp0"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

echo Starting FedWatch dashboard...
echo Browser will open automatically. Close this window to stop.
echo.

rem Extra args pass through, e.g. --port 8767 / --no-browser
%PY% -m fedwatch serve %*

if errorlevel 1 (
    echo.
    echo ================================================
    echo  Failed to start. See the error above.
    echo  If a module is missing, run the install script.
    echo ================================================
    pause
)
