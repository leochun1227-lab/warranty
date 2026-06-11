@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0logs" mkdir "%~dp0logs"
set "LOG_FILE=%~dp0logs\ctm_daily_9am_%date:~10,4%%date:~4,2%%date:~7,2%.log"

echo ==================================================>> "%LOG_FILE%"
echo CTM V44 daily 9:00 run started at %date% %time%>> "%LOG_FILE%"
echo ==================================================>> "%LOG_FILE%"

if not exist "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" (
  echo ERROR: Missing ctm_v44_history_safe_mandt800_rejection_filter.py>> "%LOG_FILE%"
  exit /b 1
)
if not exist "%~dp0fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py" (
  echo ERROR: Missing fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py>> "%LOG_FILE%"
  exit /b 1
)
if not exist "%~dp0firebase-service-account.json" (
  echo ERROR: Missing firebase-service-account.json>> "%LOG_FILE%"
  exit /b 1
)

set "FIREBASE_SA_PATH=%~dp0firebase-service-account.json"
set "FIREBASE_DB_URL=https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"
set "SOURCE_ROOT=c4cTickets_test"
set "MONITOR_ROOT=ctmTicketStatusMonitorV44"

python "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" --once --company-file "%~dp0fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py" >> "%LOG_FILE%" 2>&1
set "ERR=%ERRORLEVEL%"

echo CTM V44 daily 9:00 run finished at %date% %time% with code %ERR%>> "%LOG_FILE%"
exit /b %ERR%
