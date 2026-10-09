"""Immutable, compressed export shards. Never part of the startup download."""
import base64,csv,gzip,hashlib,json
from collections import defaultdict
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TICKET_COLUMNS=['Ticket ID','Ticket Created On','Status','Dealer','Repairer','Serial ID','Chassis','Registered Product','Product','Ticket Parts Amount (AUD)']
ISSUE_COLUMNS=['Ticket index','Issue ID','Issue position','Issue position text','Issues description','System Subcategory','System Subcategory text','Subcategory reason','Subcategory reason text','Classification source']

def parts_cents(row):
    values=[]
    for key in ('Factory Parts Claim Total Amount','Repairer Parts Claim Total Amount'):
        raw=str(row.get(key,'')).strip().replace(',','')
        if not raw:return None
        try:value=Decimal(raw)
        except InvalidOperation:return None
        if not value.is_finite():return None
        values.append(value)
    return int((sum(values)*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def detail_cents(detail):
    if len(detail)!=8 or detail[7]=='':return None
    return int(Decimal(detail[7])*100)

def enrich_parts_amounts(results,summary,ticket_details):
    tickets=defaultdict(set)
    for r in results.values():
        if r.get('status') in {'source','classified'} and not r.get('needsReview') and r.get('categoryCode'):
            tickets[(r['ticketType'],r['createdOn'][:7] or 'unknown',r['categoryCode'])].add(r['ticketId'])
    for g in summary.get('groups',[]):
        ids=tickets[(g['ticketType'],g['month'],g['subcategoryCode'])]
        if len(ids)!=g['ticketCount']:raise ValueError('Amount Ticket membership mismatch')
        amounts=[detail_cents(ticket_details.get(tid,[])) for tid in ids]
        g['partsAmountCents']=sum(a for a in amounts if a is not None)
        g['partsAmountKnownTickets']=sum(a is not None for a in amounts)
    summary['partsAmountBasis']='Factory Parts Claim Total Amount + Repairer Parts Claim Total Amount; AUD; distinct Ticket per category; categories overlap; no labour.'

def load_export_tickets(path):
    from issue_ai import ticket_id
    path=Path(path)
    if not path.is_file():raise ValueError('Ticket detail master is missing')
    result={}
    with path.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            tid=ticket_id(r.get('C4C Ticket ID'))
            if tid:
                cents=parts_cents(r)
                result[tid]=[r.get(k,'') for k in ['Status','Dealer Name','Service Technician','Serial ID','Chassis Number','Registered Product','Product']]+[format(Decimal(cents)/100,'.2f') if cents is not None else '']
    return result

def build_issue_exports(results,summary,ticket_details=None,ticket_universe=None):
    ticket_details=ticket_details or {}
    groups=defaultdict(list)
    for r in results.values():
        if r.get('status') in {'source','classified'} and not r.get('needsReview') and r.get('categoryCode'):
            groups[(r['ticketType'],r['createdOn'][:7] or 'unknown',r['categoryCode'])].append(r)
    expected={(g['ticketType'],g['month'],g['subcategoryCode']):(g['issueCount'],g['ticketCount']) for g in summary.get('groups',[])}
    actual={k:(len(v),len({r['ticketId'] for r in v})) for k,v in groups.items()}
    if expected!=actual:raise ValueError('Export details do not match the displayed summary')
    def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    shards={};index=[];content=hashlib.sha256(b'category-shards-v3-parts-amount')
    for (typ,month,code),records in sorted(groups.items()):
        records.sort(key=lambda r:(r['ticketId'],r['issueId']))
        part=0;buffer=[];weight=0
        def flush():
            nonlocal part,buffer,weight
            if not buffer:return
            tickets=[];lookup={};issues=[]
            for r in buffer:
                tid=r['ticketId']
                if tid not in lookup:
                    lookup[tid]=len(tickets)
                    detail=list(ticket_details.get(tid,['']*8))
                    if len(detail)==7:detail.append('')
                    tickets.append([tid,r['createdOn'],*detail])
                issues.append([lookup[tid],r['issueId'],r.get('position',''),r.get('positionText',''),r.get('description',''),r.get('sourceSubcategory',''),r.get('sourceSubcategoryText',''),r.get('sourceSubcategoryReason',''),r.get('sourceSubcategoryReasonText',''),r.get('method','')])
            value={'schema':'failure-export-shard-v2','type':typ,'month':month,'category':code,'tickets':tickets,'issues':issues}
            raw=encode(value)
            if len(raw)>2*1024*1024:raise ValueError('An Issue export shard is too large')
            packed=base64.b64encode(gzip.compress(raw,mtime=0)).decode('ascii')
            key=f'{typ}_{code}/{month}_{part}';shards[key]=packed
            checksum=hashlib.sha256(raw).hexdigest();content.update(checksum.encode())
            index.append([typ,month,code,key,len(issues),checksum,len(raw)])
            part+=1;buffer=[];weight=0
        for r in records:
            size=len(encode(r))
            if buffer and (len(buffer)>=300 or weight+size>256*1024):flush()
            buffer.append(r);weight+=size
        flush()
    all_ticket_index=[];periods={}
    if ticket_universe is not None:
        content.update(b'all-created-with-other-v1')
        for typ,month,code,key,*_ in index:
            periods.setdefault(typ+'_'+month,{})[key.replace('/','_')]=shards[key]
        ticket_rows=defaultdict(list);membership=defaultdict(set);issue_counts=defaultdict(int)
        for r in results.values():
            membership[r['ticketId']].add((r['categoryCode'],r['categoryName']))
            issue_counts[r['ticketId']]+=1
        for tid,r in sorted(ticket_universe.items()):
            detail=list(ticket_details.get(tid,['']*8))
            if len(detail)==7:detail.append('')
            cats=sorted(membership.get(tid) or {(summary.get('otherCategoryCode','Z072'),'Other')})
            ticket_rows[(r['ticketType'],r['createdOn'][:7] or 'unknown')].append([tid,r['createdOn'],*detail,[c[0] for c in cats],[c[1] for c in cats],issue_counts[tid]])
        for (typ,month),rows in sorted(ticket_rows.items()):
            for start in range(0,len(rows),250):
                chunk=rows[start:start+250];raw=encode({'schema':'failure-all-tickets-v1','type':typ,'month':month,'tickets':chunk})
                if len(raw)>2*1024*1024:raise ValueError('All-Ticket shard exceeds limit')
                key=f'tickets/{typ}_{month}_{start//250}'
                shards[key]=base64.b64encode(gzip.compress(raw,mtime=0)).decode('ascii')
                checksum=hashlib.sha256(raw).hexdigest();content.update(checksum.encode())
                all_ticket_index.append([typ,month,key,len(chunk),checksum,len(raw)])
    version=content.hexdigest()[:32]
    manifest={'schema':'failure-export-v1','version':version,'generatedAt':summary['generatedAt'],'ticketColumns':TICKET_COLUMNS,'issueColumns':ISSUE_COLUMNS,'shards':index}
    if ticket_universe is not None:manifest.update(allCreatedTickets=True,ticketShards=all_ticket_index,periodGroups=True)
    return {'periods':periods,'version':version,'manifest':json.dumps(manifest,ensure_ascii=False,separators=(',',':')),'shards':shards}

def publish_issue_exports(store,bundle):
    root=store.ref.child('exports').child(bundle['version'])
    if root.child('ready').get()==True:return
    entries=list(bundle['shards'].items())
    for start in range(0,len(entries),50):
        store.heartbeat();root.child('shards').update(dict(entries[start:start+50]))
    periods=list(bundle.get('periods',{}).items())
    for start in range(0,len(periods),20):
        store.heartbeat();root.child('periods').update(dict(periods[start:start+20]))
    root.update({'manifest':bundle['manifest'],'ready':True})
