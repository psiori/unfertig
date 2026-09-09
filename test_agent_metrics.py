import json
import unittest
from unittest.mock import Mock, patch
from agent_metrics import CURRENT_AGENT, observe, checked, accepted, summary
from briefings import agent_run
from efforts import resolve


class MetricsTests(unittest.TestCase):
    def test_verified_result_includes_failed_checks_and_authorized_repair(self):
        workflow=Mock(spec=['save','argv','command']); workflow.argv.return_value=['check']
        run={'worktree':'/disposable', 'repository':'/repo'}
        todo={'id':'T0001','effort':'low','workflow':run}
        with agent_run(workflow,todo,run,'implementation','bounded task'):
            pass
        self.assertNotIn('accepted_result',run)
        workflow.command.side_effect=ValueError('Assertion failed')
        with self.assertRaises(ValueError): checked(workflow,run,todo['id'])
        self.assertEqual(resolve(todo)['profile'],'sol-medium')
        self.assertEqual(resolve(dict(todo,execution_profile='terra-medium'))['profile'],'terra-medium')
        workflow.command.side_effect=None
        with agent_run(workflow,todo,run,'implementation','repair task'):
            pass
        checked(workflow,run,todo['id']); run['commit']='accepted-head'; accepted(run)
        result=summary(todo)
        self.assertEqual(result['retries'],1); self.assertEqual(result['failed_checks'],1)
        self.assertIsNotNone(result['accepted_seconds']); self.assertIsNotNone(result['test_seconds'])
        self.assertEqual(run['accepted_result']['commit'],'accepted-head')

    def test_unavailable_checks_do_not_change_model_or_claim_acceptance(self):
        workflow=Mock(spec=['save','argv','command']); workflow.argv.return_value=['missing']
        run={'worktree':'/disposable'};todo={'id':'T0001','effort':'low','workflow':run}
        with agent_run(workflow,todo,run,'implementation','task'): pass
        for error in (FileNotFoundError('missing'), ValueError('Approval rejected'),ValueError('Authentication unavailable')):
            workflow.command.side_effect=error
            with self.assertRaises(type(error)): checked(workflow,run,todo['id'])
            self.assertEqual(resolve(todo)['profile'],'terra-medium')
        self.assertIsNone(summary(todo)['accepted_seconds'])
        self.assertIsNone(summary({'id':'old'})['test_seconds'])

    def test_observed_reads_count_repetition_but_hash_checks_are_separate(self):
        entry={};token=CURRENT_AGENT.set(entry)
        try:
            for operation in ('read','verify','read'):
                output='UNFERTIG_CONTEXT_READ '+json.dumps(dict(path='/task/AGENTS.md',sha256='hash',operation=operation))
                event=dict(type='item.completed',item=dict(type='command_execution',aggregated_output=output))
                observe(json.dumps(event))
            observe('not JSON'); observe('[]'); observe('{"item": null}')
        finally:CURRENT_AGENT.reset(token)
        self.assertEqual(entry['context_reads']['reads'],2)
        self.assertEqual(entry['context_reads']['rereads'],1)
        self.assertEqual(entry['context_reads']['hash_checks'],1)
