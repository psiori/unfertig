"""Exercise the application bridge with copied host code and disposable state.

uv run --no-project --python 3.12 python maintenance_host_fixture.py --context UM
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

from instance_maintenance import Maintenance
from maintenance_update import UpdateRequest


def verify(context):
    with tempfile.TemporaryDirectory(prefix='unfertig-host-request-') as temporary:
        root = Path(temporary)
        for relative in ('scripts/run_uv.sh', 'scripts/request_tool_update.py', 'scripts/tool_restart.py',
                         'pyproject.toml', 'uv.lock', '.python-version'):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(context / relative, target)
        launch = root / 'state/unfertig/config/launch.json'
        launch.parent.mkdir(parents=True)
        launch.write_text(json.dumps(dict(cooperative_restart=True, update=dict(branch='main', policy='trusted_main'))))
        spec = importlib.util.spec_from_file_location('fixture_host_restart', root / 'scripts/tool_restart.py')
        host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host)
        child = Mock()
        child.poll.return_value = None
        tool = dict(name='unfertig', record=dict(state='running'), process=child)
        environment = {**os.environ, 'UM_PROJECT_ROOT': str(root), 'UM_TOOL_DIR': str(root / 'tools/unfertig'),
                       **host.environment(root, tool)}
        store = SimpleNamespace(context=dict(runtime_commit='a' * 40), git=False,
                                snapshot=lambda: dict(history=dict(pending=False)))
        import threading
        store.lock = threading.RLock()
        server = SimpleNamespace(store=store, workflow=None, processing=None, shutdown=Mock())
        maintenance = Maintenance(server, environment)
        maintenance.currency = Mock()
        maintenance.currency.view.return_value = dict(state='outdated', target_commit='b' * 40)
        maintenance.announce()
        action = dict(action='update_restart', target_commit='b' * 40)
        first = maintenance.action(action)['update_action']['request']
        assert first['state'] == 'pending'
        again = maintenance.action(action)['update_action']['request']
        assert first == again
        assert len(host.pending(root, 'unfertig')) == 1
        assert not tool['restart_request'].exists()
        assert host.poll(root, tool) is None
        with maintenance.mutation():
            maintenance.tick()
            assert maintenance.phase == 'draining'
            assert maintenance.blockers == ['Finishing API writes']
            assert host.poll(root, tool) is None
            server.shutdown.assert_not_called()
        maintenance.tick()
        assert maintenance.exit_code == 75
        assert host.poll(root, tool) is None, 'Ready alone cannot authorize installation'
        child.poll.return_value = 75
        batch = host.poll(root, tool)
        assert len(batch) == 1 and batch[0]['id'] == first['id']
        host.mark(root, 'unfertig', batch, state='failed', message='Fixture Git conflict')
        retained = UpdateRequest(environment).view('b' * 40)['request']
        assert retained['state'] == 'failed'
        assert UpdateRequest(environment).submit('b' * 40) == retained, 'Repeated action must not retry a failed update'
        assert not host.pending(root, 'unfertig')
        print('PASS: copied host CLI, one durable request, writer drain, ready plus child-exit gate, retained failure and restart-safe duplicate submission.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    verify(parser.parse_args().context.resolve())
