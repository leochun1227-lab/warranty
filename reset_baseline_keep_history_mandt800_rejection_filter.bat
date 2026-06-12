@echo off
cd /d "%~dp0"

echo ==================================================
echo CTM V44 reset baseline - KEEP HISTORY
echo This refreshes currentStatus baseline only.
echo It does NOT delete historical logs or unprocessed records.
echo ==================================================
echo.

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

python "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" --reset-baseline

echo.
echo Baseline reset finished. History was preserved.
pause
