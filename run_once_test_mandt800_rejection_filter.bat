@echo off
cd /d "%~dp0"

echo ==================================================
echo CTM V44 HISTORY-SAFE one-time test
echo This runs company fetch once, then compares once.
echo Existing history/unprocessed logs will NOT be deleted.
echo ==================================================
echo.

if not exist "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" (
  echo ERROR: Missing ctm_v44_history_safe_mandt800_rejection_filter.py
  pause
  exit /b 1
)

if not exist "%~dp0fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py" (
  echo ERROR: Missing fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py
  echo Put your company fetch file in this folder and rename it exactly:
  echo fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py
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

python "%~dp0ctm_v44_history_safe_mandt800_rejection_filter.py" --once --company-file "%~dp0fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py"

echo.
echo One-time test finished.
pause
