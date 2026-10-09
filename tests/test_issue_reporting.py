import unittest,copy,json,gzip,base64
from build_failure_reporting import build_reporting_view,is_approved
from build_issue_exports import build_issue_exports
from build_issue_startup import build_issue_startup
from issue_ai import summarize

class ReportingTests(unittest.TestCase):
    def test_approved_scope_uses_parent_approval_date_retains_created_date(self):
        base={'createdOn':'2025-12-20','typeText':'In Field Warranty Claims','approvedOn':'2026-01-03','statusText':'Repair in Progress'}
        master={'1':base,'2':{**base,'statusText':'Approved Claims Closed (Closed)'},
                '3':{**base,'statusText':'Unapproved Claims Closed (Closed)'},
                '4':{**base,'approvedOn':''},'5':{**base,'statusText':'New Claim'}}
        row={'ticketId':'1','issueId':'a','ticketType':'Z006','createdOn':'2025-12-20','categoryCode':'Z003','categoryName':'Electrical System','status':'source','needsReview':False,'method':'issue_position'}
        results={tid:{**row,'ticketId':tid} for tid in ['1','3','4','5']}
        summary={**summarize(results),'retrievalComplete':True,'reportingBasis':'approved_on','ticketScope':'approved_only','otherCategoryCode':'Z009'}
        projected,report,universe=build_reporting_view(results,summary,master)
        self.assertEqual(set(universe),{'1','2'})
        self.assertEqual(set(projected),{'1'})
        self.assertEqual(projected['1']['createdOn'],'2025-12-20')
        self.assertEqual(report['groups'][0]['month'],'2026-01')
        self.assertEqual(report['reportCoverage'],[{'ticketType':'Z006','month':'2026-01','ticketCount':2}])
        self.assertEqual(report['ticketsWithoutIssues'],1)
        self.assertNotIn('allCreatedTickets',report)
        snapshot=build_issue_startup(report)
        self.assertEqual(snapshot['reportingBasis'],'approved_on')
        self.assertEqual(snapshot['reportTicketCount'],2)
        bundle=build_issue_exports(projected,report,{},universe)
        manifest=json.loads(bundle['manifest'])
        self.assertEqual(manifest['ticketScope'],'approved_only')
        data=json.loads(gzip.decompress(base64.b64decode(bundle['shards'][manifest['shards'][0][3]])))
        self.assertEqual(data['tickets'][0][1],'2025-12-20')
        self.assertEqual(data['tickets'][0][10],'2026-01-03')
        self.assertEqual(data['month'],'2026-01')
        # Approval revoked or date moved: the next report must follow the current master.
        master['1']={**base,'statusText':'Unapproved Claims Closed (Closed)'}
        self.assertEqual(build_reporting_view(results,summary,master)[1]['issueCount'],0)
        master['1']={**base,'approvedOn':'2026-02-01'}
        self.assertEqual(build_reporting_view(results,summary,master)[1]['groups'][0]['month'],'2026-02')

    def test_approved_status_codes_and_exact_text(self):
        for code in ['Z9','Y0','Y1','Y2','Y4','YB','Y7']:
            self.assertTrue(is_approved({'statusCode':code,'approvedOn':'2026-01-01'}))
        for text in ['Unapproved Claims Closed (Closed)','Quote Approved','Parts Pre-Approved','Claim Assessment']:
            self.assertFalse(is_approved({'statusText':text,'approvedOn':'2026-01-01'}))
        self.assertFalse(is_approved({'statusCode':'Y7','approvedOn':'#'}))

    def test_all_created_and_other_do_not_change_ai_cache(self):
        master={'1':{'createdOn':'2026-09-01','typeText':'In Field Warranty Claims'},'2':{'createdOn':'2026-10-01','typeText':'Pre Delivery Warranty Claims'},'3':{'createdOn':'2026-09-01','typeText':'PDI'}}
        row={'ticketId':'1','issueId':'a','ticketType':'Z006','createdOn':'2025-01-01','categoryCode':'','categoryName':'','status':'deferred','needsReview':True,'method':'','description':'Lamp failed'}
        results={'a':row,'b':{**row,'issueId':'b','categoryCode':'Z017','categoryName':'Lighting','status':'classified','needsReview':False,'method':'ai'}}
        original=copy.deepcopy(results);summary=summarize(results);summary['retrievalComplete']=True
        projected,report,universe=build_reporting_view(results,summary,master)
        self.assertEqual(results,original)
        self.assertEqual(projected['a']['categoryCode'],'Z072')
        self.assertEqual(projected['a']['originalClassificationStatus'],'deferred')
        self.assertEqual(projected['b']['categoryCode'],'Z017')
        self.assertEqual(projected['a']['createdOn'],'2026-09-01')
        self.assertEqual(report['createdTicketCount'],2)
        self.assertEqual(report['ticketsWithoutIssues'],1)
        self.assertEqual(report['issueCount'],2)
        self.assertEqual(report['acceptedIssueCount'],2)
        snapshot=build_issue_startup(report)
        self.assertEqual(sum(r[2] for r in snapshot['createdCoverage']),2)
        bundle=build_issue_exports(projected,report,{},universe);manifest=json.loads(bundle['manifest'])
        rows=[]
        for entry in manifest['ticketShards']:
            data=json.loads(gzip.decompress(base64.b64decode(bundle['shards'][entry[2]])))
            rows+=data['tickets']
        self.assertEqual(len(rows),2)
        no_issue=next(r for r in rows if r[0]=='2')
        self.assertEqual(no_issue[10],['Z072']);self.assertEqual(no_issue[12],0)
        self.assertEqual(next(r for r in rows if r[0]=='1')[10],['Z017','Z072'])
        self.assertTrue(manifest['allCreatedTickets'])
        self.assertTrue(bundle['periods'])

if __name__=='__main__':unittest.main()
