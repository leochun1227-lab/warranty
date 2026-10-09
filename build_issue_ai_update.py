"""Build the private planning update; never package plaintext credentials."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import zipfile

from bootstrap_issue_ai import seal, unseal
from configure_issue_ai import collection_credentials
from issue_ai import ROOT, IssueAIError, read_env, utc_now
from check_deployment_readiness import REQUIRED_FILES

# This is an overlay for the already working Planning installation. Preserve
# machine credentials, the configured SAP fetcher, and its existing data files.
BASE_FILES = {
    'firebase-service-account.json',
    'fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py',
    'outputs/parts_classified_meta.json',
    'outputs/analysis_parts_failure_light.json',
    'outputs/analysis_parts_derived_cache.json',
    'generated_exports/ticket_timeline_completion_analytics_2026.js',
    'generated_exports/ticket_timeline_summary.js',
    'generated_exports/ticket_timeline_summary_2026.js',
    'node_modules/lucide/dist/umd/lucide.js',
}

FILES = ['run_daily_5pm_mandt800_rejection_filter.bat',
         'parts.html','parts-subcategories.css','parts-subcategories.js','sidebar-light.css','browser-page-cache.js',
         'build_issue_startup.py','build_issue_exports.py','build_failure_reporting.py','issue_claim_source.py','parts-export.js','outputs/issue_subcategory_startup.json',
         'index.html','analysis.html','delivery_flow.html','repairs.html','infieldpredelivery.html','ticketlifecycle.html',
         'dealerworkbench.html','employee-workbench.html','employeeworkbench.html','temp-interface.html',
         'bootstrap_issue_ai.py','issue_ai.py','issue_ai_daily.py',
         'sync_issue_subcategories.py','sync_issue_positions.py','issue_positions.py','configure_issue_ai.py','issue_subcategories.json',
         'issue_ai.env.example','issue-subcategory-data.js','PLANNING_ISSUE_AI_SETUP.txt',
         'ctm_v44_history_safe_mandt800_rejection_filter.py','check_deployment_readiness.py',
         'tests/test_issue_ai.py','tests/test_issue_ai_bootstrap.py',
         'tests/test_issue_startup.py','tests/test_issue_exports.py','tests/test_issue_reporting.py','tests/test_issue_positions.py','tests/test_issue_claim_source.py',
         'tests/issue-subcategory-data.test.cjs','tests/parts-subcategory-browser.mjs','.gitignore']

# Include every non-private readiness dependency, not just the Top10 frontend.
FILES = sorted(set(FILES) | (set(REQUIRED_FILES) - BASE_FILES) | {
    'run_pdv_dashboard_update.bat', 'pdv_dashboard/refresh.py',
    'pdv_dashboard/pdv_scope.py', 'pdv_dashboard/claim_trend_page.py',
    'tests/test_issue_update_package.py', 'build_issue_ai_update.py',
    'dashboard-theme.css', 'dashboard-kpis.css', 'dashboard-overview.css',
    'flow-polish.css', 'model-overview.css', 'model-overview.js',
    'claim-overview.css', 'delivery-overview.css', 'timeline-overview.css',
    'repairer-overview.css', 'repairer-overview.js',
    'assets/geo/australia-states.js', 'assets/geo/new-zealand.js',
})


def validate_package_files(names):
    names = set(names)
    missing = (set(REQUIRED_FILES) - BASE_FILES) - names
    missing |= {'run_pdv_dashboard_update.bat', 'pdv_dashboard/refresh.py',
                'pdv_dashboard/pdv_scope.py', 'pdv_dashboard/claim_trend_page.py'} - names
    for page in names:
        if not page.endswith('.html'):
            continue
        for reference in re.findall(r'(?:src|href)=["\']([^"\']+)', (ROOT / page).read_text(encoding='utf-8-sig')):
            if reference.startswith(('http:', 'https:', '//', 'data:', '#')):
                continue
            asset = reference.split('?')[0].split('#')[0]
            if asset.endswith(('.js', '.css')) and asset not in names | BASE_FILES:
                missing.add(asset)
    if missing:
        raise IssueAIError('Update is missing daily dependencies: ' + ', '.join(sorted(missing)))
    if names & BASE_FILES:
        raise IssueAIError('Update must preserve existing Planning credentials and base data')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openai-env',help='Legacy option; no OpenAI settings are loaded or packaged')
    parser.add_argument('--c4c-collection',required=True)
    parser.add_argument('--firebase-sa',default=str(ROOT/'firebase-service-account.json'))
    parser.add_argument('--output',default=str(ROOT/'outputs/issue_ai_deployment/planning_issue_ai_update.zip'))
    args=parser.parse_args()
    try:
        username,password=collection_credentials(args.c4c_collection)
        config={'C4C_ISSUE_USERNAME':username,'C4C_ISSUE_PASSWORD':password,'C4C_ISSUE_POSITION_ENABLED':'1','C4C_ISSUE_AI_ENABLED':'0'}
        envelope=seal(config,args.firebase_sa)
        if unseal(envelope,args.firebase_sa)!=config:
            raise IssueAIError('Encrypted package verification failed')
        validate_package_files(FILES)
        files={f:(ROOT/f).read_bytes() for f in FILES}
        files['.issue-ai/bootstrap.json']=json.dumps(envelope,indent=2).encode()
        private=json.loads(Path(args.firebase_sa).read_text(encoding='utf-8-sig'))['private_key']
        forbidden=[password,private]
        for content in files.values():
            if any(s.encode() in content for s in forbidden if s):
                raise IssueAIError('Refusing to package a plaintext credential')
        files['deployment_manifest.json']=json.dumps({'builtAt':utc_now(),
            'mode':'Existing run_daily_5pm; approved Tickets by Claim Approved On; Issue Position only; no AI key/calls; no installs',
            'installation':'Overlay existing working Planning directory; keep existing credentials and generated data',
            'requiredExistingFiles':sorted(BASE_FILES),
            'sha256':{name:hashlib.sha256(content).hexdigest() for name,content in files.items()}},indent=2).encode()
        output=Path(args.output)
        output.parent.mkdir(parents=True,exist_ok=True)
        temporary=output.with_suffix('.zip.tmp')
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,content in files.items(): archive.writestr(name,content)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise IssueAIError('Update archive integrity check failed')
            recovered=unseal(json.loads(archive.read('.issue-ai/bootstrap.json')),args.firebase_sa)
            if recovered!=config:
                raise IssueAIError('Packaged settings could not be verified')
        temporary.replace(output)
        print('Update package ready: '+str(output))
        print('Encrypted settings verified; no plaintext API/C4C/Firebase keys included; no network calls.')
        return 0
    except Exception as error:
        print(str(error) if isinstance(error,IssueAIError) else 'Packaging failed: '+type(error).__name__)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
