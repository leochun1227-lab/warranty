import copy,base64,gzip,json,unittest
from build_issue_exports import build_issue_exports, enrich_parts_amounts, parts_cents
from issue_ai import summarize

class ExportTests(unittest.TestCase):
    def test_details_match_distinct_ticket_and_issue_counts_without_pending(self):
        base={'recordId':'a','ticketId':'1','issueId':'001','ticketType':'Z006','createdOn':'2026-09-01','status':'classified','needsReview':False,'categoryCode':'Z017','categoryName':'Lighting','method':'ai','description':'Lamp failed','position':'Z003'}
        rows={'a':base,'b':{**base,'recordId':'b','issueId':'002'},'c':{**base,'recordId':'c','issueId':'003','status':'deferred','needsReview':True,'categoryCode':''}}
        summary=summarize(rows);bundle=build_issue_exports(rows,summary,{'1':['Open','Dealer','Repairer','00009','Chassis','Van','Product']})
        manifest=json.loads(bundle['manifest']);self.assertEqual(len(manifest['shards']),1)
        value=json.loads(gzip.decompress(base64.b64decode(next(iter(bundle['shards'].values())))))
        self.assertEqual(len(value['tickets']),1);self.assertEqual(len(value['issues']),2)
        self.assertEqual(value['tickets'][0][1],'2026-09-01');self.assertEqual(value['tickets'][0][5],'00009')
        self.assertEqual(value['issues'][0][1],'001')
        # Content-addressed details stay valid after an unchanged daily run.
        changed={**summary,'generatedAt':'2026-10-10T00:00:00Z'}
        self.assertEqual(bundle['version'],build_issue_exports(rows,changed,{'1':['Open','Dealer','Repairer','00009','Chassis','Van','Product']})['version'])
        broken=copy.deepcopy(summary);broken['groups'][0]['ticketCount']=2
        with self.assertRaisesRegex(ValueError,'do not match'):build_issue_exports(rows,broken)

class PartsAmountTests(unittest.TestCase):
    def test_parts_only_and_distinct_ticket_per_category(self):
        self.assertEqual(parts_cents({'Factory Parts Claim Total Amount':'1,234.56','Repairer Parts Claim Total Amount':'10.20','LabourHoursTotalAmount':'999'}),124476)
        self.assertIsNone(parts_cents({'Factory Parts Claim Total Amount':'','Repairer Parts Claim Total Amount':'10'}))
        base={'ticketId':'1','issueId':'001','ticketType':'Z006','createdOn':'2026-09-01','status':'classified','needsReview':False,'categoryCode':'Z017','categoryName':'Lighting','method':'ai'}
        rows={'a':base,'b':{**base,'issueId':'002'},'c':{**base,'ticketId':'2','issueId':'003'},'d':{**base,'issueId':'004','categoryCode':'Z004','categoryName':'Awning'}}
        summary=summarize(rows)
        enrich_parts_amounts(rows,summary,{'1':['']*7+['1244.76']})
        lighting=next(g for g in summary['groups'] if g['subcategoryCode']=='Z017')
        self.assertEqual(lighting['partsAmountCents'],124476)
        self.assertEqual(lighting['partsAmountKnownTickets'],1)
        self.assertEqual(lighting['ticketCount'],2)
        self.assertEqual(next(g for g in summary['groups'] if g['subcategoryCode']=='Z004')['partsAmountCents'],124476)

if __name__=='__main__':unittest.main()
