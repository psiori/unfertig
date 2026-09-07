"""T0028 source detail/priority contract on both existing transports."""
import copy
import uuid
import unittest
from unittest.mock import patch
from test_transports import Conformance
from aggregation import SourceRejected, Unreachable, exchange, filesystem
from storage import BoardStore, digest


class SourceRecords:
    def setUp(self):
        Conformance.setUp(self)

    def record(self, project='alpha'):
        return self.router.source_record(dict(project_id=project, todo_id='T0001'))

    def edit(self, detail=None, priority='urgent'):
        detail = detail or self.record()
        return dict(project_id=detail['project_id'], todo_id=detail['todo']['id'],
                    original=detail['todo'], revision=detail['revision'], preflight=detail['preflight'],
                    priority=priority, actor='Editor', request_id=uuid.uuid4().hex)

    def test_owner_details_duplicate_ids_and_priority_receipt(self):
        detail=self.record(); other=self.record('beta')
        self.assertNotEqual(detail['context']['data'],other['context']['data'])
        self.assertEqual(detail['revision'],digest(detail['todo']))
        body=self.edit(detail);saved=self.router.source_priority(body)
        self.assertEqual(saved['todo']['priority'],'urgent')
        self.assertEqual(self.beta.read()[0]['todos'][0]['priority'],other['todo']['priority'])
        self.assertEqual(self.inbox.read()[0]['todos'],[])
        self.assertEqual(self.router.source_priority(body)['todo'],saved['todo'])
        self.assertEqual(len(list(self.alpha.receipts.glob('*.json'))),1)
        for key,value in detail['todo'].items():
            if key not in ('priority','updated_at'):self.assertEqual(saved['todo'][key],value)

    def test_stale_revision_and_forged_original_do_not_overwrite(self):
        body=self.edit();self.router.source_priority(self.edit(priority='low'))
        with self.assertRaises(ValueError):self.router.source_priority(body)
        body=self.edit();body['original']=dict(body['original'],name='Forged')
        with self.assertRaisesRegex(ValueError,'exact saved original'):self.router.source_priority(body)

    def test_history_failure_and_same_request_recovery(self):
        body=self.edit();real=BoardStore.commit_pending
        def fail(store):return False if store.root==self.alpha.root else real(store)
        with patch.object(BoardStore,'commit_pending',fail):saved=self.router.source_priority(body)
        self.assertTrue(saved['history']['pending'])
        self.alpha.commit_pending()
        self.assertFalse(self.router.source_priority(body)['history']['pending'])
        self.assertEqual(len(list(self.alpha.receipts.glob('*.json'))),1)

    def test_noop_and_preflight_block(self):
        detail=self.record();body=self.edit(detail,detail['todo']['priority'])
        before=self.alpha.path.read_bytes()
        result=self.router.source_priority(body)
        self.assertEqual(result['todo'],detail['todo']);self.assertEqual(self.alpha.path.read_bytes(),before)
        body=self.edit();body['preflight']['repository']='/wrong'
        with self.assertRaisesRegex(ValueError,'context changed'):self.router.source_priority(body)


class HTTPRecords(SourceRecords, unittest.TestCase):
    enabled={'http':True,'filesystem':False}


class FilesystemRecords(SourceRecords, unittest.TestCase):
    enabled={'http':False,'filesystem':True}

    def test_filesystem_interruption_keeps_uncertain_receipt_retry(self):
        body=self.edit()
        def lose(source, validator, request=None, expected=None):
            result=filesystem(source,validator,request,expected)
            if request is not None:raise OSError('lost delivery result')
            return result
        with patch('aggregation.filesystem',side_effect=lose):
            with self.assertRaises(SourceRejected) as caught:self.router.source_priority(body)
        self.assertEqual(caught.exception.status,503)
        self.assertEqual(self.router.source_priority(body)['todo']['priority'],'urgent')
        self.assertEqual(len(list(self.alpha.receipts.glob('*.json'))),1)


class SwitchingRecords(SourceRecords, unittest.TestCase):
    enabled={'http':True,'filesystem':True}

    def test_lost_response_replays_exact_priority_request(self):
        body=self.edit();real=exchange
        def lose(source,path='/api/state',body=None,token=None):
            value=real(source,path,body,token)
            if path=='/api/changes':raise Unreachable('response lost')
            return value
        with patch('aggregation.exchange',side_effect=lose):result=self.router.source_priority(body)
        self.assertEqual(result['todo']['priority'],'urgent')
        self.assertEqual(result['transport'],'filesystem')
        self.assertEqual(len(list(self.alpha.receipts.glob('*.json'))),1)
