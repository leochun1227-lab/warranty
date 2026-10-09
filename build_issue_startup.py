"""Small, aggregate-only startup snapshot for Subcategory Top 10."""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = 'issue-startup-v2'
MAX_BYTES = 512 * 1024
TYPES = ['Z006', 'Z005']


def build_issue_startup(summary):
    if not summary.get('retrievalComplete') or not summary.get('generatedAt'):
        raise ValueError('Only a completed live scan can generate a page snapshot')
    groups = summary.get('groups') or []
    coverage = summary.get('coverage') or []
    accepted = summary['acceptedIssueCount']
    total = summary['issueCount']
    for g in coverage:
        n,c,p=(g.get(k) for k in ('ticketCount','classifiedTicketCount','pendingTicketCount'))
        if (any(type(v) is not int or v<0 for v in (n,c,p)) or not max(c,p)<=n<=c+p
                or n>g['issueCount'] or c>g['classifiedCount'] or p>g['issueCount']-g['classifiedCount']):
            raise ValueError('Invalid distinct Ticket coverage')
        matching=[r for r in groups if (r['ticketType'],r['month'])==(g['ticketType'],g['month'])]
        if (any(not 0<r['ticketCount']<=min(r['issueCount'],c) for r in matching)
                or sum(r['ticketCount'] for r in matching)<c):
            raise ValueError('Invalid distinct Ticket category counts')
    if (sum(g['issueCount'] for g in groups) != accepted
            or sum(g['issueCount'] for g in coverage) != total
            or sum(g['classifiedCount'] for g in coverage) != accepted
            or sum(g['ticketCount'] for g in coverage) != summary['ticketCount']
            or sum(g['classifiedTicketCount'] for g in coverage) != summary['acceptedTicketCount']):
        raise ValueError('Subcategory snapshot totals do not reconcile')
    names = {g['subcategoryCode']:g['subcategoryName'] for g in groups}
    categories = sorted(names)
    months = sorted({g['month'] for g in coverage} | {g['month'] for g in groups} | {g['month'] for g in summary.get('createdCoverage',[])})
    category_index = {code:i for i,code in enumerate(categories)}
    month_index = {month:i for i,month in enumerate(months)}
    snapshot = {
        'schema':SCHEMA, 'sourceRoot':summary.get('sourceRoot','c4cTickets_test'),
        'sourceVersion':summary['generatedAt'], 'generatedAt':summary['generatedAt'],
        'issueCount':total, 'acceptedIssueCount':accepted,
        'ticketCount':summary['ticketCount'], 'acceptedTicketCount':summary['acceptedTicketCount'],
        'exportVersion':summary.get('exportVersion'),
        'categories':[[code,'Other' if code==summary.get('otherCategoryCode','Z072') else names[code]] for code in categories],
        'months':months,
        # Repeated names, property keys and dates stay out of the hot path.
        'rows':[[TYPES.index(g['ticketType']),month_index[g['month']],category_index[g['subcategoryCode']],
                 g['issueCount'],g['ticketCount'],g.get('sourceIssueCount',0),
                 g.get('aiIssueCount',0),g.get('otherFallbackCount',0)] for g in groups],
        'coverage':[[TYPES.index(g['ticketType']),month_index[g['month']],g['issueCount'],g['classifiedCount'],
                     g['ticketCount'],g['classifiedTicketCount'],g['pendingTicketCount']]
                    for g in coverage],
        'automation':{'status':summary.get('runStatus','partial'), 'pauseReason':summary.get('pauseReason','')},
    }
    if summary.get('categoryDimension')=='issue_position':
        snapshot.update(categoryDimension='issue_position',otherCategoryCode=summary['otherCategoryCode'],excludedRankingCodes=summary['excludedRankingCodes'])
    if summary.get('allCreatedTickets'):
        created=summary['createdCoverage']
        if any(type(r['ticketCount']) is not int or r['ticketCount']<0 for r in created) or sum(r['ticketCount'] for r in created)!=summary['createdTicketCount']:
            raise ValueError('Invalid Created On coverage')
        snapshot['allCreatedTickets']=True
        snapshot['createdCoverage']=[[TYPES.index(r['ticketType']),month_index[r['month']],r['ticketCount']] for r in created]
        snapshot['createdTicketCount']=summary['createdTicketCount']
    if 'partsAmountBasis' in summary:
        amounts=[[g['partsAmountCents'],g['partsAmountKnownTickets']] for g in groups]
        if any(type(a) is not int or type(n) is not int or n<0 or n>g['ticketCount'] for (a,n),g in zip(amounts,groups)):
            raise ValueError('Invalid parts amount coverage')
        snapshot['partsAmounts']=amounts
        snapshot['partsAmountBasis']=summary['partsAmountBasis']
    if len(encode(snapshot).encode('utf-8')) > MAX_BYTES:
        raise ValueError('Subcategory startup exceeds the 512 KiB page budget')
    return snapshot


def encode(snapshot):
    return json.dumps(snapshot,ensure_ascii=False,separators=(',',':'))


def firebase_startup(snapshot):
    # RTDB otherwise removes empty arrays, changing the typed snapshot structure.
    return {key:snapshot[key] for key in ('schema','generatedAt','sourceVersion')} | {'data':encode(snapshot)}


def save_issue_startup(root, snapshot):
    output=Path(root)/'outputs/issue_subcategory_startup.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix('.json.tmp')
    temporary.write_text(encode(snapshot),encoding='utf-8')
    temporary.replace(output)
    return output


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',required=True)
    args=parser.parse_args()
    snapshot=build_issue_startup(json.loads(Path(args.summary).read_text(encoding='utf-8')))
    output=save_issue_startup(Path(__file__).resolve().parent,snapshot)
    print(f'Subcategory startup: {output.stat().st_size:,} bytes; aggregate data only')
