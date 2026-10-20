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


def build_historical_trend(startup, status_rows, amount_rows, status_version, quote_rows=None):
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
    snapshot['generation'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]
    return snapshot
