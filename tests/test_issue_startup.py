import copy
import json
import unittest
from unittest.mock import Mock

from build_issue_startup import build_issue_startup, firebase_startup, encode
from sync_issue_subcategories import FirebaseStore


def example():
    return {'generatedAt':'2026-10-09T00:00:00Z','retrievalComplete':True,'issueCount':4,'acceptedIssueCount':3,'ticketCount':2,'acceptedTicketCount':2,
        'runStatus':'partial','pauseReason':'ai_credit_balance_exhausted',
        'groups':[{'ticketType':'Z005','month':'2026-09','subcategoryCode':'Z017','subcategoryName':'Lighting System',
            'issueCount':2,'ticketCount':1,'sourceIssueCount':2},
            {'ticketType':'Z006','month':'unknown','subcategoryCode':'Z072','subcategoryName':'Others',
             'issueCount':1,'ticketCount':1,'otherFallbackCount':1}],
        'coverage':[{'ticketType':'Z005','month':'2026-09','issueCount':2,'classifiedCount':2,'ticketCount':1,'classifiedTicketCount':1,'pendingTicketCount':0},
                    {'ticketType':'Z006','month':'unknown','issueCount':2,'classifiedCount':1,'ticketCount':1,'classifiedTicketCount':1,'pendingTicketCount':1}]}


class StartupTests(unittest.TestCase):
    def test_small_snapshot_reconciles_and_contains_only_aggregates(self):
        source=example();source['results']={'secret':{'description':'Unneeded issue text'}}
        snapshot=build_issue_startup(source)
        self.assertEqual(snapshot['rows'],[[1,0,0,2,1,2,0,0],[0,1,1,1,1,0,0,1]])
        self.assertEqual(snapshot['categories'][1],['Z072','Other'])
        self.assertNotIn('Unneeded issue text',encode(snapshot))
        self.assertEqual(snapshot['automation']['pauseReason'],'ai_credit_balance_exhausted')
        self.assertEqual(json.loads(firebase_startup(snapshot)['data']),snapshot)

    def test_parts_amounts_stay_compact_with_coverage(self):
        source=example();source['partsAmountBasis']='Ticket parts amount'
        for g in source['groups']:g.update(partsAmountCents=12345,partsAmountKnownTickets=1)
        snapshot=build_issue_startup(source)
        self.assertEqual(snapshot['partsAmounts'],[[12345,1],[12345,1]])
        source['groups'][0]['partsAmountKnownTickets']=2
        with self.assertRaises(ValueError):build_issue_startup(source)

    def test_empty_snapshot_preserves_arrays_in_firebase_string(self):
        source=example();source.update(groups=[],coverage=[],issueCount=0,acceptedIssueCount=0,ticketCount=0,acceptedTicketCount=0)
        self.assertEqual(json.loads(firebase_startup(build_issue_startup(source))['data'])['rows'],[])

    def test_inconsistent_or_incomplete_scan_cannot_publish(self):
        for changes in [{'retrievalComplete':False},{'acceptedIssueCount':5},{'issueCount':10},{'acceptedTicketCount':3}]:
            source=copy.deepcopy(example());source.update(changes)
            store=object.__new__(FirebaseStore);store.ref=Mock();store.heartbeat=Mock()
            with self.assertRaises(ValueError):store.publish({},source,{})
            store.ref.update.assert_not_called()


if __name__=='__main__':unittest.main()
