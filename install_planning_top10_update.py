"""Installer embedded in the single-file Planning repair launcher."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime
from zipfile import ZipFile, ZIP_DEFLATED


def install(root, archive):
    root = Path(root).resolve()
    if not (root / 'run_daily_5pm_mandt800_rejection_filter.bat').is_file():
        raise ValueError('Place this repair BAT inside the existing warranty-main folder, then run it again.')
    manifest = json.loads(archive.read('deployment_manifest.json'))
    files = manifest['sha256']
    protected = set(manifest['requiredExistingFiles']) | {'firebase-service-account.json'}
    targets = {}
    # Validate the entire payload before writing any installed file.
    for name, checksum in files.items():
        target = (root / name).resolve()
        if (not target.is_relative_to(root) or Path(name).is_absolute()
                or name in protected or name.split('/')[0] in {'.git', '.codex', '.agents'}):
            raise ValueError('Repair contains an invalid destination')
        content = archive.read(name)
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError('Repair package checksum mismatch: ' + name)
        # Retain machine-local data; the next successful daily run regenerates it.
        if name.startswith(('outputs/', 'generated_exports/')) and target.exists():
            continue
        targets[name] = (target, content)
    if 'issue_position_bootstrap.json' not in files:
        raise ValueError('Repair package is missing its encrypted C4C credentials')
    from bootstrap_issue_ai import unseal
    from issue_positions import validate_position_config
    config = unseal(json.loads(archive.read('issue_position_bootstrap.json')),
                    root / 'firebase-service-account.json')
    validate_position_config(config)
    del config
    # Back up overwritten code/configuration without copying the Firebase private key.
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    backup = logs / ('top10_before_repair_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.zip')
    with ZipFile(backup, 'x', ZIP_DEFLATED) as saved:
        for name, (target, _) in targets.items():
            if target.is_file():
                saved.writestr(name, target.read_bytes())
    for name, (target, content) in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.top10-repair.tmp')
        temporary.write_bytes(content)
        temporary.replace(target)
    print('PASS: repair installed; encrypted credentials verified; previous files backed up.', flush=True)
    return backup


def main(archive, root):
    try:
        install(root, archive)
        result = subprocess.run([sys.executable, str(Path(root) / 'sync_issue_positions.py'),
                                 '--check-config'], cwd=root, check=False)
        if result.returncode:
            return result.returncode
        print('PASS: ready. Starting the existing full daily update, including Top10 and PDV.', flush=True)
        return 0
    except Exception as error:
        from issue_ai import IssueAIError
        message = str(error) if isinstance(error, (IssueAIError, ValueError)) else type(error).__name__
        print('ERROR: Top10 repair stopped: ' + message, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main(archive, Path(sys.argv[1]).resolve().parent))
