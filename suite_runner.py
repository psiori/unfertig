"""Complete unittest discovery in bounded, duration-balanced process shards.

Run through uv, or workflow_support.py. This is not a selected-test cache: every
discovered ID is assigned exactly once. Duration hints affect ordering only.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path.cwd()))

LIMIT = 4


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def discover():
    loader = unittest.TestLoader()
    tests = list(flatten(loader.discover('.')))
    if loader.errors:
        raise RuntimeError('\n'.join(loader.errors))
    ids = [test.id() for test in tests]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Empty or duplicate discovered test identities')
    return tests


def partition(tests, workers, durations):
    groups = {}
    for test in tests:
        module = sys.modules[test.__class__.__module__]
        # Future shared class/module fixtures remain intact in one process.
        shared = any(hasattr(module, name) for name in ('setUpModule', 'tearDownModule'))
        shared |= any(name in cls.__dict__ for cls in type(test).__mro__
                      if cls is not unittest.TestCase
                      for name in ('setUpClass', 'tearDownClass'))
        key = module.__name__ if shared else test.id()
        groups.setdefault(key, []).append(test.id())
    shards = [[] for _ in range(workers)]
    totals = [0.0] * workers
    weight = lambda ids: sum(durations.get(ident, 0.2) for ident in ids)
    for ids in sorted(groups.values(), key=lambda ids: (-weight(ids), ids)):
        index = min(range(workers), key=lambda i: (totals[i], i))
        shards[index].extend(ids)
        totals[index] += weight(ids)
    return [sorted(ids) for ids in shards if ids]


@contextmanager
def slot():
    """OS-released leases bound aggregate workers across unrelated ticket jobs."""
    # Fixed per-user root: per-ticket TMPDIR changes cannot multiply the budget.
    root = Path('/tmp') if os.name != 'nt' else Path(os.environ['LOCALAPPDATA'])
    root = root / ('unfertig-test-slots-' + (str(os.getuid()) if os.name != 'nt' else 'user'))
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or (os.name != 'nt' and root.stat().st_uid != os.getuid()):
        raise ValueError('Test slot directory has a different owner')
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        for index in range(LIMIT):
            path = root / str(index)
            if path.is_symlink():
                raise ValueError('Test slot cannot be a symlink')
            with path.open('a+b') as lease:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        lease.seek(0)
                        if not lease.read(1):
                            lease.write(b'0'); lease.flush()
                        lease.seek(0)
                        msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                yield
                return
        time.sleep(0.05)
    raise TimeoutError('Timed out waiting for the shared test CPU budget')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, choices=range(1, LIMIT + 1), default=4)
    parser.add_argument('--manifest', action='store_true')
    parser.add_argument('--javascript', action='store_true')
    parser.add_argument('--reverse', action='store_true')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        ids = json.loads(args.worker.read_text())
        with slot():
            suite = unittest.defaultTestLoader.loadTestsFromNames(ids)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    tests = discover()
    if args.manifest:
        print(json.dumps(sorted(test.id() for test in tests), indent=2))
        return 0
    hint = Path(__file__).with_name('suite_durations.json')
    durations = json.loads(hint.read_text()) if hint.exists() else {}
    shards = partition(tests, args.workers, durations)
    children = []
    failed = False
    with tempfile.TemporaryDirectory(prefix='unfertig-suite-') as directory:
        try:
            for index, ids in enumerate(shards):
                file = Path(directory) / f'{index}.json'
                file.write_text(json.dumps(list(reversed(ids)) if args.reverse else ids))
                log = file.with_suffix('.log')
                with log.open('w') as output:
                    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                              '--worker', str(file)], stdout=output, stderr=output)
                children.append((child, log))
            for index, (child, log) in enumerate(children):
                failed |= child.wait(timeout=900) != 0
                print(f'=== Python shard {index + 1}/{len(shards)} ===', flush=True)
                print(log.read_text(), flush=True)
        finally:
            for child, _ in children:
                if child.poll() is None:
                    child.terminate()
            for child, _ in children:
                child.wait()
    if failed:
        return 1
    if args.javascript:
        with slot():
            return subprocess.call(['node', '--test', '--test-concurrency=1',
                                    *map(str, sorted(Path('.').glob('test_*.cjs')))])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
