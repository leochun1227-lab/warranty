@echo off
cd /d "%~dp0"

echo ==================================================
echo CTM V44 DANGEROUS: manual baseline reset - KEEP HISTORY
echo This refreshes currentStatus baseline only and creates ZERO change events.
echo Do NOT run this for daily refresh or dashboard rebuild.
echo Normal daily/test runs should use run_once_test_mandt800_rejection_filter.bat
echo or run_daily_5pm_mandt800_rejection_filter.bat instead.
echo It does NOT delete historical logs or unprocessed records.
echo ==================================================
echo.

set /p CONFIRM=Type RESET BASELINE to continue, or press Enter to cancel: 
if /I not "%CONFIRM%"=="RESET BASELINE" (
  echo Cancelled. Baseline was NOT changed.
  pause
  exit /b 0
)

if not exist "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" (
  echo ERROR: Missing ctm_v44_history_safe_mandt800_rejection_filter.py
  pause
  exit /b 1
)

if not exist "%~dp0firebase-service-account.json" (
  echo ERROR: Missing firebase-service-account.json
  pause
  exit /b 1
)

set "FIREBASE_SA_PATH=%~dp0firebase-service-account.json"

python "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" --reset-baseline --confirm-reset-baseline

echo.
echo Baseline reset finished. History was preserved.
pause
