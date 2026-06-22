@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0run_daily_5pm_mandt800_rejection_filter.bat" (
  echo ERROR: Missing run_daily_5pm_mandt800_rejection_filter.bat
  pause
  exit /b 1
)

set "TASK_CMD=%ComSpec% /d /c ""%~dp0run_daily_5pm_mandt800_rejection_filter.bat"""

schtasks /Delete /TN "CTM V44 Daily 9AM Firebase Refresh" /F >nul 2>nul
schtasks /Delete /TN "CTM V44 Daily 5PM Firebase Refresh" /F >nul 2>nul
schtasks /Create /TN "CTM V44 Daily 12PM Firebase Refresh" /TR "%TASK_CMD%" /SC DAILY /ST 12:00 /F
if errorlevel 1 (
  echo.
  echo Failed to create scheduled task. Please right-click this file and choose Run as administrator.
  pause
  exit /b 1
)

echo.
echo Scheduled task created: CTM V44 Daily 12PM Firebase Refresh
echo It will run every day at 12:00 and write logs into the logs folder.
pause
