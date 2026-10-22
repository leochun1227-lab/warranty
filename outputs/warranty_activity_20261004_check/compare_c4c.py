"""Read-only C4C comparison. No Firebase or C4C writes."""
import ast
import csv
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import base64
from urllib.request import Request, urlopen
from urllib.parse import quote
from urllib.error import URLError, HTTPError

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
SOURCE = ROOT / 'fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py'
CONFIG = {}
for node in ast.parse(SOURCE.read_text(encoding='utf-8-sig')).body:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        continue
    name = node.targets[0].id
    if name not in {'BASE_URL', 'PATH', 'USERNAME', 'PASSWORD'}:
        continue
    value = node.value
    if isinstance(value, ast.Constant):
        CONFIG[name] = value.value
    elif isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == 'getenv':
        args = [ast.literal_eval(a) for a in value.args]
        CONFIG[name] = os.getenv(*args)
ENDPOINT = CONFIG['BASE_URL'].rstrip('/') + CONFIG['PATH']
FIELDS = ['TicketID', 'TicketType', 'TicketTypeText', 'WarrantyHandlingDealerID', 'DealerID', 'CreatedOn',
          'CreatedOnDateTime', 'ClaimApprovedOnDateTime', 'ClaimApprovedOn', 'TicketStatus', 'TicketStatusText']
TARGETS = {'3135','3133','3138','204642','204670'}
RETAIN = ['TicketID', 'TicketType', 'WarrantyHandlingDealerID', 'CreatedOn', 'ClaimApprovedOnDateTime', 'TicketStatus']

def relevant_date(value):
    text = str(value or '')
    if not text:
        return False
    try:
        date = datetime.strptime(text[:10], '%d/%m/%Y').date().isoformat() if '/' in text[:10] else text[:10]
        return '2026-09-28' <= date < '2026-10-22'
    except ValueError:
        return False

def fetch(skip, top=1000, role='1001', targeted=False):
    for attempt in range(3):
        try:
            query_filter = f"(CCSRQ_DPY_ROLE_CD eq '{role}')"
            if targeted:
                query_filter += ' and (' + ' or '.join(f"WarrantyHandlingDealerID eq '{d}'" for d in ['3135','3133','3138','204642','204670']) + ')'
            # Match the production client: CPI requires literal OData parameter
            # names and percent-encoded spaces, rather than form encoding.
            params = f"$top={top}&$skip={skip}&$filter=" + quote(query_filter, safe="()'")
            auth = base64.b64encode((CONFIG['USERNAME'] + ':' + CONFIG['PASSWORD']).encode()).decode()
            req = Request(ENDPOINT + '?' + params, headers={'Accept': 'application/json', 'Authorization': 'Basic ' + auth})
            with urlopen(req, timeout=60) as response:
                payload = json.load(response)
            assert isinstance(payload.get('data'), list), 'Missing C4C data array'
            return payload
        except HTTPError as exc:
            raise RuntimeError(f'C4C HTTP {exc.code}') from None
        except (URLError, TimeoutError, ValueError):
            if attempt == 2:
                raise RuntimeError('C4C connection or JSON error') from None
            time.sleep(attempt + 1)

if __name__ == '__main__' and '--target-probe' in sys.argv:
    probe = fetch(0, 5, targeted='--target-probe' in sys.argv)
    print('Page metadata:', {k: v for k, v in probe.items() if k != 'data'}, flush=True)
    print('Returned handling dealer IDs:', [r.get('WarrantyHandlingDealerID') for r in probe['data']], flush=True)
    if '--target-probe' in sys.argv:
        assert probe['data'], 'Empty targeted probe; filter not validated'
        assert all(str(r.get('WarrantyHandlingDealerID')) in {'3135','3133','3138','204642','204670'} for r in probe['data']), 'C4C ignored the dealer filter; stop targeted query'

if __name__ == '__main__' and '--page-probe' in sys.argv:
    pages = [fetch(skip, 5) for skip in (0, 5)]
    sets = []
    for offset, payload in zip((0,5), pages):
        ids = [str(r.get('TicketID')) for r in payload['data']]
        sets.append(set(ids))
        print('Pagination probe:', {'offset': offset, 'rows': len(ids), 'ids': ids, 'count': payload.get('count'), 'pageSize': payload.get('pageSize')}, flush=True)
    assert not sets[0].intersection(sets[1]), 'Repeated IDs across pages'

def stream_role(role, baseline_ids):
    # No raw response/page files. Discard all unrelated records immediately.
    started = datetime.now(timezone.utc).isoformat()
    selected, identities = {}, set()
    row_count, expected, page_count = 0, 0, 0
    def read_page(skip):
        payload = fetch(skip, 1000, role)
        reported = int(payload.get('totalCount') or payload.get('count') or 0)
        rows = payload['data']
        n = len(rows)
        kept, ids = {}, set()
        for row in rows:
            tid = str(row.get('TicketID') or '')
            if not tid:
                raise RuntimeError('Missing ticket identity')
            ids.add(tid)
            in_scope = (str(row.get('WarrantyHandlingDealerID')) in TARGETS and row.get('TicketType') in {'Z005','Z006'}
                        and (relevant_date(row.get('CreatedOn')) or relevant_date(row.get('ClaimApprovedOnDateTime'))))
            if in_scope or tid in baseline_ids:
                slim = {key: row.get(key) for key in RETAIN}
                if tid in kept and kept[tid] != slim:
                    raise RuntimeError('Relevant ticket changed during paging; comparison needs retry')
                kept[tid] = slim
        return n, reported, kept, ids
    def consume(page):
        nonlocal row_count, expected, page_count
        n, reported, kept, ids = page
        expected = max(expected, reported)
        if n and ids.issubset(identities):
            raise RuntimeError('Repeated C4C page')
        identities.update(ids)
        for tid, slim in kept.items():
            if tid in selected and selected[tid] != slim:
                raise RuntimeError('Relevant ticket changed during paging; comparison needs retry')
            selected[tid] = slim
        row_count += n
        page_count += 1
        if page_count % 5 == 0 or page_count == 1:
            print(f'C4C role {role}: {page_count} pages read, {len(selected)} relevant records retained', flush=True)
    first = read_page(0)
    consume(first)
    assert first[0] == 1000, f'Unexpected page length {first[0]}'
    initial_total = expected
    with ThreadPoolExecutor(max_workers=3) as pool:
        for page in pool.map(read_page, range(1000, initial_total, 1000)):
            consume(page)
    tail_skip = ((initial_total + 999) // 1000) * 1000
    for _ in range(5):
        tail = read_page(tail_skip)
        consume(tail)
        if not tail[0]:
            break
        tail_skip += 1000
    else:
        raise RuntimeError('No terminal page')
    if row_count < expected:
        raise RuntimeError(f'Incomplete C4C scan: {row_count} < {expected}')
    return list(selected.values()), {'role': role, 'reported_count': expected, 'rows_read': row_count, 'unique_ids': len(identities),
                                    'pages': page_count, 'start': started, 'end': datetime.now(timezone.utc).isoformat()}

if __name__ == '__main__' and '--count-only' in sys.argv:
    with (OUT / 'handling_dealer_counted_tickets_20261021.csv').open(encoding='utf-8-sig', newline='') as f:
        baseline_ids = {r['ticket_id'] for r in csv.DictReader(f)}
    with ThreadPoolExecutor(max_workers=3) as pool:
        scans = list(pool.map(lambda role: stream_role(role, baseline_ids), ['1001', '40', '43']))
    tickets = {}
    conflicts = []
    for rows, meta in scans:
        for row in rows:
            tid = str(row['TicketID'])
            if tid in tickets and tickets[tid] != row:
                conflicts.append({'ticket_id': tid, 'different_fields': [k for k in RETAIN if tickets[tid].get(k) != row.get(k)]})
            tickets[tid] = row
    result = {'source': ENDPOINT, 'scans': [meta for _, meta in scans], 'conflicts': conflicts, 'tickets': list(tickets.values())}
    (OUT / 'c4c_relevant_tickets.json').write_text(json.dumps(result), encoding='utf-8')
    print('FINISHED:', json.dumps({'scans': result['scans'], 'unique_tickets': len(tickets), 'conflicts': conflicts[:20], 'conflict_count': len(conflicts)}), flush=True)
