"""Credentials for mandatory, direct Issue Position sync. No AI settings needed."""
import json
from pathlib import Path

from bootstrap_issue_ai import unseal
from issue_ai import ROOT, IssueAIError, load_config
from issue_positions import validate_position_config


def load_position_config(service_account, env_file=None, *, root=None):
    root = Path(root or ROOT)
    existing = load_config(env_file)
    keys = ('C4C_ISSUE_USERNAME', 'C4C_ISSUE_PASSWORD')
    config = {key: existing.get(key, '') for key in keys}
    if not all(config.values()):
        # The visible filename survives copying a deployment folder on Windows.
        bundle = next((p for p in (root / 'issue_position_bootstrap.json',
                                   root / '.issue-ai/bootstrap.json') if p.is_file()), None)
        if bundle:
            packaged = unseal(json.loads(bundle.read_text(encoding='utf-8')), service_account)
            # Use a complete credential pair, never mix accounts.
            config = {key: packaged.get(key, '') for key in keys}
        else:
            raise IssueAIError('Top10 credentials missing: copy issue_position_bootstrap.json '
                               'from the Planning update package. Top10 was NOT updated.')
    validate_position_config(config)
    return config
