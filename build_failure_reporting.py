"""Reporting projection only: never overwrites or fabricates paid AI results."""
from collections import Counter
from issue_ai import parse_date, summarize

TYPES={'In Field Warranty Claims':'Z006','Pre Delivery Warranty Claims':'Z005'}

def build_reporting_view(results,summary,master):
    universe={}
    for tid,r in master.items():
        typ=TYPES.get(r.get('typeText'))
        if not typ:continue
        date=parse_date(r.get('createdOn'))
        universe[tid]={'ticketType':typ,'createdOn':date.isoformat() if date else ''}
    projected={}
    for key,r in results.items():
        if r['ticketId'] not in universe:continue
        row={**r,**universe[r['ticketId']]}
        if row.get('status') not in {'source','classified'} or row.get('needsReview') or not row.get('categoryCode'):
            row.update(categoryCode=summary.get('otherCategoryCode','Z072'),categoryName='Other',status='classified',needsReview=False,
                       method='other_unclassified',resultBasis='other_fallback',originalClassificationStatus=r.get('status',''))
        projected[key]=row
    calculated=summarize(projected)
    report={**summary,**calculated,'ruleVersion':summary.get('ruleVersion',calculated['ruleVersion']),'decisionVersion':summary.get('decisionVersion',calculated['decisionVersion']),'generatedAt':summary['generatedAt'],'allCreatedTickets':True}
    counts=Counter((r['ticketType'],r['createdOn'][:7] or 'unknown') for r in universe.values())
    report['createdCoverage']=[{'ticketType':t,'month':m,'ticketCount':n} for (t,m),n in sorted(counts.items())]
    report['createdTicketCount']=len(universe)
    report['unclassifiedAsOtherIssues']=sum(r.get('method')=='other_unclassified' for r in projected.values())
    report['ticketsWithoutIssues']=len(universe)-len({r['ticketId'] for r in projected.values()})
    report['excludedIssuesWithoutMaster']=len(results)-len(projected)
    return projected,report,universe
