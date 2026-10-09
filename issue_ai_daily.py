"""Optional planning-PC hook; no secret/config dependency for existing installations."""
import os
import subprocess
import sys
from pathlib import Path


def run_after_daily_fetch(source_root, db_url, sa_path):
    from bootstrap_issue_ai import ensure_private_configuration
    from issue_ai import load_config
    from issue_positions import position_enabled
    result=ensure_private_configuration(sa_path)
    if result=='created':
        print('[ISSUE POSITION] Private settings prepared automatically for the daily-task account.',flush=True)
    config=load_config()
    if not position_enabled(config):
        print('[ISSUE POSITION] Not enabled for this Windows account; core daily refresh is unchanged.',flush=True)
        return
    root=Path(__file__).resolve().parent
    command=[sys.executable,str(root/'sync_issue_positions.py'),'--publish',
        '--env-file',config['envPath'],'--source-root',source_root,
        '--firebase-db-url',db_url,'--firebase-sa-path',sa_path]
    result=subprocess.run(command,cwd=root,env=os.environ.copy(),check=False)
    if result.returncode:
        raise RuntimeError('Core daily refresh completed, but Issue Position sync failed; inspect its separate status/log. No C4C writes occurred.')
