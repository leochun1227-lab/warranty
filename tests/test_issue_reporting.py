import unittest,copy,json,gzip,base64
from build_failure_reporting import build_reporting_view
from build_issue_exports import build_issue_exports
from build_issue_startup import build_issue_startup
from issue_ai import summarize

class ReportingTests(unittest.TestCase):
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
