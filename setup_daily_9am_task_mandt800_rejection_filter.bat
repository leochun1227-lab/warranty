@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0run_daily_9am_mandt800_rejection_filter.bat" (
  echo ERROR: Missing run_daily_9am_mandt800_rejection_filter.bat
  pause
  exit /b 1
)

schtasks /Create /TN "CTM V44 Daily 9AM Firebase Refresh" /TR "\"%~dp0run_daily_9am_mandt800_rejection_filter.bat\"" /SC DAILY /ST 09:00 /F
if errorlevel 1 (
  echo.
  echo Failed to create scheduled task. Please right-click this file and choose Run as administrator.
  pause
  exit /b 1
)

echo.
echo Scheduled task created: CTM V44 Daily 9AM Firebase Refresh
echo It will run every day at 09:00 and write logs into the logs folder.
pause
