"""Apply reviewed code patches while preserving Planning's credentials/settings."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

REPLACEABLE = {'pdv_dashboard/refresh.py', 'pdv_dashboard/claim_trend_snapshot.py',
               'pdv_dashboard/claim_trend_page.py', 'pdv_dashboard/pdv_scope.py',
               'daily_refresh_support.py', 'vehicle_summary_publication.py'}

def prepare(root, manifest):
    root = Path(root).resolve()
    planned = {}
    for name, entry in manifest['files'].items():
        target = (root / name).resolve()
        if not target.is_relative_to(root) or Path(name).is_absolute():
            raise ValueError('Unsafe package path: ' + name)
        if 'content' in entry:
            content = entry['content']
            replace = entry.get('replace_existing') and name in REPLACEABLE
            if target.exists() and not replace and target.read_text(encoding='utf-8-sig') != content:
                raise ValueError('New helper already exists with different contents: ' + name)
        else:
            content = target.read_text(encoding='utf-8-sig')
            for change in entry['changes']:
                before, after = change['before'], change['after']
                if content.count(after) == 1 and after:
                    continue
                if content.count(before) != 1 or not before:
                    raise ValueError('Installed version differs; no files changed: ' + name)
                content = content.replace(before, after, 1)
        if name.endswith('.py'):
            compile(content, str(target), 'exec')
        raw = content.replace('\n', '\r\n').encode('utf-8') if name.endswith('.bat') else content.encode('utf-8')
        planned[name] = (target, raw)
    return planned


def install(root, manifest):
    root = Path(root).resolve()
    if not (root / 'run_daily_5pm_mandt800_rejection_filter.bat').is_file():
        raise ValueError('Select the existing warranty-main folder, not a new empty folder')
    planned = prepare(root, manifest)  # Validate every patch before changing anything.
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    backup = logs / ('before_resilience_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.zip')
    with ZipFile(backup, 'x', ZIP_DEFLATED) as archive:
        for name, (target, _) in planned.items():
            if target.exists():
                archive.writestr(name, target.read_bytes())
    staged = []
    installed = []
    try:
        # Stage every file before replacing any installed code.
        for name, (target, raw) in planned.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + '.resilience.tmp')
            staged.append(temp)
            temp.write_bytes(raw)
        for (name, (target, _)), temp in zip(planned.items(), staged):
            temp.replace(target)
            installed.append((name, target))
    except Exception:
        with ZipFile(backup) as archive:
            for name, target in reversed(installed):
                if name in archive.namelist():
                    target.write_bytes(archive.read(name))
                else:
                    target.unlink(missing_ok=True)
        raise
    finally:
        for temp in staged:
            temp.unlink(missing_ok=True)
    print('PASS: repaired update code installed; existing credentials/settings retained.')
    print('Backup: ' + str(backup))
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warranty-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).with_name('code_changes.json'))
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    expected = args.manifest.with_suffix('.sha256').read_text().strip()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Package checksum mismatch')
    install(args.warranty_root, json.loads(raw))


if __name__ == '__main__':
    main()
