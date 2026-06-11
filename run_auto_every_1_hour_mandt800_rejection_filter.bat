@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo CTM V44 daily 9:00 foreground runner
echo This replaces the old hourly foreground loop.
echo Keep this window open. Close it to stop the foreground runner.
echo For a true background daily task, run setup_daily_9am_task_mandt800_rejection_filter.bat instead.
echo ==================================================
echo.

:loop
for /f "tokens=1-2 delims=:" %%a in ("%time%") do (
  set /a HH=1%%a-100
  set /a MM=1%%b-100
)
set /a NOW_MIN=!HH!*60+!MM!
set /a TARGET_MIN=9*60
set /a WAIT_MIN=!TARGET_MIN!-!NOW_MIN!
if !WAIT_MIN! LEQ 0 set /a WAIT_MIN=!WAIT_MIN!+1440
set /a WAIT_SEC=!WAIT_MIN!*60

echo Next run at 09:00. Waiting !WAIT_MIN! minutes ...
timeout /t !WAIT_SEC! /nobreak >nul
call "%~dp0run_daily_9am_mandt800_rejection_filter.bat"
goto loop
