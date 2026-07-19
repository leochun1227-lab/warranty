@echo off
cd /d "%~dp0"

set FIREBASE_SA_PATH=%CD%\firebase-service-account.json
set FIREBASE_DB_URL=https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app
set SOURCE_ROOT=c4cTickets_test
set MONITOR_ROOT=ctmTicketStatusMonitorV44

set "PYTHON_CMD=python"
python --version >nul 2>&1
if errorlevel 1 (
  py -3 --version >nul 2>&1
  if errorlevel 1 (
    echo ERROR: Python was not found. Install Python 3.11+ and tick "Add python.exe to PATH".
    pause
    exit /b 1
  )
  set "PYTHON_CMD=py -3"
)

if "%PYTHON_CMD%"=="py -3" (
  py -3 ctm_v44_history_safe_mandt800_rejection_filter.py --rebuild-analytics-only
) else (
  "%PYTHON_CMD%" ctm_v44_history_safe_mandt800_rejection_filter.py --rebuild-analytics-only
)
if errorlevel 1 (
  echo.
  echo Analytics rebuild FAILED. Please copy the full error and send it to ChatGPT.
  pause
  exit /b 1
)

echo.
echo Analytics rebuild finished. Refresh index.html / dealer-workbench.html / employee-workbench.html.
pause
