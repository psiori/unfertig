import json
from pathlib import Path
import tempfile
import unittest

from briefings import advice, context_guide, copied_context, task_input, agent_run
from context_sources import bundle, SOURCE_LIMIT, TOTAL_LIMIT
from unittest.mock import patch
import subprocess
import sys
import hashlib
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
            (root/'ruleset').mkdir()
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

    def test_complete_bounded_sources_and_role_additions_shared_by_copied_briefings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name, text in [('AGENTS.md','Full authority'), ('host.md','Host additions'), ('routing.md','Only aggregation'), ('PROCESS.md','Short process')]:
                (root/name).write_text(text)
            options=dict(working_directory=directory, developer='sl', context_sources={'common':['host.md'], 'aggregation':['routing.md']})
            snapshot={'context':dict(process=str(root/'PROCESS.md'), processing=options)}
            copied=copied_context(snapshot, {'id':'T0001'})
            launched=context_guide(root, 'sl', {'id':'T0001'}, sources=options['context_sources'], process=root/'PROCESS.md')
            self.assertEqual(copied, launched)
            self.assertIn('Full authority', copied); self.assertIn('Host additions', copied)
            self.assertNotIn('Only aggregation', copied)
            self.assertIn('Only aggregation', copied_context(snapshot, role='aggregation'))
            (root/'large.md').write_text('x'*(SOURCE_LIMIT+1))
            packet=bundle([root/'AGENTS.md',root/'large.md'], root=root,developer='sl',role='implementation',task={})
            self.assertEqual(packet['sources'][0]['text'], 'Full authority')
            self.assertNotIn('text', packet['sources'][1]); self.assertFalse(packet['complete'])
            self.assertIn('size limit', packet['sources'][1]['status'])
            paths=[]
            for i in range(4):
                path=root/str(i); path.write_text('a'*SOURCE_LIMIT); paths.append(path)
            packet=bundle(paths,root=root,developer='sl',role='implementation',task={})
            self.assertEqual(sum(len(s.get('text','')) for s in packet['sources']), TOTAL_LIMIT)

    def test_source_changed_during_packet_assembly_is_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            from context_sources import read_source
            path=Path(directory)/'AGENTS.md'; path.write_text('Before')
            calls=0
            def changing(source):
                nonlocal calls
                calls+=1
                if calls==3: path.write_text('After')
                return read_source(source)
            with patch('context_sources.read_source', side_effect=changing):
                packet=bundle([path],root=directory,developer='sl',role='implementation',task={})
            self.assertFalse(packet['complete']); self.assertNotIn('text',packet['sources'][0])

    def test_chunk_reader_preserves_utf8_and_rejects_stale_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'unicode.md'; text='ü'*40_000;path.write_text(text)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            command=[sys.executable,str(Path(__file__).with_name('context_sources.py')),str(path),'--sha256',digest]
            offset=0; result=''
            while offset<path.stat().st_size:
                completed=subprocess.run([*command,'--offset',str(offset)],capture_output=True,text=True,check=True)
                item=json.loads(completed.stdout.split(' ',1)[1]);result+=item['text'];offset=item['next_offset']
            self.assertEqual(result,text)
            path.write_text('Changed')
            self.assertNotEqual(subprocess.run([*command,'--verify'],capture_output=True).returncode,0)

    def test_host_addition_migration_and_validation_preserve_explicit_sources(self):
        from versions import migrate
        from processing import settings
        value={'format_version':'1.20.0','processing':{'context_sources':{'integration':['review.md']},'extension':'keep'}}
        result=migrate(value,'config')
        self.assertEqual(result['processing'], value['processing'])
        self.assertEqual(migrate(result,'config'),result)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for sources in (None, [], {'wrong_role':[]}, {'common':'file.md'}, {'common':[None]}):
                with self.assertRaises(ValueError):settings({'context_sources':sources},root,root,root)
