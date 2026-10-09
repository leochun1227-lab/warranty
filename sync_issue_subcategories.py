"""Run after the existing daily fetch. Never writes to C4C or core ticket nodes."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from build_failure_reporting import build_reporting_view
from build_issue_startup import build_issue_startup, firebase_startup, save_issue_startup
from build_issue_exports import build_issue_exports, publish_issue_exports, load_export_tickets, enrich_parts_amounts

from issue_ai import (ROOT, C4CReader, GPTClassifier, IssueAIError, ResultCache,
    classify_records, enabled, fetch_type, load_config, load_master, load_taxonomy,
    normalize, private_dir, summarize, utc_now, validate_config)


def log(message):
    print('[ISSUE AI] '+message, flush=True)


class FirebaseStore:
    """All mutations are confined to this generated-analysis namespace."""
    def __init__(self, url, service_account, source_root):
        import re
        import firebase_admin
        from firebase_admin import credentials, db
        if not re.fullmatch(r'[A-Za-z0-9_-]+',source_root):
            raise IssueAIError('Invalid source root name')
        self.app=firebase_admin.initialize_app(credentials.Certificate(service_account),
            {'databaseURL':url}, name='issue-ai-'+uuid.uuid4().hex)
        self.path='issueSubcategoryAnalysis/v1/'+source_root
        self.ref=db.reference(self.path,app=self.app)
        self.owner=uuid.uuid4().hex
        self.locked=False

    def acquire(self):
        now=time.time()
        def take(current):
            current=current or {}
            if current.get('owner') and current.get('expiresAt',0)>now:
                return current
            return {'owner':self.owner,'expiresAt':now+3*3600}
        value=self.ref.child('lease').transaction(take)
        if not value or value.get('owner')!=self.owner:
            raise IssueAIError('Another computer/process is already running the Issue AI sync')
        self.locked=True
        self.ref.child('automation').update({'status':'running','startedAt':utc_now(),'runId':self.owner})

    def heartbeat(self):
        value=self.ref.child('lease').transaction(lambda current:
            {'owner':self.owner,'expiresAt':time.time()+3*3600}
            if (current or {}).get('owner')==self.owner else current)
        if not value or value.get('owner')!=self.owner:
            raise IssueAIError('Issue AI run lease was lost')

    def cached(self):
        value=self.ref.child('results').get() or {}
        if not isinstance(value,dict):
            raise IssueAIError('Unexpected Firebase classification cache shape')
        return value

    def publish(self, results, summary, previous, ticket_details=None, master=None, validate_source=None):
        if validate_source:validate_source()
        report_results,report,universe=(build_reporting_view(results,summary,master) if master is not None else (results,summary,None))
        if ticket_details is not None:
            enrich_parts_amounts(report_results,report,ticket_details)
            bundle=build_issue_exports(report_results,report,ticket_details,universe)
            publish_issue_exports(self,bundle)
            report['exportVersion']=bundle['version']
            summary['exportVersion']=bundle['version']
        startup=build_issue_startup(report)
        self.heartbeat()
        changes={}
        for key,value in ({} if summary.get('categoryDimension')=='issue_position' else results).items():
            old=previous.get(key) or {}
            # Unchanged results need no repeated database write. Run freshness lives in summary.
            # RTDB removes empty arrays/objects and null properties. Compare that
            # persisted shape so unchanged source rows aren't uploaded every day.
            comparable=lambda r:{k:v for k,v in r.items() if k!='classifiedAt' and v is not None and v!=[] and v!={}}
            if comparable(old)!=comparable(value):
                changes[('positionResults/' if summary.get('categoryDimension')=='issue_position' else 'results/')+key]=value
        items=list(changes.items())
        for start in range(0,len(items),500):
            self.heartbeat()
            self.ref.update(dict(items[start:start+500]))
        # No deletion: absence in offset pagination is not proof that a ticket was deleted.
        # The public summary changes only after every result update has succeeded.
        if validate_source:validate_source()
        self.ref.update({'summary':summary,'reportSummary':report,'startup':firebase_startup(startup),'automation':{'status':summary['runStatus'],
            'finishedAt':utc_now(),'runId':self.owner,'model':summary['configuredModel'],
            'retrievalComplete':True,'statusCounts':summary['statusCounts'],
            'pauseReason':summary.get('pauseReason','')}})
        return startup

    def failed(self, error):
        if self.locked:
            self.ref.child('automation').update({'status':'failed','failedAt':utc_now(),
                'runId':self.owner,'error':error,'previousSummaryPreserved':True})

    def close(self):
        import firebase_admin
        try:
            if self.locked:
                self.ref.child('lease').transaction(lambda current:
                    {} if (current or {}).get('owner')==self.owner else current)
        finally:
            firebase_admin.delete_app(self.app)


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--env-file')
    ap.add_argument('--model')
    ap.add_argument('--check-config',action='store_true')
    ap.add_argument('--probe-model',action='store_true')
    ap.add_argument('--publish',action='store_true',help='Publish a complete LIVE scan to the independent Firebase namespace')
    ap.add_argument('--sample-file',action='append',default=[],help='Offline sample pages for model testing; cannot publish')
    ap.add_argument('--master-csv',default=str(ROOT/'outputs/analysis_ticket_base.csv'))
    ap.add_argument('--state-dir')
    ap.add_argument('--max-ai-issues',type=int)
    ap.add_argument('--firebase-db-url',default=os.getenv('FIREBASE_DB_URL','https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app'))
    ap.add_argument('--firebase-sa-path',default=os.getenv('FIREBASE_SA_PATH',str(ROOT/'firebase-service-account.json')))
    ap.add_argument('--source-root',default=os.getenv('SOURCE_ROOT',os.getenv('FIREBASE_ROOT','c4cTickets_test')))
    return ap


def probe_model(config,categories):
    record={'recordId':'connectivity-light-test','description':'Amber marker light LED not working.',
            'position':'Z003','positionText':'Electrical System'}
    client=GPTClassifier(config,categories)
    result,meta=client.classify([record])
    if result[record['recordId']]['category_ids'] != ['Z017']:
        raise IssueAIError('Model connected but did not pass the basic classification check')
    log('Model check passed: '+meta['model'])


def main(argv=None):
    # Historical entry point now routes to the position-only updater as well.
    from sync_issue_positions import main as position_main
    return position_main(argv)


def legacy_ai_main(argv=None):
    args=parser().parse_args(argv)
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')
    store=cache=None
    try:
        if args.publish and args.sample_file:
            raise IssueAIError('Offline samples cannot be published as current production data')
        config=load_config(args.env_file,args.model)
        validate_config(config,require_c4c=not (args.sample_file or args.probe_model))
        categories=load_taxonomy()
        if args.check_config:
            log('Configuration valid; model='+config['ISSUE_AI_MODEL']+'; enabled='+str(enabled(config)))
            if not args.probe_model:
                return 0
        if args.probe_model:
            probe_model(config,categories)
            return 0
        if args.publish and not enabled(config):
            raise IssueAIError('Publishing requires C4C_ISSUE_AI_ENABLED=1 in the private configuration')
        limit=args.max_ai_issues if args.max_ai_issues is not None else int(config.get('ISSUE_AI_MAX_PER_RUN','250'))
        batch_size=int(config.get('ISSUE_AI_BATCH_SIZE','10'))
        if limit<0 or not 1<=batch_size<=20:
            raise IssueAIError('Invalid per-run issue limit or batch size')
        state=Path(args.state_dir or config.get('ISSUE_AI_STATE_DIR') or private_dir()/'state')
        state.mkdir(parents=True,exist_ok=True)
        master=load_master(args.master_csv,require_fresh=args.publish)
        previous={}
        if args.publish:
            store=FirebaseStore(args.firebase_db_url,args.firebase_sa_path,args.source_root)
            store.acquire()
            previous=store.cached()
        def progress(message):
            log(message)
            if store:
                store.heartbeat()
        scans={}
        tickets=[]
        if args.sample_file:
            for filename in args.sample_file:
                payload=json.loads(Path(filename).read_text(encoding='utf-8-sig'))
                tickets.extend(payload.get('tickets',payload.get('data',[])))
            scans={'sample':{'retrievalComplete':False,'mode':'offline sample; not live'}}
        else:
            reader=C4CReader(config)
            for typ in ('Z005','Z006'):
                rows,meta=fetch_type(reader,typ,progress)
                tickets.extend(rows);scans[typ]=meta
        records=normalize(tickets,master)
        cache=ResultCache(state/'classifications.sqlite3')
        client=GPTClassifier(config,categories)
        results,model_stats=classify_records(records,categories,client,cache,previous,
            limit=limit,batch_size=batch_size,progress=progress)
        summary=summarize(results)
        unresolved=sum(v for k,v in summary['statusCounts'].items() if k not in {'source','classified'})
        summary.update(configuredModel=config['ISSUE_AI_MODEL'],sourceRoot=args.source_root,
            scans=scans,modelRun=model_stats,runStatus='partial' if unresolved else 'success',
            masterSnapshotModifiedAt=datetime_from_mtime(args.master_csv),
            retrievalComplete=not bool(args.sample_file),
            ruleBoundary='Pre Delivery: source when resolvable, otherwise AI. In Field: missing/invalid source always AI; valid source before 2026-09-09 uses source; on/after that date uses AI.')
        if any('credit_balance_exhausted' in error for error in model_stats['errors']):
            summary['pauseReason']='ai_credit_balance_exhausted'
        output={'summary':summary,'results':results}
        tmp=state/'latest-result.json.tmp'
        tmp.write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
        tmp.replace(state/'latest-result.json')
        if store:
            startup=store.publish(results,summary,previous,load_export_tickets(args.master_csv),master)
            save_issue_startup(ROOT,startup)
        log(json.dumps({'issues':summary['issueCount'],'statuses':summary['statusCounts'],
            'model':config['ISSUE_AI_MODEL'],'tokens':model_stats['usage'],'published':bool(store),
            'reusedIssues':model_stats['reusedLocal']+model_stats['reusedFirebase'],
            'firstTimeNewIssues':model_stats['newIssues'],'firstTimeBacklog':model_stats['backlogIssues'],
            'submittedIssues':model_stats['submittedIssues'],'modelBatches':model_stats['modelBatches'],
            'blockedSavedResults':model_stats['savedResultBlocked']},ensure_ascii=False))
        return 2 if model_stats['errors'] else 0
    except Exception as error:
        # SDK/HTTP exception strings may contain private configuration: never print them.
        message=str(error) if isinstance(error,IssueAIError) else 'Runner failure: '+type(error).__name__
        log(message)
        if store:
            try:
                store.failed(message)
            except Exception:
                log('Could not update independent AI run status in Firebase')
        return 1
    finally:
        if cache:
            cache.close()
        if store:
            try:
                store.close()
            except Exception:
                log('Lease cleanup failed; it will expire automatically')


def datetime_from_mtime(path):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(Path(path).stat().st_mtime,timezone.utc).isoformat()


if __name__=='__main__':
    raise SystemExit(main())
