import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from issue_ai import (C4CReader, GPTClassifier, IssueAIError, ResultCache, classify_records,
    fetch_type, input_hash, load_config, load_master, normalize, output_schema,
    read_env, summarize, validate_config, validate_outputs)
from sync_issue_subcategories import FirebaseStore, main

CATS={'Z017':{'name':'Lighting System','position':'Z003'},'Z074':{'name':'Reverse Camera','position':'Z010'},'Z072':{'name':'Others','position':'Z009'}}
CFG={'OPENAI_API_KEY':'test-key','OPENAI_BASE_URL':'https://api.openai.com/v1',
     'ISSUE_AI_MODEL':'gpt-5.5','C4C_ISSUE_USERNAME':'user','C4C_ISSUE_PASSWORD':'password'}


def ticket(tid='1',typ='Z006',**issue):
    return {'TicketID':tid,'TicketType':typ,'IssueItems':[{'IssueID':'0001001','IssuesPosition':'Z003',
        'IssuesPositionText':'Electrical System','IssuesDescription':'Marker light cracked.',
        'Subcategory':'Z017',**issue}]}


def records(typ='Z006',created='2026-09-10',**issue):
    name='Pre Delivery Warranty Claims' if typ=='Z005' else 'In Field Warranty Claims'
    return normalize([ticket(typ=typ,**issue)],{'1':{'createdOn':created,'typeText':name}})


def valid(record,code='Z017',review=False):
    return {'record_id':record['recordId'],'category_ids':[code] if code else [],
        'business_scenario':'Marker light damage','evidence':['Marker light'],
        'reason':'The described component is a marker light.','needs_review':review}


class FakeClassifier:
    def __init__(self,fail=False,review=False):
        self.config=CFG.copy();self.usage={};self.calls=[];self.fail=fail;self.review=review

    def classify(self,rows):
        self.calls.append(rows)
        if self.fail:
            raise IssueAIError('OpenAI HTTP 429')
        return {r['recordId']:valid(r,review=self.review) for r in rows}, {'model':'gpt-5.5-snapshot','classifiedAt':'now','responseId':'r1'}


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cache=ResultCache(Path(self.temp.name)/'cache.sqlite3')

    def tearDown(self):
        self.cache.close();self.temp.cleanup()

    def test_routes_and_boundary_do_not_infer_dates(self):
        self.assertEqual(records('Z005','',Subcategory='')[0]['route'],'ai')
        for date,route in [('2026-09-08','source'),('09/09/2026','ai'),('2026-09-10','ai'),('','missing_date')]:
            self.assertEqual(records(created=date)[0]['route'],route)
        mismatched=normalize([ticket()],{'1':{'createdOn':'2026-09-10','typeText':'Repair ticket'}})
        self.assertEqual(mismatched[0]['route'],'missing_date')

    def test_typo_alias_and_leading_zeros_preserved(self):
        r=records(Subcategory='',Subcatgory='Z074')[0]
        self.assertEqual(r['sourceSubcategory'],'Z074')
        self.assertEqual(r['issueId'],'0001001')
        with self.assertRaises(IssueAIError):
            records(Subcatgory='Z074')
        with self.assertRaises(IssueAIError):
            normalize([ticket(),ticket()],{})

    def test_all_issues_inherit_ticket_date_for_cutoff_and_month(self):
        t=ticket(CreatedOn='2026-10-01')
        other=copy.deepcopy(t['IssueItems'][0]);other.update(IssueID='0001002',CreatedOn='2026-08-01')
        t['IssueItems'].append(other)
        master={'1':{'createdOn':'2026-09-08','typeText':'In Field Warranty Claims'}}
        rr=normalize([t],master)
        self.assertEqual([r['createdOn'] for r in rr],['2026-09-08','2026-09-08'])
        self.assertEqual([r['route'] for r in rr],['source','source'])
        results,_=classify_records(rr,CATS,FakeClassifier(),self.cache)
        self.assertEqual(summarize(results)['groups'][0]['month'],'2026-09')
        missing=normalize([t],{})
        self.assertTrue(all(r['createdOn']=='' and r['route']=='missing_date' for r in missing))

    def test_source_and_empty_description_never_call_gpt(self):
        client=FakeClassifier()
        rows=records('Z005',Subcategory='Z017')
        result,_=classify_records(rows,CATS,client,self.cache)
        self.assertEqual(next(iter(result.values()))['status'],'source')
        rows=records(IssuesDescription='')
        result,_=classify_records(rows,CATS,client,self.cache)
        self.assertEqual(next(iter(result.values()))['categoryCode'],'Z072')
        self.assertEqual(next(iter(result.values()))['categoryName'],'Other')
        self.assertEqual(client.calls,[])

    def test_model_required_even_if_post_cutoff_source_has_value(self):
        client=FakeClassifier()
        rows=records(Subcategory='Z074')
        result,_=classify_records(rows,CATS,client,self.cache)
        self.assertEqual(len(client.calls),1)
        self.assertEqual(next(iter(result.values()))['categoryCode'],'Z017')

    def test_daily_reuses_completed_issue_despite_text_model_or_rule_changes(self):
        client=FakeClassifier();rows=records()
        first,_=classify_records(rows,CATS,client,self.cache)
        second,_=classify_records(rows,CATS,client,self.cache)
        self.assertEqual(len(client.calls),1)
        self.assertEqual(next(iter(second.values()))['responseId'],'r1')
        changed=copy.deepcopy(rows);changed[0]['description']+=' More damage.'
        classify_records(changed,CATS,client,self.cache)
        self.assertEqual(len(client.calls),1)
        client.config['ISSUE_AI_MODEL']='another-model'
        with patch('issue_ai.RULE_VERSION','new-rule-version'),patch('issue_ai.PROMPT','new prompt'):
            reused,meta=classify_records(changed,CATS,client,self.cache)
        self.assertEqual(len(client.calls),1)
        self.assertEqual(meta['submittedIssues'],0)
        self.assertEqual(next(iter(reused.values()))['classificationInput']['description'],rows[0]['description'])
        self.assertNotEqual(input_hash(rows[0],'gpt-5.5',CATS),input_hash(rows[0],'gpt-5.5',{**CATS,'Z099':{'name':'Windows','position':'Z015'}}))

    def test_firebase_cache_reuses_results_on_another_computer(self):
        first,_=classify_records(records(),CATS,FakeClassifier(),self.cache)
        with tempfile.TemporaryDirectory() as other:
            c=ResultCache(Path(other)/'new.sqlite3');client=FakeClassifier()
            try:
                result,_=classify_records(records(),CATS,client,c,remote=first)
                self.assertEqual(client.calls,[])
                self.assertEqual(next(iter(result.values()))['status'],'classified')
            finally:c.close()

    def test_candidate_cached_and_counted_as_result(self):
        client=FakeClassifier(review=True)
        result,_=classify_records(records(),CATS,client,self.cache)
        classify_records(records(),CATS,client,self.cache)
        self.assertEqual(len(client.calls),1)
        row=next(iter(result.values()))
        self.assertEqual(row['categoryCode'],'Z017')
        self.assertFalse(row['needsReview'])
        self.assertTrue(row['modelNeedsReview'])
        self.assertEqual(row['suggestedCategoryIds'],['Z017'])
        self.assertEqual(summarize(result)['acceptedIssueCount'],1)

    def test_no_model_category_counts_as_other_and_reuses_cache(self):
        client=FakeClassifier(review=True)
        client.classify=Mock(side_effect=lambda rr:({r['recordId']:valid(r,code='',review=True) for r in rr},{}))
        result,_=classify_records(records(),CATS,client,self.cache)
        again,_=classify_records(records(),CATS,client,self.cache)
        self.assertEqual(client.classify.call_count,1)
        row=next(iter(again.values()))
        self.assertEqual((row['status'],row['categoryCode'],row['categoryName']),('classified','Z072','Other'))
        self.assertEqual(summarize(result)['acceptedIssueCount'],1)

    def test_legacy_review_cache_migrates_without_api_call(self):
        rr=records();client=FakeClassifier(review=True)
        result,_=classify_records(rr,CATS,client,self.cache)
        legacy=next(iter(result.values())).copy()
        for k in ['modelNeedsReview','decisionVersion','resultBasis']:legacy.pop(k,None)
        legacy.update(status='needs_review',needsReview=True,categoryCode='',categoryName='')
        self.cache.put(legacy['inputHash'],legacy)
        updated,_=classify_records(rr,CATS,client,self.cache)
        self.assertEqual(len(client.calls),1)
        self.assertEqual(next(iter(updated.values()))['categoryCode'],'Z017')

    def test_bad_cached_category_is_not_accepted(self):
        rr=records();client=FakeClassifier()
        result,_=classify_records(rr,CATS,client,self.cache)
        row=next(iter(result.values()));row['categoryCode']='Z999'
        self.cache.put(row['inputHash'],row)
        fixed,_=classify_records(rr,CATS,client,self.cache,remote=result)
        self.assertEqual(len(client.calls),1)
        self.assertEqual(next(iter(fixed.values()))['categoryCode'],'')
        self.assertEqual(next(iter(fixed.values()))['status'],'saved_result_invalid')
        with tempfile.TemporaryDirectory() as other:
            c=ResultCache(Path(other)/'fresh.sqlite3');fresh=FakeClassifier()
            try:
                held,meta=classify_records(rr,CATS,fresh,c,remote=fixed)
                self.assertEqual(fresh.calls,[])
                self.assertEqual(meta['savedResultBlocked'],1)
            finally:c.close()

    def test_daily_only_submits_new_issues_and_other_computer_reuses_all(self):
        client=FakeClassifier()
        old,_=classify_records(records(),CATS,client,self.cache)
        existing=ticket();existing['IssueItems'].append({**existing['IssueItems'][0],'IssueID':'0001002'})
        new=ticket('2')  # Same Issue ID, different Ticket: a distinct Issue.
        master={tid:{'createdOn':'2026-09-10','typeText':'In Field Warranty Claims'} for tid in ('1','2')}
        rr=normalize([existing,new],master)
        result,meta=classify_records(rr,CATS,client,self.cache,remote=old)
        self.assertEqual(meta['reusedLocal'],1)
        self.assertEqual(meta['newIssues'],2)
        self.assertEqual(meta['submittedIssues'],2)
        self.assertEqual({r['recordId'] for r in client.calls[-1]},set(result)-set(old))
        repeated,again=classify_records(rr,CATS,client,self.cache,remote=result)
        self.assertEqual(again['submittedIssues'],0)
        self.assertEqual(len(client.calls),2)
        with tempfile.TemporaryDirectory() as other:
            cache=ResultCache(Path(other)/'planning.sqlite3');other_client=FakeClassifier()
            other_client.config['ISSUE_AI_MODEL']='changed-model'
            try:
                # Changing an old description must not bill it again, even on a fresh PC.
                rr[0]['description']='A rewritten description with entirely different words.'
                _,stats=classify_records(rr,CATS,other_client,cache,remote=repeated)
                self.assertEqual(stats['reusedFirebase'],3)
                self.assertEqual(other_client.calls,[])
                _,stats=classify_records(rr,CATS,other_client,cache)
                self.assertEqual(stats['reusedLocal'],3)
                self.assertEqual(other_client.calls,[])
            finally:cache.close()

    def test_legacy_local_cache_migrates_without_rebilling(self):
        result,_=classify_records(records(),CATS,FakeClassifier(),self.cache)
        with tempfile.TemporaryDirectory() as other:
            import sqlite3
            path=Path(other)/'legacy.sqlite3'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE classifications (hash TEXT PRIMARY KEY,result TEXT NOT NULL)')
                row=next(iter(result.values()))
                conn.execute('INSERT INTO classifications VALUES (?,?)',(row['inputHash'],json.dumps(row)))
            conn.close()
            cache=ResultCache(path);client=FakeClassifier();client.config['ISSUE_AI_MODEL']='changed-model'
            try:
                _,stats=classify_records(records(),CATS,client,cache)
                self.assertEqual(stats['reusedLocal'],1)
                self.assertEqual(client.calls,[])
            finally:cache.close()

    def test_new_issue_on_old_ticket_precedes_unclassified_history(self):
        rr=records();new=copy.deepcopy(rr[0]);new.update(recordId='new-on-old-ticket',issueId='new',createdOn='2026-08-01')
        remote={rr[0]['recordId']:{'status':'deferred'}}
        client=FakeClassifier()
        _,stats=classify_records(rr+[new],CATS,client,self.cache,remote=remote,limit=1)
        self.assertEqual(client.calls[0][0]['recordId'],'new-on-old-ticket')
        self.assertEqual((stats['newIssues'],stats['backlogIssues'],stats['submittedIssues']),(1,1,1))

    def test_daily_integration_follows_successful_fetch_and_skips_analytics_only(self):
        import ast
        from types import SimpleNamespace
        path=Path(__file__).resolve().parents[1]/'ctm_v44_history_safe_mandt800_rejection_filter.py'
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_once')
        func.args.args[0].annotation=None;func.returns=None
        fetch,compare,db=Mock(),Mock(),Mock()
        scope={'run_company_fetch':fetch,'compare_and_write':compare,'db':db,'now_iso':lambda:'now'}
        exec(compile(ast.Module(body=[func],type_ignores=[]),str(path),'exec'),scope)
        args=SimpleNamespace(skip_fetch=False,skip_dealer_analytics=False,company_file='company',firebase_db_url='db',firebase_sa_path='sa',source_root='source',monitor_root='monitor')
        with patch('issue_ai_daily.run_after_daily_fetch') as hook:
            scope['run_once'](args);hook.assert_called_once_with('source','db','sa')
            args.skip_fetch=True;scope['run_once'](args);self.assertEqual(hook.call_count,1)
            args.skip_fetch=False;fetch.side_effect=RuntimeError('core failed')
            with self.assertRaises(RuntimeError):scope['run_once'](args)
            self.assertEqual(hook.call_count,1)

    def test_failure_does_not_fall_back_to_source_or_cache_failure(self):
        client=FakeClassifier(fail=True)
        result,meta=classify_records(records(),CATS,client,self.cache)
        row=next(iter(result.values()))
        self.assertEqual(row['status'],'api_error');self.assertEqual(row['categoryCode'],'')
        self.assertIsNone(self.cache.get(row['inputHash']))
        self.assertTrue(meta['errors'])

    def test_exhausted_credit_stops_without_retrying_or_saving_a_category(self):
        session=Mock();session.post.return_value.status_code=429
        session.post.return_value.json.return_value={'error':{'code':'credit_balance_exhausted'}}
        client=GPTClassifier(CFG,CATS,session)
        result,meta=classify_records(records(),CATS,client,self.cache)
        self.assertEqual(session.post.call_count,1)
        self.assertIn('credit_balance_exhausted',meta['errors'][0])
        self.assertEqual(next(iter(result.values()))['categoryCode'],'')

    def test_call_cap_keeps_backlog_explicit_and_newest_first(self):
        rr=records();newer=copy.deepcopy(rr[0]);newer.update(recordId='newer',createdOn='2026-10-01')
        client=FakeClassifier()
        result,_=classify_records(rr+[newer],CATS,client,self.cache,limit=1)
        self.assertEqual(client.calls[0][0]['recordId'],'newer')
        self.assertEqual(result[rr[0]['recordId']]['status'],'deferred')

    def test_bad_batch_retries_individually_without_discarding_valid_results(self):
        rr=records();second=copy.deepcopy(rr[0]);second.update(recordId='second',issueId='0001002')
        client=FakeClassifier();original=client.classify
        def classify(batch):
            if len(batch)>1:
                raise IssueAIError('Model response omitted records')
            return original(batch)
        client.classify=classify
        result,meta=classify_records(rr+[second],CATS,client,self.cache)
        self.assertEqual(len(client.calls),2)
        self.assertFalse(meta['errors'])
        self.assertTrue(all(r['status']=='classified' for r in result.values()))

    def test_invalid_single_output_does_not_block_other_issues_or_become_other(self):
        rr=records();second=copy.deepcopy(rr[0]);second.update(recordId='second',issueId='0001002')
        client=FakeClassifier();original=client.classify
        def classify(batch):
            if any(r['recordId']=='second' for r in batch):
                raise IssueAIError('Model evidence is not an exact short quote')
            return original(batch)
        client.classify=classify
        result,meta=classify_records(rr+[second],CATS,client,self.cache)
        self.assertEqual(result[rr[0]['recordId']]['status'],'classified')
        self.assertEqual(result['second']['status'],'api_error')
        self.assertEqual(result['second']['categoryCode'],'')
        self.assertIsNone(self.cache.get(result['second']['inputHash']))
        self.assertEqual(len(meta['errors']),1)

    def test_summary_counts_issues_and_distinct_tickets_separately(self):
        rows=records();second=copy.deepcopy(rows[0]);second.update(recordId='other',issueId='0001002')
        result,_=classify_records(rows+[second],CATS,FakeClassifier(),self.cache)
        s=summarize(result)
        self.assertEqual(s['groups'][0]['issueCount'],2)
        self.assertEqual(s['groups'][0]['ticketCount'],1)
        self.assertEqual(s['acceptedTicketCount'],1)

    def test_ticket_totals_deduplicate_categories_and_keep_partly_pending_tickets(self):
        result,_=classify_records(records(),CATS,FakeClassifier(),self.cache)
        row=next(iter(result.values()))
        result['duplicate']={**row}
        result['second-category']={**row,'categoryCode':'Z074','categoryName':'Reverse Camera'}
        result['partly-pending']={**row,'status':'deferred','needsReview':True,'categoryCode':''}
        result['only-pending']={**result['partly-pending'],'ticketId':'2'}
        result['next-month']={**row,'ticketId':'3','createdOn':'2026-10-01'}
        s=summarize(result)
        self.assertEqual(s['ticketCount'],3)
        self.assertEqual(s['acceptedTicketCount'],2)
        self.assertEqual(s['pendingTicketCount'],2)
        self.assertEqual(sum(g['ticketCount'] for g in s['groups']),3)
        september=next(c for c in s['coverage'] if c['month']=='2026-09')
        self.assertEqual([september[k] for k in ('ticketCount','classifiedTicketCount','pendingTicketCount')],[2,1,2])
        result['bad-date']={**row,'createdOn':'2026-10-01'}
        with self.assertRaisesRegex(ValueError,'Conflicting Ticket'):summarize(result)

    def test_output_validation_rejects_hallucinations_omissions_and_quotes(self):
        rows=records();good=valid(rows[0])
        validate_outputs({'results':[good]},rows,CATS)
        bads=[{**good,'record_id':'someone-else'},{**good,'category_ids':['Z999']},
              {**good,'category_ids':['Z017','Z074']},{**good,'evidence':['invented evidence']},
              {**good,'needs_review':'false'},{**good,'category_ids':[]},{**good,'evidence':[]}]
        for bad in bads:
            with self.subTest(bad=bad),self.assertRaises(IssueAIError):validate_outputs({'results':[bad]},rows,CATS)
        for bad in [[],[good,good]]:
            with self.assertRaises(IssueAIError):validate_outputs({'results':bad},rows,CATS)

    def test_c4c_reads_only_get_and_no_credential_redirect(self):
        session=Mock();session.get.return_value.status_code=200;session.get.return_value.json.return_value={'data':[]}
        C4CReader(CFG,session).page('Z006',0)
        self.assertEqual(session.method_calls[0][0],'get')
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertIn('/PC4C/Ticket/list',session.get.call_args.args[0])
        self.assertNotIn('OPENAI_API_KEY',session.get.call_args.kwargs)

    def test_prompt_has_no_tools_and_sends_only_minimum_fields(self):
        rows=records();session=Mock();session.post.return_value.status_code=200
        session.post.return_value.json.return_value={'status':'completed','model':'gpt-5.5','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'results':[valid(rows[0])]})}]}]}
        GPTClassifier(CFG,CATS,session).classify(rows)
        body=session.post.call_args.kwargs['json'];sent=json.loads(body['input'])[0]
        self.assertFalse(body['store']);self.assertNotIn('tools',body)
        self.assertEqual(set(sent),{'record_id','description','position_code','position_text','source_subcategory','source_subcategory_text','source_reason','source_reason_text'})
        self.assertNotIn(CFG['OPENAI_API_KEY'],json.dumps(body))
        self.assertTrue(body['text']['format']['strict'])
        id_schema=body['text']['format']['schema']['properties']['results']['items']['properties']['record_id']
        self.assertEqual(id_schema['enum'],[rows[0]['recordId']])

    def test_missing_or_invalid_source_routes_to_ai_for_both_types(self):
        for typ in ['Z005','Z006']:
            for created in ['2026-09-01','2026-09-10','']:
                for code in ['', 'Z999']:
                    with self.subTest(typ=typ,created=created,code=code):
                        rr=records(typ,created,Subcategory=code)
                        self.assertEqual(rr[0]['route'],'ai')
                        client=FakeClassifier();result,_=classify_records(rr,CATS,client,self.cache)
                        self.assertEqual(next(iter(result.values()))['categoryCode'],'Z017')

    def test_pre_delivery_exact_label_can_resolve_but_empty_infield_uses_ai(self):
        self.assertEqual(records('Z005',Subcategory='',SubcategoryText='Lighting System')[0]['route'],'source')
        self.assertEqual(records('Z006','2026-09-01',Subcategory='',SubcategoryText='Lighting System')[0]['route'],'ai')

    def test_supporting_reason_changes_invalidate_ai_cache(self):
        a=records(Subcategory='',SubcategoryReasonText='Damage')[0]
        b=records(Subcategory='',SubcategoryReasonText='Product Failure')[0]
        self.assertNotEqual(input_hash(a,'gpt-5.5',CATS),input_hash(b,'gpt-5.5',CATS))

    def test_evidence_can_quote_supplied_position_without_inventing_text(self):
        rr=records();output=valid(rr[0]);output['evidence']=['Marker light','Electrical System']
        validate_outputs({'results':[output]},rr,CATS)

    def test_scan_uses_actual_page_size_until_empty(self):
        reader=Mock();reader.page.side_effect=[{'count':'3','data':[ticket('1'),ticket('2')]},
            {'count':'3','data':[ticket('3')]},{'count':'3','data':[]}]
        rows,meta=fetch_type(reader,'Z006')
        self.assertEqual([c.args[1] for c in reader.page.call_args_list],[0,2,3])
        self.assertEqual(len(rows),3);self.assertTrue(meta['retrievalComplete'])

    def test_incomplete_repeated_wrong_type_or_unexpanded_scan_fails(self):
        for pages in [
            [{'count':2,'data':[ticket()]},{'count':2,'data':[]}],
            [{'count':2,'data':[ticket()]},{'count':2,'data':[ticket()]}],
            [{'count':1,'data':[ticket(typ='Z005')]}],
            [{'count':1,'data':[{'TicketID':'1','TicketType':'Z006'}]}],
        ]:
            with self.subTest(pages=pages),self.assertRaises(IssueAIError):
                reader=Mock();reader.page.side_effect=pages;fetch_type(reader,'Z006')

    def test_private_env_is_data_not_shell_and_wrong_endpoint_rejected(self):
        file=Path(self.temp.name)/'private.env'
        file.write_text('OPENAI_API_KEY="$(do-not-execute)"\nOPENAI_MODEL=gpt-5.5\n',encoding='utf-8')
        self.assertEqual(read_env(file)['OPENAI_API_KEY'],'$(do-not-execute)')
        with self.assertRaises(IssueAIError):validate_config({**CFG,'OPENAI_BASE_URL':'https://example.com/v1'})

    def test_publish_rejects_offline_samples_before_database_initialization(self):
        with patch('sync_issue_subcategories.FirebaseStore') as store:
            self.assertEqual(main(['--publish','--sample-file','example.json']),1)
            store.assert_not_called()

    def test_master_uses_old_ticket_id_not_title_and_detects_conflicts(self):
        p=Path(self.temp.name)/'master.csv'
        p.write_text('C4C Ticket ID,Ticket ID,Created On,Ticket Type\n001,title,2026-09-10,In Field Warranty Claims\n',encoding='utf-8')
        self.assertEqual(load_master(p)['1']['createdOn'],'2026-09-10')
        p.write_text(p.read_text()+'1,title,2026-09-11,In Field Warranty Claims\n')
        with self.assertRaises(IssueAIError):load_master(p)

    def test_daily_hook_disabled_does_not_invoke_paid_runner(self):
        from issue_ai_daily import run_after_daily_fetch
        with patch('issue_ai.load_config',return_value={}),patch('issue_ai_daily.subprocess.run') as run:
            run_after_daily_fetch('tickets','db','key')
            run.assert_not_called()

    def test_firebase_publish_preserves_old_keys_and_only_publishes_summary_last(self):
        store=object.__new__(FirebaseStore);store.ref=Mock();store.owner='run';store.heartbeat=Mock()
        record={'recordId':'new','status':'classified','classifiedAt':'now'}
        rr,_=classify_records(records(),CATS,FakeClassifier(),self.cache)
        summary={**summarize(rr),'retrievalComplete':True,'runStatus':'partial','configuredModel':'gpt-5.5'}
        store.publish({'new':record},summary,{'old':{'recordId':'old'}})
        calls=store.ref.update.call_args_list
        self.assertEqual(list(calls[0].args[0]),['results/new'])
        self.assertIn('summary',calls[-1].args[0])
        self.assertIn('startup',calls[-1].args[0])
        self.assertEqual(calls[-1].args[0]['startup']['sourceVersion'],summary['generatedAt'])
        self.assertTrue(all(v is not None for c in calls for v in c.args[0].values()))
        store.ref.delete.assert_not_called()

    def test_firebase_dropped_empty_arrays_do_not_reupload_unchanged_results(self):
        store=object.__new__(FirebaseStore);store.ref=Mock();store.owner='run';store.heartbeat=Mock()
        old={'recordId':'source','status':'source','categoryCode':'Z017','classifiedAt':'yesterday'}
        current={**old,'classifiedAt':'today','evidence':[],'suggestedCategoryIds':[]}
        rr,_=classify_records(records('Z005'),CATS,FakeClassifier(),self.cache)
        summary={**summarize(rr),'retrievalComplete':True,'runStatus':'success','configuredModel':'gpt-5.5'}
        store.publish({'source':current},summary,{'source':old})
        self.assertEqual(store.ref.update.call_count,1)
        self.assertIn('summary',store.ref.update.call_args.args[0])


if __name__=='__main__':unittest.main()
