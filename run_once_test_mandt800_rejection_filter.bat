@echo off
cd /d "%~dp0"

echo ==================================================
echo CTM V44 HISTORY-SAFE dashboard compare/rebuild test
echo This compares current Firebase tickets, writes critical history changes,
echo then rebuilds dashboard analytics. It does NOT run company fetch.
echo Existing history/unprocessed logs will NOT be deleted.
echo ==================================================
echo.

if not exist "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" (
  echo ERROR: Missing ctm_v44_history_safe_mandt800_rejection_filter.py
  pause
  exit /b 1
)

if not exist "%~dp0firebase-service-account.json" (
  echo ERROR: Missing firebase-service-account.json
  echo Copy your Firebase private key JSON into this folder and rename it exactly:
  echo firebase-service-account.json
  pause
  exit /b 1
)

set "FIREBASE_SA_PATH=%~dp0firebase-service-account.json"
set "FIREBASE_DB_URL=https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"
set "SOURCE_ROOT=c4cTickets_test"
set "FIREBASE_ROOT=c4cTickets_test"
set "MONITOR_ROOT=ctmTicketStatusMonitorV44"
set "PYTHONUNBUFFERED=1"

python -u "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" --rebuild-analytics-only
if errorlevel 1 (
  echo.
  echo Dashboard compare/rebuild test FAILED. Please copy the full error and send it to ChatGPT.
  pause
  exit /b 1
)

echo.
echo Dashboard compare/rebuild test finished. Refresh index.html.
pause
