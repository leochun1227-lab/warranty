"""Top10 reads the exact Claim Trend Ticket generation; never changes Claim Trend."""
import json
from collections import Counter
from pathlib import Path
from issue_ai import IssueAIError, digest, parse_date, ticket_id
from build_failure_reporting import is_approved

TYPE_TEXT={'Z006':'In Field Warranty Claims','Z005':'Pre Delivery Warranty Claims'}

def first(row,keys):
    return next((str(row[k]).strip() for k in keys if row.get(k) is not None and str(row[k]).strip()),'')

def normalize_claim_master(nodes):
    master={}
    for node in (nodes.values() if isinstance(nodes,dict) else nodes):
        if not isinstance(node,dict):continue
        r=node.get('ticket',node)
        if not isinstance(r,dict):continue
        text=first(r,['claimType','ClaimType','claim','Claim','TicketTypeText','Ticket Type Text','TicketType','Ticket Type']).lower()
        if first(r,['TicketType','Ticket Type']).upper()=='Z010' or 'pdi' in text:continue
        typ='Z005' if any(v in text for v in ['pre delivery','pre-delivery','predelivery']) else 'Z006' if any(v in text for v in ['in field','in-field','infield','field warranty']) else ''
        if not typ:continue
        tid=ticket_id(first(r,['TicketID','Ticket Id','Ticket ID','C4C Ticket ID','id','ID']))
        if not tid:continue
        row={
            'typeText':TYPE_TEXT[typ],
            'createdOn':first(r,['CreatedOnDateTime','CreatedOnDate','CreatedOn','Created On','Created On DateTime','createdOnDateTime','createdOnDate','createdOn','CreatedAt','createdAt']),
            'approvedOn':first(r,['ClaimApprovedOnDateTime','ClaimApprovedOnDate','ClaimApprovedOn','Claim Approved On','Claim Approved On DateTime','claimApprovedOnDateTime','claimApprovedOnDate','claimApprovedOn']),
            'statusCode':first(r,['TicketStatus','statusCode','Status','TicketStatusCode','StatusCode']),
            'statusText':first(r,['TicketStatusText','ticketStatusText','statusText','StatusText','Status','status']),
        }
        if tid in master and master[tid]!=row:raise IssueAIError('Conflicting Claim Trend Ticket identities')
        master[tid]=row
    return master

def decode_claim(value):
    if not isinstance(value,dict):return None
    value=dict(value)
    if isinstance(value.get('data'),str):value.update(json.loads(value['data']))
    if value.get('schema')!='claim-startup-v1' or not value.get('monthly'):return None
    try:versions=json.loads(value['sourceVersion'])
    except (KeyError,TypeError,ValueError):return None
    if not isinstance(versions,list) or len(versions)!=3 or not all(isinstance(v,str) and v for v in versions):return None
    return value

def select_claim(remote,local):
    # Match Claim Trend's local/remote snapshot selection without modifying it.
    values=[v for v in (decode_claim(remote),decode_claim(local)) if v]
    if not values:raise IssueAIError('Claim Trend snapshot unavailable; previous Top10 preserved')
    return max(values,key=lambda v:v['sourceVersion'])

def verify_claim_master(master,claim):
    counts=Counter()
    for r in master.values():
        suffix='Pre' if r['typeText']==TYPE_TEXT['Z005'] else 'In'
        created=parse_date(r.get('createdOn'));approved=parse_date(r.get('approvedOn'))
        if created and created.isoformat()[:7]>='2025-01':counts[(created.isoformat()[:7],'created'+suffix)]+=1
        if is_approved(r):
            if approved and approved.isoformat()[:7]>='2025-01':counts[(approved.isoformat()[:7],'approved'+suffix)]+=1
            if created and created.isoformat()[:7]>='2025-01':counts[(created.isoformat()[:7],'approvedCreated'+suffix)]+=1
    expected={(r['month'],key):int(r.get(key,0)) for r in claim['monthly'] for key in ['createdIn','createdPre','approvedIn','approvedPre','approvedCreatedIn','approvedCreatedPre']}
    for key in counts.keys()|expected.keys():
        if counts[key]!=expected.get(key,0):
            raise IssueAIError('Top10/Claim Trend source mismatch: '+key[0]+' '+key[1]+f' ({counts[key]} vs {expected.get(key,0)}); previous Top10 preserved')

def claim_metadata(claim):
    return {'claimSourceVersion':claim['sourceVersion'],'ticketDataAsOf':json.loads(claim['sourceVersion'])[1],
            'claimGeneratedAt':claim['generatedAt'],'ticketSource':'claim_trend_firebase_snapshot'}

def load_claim_master(read,local_path,state_dir,source_root='c4cTickets_test',monitor_root='ctmTicketStatusMonitorV44'):
    claim_path=monitor_root+'/analytics/claimTrendStartup'
    def selected():
        p=Path(local_path)
        local=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None
        return select_claim(read(claim_path),local)
    claim=selected();version=claim['sourceVersion']
    state=Path(state_dir);state.mkdir(parents=True,exist_ok=True)
    cache=state/('claim-master-'+digest([source_root,version])+'.json')
    def validate():
        if selected()['sourceVersion']!=version:raise IssueAIError('Claim Trend changed during Top10 calculation; previous Top10 preserved')
    if cache.is_file():
        value=json.loads(cache.read_text(encoding='utf-8'))
        if value.get('sourceVersion')!=version:raise IssueAIError('Claim Trend master cache version mismatch')
        master=value['master'];verify_claim_master(master,claim);validate()
        return master,claim_metadata(claim),validate
    def versions():
        return [read(monitor_root+'/analytics/team/generatedAt'),read(source_root+'/ticketCoreSyncAt'),read(source_root+'/ticketSoSyncAt')]
    expected=json.loads(version)
    if versions()!=expected:raise IssueAIError('Claim Trend still shows an older snapshot; matching Ticket details are not cached. Top10 will wait for the normal daily snapshot publication.')
    master=normalize_claim_master(read(source_root+'/tickets') or {})
    if versions()!=expected:raise IssueAIError('Claim Trend Ticket source changed during reading; previous Top10 preserved')
    verify_claim_master(master,claim);validate()
    temp=cache.with_suffix('.tmp');temp.write_text(json.dumps({'sourceVersion':version,'master':master},ensure_ascii=False),encoding='utf-8');temp.replace(cache)
    return master,claim_metadata(claim),validate
