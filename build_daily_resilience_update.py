"""Package only code changes; never include machine credentials or generated data."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
FILES = (
    'fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py',
    'ctm_v44_history_safe_mandt800_rejection_filter.py',
    'export_vehicle_failure_timing_2025_2026.py', 'check_deployment_readiness.py',
    'run_daily_5pm_mandt800_rejection_filter.bat',
    'pdv_dashboard/refresh.py', 'pdv_dashboard/claim_trend_snapshot.py',
    'pdv_dashboard/claim_trend_page.py',
    'sync_issue_positions.py',
)
HELPERS = ('daily_refresh_support.py', 'vehicle_summary_publication.py')
REPLACEABLE = {'pdv_dashboard/refresh.py', 'pdv_dashboard/claim_trend_snapshot.py',
               'pdv_dashboard/claim_trend_page.py', 'pdv_dashboard/pdv_scope.py'}


def build_manifest():
    files = {}
    for name in FILES:
        if name in REPLACEABLE:
            files[name] = {'content': (ROOT/name).read_text(encoding='utf-8-sig'), 'replace_existing': True}
            continue
        before = subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=ROOT).decode('utf-8-sig').replace('\r\n', '\n').splitlines(keepends=True)
        after = (ROOT / name).read_text(encoding='utf-8-sig').splitlines(keepends=True)
        changes = []
        for group in difflib.SequenceMatcher(None, before, after, autojunk=False).get_grouped_opcodes(3):
            old = ''.join(before[group[0][1]:group[-1][2]])
            new = ''.join(after[group[0][3]:group[-1][4]])
            # Defensive check: source configuration must never become package content.
            if any(token in old + new for token in ('PWD=', 'C4C_PASSWORD", "', 'C4C_USERNAME", "')):
                raise ValueError('Patch touches private configuration: ' + name)
            changes.append({'before': old, 'after': new})
        files[name] = {'changes': changes}
    for name in set(HELPERS) | REPLACEABLE:
        files[name] = {'content': (ROOT/name).read_text(encoding='utf-8-sig'), 'replace_existing': True}
    return {'files': files}


def main():
    manifest = json.dumps(build_manifest(), ensure_ascii=False, indent=2).encode('utf-8')
    destination = ROOT / 'dist/daily-resilience-update-v2.zip'
    destination.parent.mkdir(exist_ok=True)
    launcher = '''@echo off
setlocal
cd /d "%~dp0"
set "TARGET=%~dp0.."
if not exist "%TARGET%\\run_daily_5pm_mandt800_rejection_filter.bat" (
  echo Extract this folder INSIDE your existing warranty-main folder.
  pause
  exit /b 1
)
echo Close any running daily update before installing this repair.
python "%~dp0install_daily_resilience_update.py" --warranty-root "%TARGET%"
if errorlevel 1 (
  echo Repair not installed. Keep the error message above.
  pause
  exit /b 1
)
if /I "%~1"=="--install-only" exit /b 0
echo Starting the existing daily update. Progress is in warranty-main\\logs.
call "%TARGET%\\run_daily_5pm_mandt800_rejection_filter.bat"
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" echo SUCCESS: daily update and dashboard publication checks passed.
if "%RESULT%"=="2" echo PARTIAL: core fetch completed, but some page updates need attention. See the daily log.
if not "%RESULT%"=="0" if not "%RESULT%"=="2" echo FAILED: core fetch failed. See the daily log.
pause
exit /b %RESULT%
'''
    with ZipFile(destination, 'w', ZIP_DEFLATED) as archive:
        prefix = 'daily-resilience-update/'
        archive.writestr(prefix+'code_changes.json', manifest)
        archive.writestr(prefix+'code_changes.sha256', hashlib.sha256(manifest).hexdigest())
        archive.write(ROOT/'install_daily_resilience_update.py', prefix+'install_daily_resilience_update.py')
        archive.writestr(prefix+'INSTALL_AND_RUN.bat', launcher.replace('\n','\r\n'))
        archive.writestr(prefix+'INSTALL_ONLY.bat', '@echo off\r\ncall "%~dp0INSTALL_AND_RUN.bat" --install-only\r\nset "RESULT=%ERRORLEVEL%"\r\npause\r\nexit /b %RESULT%\r\n')
        archive.writestr(prefix+'README.txt', 'Extract daily-resilience-update inside the existing warranty-main folder.\nWhen no daily run is active, double-click INSTALL_ONLY.bat to install without running another refresh.\nUse INSTALL_AND_RUN.bat if you also want a full immediate update.\nAll patches are checked before installation; an unrecognized version is rejected.\nOriginal code is backed up in logs. Credentials and existing data are not replaced.\nThe existing Issue Position credential bundle must still be present on Planning.\nExit 0 = complete, 2 = completed with auxiliary warnings, other = core failure.\n')
    print(destination)


if __name__ == '__main__':
    main()
