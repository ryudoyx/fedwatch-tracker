@echo off
rem Keep this file ASCII-only: cmd re-parses following lines after chcp.
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo   Register daily fetch (weekdays 15:40)
echo ================================================
echo.
echo Yahoo drops a futures contract once it expires,
echo so the archive only grows if this runs regularly.
echo Each run re-fetches 3 months, so a few missed
echo days are filled in automatically.
echo.

schtasks /create /tn "FedWatch_daily_fetch" /tr "\"%~dp0daily_fetch.bat\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:40 /f

if errorlevel 1 (
    echo.
    echo [x] Failed. Try running as administrator.
) else (
    echo.
    echo [v] Registered.
    echo     Check:  schtasks /query /tn "FedWatch_daily_fetch"
    echo     Remove: schtasks /delete /tn "FedWatch_daily_fetch" /f
)
echo.
pause
