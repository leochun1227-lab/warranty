@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0logs" mkdir "%~dp0logs"
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "LOG_STAMP=%%I"
set "LOG_FILE=%~dp0logs\ctm_daily_5pm_%LOG_STAMP%.log"

echo ==================================================>> "%LOG_FILE%"
echo CTM V44 daily 5:00 PM run started at %date% %time%>> "%LOG_FILE%"
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

set "PYTHON_CMD=python"
python --version >nul 2>&1
if errorlevel 1 (
  py -3 --version >nul 2>&1
  if errorlevel 1 (
    echo ERROR: Python was not found. Install Python 3.11+ and tick "Add python.exe to PATH".>> "%LOG_FILE%"
    exit /b 1
  )
  set "PYTHON_CMD=py -3"
)
echo Using Python command: %PYTHON_CMD%>> "%LOG_FILE%"

set "FIREBASE_SA_PATH=%~dp0firebase-service-account.json"
set "FIREBASE_DB_URL=https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"
set "SOURCE_ROOT=c4cTickets_test"
set "FIREBASE_ROOT=c4cTickets_test"
set "MONITOR_ROOT=ctmTicketStatusMonitorV44"
set "PYTHONUNBUFFERED=1"
set "SKIP_MODEL_SERIES_ASSETS_AFTER_FETCH=0"

rem The existing CTM --once flow runs Issue AI after a successful ticket refresh.
rem Its first run prepares private settings from the encrypted update package.
rem Reuse this Python/Firebase environment; no pip install or separate setup task.
if exist "%~dp0.issue-ai\bootstrap.json" echo Issue AI automatic configuration is included in this update.>> "%LOG_FILE%"

if exist "%~dp0check_deployment_readiness.py" (
  if "%PYTHON_CMD%"=="py -3" (
    py -3 "%~dp0check_deployment_readiness.py" >> "%LOG_FILE%" 2>&1
  ) else (
    "%PYTHON_CMD%" "%~dp0check_deployment_readiness.py" >> "%LOG_FILE%" 2>&1
  )
  if errorlevel 1 (
    echo ERROR: Deployment readiness check failed.>> "%LOG_FILE%"
    exit /b 1
  )
)

if "%PYTHON_CMD%"=="py -3" (
  py -3 -u "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" --once --company-file "%~dp0fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py" >> "%LOG_FILE%" 2>&1
) else (
  "%PYTHON_CMD%" -u "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" --once --company-file "%~dp0fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py" >> "%LOG_FILE%" 2>&1
)
set "ERR=%ERRORLEVEL%"

if "%ERR%"=="0" (
  echo Verifying all three dashboard publications...>> "%LOG_FILE%"
  if "%PYTHON_CMD%"=="py -3" (
    py -3 "%~dp0refresh_dashboard_snapshots.py" --check >> "%LOG_FILE%" 2>&1
  ) else (
    "%PYTHON_CMD%" "%~dp0refresh_dashboard_snapshots.py" --check >> "%LOG_FILE%" 2>&1
  )
  if errorlevel 1 set "ERR=1"
)

echo CTM V44 daily 5:00 PM run finished at %date% %time% with code %ERR%>> "%LOG_FILE%"
exit /b %ERR%
