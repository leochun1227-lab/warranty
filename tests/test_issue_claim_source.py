import json,tempfile,unittest
from pathlib import Path
from issue_ai import IssueAIError
from issue_claim_source import normalize_claim_master,verify_claim_master,load_claim_master,select_claim

class ClaimSourceTests(unittest.TestCase):
    def setUp(self):
        self.versions=['2026-10-07T07:00:00Z','2026-10-07T06:00:00Z','2026-10-07T06:30:00Z']
        self.claim={'schema':'claim-startup-v1','generatedAt':self.versions[0],'sourceVersion':json.dumps(self.versions),
            'monthly':[{'month':'2026-09','createdPre':2,'approvedPre':1,'approvedCreatedPre':1}]}
        row={'TicketID':'001','TicketTypeText':'Pre Delivery Warranty Claims','TicketStatus':'Y7','TicketStatusText':'Approved Claims Closed (Closed)',
             'CreatedOn':'2026-09-01','ClaimApprovedOnDateTime':'2026-09-20'}
        self.nodes=[None,{'ticket':row},{'ticket':{**row,'TicketID':'2','TicketStatus':'Y8','TicketStatusText':'Unapproved Claims Closed (Closed)'}}]

    def test_exact_claim_months_and_parent_dates(self):
        master=normalize_claim_master(self.nodes)
        self.assertEqual(set(master),{'1','2'})
        verify_claim_master(master,self.claim)
        master['2']['statusCode']='Y7'
        with self.assertRaisesRegex(IssueAIError,'source mismatch'):verify_claim_master(master,self.claim)

    def test_current_source_then_exact_version_cache_never_reads_newer_tickets(self):
        with tempfile.TemporaryDirectory() as folder:
            local=Path(folder)/'claim.json';local.write_text(json.dumps(self.claim),encoding='utf-8')
            values={'ctmTicketStatusMonitorV44/analytics/claimTrendStartup':None,
                'ctmTicketStatusMonitorV44/analytics/team/generatedAt':self.versions[0],
                'c4cTickets_test/ticketCoreSyncAt':self.versions[1],
                'c4cTickets_test/ticketSoSyncAt':self.versions[2],
                'c4cTickets_test/tickets':self.nodes}
            master,meta,validate=load_claim_master(values.__getitem__,local,Path(folder)/'state')
            self.assertEqual(meta['claimSourceVersion'],self.claim['sourceVersion']);validate()
            values['c4cTickets_test/ticketCoreSyncAt']='2026-10-08T06:00:00Z'
            del values['c4cTickets_test/tickets']
            cached,_,_=load_claim_master(values.__getitem__,local,Path(folder)/'state')
            self.assertEqual(cached,master)
            with self.assertRaisesRegex(IssueAIError,'older snapshot'):
                load_claim_master(values.__getitem__,local,Path(folder)/'empty-state')
            newer={**self.claim,'sourceVersion':json.dumps(['2026-10-08T07:00:00Z']+self.versions[1:])}
            values['ctmTicketStatusMonitorV44/analytics/claimTrendStartup']=newer
            with self.assertRaisesRegex(IssueAIError,'changed'):validate()

    def test_remote_envelope_selection_matches_claim_page(self):
        newer={**self.claim,'sourceVersion':json.dumps(['2026-10-08T07:00:00Z']+self.versions[1:])}
        remote={k:v for k,v in newer.items() if k!='monthly'}
        remote['data']=json.dumps({'monthly':newer['monthly']})
        self.assertEqual(select_claim(remote,self.claim)['sourceVersion'],newer['sourceVersion'])

if __name__=='__main__':unittest.main()
