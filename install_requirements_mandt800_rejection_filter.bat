@echo off
setlocal
cd /d "%~dp0"
python -m pip install --user requests pandas pyodbc firebase-admin
pause
