import unittest
from claim_trend_snapshot import build_historical_trend, build_cohort_monthly, claim_rows_from_tickets
from refresh import approved


class DailyTrendTest(unittest.TestCase):
    def build(self, rows, amounts=(), quotes=None):
        startup={'schema':'claim-startup-v1','sourceVersion':'["team","core","so"]',
                 'generatedAt':'2026-10-20','monthly':[{'month':'2025-02','createdPre':5,'approvedCreatedPre':2,'approvedAmountPre':40}],
                 'closedIndex':{}}
        return build_historical_trend(startup, rows, amounts, 'status-version', quotes)

    def ticket(self, tid, **fields):
        return {'id':tid,'claimType':'Pre Delivery Warranty Claims','created':'2025-02-03',
                'statusCode':'Z1','statusText':'New', **fields}

    def test_historical_pending_matches_open_population_and_distinguishes_missing_quotes(self):
        rows=[self.ticket('a',quotedClaimAmount='1,234.56'), self.ticket('a',quotedClaimAmount=999),
              self.ticket('zero',quotedClaimAmount=0), self.ticket('missing',quotedClaimAmount=''),
              self.ticket('approved',claimApprovedOn='2026-10-01'), self.ticket('closed',resolvedOn='2026-10-01'),
              self.ticket('cancelled',statusText='Cancelled'), self.ticket('rejected',statusText='Rejected'),
              self.ticket('unknown',statusCode='UNKNOWN'), self.ticket('in',claimType='In Field Warranty Claims')]
        snapshot=self.build(rows)
        self.assertEqual(snapshot['pendingMonthly'][0]['pendingPre'],3)
        self.assertEqual(snapshot['pendingAmountMonthly'][0],{'month':'2025-02','amountPre':1234.56,'knownPre':2,'missingPre':1})
        self.assertEqual(snapshot['monthly'][0]['createdPre'],5)

    def test_created_amounts_use_historical_sap_rows_and_preserve_approved_monthly(self):
        amounts=[{'id':'old','claim':'Pre Delivery','created':'2025-02-03','decisionKey':'approved','amount':42.25},
                 {'id':'new','claim':'Pre Delivery','created':'2026-10-01','decisionKey':'approved','amount':5},
                 {'id':'in','claim':'In Field Warranty','created':'2025-02-03','decisionKey':'approved','amount':999}]
        snapshot=self.build([],amounts)
        self.assertEqual(snapshot['monthly'][0]['createdAmountPre'],42.25)
        self.assertEqual(snapshot['monthly'][0]['approvedAmountPre'],40)
        self.assertEqual(snapshot['monthly'][1]['createdAmountPre'],5)
        self.assertEqual(snapshot['pendingMonthly'],[])
        self.assertEqual(snapshot['pendingAmountMonthly'],[])

    def test_legacy_quotes_use_original_fields_never_compact_amount(self):
        snapshot=self.build([self.ticket('a',amount=999),self.ticket('b',amount=888)],quotes=[
            None, {'TicketID':'a','ClaimTotalAmount':'20.50'}, {'TicketID':'b','quotedClaimAmount':'','AmountIncludingTax':999}])
        self.assertEqual(snapshot['pendingAmountMonthly'][0]['amountPre'],20.5)
        self.assertEqual(snapshot['pendingAmountMonthly'][0]['missingPre'],1)

    def test_generation_is_stable_until_result_or_source_changes(self):
        rows=[self.ticket('a',quotedClaimAmount=5)]
        first=self.build(rows)
        self.assertEqual(first['generation'],self.build(rows)['generation'])
        rows[0]['quotedClaimAmount']=6
        self.assertNotEqual(first['generation'],self.build(rows)['generation'])

    def test_claims_and_issues_use_parent_creation_month_and_current_distinct_statuses(self):
        rows=[self.ticket('approved',statusCode='Y2',claimApprovedOn='2026-10-01'),
              self.ticket('pending'), self.ticket('rejected',statusText='Rejected'),
              self.ticket('no-issues',created='2026-09-08'),self.ticket('outside',claimType='In Field')]
        issues=[('approved',[0,'1']),('approved',[0,'2']),('pending',[0,'1']),('rejected',[0,'1'])]
        result=build_cohort_monthly(rows,issues,approved)
        self.assertEqual(result[0],{'month':'2025-02','totalClaims':3,'approvedClaims':1,'pendingClaims':1,
            'totalIssues':4,'approvedIssues':2,'pendingIssues':1})
        self.assertEqual(result[1]['totalClaims'],1)
        self.assertEqual(result[1]['totalIssues'],0)

    def test_duplicate_issues_and_unmatched_parents_fail_before_publication(self):
        with self.assertRaisesRegex(ValueError,'Duplicate historical Issue'):
            build_cohort_monthly([self.ticket('a')],[('a',[0,'1']),('a',[0,'1'])],approved)
        with self.assertRaisesRegex(ValueError,'no matching'):
            build_cohort_monthly([],[('a',[0,'1'])],approved)

    def test_cohort_normalization_uses_canonical_ticket_dates(self):
        rows=claim_rows_from_tickets([None,{'ticket':{'TicketID':'a','TicketTypeText':'Pre Delivery Warranty Claims',
            'CreatedOnDateTime':'2025-02-03','TicketStatus':'Y7','TicketStatusText':'Approved Claims Closed',
            'ClaimApprovedOnDateTime':'2026-10-01'}}])
        result=build_cohort_monthly(rows,[('a',[0,'1'])],approved)
        self.assertEqual(result[0]['month'],'2025-02')
        self.assertEqual(result[0]['approvedIssues'],1)


if __name__=='__main__':
    unittest.main()

