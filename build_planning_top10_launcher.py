"""Create one BAT containing the verified overlay and encrypted credential bundle."""
import base64
import io
from pathlib import Path
import zipfile
import argparse

ROOT = Path(__file__).resolve().parent


def build(package, output):
    payload = io.BytesIO()
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr('_installer.py', (ROOT / 'install_planning_top10_update.py').read_bytes())
    command = ('import sys,base64,io,zipfile;from pathlib import Path;'
               'p=Path(sys.argv[1]).resolve();sys.path.insert(0,str(p.parent));'
               'raw=p.read_bytes().split(bytes([10])+b\'::TOP10_PAYLOAD\'+bytes([10]),1)[1];'
               'archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(raw)));'
               'exec(compile(archive.read(\'_installer.py\'),\'_installer.py\',\'exec\'))')
    header = r'''@echo off
setlocal
cd /d "%~dp0"
echo Top10 Position repair - no AI. Run only when the daily task is idle.
set "REPAIR_PYTHON=python"
python --version >nul 2>&1
if errorlevel 1 set "REPAIR_PYTHON=py -3"
%REPAIR_PYTHON% -c "COMMAND" "%~f0"
if errorlevel 1 goto repair_failed
call "%~dp0run_daily_5pm_mandt800_rejection_filter.bat"
if errorlevel 1 goto update_failed
echo SUCCESS: daily update, Top10 and PDV completed. Refresh the website.
pause
exit /b 0
:repair_failed
echo Repair did not complete. The reason is shown above. Send that message for diagnosis.
pause
exit /b 1
:update_failed
echo Daily update failed. See logs\ctm_daily_5pm_YYYYMMDD.log for the failed step.
pause
exit /b 1
'''.replace('COMMAND', command)
    encoded = base64.b64encode(payload.getvalue()).decode('ascii')
    # LF delimiter is fixed and read as bytes by the launcher itself.
    Path(output).write_bytes((header + '::TOP10_PAYLOAD\n' + encoded + '\n').encode('ascii'))
    print('Single-file repair ready: ' + str(output))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('package')
    ap.add_argument('output')
    args = ap.parse_args()
    build(args.package, args.output)
