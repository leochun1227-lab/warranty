"""Compile the historical PDV charts once, during the existing daily publication."""
import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pdv_scope import day

PENDING_CODES = {'Z1','Z2','Z3','Z4','Z5','Z6','ZM','ZR','ZV','Y6','YA','YC'}


def entries(value):
    return [row for row in (value.values() if isinstance(value, dict) else value or []) if isinstance(row, dict)]


def first(row, *keys):
    return next((row[k] for k in keys if row.get(k) is not None and str(row[k]).strip()), '')


def predelivery(row):
    text = str(first(row, 'claimType', 'claim', 'TicketTypeText', 'Ticket Type Text')).lower()
    return any(label in text for label in ('pre delivery', 'pre-delivery', 'predelivery'))


def pending(row):
    return (predelivery(row) and str(first(row, 'statusCode', 'TicketStatus')).upper() in PENDING_CODES
            and not first(row, 'claimApprovedOnDateTime', 'claimApprovedOn', 'ClaimApprovedOnDateTime')
            and not first(row, 'resolvedOnDateTime', 'resolvedOn', 'ResolvedOnDateTime')
            and not re.search(r'closed|reject|cancel|completed', str(first(row, 'statusText', 'TicketStatusText')).lower()))


def quote_amount(row):
    raw = row['quotedClaimAmount'] if 'quotedClaimAmount' in row else first(row, 'ClaimTotalAmount', 'Claim Total Amount', 'AmountIncludingTax')
    text = re.sub(r'[$,\s]', '', str(raw if raw is not None else ''))
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', text):
        return None
    try:
        value = Decimal(text)
        return value if value.is_finite() else None
    except InvalidOperation:
        return None


def claim_rows_from_tickets(nodes):
    rows = []
    for node in entries(nodes):
        row = node.get('ticket') or node
        if not predelivery(row):
            continue
        rows.append({'id': str(first(row, 'TicketID', 'id')),
            'claimType': 'Pre Delivery Warranty Claims',
            'created': first(row, 'CreatedOnDateTime', 'CreatedOnDate', 'CreatedOn', 'Created On'),
            'statusCode': first(row, 'TicketStatus', 'statusCode', 'StatusCode'),
            'statusText': first(row, 'TicketStatusText', 'statusText', 'Status'),
            'claimApprovedOn': first(row, 'ClaimApprovedOnDateTime', 'ClaimApprovedOn', 'Claim Approved On'),
            'resolvedOn': first(row, 'ResolvedOnDateTime', 'ResolvedOn', 'Resolved On')})
    return rows


def build_cohort_monthly(status_rows, issue_rows, is_approved):
    """Count Claims and their Issues in the Claim's creation month, once per source ID."""
    monthly = defaultdict(lambda: dict(totalClaims=0, approvedClaims=0, pendingClaims=0,
                                      totalIssues=0, approvedIssues=0, pendingIssues=0))
    tickets = {}
    for row in entries(status_rows):
        if not predelivery(row):
            continue
        tid, created = str(first(row, 'id', 'TicketID')), day(first(row, 'created', 'CreatedOnDateTime'))
        if not tid or not re.match(r'^\d{4}-\d{2}-\d{2}$', created) or created < '2025-01-01':
            continue
        if tid in tickets:
            raise ValueError('Duplicate historical Claim: ' + tid)
        approved_now = is_approved({'Status': first(row, 'statusText', 'TicketStatusText'),
            'StatusCode': first(row, 'statusCode', 'TicketStatus'),
            'Claim Approved On': day(first(row, 'claimApprovedOnDateTime', 'claimApprovedOn', 'ClaimApprovedOnDateTime'))})
        pending_now = pending(row)
        if approved_now and pending_now:
            raise ValueError('Claim cannot be both approved and pending: ' + tid)
        month = created[:7]
        tickets[tid] = (month, approved_now, pending_now)
        for key, value in [('totalClaims', 1), ('approvedClaims', approved_now), ('pendingClaims', pending_now)]:
            monthly[month][key] += int(value)
    seen = set()
    for tid, issue in issue_rows:
        tid = str(tid)
        if tid not in tickets:
            raise ValueError('Historical Issue has no matching dated Claim status: ' + tid)
        key = (tid, str(issue[1]))
        if key in seen:
            raise ValueError('Duplicate historical Issue: ' + repr(key))
        seen.add(key)
        month, approved_now, pending_now = tickets[tid]
        for field, value in [('totalIssues', 1), ('approvedIssues', approved_now), ('pendingIssues', pending_now)]:
            monthly[month][field] += int(value)
    return [{'month': month, **counts} for month, counts in sorted(monthly.items())]


def build_historical_trend(startup, status_rows, amount_rows, status_version, quote_rows=None,
                           cohort_monthly=None, issue_source_version=None, issues_complete=True, issue_generated_at=None):
    snapshot = dict(startup)
    if isinstance(snapshot.get('data'), str):
        snapshot.update(json.loads(snapshot.pop('data')))
    if snapshot.get('schema') != 'claim-startup-v1' or not snapshot.get('monthly'):
        raise ValueError('Historical Claim Trend summary unavailable')
    monthly = {r['month']: {**r, 'createdAmountIn': 0, 'createdAmountPre': 0} for r in snapshot['monthly']}
    amount_sums = defaultdict(Decimal)
    amounts = {}
    for row in entries(amount_rows):
        if str(first(row, 'decisionKey', 'decision')).lower() != 'approved' or not predelivery(row):
            continue
        tid = str(first(row, 'id', 'TicketID'))
        if tid:
            amounts[tid] = row  # Same last-record rule as the existing browser loader.
    for row in amounts.values():
        month = day(row.get('created'))[:7]
        if month < '2025-01':
            continue
        value = Decimal(str(row.get('amount') or 0))
        if not value.is_finite():
            raise ValueError('Invalid approved amount')
        amount_sums[month] += value
    for month, value in amount_sums.items():
        monthly.setdefault(month, {'month': month, 'createdAmountIn': 0})['createdAmountPre'] = float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    quotes = {str(first(r, 'TicketID', 'id')): r for node in entries(quote_rows) for r in [node.get('ticket') or node]}
    pending_months, quote_months, seen = {}, {}, set()
    for row in entries(status_rows):
        if not pending(row):
            continue
        tid, month = str(first(row, 'id', 'TicketID')), day(first(row, 'created', 'CreatedOnDateTime'))[:7]
        if not tid or tid in seen or month < '2025-01':
            continue
        seen.add(tid)
        count = pending_months.setdefault(month, {'month': month, 'pendingPre': 0, 'pendingIn': 0})
        count['pendingPre'] += 1
        total = quote_months.setdefault(month, {'month': month, 'amountPre': Decimal(0), 'knownPre': 0, 'missingPre': 0})
        value = quote_amount(row if 'quotedClaimAmount' in row else quotes.get(tid, {}))
        if value is None:
            total['missingPre'] += 1
        else:
            total['knownPre'] += 1
            total['amountPre'] += value
    for row in quote_months.values():
        row['amountPre'] = float(row['amountPre'].quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    snapshot.update(publication='pdv-daily-v1', statusVersion=status_version, generatedAt=status_version,
                    monthly=[monthly[m] for m in sorted(monthly)],
                    pendingMonthly=[pending_months[m] for m in sorted(pending_months)],
                    pendingAmountMonthly=[quote_months[m] for m in sorted(quote_months)])
    if cohort_monthly is not None:
        snapshot.update(cohortMonthly=cohort_monthly, issueSourceVersion=issue_source_version,
                        cohortBasis='claim_created_on', cohortSchema='claims-issues-v1',
                        issuePopulationComplete=issues_complete, issueGeneratedAt=issue_generated_at or issue_source_version)
        for row in cohort_monthly:
            source = monthly.get(row['month'], {})
            if row['totalClaims'] != source.get('createdPre', 0) or row['approvedClaims'] != source.get('approvedCreatedPre', 0):
                raise ValueError('Historical Claim cohorts do not reconcile for ' + row['month'])
            if row['pendingClaims'] != pending_months.get(row['month'], {}).get('pendingPre', 0):
                raise ValueError('Historical Pending cohorts do not reconcile for ' + row['month'])
    snapshot['generation'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]
    return snapshot
