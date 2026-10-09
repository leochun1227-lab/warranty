"""Direct C4C Issue Position grouping. No AI, dates come only from parent Tickets."""
import re
from collections import Counter,defaultdict
from issue_ai import IssueAIError,digest,parse_date,ticket_id

POSITION_NAMES={'Z001':'External Fixtures','Z002':'Plumbing','Z003':'Electrical System','Z004':'Mechanical','Z005':'Exterior Components','Z006':'Interior Components','Z007':'Floor','Z008':'Gas System','Z009':'Other','Z010':'Appliances','Z011':'Service','Z012':'Labour','Z013':'Accessories','Z014':'Repairer misc.','Z015':'Openings','Z016':'Chassis & Running Gear','Z017':'Compliance','Z018':'Water System','9997':'9997'}
TYPE_NAMES={'Z006':'In Field Warranty Claims','Z005':'Pre Delivery Warranty Claims'}

def position_enabled(config):
    return config.get('C4C_ISSUE_POSITION_ENABLED',config.get('C4C_ISSUE_AI_ENABLED','')).lower() in {'1','true','yes'}

def validate_position_config(config):
    missing=[k for k in ('C4C_ISSUE_USERNAME','C4C_ISSUE_PASSWORD') if not config.get(k)]
    if missing:raise IssueAIError('Missing settings: '+', '.join(missing))

def classify_positions(tickets,master):
    labels=defaultdict(Counter);excluded={'Z009','Z012','9997'}
    for t in tickets:
        for i in t.get('IssueItems',[]):
            code=str(i.get('IssuesPosition') or '').strip();name=str(i.get('IssuesPositionText') or '').strip()
            if code and name:labels[code][name]+=1
            if name.casefold() in {'labour','labor','other','others'} and code:excluded.add(code)
    names={**POSITION_NAMES,**{code:counts.most_common(1)[0][0] for code,counts in labels.items()}}
    names['Z009']='Other';results={}
    for t in tickets:
        typ=t.get('TicketType');tid=ticket_id(t.get('TicketID'))
        if typ not in TYPE_NAMES or not tid or not isinstance(t.get('IssueItems'),list):raise IssueAIError('Invalid Ticket for Issue Position grouping')
        date=parse_date(master.get(tid,{}).get('createdOn')) if master.get(tid,{}).get('typeText')==TYPE_NAMES[typ] else None
        for i in t['IssueItems']:
            iid=str(i.get('IssueID') or '').strip()
            if not iid:raise IssueAIError('Missing Issue ID')
            key=digest([tid,iid])
            if key in results:raise IssueAIError('Duplicate Ticket/Issue ID')
            position=str(i.get('IssuesPosition') or '').strip();code=position or 'Z009'
            if not re.fullmatch(r'(?:Z\d{3}|9997)',code):raise IssueAIError('Unexpected Issue Position code')
            results[key]={'recordId':key,'ticketId':tid,'issueId':iid,'ticketType':typ,'createdOn':date.isoformat() if date else '',
                'position':position,'positionText':str(i.get('IssuesPositionText') or ''),'description':str(i.get('IssuesDescription') or ''),
                'sourceSubcategory':str(i.get('Subcategory') or i.get('Subcatgory') or i.get('Subcatgorycontent_SDK') or ''),
                'sourceSubcategoryText':str(i.get('SubcategoryText') or i.get('SubcatgoryText') or ''),
                'sourceSubcategoryReason':str(i.get('SubcategoryReason') or ''),'sourceSubcategoryReasonText':str(i.get('SubcategoryReasonText') or ''),
                'categoryCode':code,'categoryName':names.get(code,code),'status':'source','needsReview':False,'method':'issue_position',
                'dateSource':'old_interface_master_csv','classificationBasis':'IssuesPosition','missingPosition':not bool(position)}
    return results,{'categoryDimension':'issue_position','otherCategoryCode':'Z009','excludedRankingCodes':sorted(excluded),
                    'reportingBasis':'approved_on','ticketScope':'approved_only',
                    'ruleVersion':'issue-position-approved-v2','decisionVersion':'direct-source-no-ai','configuredModel':'none',
                    'modelRun':{'modelCalls':0,'usage':{}},'runStatus':'success','missingPositionIssues':sum(r['missingPosition'] for r in results.values())}
