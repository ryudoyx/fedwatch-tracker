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

if not exist "logs" mkdir logs

for /f %%m in ('powershell -NoProfile -Command "Get-Date -Format yyyyMM"') do set YM=%%m
set LOG=logs\daily_%YM%.log
echo. >> "%LOG%"
echo ===== %date% %time% ===== >> "%LOG%"
%PY% -m fedwatch daily >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo [%time%] exit code %RC% >> "%LOG%"

rem 2 = some data missing, 1 = crashed. Pop a warning either way.
rem notify.ps1 does not depend on Python on purpose.
if not "%RC%"=="0" (
    powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0notify.ps1" -Kind fetch_failed -Log "%~dp0%LOG%"
    exit /b %RC%
)

rem Morning digest: exit code 3 means nothing new (weekend / already notified).
set DIGEST=%TEMP%\fedwatch_digest.txt
%PY% -m fedwatch digest --file "%DIGEST%" >> "%LOG%" 2>&1
set DRC=%ERRORLEVEL%
echo [%time%] digest exit code %DRC% >> "%LOG%"
if "%DRC%"=="0" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0notify.ps1" -Kind digest -File "%DIGEST%"
exit /b 0
