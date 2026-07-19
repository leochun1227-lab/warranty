@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0run_daily_5pm_mandt800_rejection_filter.bat" (
  echo ERROR: Missing run_daily_5pm_mandt800_rejection_filter.bat
  pause
  exit /b 1
)

set "PYTHON_CMD=python"
python --version >nul 2>&1
if errorlevel 1 (
  py -3 --version >nul 2>&1
  if errorlevel 1 (
    echo ERROR: Python was not found. Run install_requirements_mandt800_rejection_filter.bat first.
    pause
    exit /b 1
  )
  set "PYTHON_CMD=py -3"
)

if exist "%~dp0check_deployment_readiness.py" (
  if "%PYTHON_CMD%"=="py -3" (
    py -3 "%~dp0check_deployment_readiness.py"
  ) else (
    "%PYTHON_CMD%" "%~dp0check_deployment_readiness.py"
  )
  if errorlevel 1 (
    echo.
    echo Readiness check failed. Fix the items above before creating the scheduled task.
    pause
    exit /b 1
  )
)

set "TASK_CMD=%ComSpec% /d /c ""%~dp0run_daily_5pm_mandt800_rejection_filter.bat"""

schtasks /Delete /TN "CTM V44 Daily 5PM Firebase Refresh" /F >nul 2>nul
schtasks /Create /TN "CTM V44 Daily 5PM Firebase Refresh" /TR "%TASK_CMD%" /SC DAILY /ST 17:00 /F
if errorlevel 1 (
  echo.
  echo Failed to create scheduled task. Please right-click this file and choose Run as administrator.
  pause
  exit /b 1
)

echo.
echo Scheduled task created: CTM V44 Daily 5PM Firebase Refresh
echo It will run every day at 17:00 and write logs into the logs folder.
pause
