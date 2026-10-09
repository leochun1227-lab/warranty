"""Shared inclusive creation-date scope for the standalone PDV dashboard."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

START_DATE = '2026-09-09'

def day(value):
    text = str(value or '').strip()
    for fmt, length in (('%Y-%m-%d', 10), ('%d/%m/%Y', 10)):
        try:
            return datetime.strptime(text[:length], fmt).date().isoformat()
        except ValueError:
            pass
    return ''

def scoped_status(rows):
    return [r for r in rows if r.get('claimType') == 'Pre Delivery Warranty Claims'
            and day(r.get('created')) >= START_DATE]

def build_claim(rows, amount_rows, is_approved, version, generated_at):
    fields = ('createdIn createdPre approvedIn approvedPre approvedCreatedIn approvedCreatedPre '
              'unapprovedIn unapprovedPre unapprovedCreatedIn unapprovedCreatedPre '
              'unapprovedClosedIn unapprovedClosedPre approvedAmountIn approvedAmountPre '
              'createdAmountIn createdAmountPre unapprovedAmountIn unapprovedAmountPre').split()
    monthly = defaultdict(lambda: dict.fromkeys(fields, 0))
    tickets = []
    eligible = {}
    for row in scoped_status(rows):
        tid, created = str(row['id']), day(row['created'])
        approval = day(row.get('claimApprovedOnDateTime') or row.get('claimApprovedOn'))
        master = {'Status': row.get('statusText', ''), 'StatusCode': row.get('statusCode', ''),
                  'Claim Approved On': approval}
        ok = is_approved(master)
        if tid in eligible:
            raise ValueError('Duplicate scoped Claim: ' + tid)
        eligible[tid] = (created, ok)
        monthly[created[:7]]['createdPre'] += 1
        if ok:
            if approval < created:
                raise ValueError('Approval precedes creation for Claim ' + tid)
            monthly[created[:7]]['approvedCreatedPre'] += 1
            monthly[approval[:7]]['approvedPre'] += 1
        tickets.append({'TicketID': tid, 'CreatedOnDateTime': created,
            'ClaimApprovedOnDateTime': approval, 'TicketStatus': master['StatusCode'],
            'TicketStatusText': master['Status'], 'claimType': row['claimType'],
            'Chassis Number': row.get('chassis', ''), 'Serial ID': row.get('serial', ''),
            'Sales Order': row.get('salesOrder', ''), 'ERP Purchase Order ID': row.get('erpPurchaseOrder', '')})
    amounts = {}
    sums = defaultdict(Decimal)
    for row in amount_rows:
        tid = str(row.get('id', ''))
        if tid not in eligible or not eligible[tid][1] or str(row.get('decisionKey') or row.get('decision', '')).lower() != 'approved':
            continue
        if tid in amounts:
            raise ValueError('Duplicate approved amount: ' + tid)
        decision = day(row.get('decisionDate'))
        if not decision or decision < START_DATE:
            raise ValueError('Invalid scoped amount decision date: ' + tid)
        created = eligible[tid][0]
        amount = Decimal(str(row.get('amount') or 0))
        amounts[tid] = {'id': tid, 'created': created, 'decisionDate': decision,
                        'decisionKey': 'approved', 'claim': 'Pre Delivery Warranty Claims',
                        'amount': float(amount)}
        sums[(created[:7], 'createdAmountPre')] += amount
        sums[(decision[:7], 'approvedAmountPre')] += amount
    for (month, key), value in sums.items():
        monthly[month][key] = float(value.quantize(Decimal('.01')))
    return {'schema': 'claim-startup-v1', 'generatedAt': generated_at, 'sourceVersion': version,
            'scopeStart': START_DATE, 'scopeBasis': 'created_on',
            'monthly': [{'month': m, **v} for m, v in sorted(monthly.items())], 'closedIndex': {}}, {
            'scopeStart': START_DATE, 'tickets': tickets, 'amountRows': list(amounts.values())}
