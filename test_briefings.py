import json
from pathlib import Path
import tempfile
import unittest

from briefings import advice, context_guide, task_input
from efforts import launch_arguments, resolve


class BriefingTests(unittest.TestCase):
    def test_profiles_match_scope_acceptance_and_floor(self):
        for todo, role, expected in json.loads(Path(__file__).with_name('test_profile_cases.json').read_text()):
            with self.subTest(todo=todo, role=role):
                selected = resolve(todo, role)
                self.assertEqual(selected['profile'], expected)
                self.assertIn(selected['reasoning_effort'], ('medium', 'high'))
                self.assertEqual(launch_arguments(todo, role)[1], selected['model'])
        for value in ('', None, 'luna-low', {}, 1):
            with self.assertRaises(ValueError):
                resolve({'execution_profile': value})

    def test_role_advice_is_concise_without_unrelated_development_reads(self):
        for role in ('managed','manual','processing','context_managed','integration'):
            text = advice(role)
            self.assertIn('well-structured, concise', text)
            self.assertNotIn('TRANSPORTS.md', text)
            self.assertNotIn('VERSIONING.md', text)
            self.assertNotIn('versions.py', text)
            self.assertIn('required source read', text)
        todo = {'name':'Task', 'extension':{'keep':True}, 'workflow':{'message':'large old log'}}
        self.assertEqual(json.loads(task_input(todo)), {'name':'Task','extension':{'keep':True}})

    def test_uncached_index_reloads_changed_deleted_and_symlinked_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'design').mkdir()
            (root/'AGENTS.md').write_text('Required source')
            design=root/'design/task.md'; design.write_text('First')
            unrelated=root/'design/unrelated.md'; unrelated.write_text('Not selected')
            todo={'description':'See design/task.md'}
            first=context_guide(root, 'sl', todo)
            self.assertIn(str(design), first); self.assertNotIn(str(unrelated), first)
            self.assertIn('missing; resolve if required', first)
            design.write_text('Second'); second=context_guide(root,'sl',todo)
            self.assertNotEqual(first, second)
            design.unlink(); third=context_guide(root,'sl',todo)
            self.assertNotEqual(second, third)
            design.symlink_to(unrelated); fourth=context_guide(root,'sl',todo)
            self.assertNotEqual(third, fourth)
            self.assertIn('not a substitute for source contents', fourth)
