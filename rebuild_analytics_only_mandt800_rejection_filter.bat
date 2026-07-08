@echo off
cd /d "%~dp0"

set FIREBASE_SA_PATH=%CD%\firebase-service-account.json
set FIREBASE_DB_URL=https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app
set SOURCE_ROOT=c4cTickets_test
set MONITOR_ROOT=ctmTicketStatusMonitorV44

python ctm_v44_history_safe_mandt800_rejection_filter.py --rebuild-analytics-only
if errorlevel 1 (
  echo.
  echo Analytics rebuild FAILED. Please copy the full error and send it to ChatGPT.
  pause
  exit /b 1
)

echo.
echo Analytics rebuild finished. Refresh index.html / dealer-workbench.html / employee-workbench.html.
pause
