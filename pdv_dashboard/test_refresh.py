import unittest
from refresh import compile_overview,approved
from pdv_scope import build_claim,day

class OverviewTest(unittest.TestCase):
    def test_position_usage_retains_codes_without_double_counting_labour_failure(self):
        tickets=[['a','2026-09-09'],['old','2026-09-08'],['pending','2026-09-10']]
        master={tid:{'StatusCode':'Z9','Claim Approved On':'2026-10-02'} for tid in ['a','old']}
        master['pending']={'Status':'Submitted','Claim Approved On':''}
        def issue(iid,code,label):return [0,iid,code,label,'','C','Component','R','Labour Failure','issue_position']
        source=[('a',issue('1','Z012','Different source label')),('a',issue('2','9997','Other label')),
                ('a',issue('3','Z003','Electrical')),('old',issue('4','Z012','Labour')),
                ('pending',issue('5','Z012','Labour'))]
        result,details=compile_overview(tickets,source,master,{},'2026-10-09')
        approved_usage={r[1]:r[3] for r in result['positionUsage'] if r[2]==1}
        self.assertEqual(approved_usage,{'Z012':1,'9997':1,'Z003':1})
        self.assertEqual([r[6] for r in details['2026-09'] if r[5]],['Z012','9997','Z003'])
        self.assertEqual(sum(r[5] for r in result['groups'] if r[4]==1),3)

    def test_exact_creation_cutoff_and_issue_details(self):
        tickets=[['old','2026-09-08'],['boundary','2026-09-09'],['new','2026-10-01']]
        master={r[0]:{'StatusCode':'Z9','Claim Approved On':'2026-10-02'} for r in tickets}
        issues=[(r[0],[0,r[0],'S','System','','C','Component','F','Failure','']) for r in tickets]
        result,details=compile_overview(tickets,issues,master,{},'2026-10-09')
        self.assertEqual(sum(r['approvedClaims'] for r in result['monthly']),2)
        self.assertEqual(sum(r['approvedIssues'] for r in result['monthly']),2)
        self.assertEqual({r[0] for rows in details.values() for r in rows},{'boundary','new'})
        self.assertEqual(sum(r[3] for r in result['rankings'] if r[1]==2),2)

    def test_claim_amounts_exclude_old_claim_approved_after_cutoff(self):
        rows=[{'id':tid,'created':created,'claimType':'Pre Delivery Warranty Claims','statusCode':'Z9',
               'claimApprovedOn':'2026-10-02'} for tid,created in [('old','08/09/2026'),('boundary','09/09/2026')]]
        amounts=[{'id':tid,'created':created,'decisionKey':'approved','decisionDate':'2026-10-02','amount':100}
                 for tid,created in [('old','2026-09-08'),('boundary','2026-09-09')]]
        claim,details=build_claim(rows,amounts,approved,'version','2026-10-09')
        self.assertEqual(sum(r['createdPre'] for r in claim['monthly']),1)
        self.assertEqual(sum(r['approvedPre'] for r in claim['monthly']),1)
        self.assertEqual(sum(r['approvedAmountPre'] for r in claim['monthly']),100)
        self.assertEqual(sum(r['createdAmountPre'] for r in claim['monthly']),100)
        self.assertEqual([r['TicketID'] for r in details['tickets']],['boundary'])
        self.assertEqual(day('09/09/2026'),'2026-09-09')
        self.assertEqual(day('bad date'),'')
    def test_approved_hierarchy_and_unique_claims(self):
        tickets=[['1','2026-09-09'],['2','2026-09-10'],['3','2026-09-11']]
        master={'1':{'Status':'Approved Claims Closed (Closed)','Claim Approved On':'01/02/2026'},'2':{'Status':'Unapproved Claims Closed (Closed)','Claim Approved On':''}}
        issue=lambda iid:[0,iid,'Z003','Electrical','description','Z100','Battery','R1','Functional','issue_position']
        result,details=compile_overview(tickets,[('1',issue('a')),('1',issue('b')),('2',issue('c')),('3',issue('d'))],master,{},'2026-02-01T00:00:00Z')
        self.assertEqual(result['monthly'][0],{'month':'2026-09','claims':3,'approvedClaims':1,'issues':4,'approvedIssues':2,'approvalUnknown':1})
        self.assertEqual([r for r in result['rankings'] if r[1]==2],[['2026-09',2,'Battery',2,1]])
        self.assertEqual(sum(r[5] for r in result['groups'] if r[4]==1),2)
        self.assertFalse(result['production']['available'])
        self.assertIsNone(details['2026-09'][-1][5])

    def test_missing_categories_preserved(self):
        master={'1':{'Status':'Repair in Progress','Claim Approved On':'2026-09-09'}}
        result,_=compile_overview([['1','2026-09-09']],[('1',[0,'a','','','','','','','',''])],master,{},'2026-09-10T00:00:00Z')
        self.assertEqual(result['groups'][0][1:4],['Unclassified']*3)
        self.assertEqual(result['missingCategories'],[1,1,1])

    def test_duplicate_issues_rejected(self):
        row=('1',[0,'a','','','','','','','',''])
        with self.assertRaisesRegex(ValueError,'Duplicate issue'):
            compile_overview([['1','2026-09-09']],[row,row],{}, {},'2026-09-10T00:00:00Z')

    def test_status_requires_approval_date(self):
        self.assertFalse(approved({'Status':'Repair in Progress','Claim Approved On':''}))
        self.assertFalse(approved({'Status':'Unapproved Claims Closed (Closed)','Claim Approved On':'2026-09-09'}))
        self.assertTrue(approved({'Status':'Sales Order Approved','Claim Approved On':'2026-09-09'}))

if __name__=='__main__':unittest.main()

