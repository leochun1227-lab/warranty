import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

OUT = Path(__file__).resolve().parent
GROUPS = {'Green RV': ['204642','204670'], 'Leisure Lion': ['3133','3135','3138'], 'Caravan Hub': ['200035','204677']}
LOOKUP = {d:g for g,ds in GROUPS.items() for d in ds}
APPROVED = {'Z9','Y0','Y1','Y2','Y4','YB','Y7'}

def date(value):
    text = str(value or '').strip()
    if not text or text == '#':
        return None
    if '/' in text[:10]:
        return datetime.strptime(text[:10], '%d/%m/%Y').date().isoformat()
    return datetime.fromisoformat(text.replace('Z','+00:00')).date().isoformat()

def amount(value):
    if value is None or str(value).strip() in {'','#'}:
        return None
    try:
        n = Decimal(str(value).strip().replace(',','').replace('$',''))
    except InvalidOperation:
        raise ValueError('Invalid amount in source') from None
    if not n.is_finite():
        raise ValueError('Non-finite source amount')
    return n

source = json.loads((OUT/'c4c_scoped_tickets.json').read_text(encoding='utf-8'))
assert not source['conflicts']
costs = json.loads((OUT/'sap_scoped_approved_cost.json').read_text(encoding='utf-8'))
records = []
seen = set()
for row in source['tickets']:
    tid = str(row['TicketID'])
    assert tid not in seen
    seen.add(tid)
    dealer = str(row.get('WarrantyHandlingDealerID') or '')
    approved_on = date(row.get('ClaimApprovedOnDateTime'))
    if dealer not in LOOKUP or row.get('TicketType') not in {'Z005','Z006'} or row.get('TicketStatus') not in APPROVED:
        continue
    if not approved_on or not '2025-01-01' <= approved_on < '2026-10-22':
        continue
    quote = amount(row.get('AmountIncludingTax'))
    rec = costs['byTicket'].get(tid)
    sap = amount(rec.get('amount') if rec and rec.get('amount') is not None else rec.get('netOrderValue') if rec else None)
    raw_sap = sap
    currencies = rec.get('currencies') or [] if rec else []
    sap_issue = 'No matched SAP amount' if sap is None else ''
    if sap is not None and currencies != ['AUD']:
        sap_issue = 'Currency not exclusively AUD: ' + ', '.join(currencies)
        sap = None
    adjusted = sap
    sanity = False
    if sap is not None and quote is not None and quote > 0 and sap >= 50000 and sap / quote >= 10:
        adjusted = quote.quantize(Decimal('0.01'))
        sanity = True
    records.append({'group':LOOKUP[dealer], 'dealer':dealer, 'ticket':tid, 'type':row['TicketType'],
                    'approved_on':approved_on,'month':approved_on[:7], 'status':row['TicketStatus'],
                    'c4c_amount': float(quote) if quote is not None else None,
                    'sap_amount': float(sap) if sap is not None else None,
                    'dashboard_amount':float(adjusted) if adjusted is not None else None,
                    'sap_issue':sap_issue,'dashboard_adjustment':sanity,
                    'sap_currency': ', '.join(currencies), 'sap_source_amount':float(raw_sap) if raw_sap is not None else None,
                    'po_documents': ', '.join(rec.get('poDocuments') or []) if rec else ''})
records.sort(key=lambda r:(r['approved_on'],r['group'],int(r['ticket'])))
months = [f'{y}-{m:02}' for y in (2025,2026) for m in range(1,13) if y==2025 or m<=10]
summary = {}
for basis in ['c4c_amount','sap_amount','dashboard_amount']:
    summary[basis] = {}
    for g in GROUPS:
        summary[basis][g] = {}
        for m in months:
            rows = [r for r in records if r['group']==g and r['month']==m]
            known = [Decimal(str(r[basis])) for r in rows if r[basis] is not None]
            summary[basis][g][m] = {'known_total':float(sum(known,Decimal('0'))), 'approved_tickets':len(rows), 'missing_amount':len(rows)-len(known)}
payload = {'groups':GROUPS,'report_cutoff':'2026-10-21','records':records,'summary':summary,
           'c4c_source':source['source'],'c4c_scans':source['scans'],'sap_source':costs['source'],'sap_generated_at':costs['generatedAt']}
(OUT/'analysis.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
print(json.dumps({'approved_tickets':len(records), 'c4c_missing':sum(r['c4c_amount'] is None for r in records),
                  'sap_missing':sum(r['sap_amount'] is None for r in records),
                  'sap_issue_counts':dict(Counter(r['sap_issue'] for r in records if r['sap_issue'])),
                  'dashboard_adjustments':sum(r['dashboard_adjustment'] for r in records),
                  'october':{basis:{g:summary[basis][g]['2026-10'] for g in GROUPS} for basis in summary}},indent=2))
