"""Reporting projection only: never overwrites or fabricates paid AI results."""
from collections import Counter
import re
from issue_ai import parse_date, summarize

TYPES={'In Field Warranty Claims':'Z006','Pre Delivery Warranty Claims':'Z005'}

def is_approved(row):
    """Same eligibility as ClaimTrendTicketMetrics.isApproved, on normalized master fields."""
    if not parse_date(row.get('approvedOn')):return False
    code=str(row.get('statusCode') or row.get('statusText') or '').strip().upper()
    text=str(row.get('statusText') or '').strip().lower()
    return code in {'Z9','Y0','Y1','Y2','Y4','YB','Y7'} or text in {
        'sales order approved','partially picked','dispatch parts','repair in progress',
        'repairer invoiced received','repairer invoiced processed'
    } or bool(re.fullmatch(r'approved claims closed(?:\s*\(closed\))?',text))

def build_reporting_view(results,summary,master):
    approved=summary.get('reportingBasis')=='approved_on'
    universe={}
    for tid,r in master.items():
        typ=TYPES.get(r.get('typeText'))
        if not typ:continue
        if approved and not is_approved(r):continue
        date=parse_date(r.get('createdOn'))
        universe[tid]={'ticketType':typ,'createdOn':date.isoformat() if date else ''}
        if approved:
            universe[tid].update(approvedOn=parse_date(r['approvedOn']).isoformat(),reportOn=parse_date(r['approvedOn']).isoformat())
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
    counts=Counter((r['ticketType'],r.get('reportOn',r['createdOn'])[:7] or 'unknown') for r in universe.values())
    report['createdCoverage']=[{'ticketType':t,'month':m,'ticketCount':n} for (t,m),n in sorted(counts.items())]
    report['createdTicketCount']=len(universe)
    report['unclassifiedAsOtherIssues']=sum(r.get('method')=='other_unclassified' for r in projected.values())
    report['ticketsWithoutIssues']=len(universe)-len({r['ticketId'] for r in projected.values()})
    report['excludedIssuesWithoutMaster']=len(results)-len(projected)
    if approved:
        report.pop('allCreatedTickets',None)
        report.update(allReportTickets=True,reportingBasis='approved_on',ticketScope='approved_only',
            reportCoverage=report.pop('createdCoverage'),reportTicketCount=report.pop('createdTicketCount'),
            dateBasis='Ticket Claim Approved On; approved statuses including Approved Claims Closed; never Issue dates.',
            rankingPolicy='Direct Issue Position; 9997, Labour and Other excluded from ranking, retained in totals and details.')
        report['excludedIssuesWithoutMaster']=sum(r['ticketId'] not in master for r in results.values())
        report['excludedIssuesOutsideApprovedScope']=len(results)-len(projected)-report['excludedIssuesWithoutMaster']
    return projected,report,universe
