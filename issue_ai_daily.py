"""Legacy entry point for mandatory Issue Position sync; no AI calls or enable flag."""
import os
import subprocess
import sys
from pathlib import Path


def run_after_daily_fetch(source_root, db_url, sa_path):
    from issue_position_config import load_position_config
    load_position_config(sa_path)
    root=Path(__file__).resolve().parent
    command=[sys.executable,str(root/'sync_issue_positions.py'),'--publish',
        '--source-root',source_root,
        '--firebase-db-url',db_url,'--firebase-sa-path',sa_path]
    result=subprocess.run(command,cwd=root,env=os.environ.copy(),check=False)
    if result.returncode:
        raise RuntimeError('Core daily refresh completed, but Issue Position sync failed; inspect its separate status/log. No C4C writes occurred.')
