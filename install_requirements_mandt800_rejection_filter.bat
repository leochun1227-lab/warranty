@echo off
setlocal
cd /d "%~dp0"

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

echo Using Python command: %PYTHON_CMD%
if "%PYTHON_CMD%"=="py -3" (
  py -3 -m pip install --user --upgrade pip
  if errorlevel 1 goto failed
  py -3 -m pip install --user -r "%~dp0requirements.txt"
  if errorlevel 1 goto failed
  py -3 "%~dp0check_deployment_readiness.py"
) else (
  "%PYTHON_CMD%" -m pip install --user --upgrade pip
  if errorlevel 1 goto failed
  "%PYTHON_CMD%" -m pip install --user -r "%~dp0requirements.txt"
  if errorlevel 1 goto failed
  "%PYTHON_CMD%" "%~dp0check_deployment_readiness.py"
)

if errorlevel 1 goto failed

echo.
echo Install and readiness check finished.
pause
exit /b 0

:failed
echo.
echo Install or readiness check FAILED. Check the messages above.
pause
exit /b 1
