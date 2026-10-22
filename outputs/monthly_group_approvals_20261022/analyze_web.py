import json
from pathlib import Path
from decimal import Decimal
from collections import Counter
OUT=Path(__file__).resolve().parent
src=json.loads((OUT/'website_scoped_approval_rows.json').read_text())
sap=json.loads((OUT/'sap_scoped_approved_cost.json').read_text())
groups={'Green RV':['204642','204670'],'Leisure Lion':['3133','3135','3138'],'Caravan Hub':['200035','204677']}
lookup={d:g for g,ds in groups.items() for d in ds}
records=[]
for row in src['rows']:
    raw=row['raw']; tid=str(row['id']); dealer=str(raw['WarrantyHandlingDealerID'])
    assert raw['TicketType'] in {'Z005','Z006'}
    amount=Decimal(str(row.get('exportAmount') or row.get('amount') or 0))
    claim=Decimal(str(raw.get('ClaimTotalAmount') or raw.get('AmountIncludingTax') or 0).replace(',',''))
    if amount>=50000 and claim>0 and amount/claim>=10: amount=claim.quantize(Decimal('.01'))
    amount=max(Decimal(0),amount)
    po=sap['byTicket'].get(tid) or {}; currencies=po.get('currencies') or []
    records.append({'group':lookup[dealer],'dealer':dealer,'ticket':tid,'type':raw['TicketType'],'approved_on':row['decisionDate'], 'month':row['decisionDate'][:7],'status':raw['TicketStatus'],
        'c4c_amount':float(claim),'sap_amount':po.get('amount'),'dashboard_amount':float(amount),'sap_source_amount':po.get('amount'),
        'sap_currency':', '.join(currencies),'po_documents':', '.join(po.get('poDocuments') or []),
        'sap_issue':'Website zero cost' if not amount else ('Website includes source currency: '+', '.join(currencies) if currencies and currencies!=['AUD'] else '')})
assert len({r['ticket'] for r in records})==len(records)
records.sort(key=lambda r:(r['approved_on'],r['group'],r['ticket']))
months=[f'{y}-{m:02}' for y in (2025,2026) for m in range(1,13) if y==2025 or m<=10]
summary={'dashboard_amount':{g:{m:{'known_total':float(sum((Decimal(str(r['dashboard_amount'])) for r in records if r['group']==g and r['month']==m),Decimal(0)))} for m in months} for g in groups}}
payload={'groups':groups,'records':records,'summary':summary,'website_source':src['source'],'website_generated_at':src['generatedAt'],'sap_source':sap['source'],'sap_generated_at':sap['generatedAt']}
(OUT/'website_analysis.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
print(json.dumps({'records':len(records),'zeroCosts':sum(r['dashboard_amount']==0 for r in records),'currencyIssues':dict(Counter(r['sap_issue'] for r in records if 'currency' in r['sap_issue'])),'october':{g:summary['dashboard_amount'][g]['2026-10'] for g in groups}}))
