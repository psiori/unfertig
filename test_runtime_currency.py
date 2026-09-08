"""Disposable remote observations: no actual GitHub requests."""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import threading
import unittest
from unittest.mock import patch
from runtime_currency import RuntimeCurrency

class RuntimeCurrencyTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.currency=RuntimeCurrency(Path('/disposable/app'),'a'*40,clock=lambda:self.now)
    def response(self, commit='a'*40):
        return SimpleNamespace(returncode=0,stdout=commit+'\trefs/heads/main\n')
    def test_current_outdated_unavailable_and_expired(self):
        c=self.currency
        self.assertEqual(c.view()['state'],'unknown')
        with patch('runtime_currency.subprocess.run',return_value=self.response()) as run:
            c.check();self.assertEqual(c.view()['state'],'current')
            self.assertEqual(run.call_args.args[0],['git','-C','/disposable/app','ls-remote','--exit-code','origin','refs/heads/main'])
            self.assertEqual(run.call_args.kwargs['timeout'],8)
        self.now=121;self.assertEqual(c.view()['state'],'unknown')
        with patch('runtime_currency.subprocess.run',return_value=self.response('b'*40)):c.check()
        self.assertEqual(c.view()['state'],'outdated')
        for failure in [OSError('offline'),subprocess.TimeoutExpired('git',8)]:
            with patch('runtime_currency.subprocess.run',side_effect=failure):c.check()
            self.assertEqual(c.view()['state'],'unknown')
        for response in [SimpleNamespace(returncode=1,stdout=''),self.response('invalid'),SimpleNamespace(returncode=0,stdout='a'*40+'\trefs/heads/other')]:
            with patch('runtime_currency.subprocess.run',return_value=response):c.check()
            self.assertEqual(c.view()['state'],'unknown')
    def test_slow_observation_does_not_block_reads_or_launch_duplicates(self):
        started,release=threading.Event(),threading.Event()
        def read(*args,**kwargs):
            started.set();release.wait(2);return self.response()
        c=self.currency
        with patch('runtime_currency.subprocess.run',side_effect=read) as run:
            try:
                c.tick();self.assertTrue(started.wait(1))
                for _ in range(3):c.tick();self.assertEqual(c.view()['state'],'unknown')
                self.assertEqual(run.call_count,1)
            finally:release.set();c.worker.join(2)
            self.assertEqual(c.view()['state'],'current');c.tick();self.assertEqual(run.call_count,1)
            self.now=61;c.tick();c.worker.join(2);self.assertEqual(run.call_count,2)
    def test_preview_or_unknown_revision_never_contacts_remote(self):
        for c in [RuntimeCurrency(Path('/tmp/preview'),'a'*40,enabled=False),RuntimeCurrency(Path('/tmp/app'),'')]:
            with patch('runtime_currency.subprocess.run') as run:
                c.tick();self.assertEqual(c.view()['state'],'unknown');run.assert_not_called()
