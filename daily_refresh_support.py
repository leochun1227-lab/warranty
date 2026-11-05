"""Keep independent post-fetch jobs running, with a shared per-run warning log."""
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)
_warnings = []


def start_run(root):
    _warnings.clear()
    folder = Path(root) / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix='daily_warnings_', suffix='.jsonl', dir=folder)
    os.close(fd)
    os.environ['CTM_WARNING_FILE'] = path
    return path


def run_optional_step(label, action, default=None):
    """Only use for auxiliary work; never wrap core fetching or ticket writes."""
    try:
        return action()
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            return default
        warning = {'step': label, 'error': str(exc)}
        _warnings.append(warning)
        logger.warning('[PARTIAL UPDATE] %s failed; continuing other steps: %s', label, exc)
        path = os.getenv('CTM_WARNING_FILE')
        if path:
            try:
                with open(path, 'a', encoding='utf-8') as out:
                    out.write(json.dumps(warning, ensure_ascii=False) + '\n')
            except OSError:
                logger.exception('Could not save warning report; see console log')
        return default


def run_warnings():
    warnings = list(_warnings)
    path = os.getenv('CTM_WARNING_FILE')
    if path:
        try:
            for line in Path(path).read_text(encoding='utf-8').splitlines():
                warning = json.loads(line)
                if warning not in warnings:
                    warnings.append(warning)
        except (OSError, ValueError) as exc:
            warnings.append({'step': 'Warning report', 'error': str(exc)})
    return warnings
