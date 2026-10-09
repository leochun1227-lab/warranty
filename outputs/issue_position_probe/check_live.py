"""Fresh read-only first/middle/last page check; no AI or Firebase writes."""
import sys,json,collections
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'outputs/issue_ai_web_rollout'))
from fetch_live import config
from issue_ai import C4CReader,utc_now,IssueAIError,ENDPOINT
OUT=Path(__file__).resolve().parent

def inspect(typ,cfg):
 reader=C4CReader(cfg);first=reader.page(typ,0);count=int(first.get('count',0));offsets=sorted({0,(count//2//50)*50,((max(count,1)-1)//50)*50});tickets={};pages=[]
 for skip in offsets:
  payload=first if skip==0 else reader.page(typ,skip)
  rows=payload.get('data')
  if not isinstance(rows,list) or len(rows)>50:raise IssueAIError('Invalid live page')
  pages.append({'skip':skip,'tickets':len(rows),'reportedCount':payload.get('count')})
  for t in rows:
   if t.get('TicketType')!=typ or not t.get('TicketID') or not isinstance(t.get('IssueItems'),list):raise IssueAIError('Invalid live IssueItems expansion')
   tid=str(t['TicketID'])
   if tid in tickets:raise IssueAIError('Repeated Ticket across sample pages')
   tickets[tid]={'TicketID':tid,'TicketType':typ,'IssueItems':[{k:i.get(k) for k in ['IssueID','IssuesPosition','IssuesPositionText']} for i in t['IssueItems']]}
 pairs=collections.Counter((str(i.get('IssuesPosition') or '').strip(),str(i.get('IssuesPositionText') or '').strip()) for t in tickets.values() for i in t['IssueItems'])
 report={'type':typ,'fetchedAt':utc_now(),'samplePages':pages,'tickets':len(tickets),'issues':sum(pairs.values()),'missingCode':sum(n for (c,t),n in pairs.items() if not c),'missingText':sum(n for (c,t),n in pairs.items() if not t),'positions':[{'code':c,'name':t,'issues':n} for (c,t),n in pairs.most_common()]}
 (OUT/(typ+'_fresh_sample.json')).write_text(json.dumps({'report':report,'tickets':list(tickets.values())},ensure_ascii=False),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False),flush=True);return report

if __name__=='__main__':
 try:
  cfg=config()
  with ThreadPoolExecutor(max_workers=2) as pool:reports=list(pool.map(lambda t:inspect(t,cfg),['Z006','Z005']))
  (OUT/'fresh_summary.json').write_text(json.dumps({'endpoint':ENDPOINT,'scope':'fresh sampled first/middle/last pages; not a new full scan','reports':reports},ensure_ascii=False,indent=2),encoding='utf-8')
 except Exception as e:
  print(str(e) if isinstance(e,IssueAIError) else type(e).__name__,flush=True);raise SystemExit(1)
