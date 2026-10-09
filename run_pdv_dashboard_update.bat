@echo off
setlocal
rem All paths are relative to this Warranty folder, including on Planning.
if not exist "%~dp0pdv_dashboard\refresh.py" (
  echo ERROR: PDV updater missing: "%~dp0pdv_dashboard\refresh.py"
  exit /b 1
)
for %%F in (pdv_scope.py claim_trend_page.py) do (
  if not exist "%~dp0pdv_dashboard\%%F" (
    echo ERROR: PDV companion file missing: "%~dp0pdv_dashboard\%%F"
    exit /b 1
  )
)
if not defined PYTHON_CMD (
  python --version >nul 2>&1
  if errorlevel 1 (
    py -3 --version >nul 2>&1
    if errorlevel 1 (
      echo ERROR: Python is not available for the PDV updater.
      exit /b 1
    )
    set "PYTHON_CMD=py -3"
  ) else (
    set "PYTHON_CMD=python"
  )
)
if "%PYTHON_CMD%"=="py -3" (
  py -3 "%~dp0pdv_dashboard\refresh.py" --warranty-root "%~dp0." --publish-only %*
) else (
  "%PYTHON_CMD%" "%~dp0pdv_dashboard\refresh.py" --warranty-root "%~dp0." --publish-only %*
)
exit /b %ERRORLEVEL%
