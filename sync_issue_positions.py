"""Daily Issue Position updater; C4C GET-only, no OpenAI or model dependency."""
import argparse,json,os,sys
from pathlib import Path
from issue_ai import ROOT,C4CReader,fetch_type,load_config,load_master,summarize,IssueAIError,private_dir
from issue_positions import classify_positions,position_enabled,validate_position_config
from build_issue_exports import load_export_tickets
from build_issue_startup import save_issue_startup

def main(argv=None):
    from sync_issue_subcategories import FirebaseStore,datetime_from_mtime
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--env-file');ap.add_argument('--publish',action='store_true');ap.add_argument('--check-config',action='store_true')
    ap.add_argument('--sample-file',action='append',default=[])
    ap.add_argument('--master-csv',default=str(ROOT/'outputs/analysis_ticket_base.csv'));ap.add_argument('--state-dir')
    ap.add_argument('--source-root',default=os.getenv('SOURCE_ROOT','c4cTickets_test'))
    ap.add_argument('--firebase-db-url',default='https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app')
    ap.add_argument('--firebase-sa-path',default=str(ROOT/'firebase-service-account.json'))
    args=ap.parse_args(argv);store=None
    try:
        if args.publish and args.sample_file:raise IssueAIError('Offline samples cannot be published as current production data')
        config=load_config(args.env_file)
        if not args.sample_file:validate_position_config(config)
        if args.check_config:
            print('[ISSUE POSITION] C4C configuration valid; AI disabled.',flush=True);return 0
        if args.publish and not position_enabled(config):raise IssueAIError('Issue Position update is not enabled')
        master=load_master(args.master_csv,require_fresh=args.publish)
        if args.publish:
            store=FirebaseStore(args.firebase_db_url,args.firebase_sa_path,args.source_root);store.acquire()
        def progress(message):
            print('[ISSUE POSITION] '+message,flush=True)
            if store:store.heartbeat()
        tickets=[];scans={}
        if args.sample_file:
            for filename in args.sample_file:
                payload=json.loads(Path(filename).read_text(encoding='utf-8-sig'));tickets.extend(payload.get('tickets',payload.get('data',[])))
        else:
            reader=C4CReader(config)
            for typ in ('Z005','Z006'):
                rows,scan=fetch_type(reader,typ,progress);tickets.extend(rows);scans[typ]=scan
        results,meta=classify_positions(tickets,master)
        summary={**summarize(results),**meta,'sourceRoot':args.source_root,'scans':scans,'retrievalComplete':not bool(args.sample_file),'masterSnapshotModifiedAt':datetime_from_mtime(args.master_csv)}
        state=Path(args.state_dir or private_dir()/'state');state.mkdir(parents=True,exist_ok=True)
        target=state/'latest-position-result.json';temp=target.with_suffix('.json.tmp');temp.write_text(json.dumps({'summary':summary,'results':results},ensure_ascii=False),encoding='utf-8');temp.replace(target)
        if store:
            previous={} # Direct grouping needs no paid cache or duplicate per-Issue Firebase table.
            startup=store.publish(results,summary,previous,load_export_tickets(args.master_csv),master);save_issue_startup(ROOT,startup)
        progress(json.dumps({'issues':len(results),'missingPosition':meta['missingPositionIssues'],'aiCalls':0,'published':bool(store)}))
        return 0
    except Exception as e:
        message=str(e) if isinstance(e,IssueAIError) else 'Position updater failed: '+type(e).__name__
        print('[ISSUE POSITION] '+message,flush=True)
        if store:store.failed(message)
        return 1
    finally:
        if store:store.close()

if __name__=='__main__':raise SystemExit(main())
