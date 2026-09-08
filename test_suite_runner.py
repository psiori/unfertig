import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import suite_runner


class SuiteRunnerTests(unittest.TestCase):
    def test_partition_preserves_all_ids_and_durations_only_affect_order(self):
        tests = suite_runner.discover()
        ids = sorted(t.id() for t in tests)
        for workers in (1, 2, 4):
            shards = suite_runner.partition(tests, workers, {})
            self.assertEqual(sorted(i for shard in shards for i in shard), ids)
            self.assertLessEqual(len(shards), workers)

    def test_worker_failures_surface_original_test_name_and_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'test_injected.py').write_text('import unittest\nclass Broken(unittest.TestCase):\n def test_regression(self): self.assertEqual(1, 2, "injected regression")\n')
            ids = root / 'ids.json'
            ids.write_text(json.dumps(['test_injected.Broken.test_regression']))
            # Exercise the worker directly: nesting another slot-acquiring recipe
            # inside a leased worker could deadlock four concurrent ticket jobs.
            code = ('import sys; from contextlib import nullcontext; '
                    f'sys.path.insert(0, {str(Path(suite_runner.__file__).parent)!r}); '
                    'import suite_runner; suite_runner.slot = nullcontext; '
                    f'sys.argv = ["suite_runner", "--worker", {str(ids)!r}]; '
                    'raise SystemExit(suite_runner.main())')
            result = subprocess.run([sys.executable, '-c', code],
                                    cwd=root, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('test_injected.Broken.test_regression', (result.stdout + result.stderr))
        self.assertIn('injected regression', (result.stdout + result.stderr))

