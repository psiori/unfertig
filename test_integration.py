import unittest
from pathlib import Path

from integration import registry, migration_issues
from versions import FORMAT_VERSION, MIGRATIONS, inspect, migrate


class MigrationIntegrationTests(unittest.TestCase):
    def test_competing_successors_cannot_disappear_in_a_clean_merge(self):
        first = "MIGRATIONS = {'1.9.0': completion_format}"
        second = "MIGRATIONS = {'1.9.0': discovery_format}"
        duplicate = "MIGRATIONS = {'1.9.0': completion_format, '1.9.0': discovery_format}"
        self.assertIn('Competing', migration_issues(duplicate, [first, second])[0])
        self.assertIn('completion_format', migration_issues(second, [first, second])[0])
        sequential = "MIGRATIONS = {'1.9.0': completion_format, '1.10.0': discovery_format}"
        self.assertEqual(migration_issues(sequential, [first, second]), [])

    def test_every_supported_upgrade_preserves_queue_identity_and_extensions(self):
        claim = dict(phase='merge_queued', queued_at='2026-09-08T00:00:00Z',
                     action_requests={'request':'scope'}, scope='approved', extension=[1, 2],
                     deployment_driver='startup', external_completions=[dict(
                         outcome='superseded', actor='SL', reason='Replacement implementation',
                         at='2026-09-08T00:00:00Z', extension={'retained':True})])
        for version in MIGRATIONS:
            with self.subTest(version=version):
                old = dict(format_version=version, workflow=claim, extension={'original':'unchanged'})
                result = migrate(old, 'todo')
                self.assertEqual(result['format_version'], FORMAT_VERSION)
                self.assertEqual(result['workflow'], claim)
                self.assertEqual(result['extension'], old['extension'])
                self.assertEqual(migrate(result, 'todo'), result)
        self.assertEqual(inspect({'format_version':FORMAT_VERSION}, supported='1.12.0')[0], 'read_only')

    def test_actual_registry_has_no_duplicate_successors(self):
        parsed = registry(Path(__file__).with_name('versions.py').read_text())
        self.assertEqual(set(parsed), set(MIGRATIONS))


if __name__ == '__main__':
    unittest.main()
