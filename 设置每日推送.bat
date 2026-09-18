@echo off
rem Keep this file ASCII-only: cmd re-parses following lines after chcp.
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo   Register the 08:00 daily fetch + notification
echo ================================================
echo.
echo The US session that just closed is already final
echo at 08:00 Beijing time, so the notification shows
echo yesterday's move. If the PC is off or asleep at
echo 08:00, the task runs as soon as it is awake.
echo.

schtasks /create /tn "FedWatch_daily_fetch" /tr "\"%~dp0daily_fetch.bat\"" /sc daily /st 08:00 /f
if errorlevel 1 goto failed

rem Run it as soon as possible after a missed 08:00 (PC off / asleep).
powershell -NoProfile -Command "$t = Get-ScheduledTask -TaskName 'FedWatch_daily_fetch'; $t.Settings.StartWhenAvailable = $true; $t.Settings.ExecutionTimeLimit = 'PT30M'; Set-ScheduledTask -InputObject $t | Out-Null"
if errorlevel 1 echo [!] Registered, but could not turn on "run as soon as possible after a missed start".

echo.
echo [v] Registered: every day 08:00.
echo     Test now:  schtasks /run /tn "FedWatch_daily_fetch"
echo     Check:     schtasks /query /tn "FedWatch_daily_fetch"
echo     Remove:    schtasks /delete /tn "FedWatch_daily_fetch" /f
echo.
echo     Test the notification only:
echo     powershell -ExecutionPolicy Bypass -File "%~dp0notify.ps1" -Kind test
echo.
pause
exit /b 0

:failed
echo.
echo [x] Failed. Try running as administrator.
echo.
pause
exit /b 1
