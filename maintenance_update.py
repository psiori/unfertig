"""Application adapter to the UM host's supported update-request CLI.

No runtime checkout writes or process signals. The host owns durable schema-1
requests, installation, and startup; acceptance is never installation evidence.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess


class UpdateRequest:
    def __init__(self, environment):
        self.root = Path(environment['UM_PROJECT_ROOT']).resolve() if environment.get('UM_PROJECT_ROOT') else None
        self.tool = Path(environment['UM_TOOL_DIR']).resolve() if environment.get('UM_TOOL_DIR') else None
        self.environment = dict(environment)

    def availability(self):
        if not self.root or self.tool != self.root / 'tools' / 'unfertig':
            return False, 'Update requests require a managed UM runtime.'
        if not all((self.root / path).is_file() for path in ('scripts/run_uv.sh', 'scripts/request_tool_update.py')):
            return False, 'The host update-request CLI is unavailable.'
        try:
            config = json.loads((self.root / 'state/unfertig/config/launch.json').read_text())
            if config.get('cooperative_restart') is not True or config.get('update', {}).get('branch') != 'main':
                return False, 'The host must enable cooperative updates from main.'
        except (OSError, ValueError, AttributeError):
            return False, 'The host update configuration is unavailable or invalid.'
        return True, ''

    def event(self, target):
        if not isinstance(target, str) or not re.fullmatch('[0-9a-f]{40,64}', target):
            raise ValueError('A confirmed published revision is required.')
        # One durable request per observed release, shared across tabs/restarts.
        return 'manual-' + hashlib.sha256(target.encode()).hexdigest()

    def retained(self, target):
        event = self.event(target)
        path = self.root / '.local/tools/update-requests/unfertig' / (event + '.json')
        if not path.exists():
            return {}
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or value.get('schema_version') != 1 or
                value.get('id') != event or value.get('tool') != 'unfertig' or
                value.get('commit') != target or value.get('state') not in ('pending', 'applying', 'complete', 'failed')):
            raise ValueError('Invalid retained update request; inspect host evidence before retrying.')
        return value

    def view(self, target):
        available, reason = self.availability()
        result = dict(available=available, reason=reason, request={})
        if available and target:
            result['request'] = self.retained(target)
        return result

    def submit(self, target):
        available, reason = self.availability()
        if not available:
            raise ValueError(reason)
        event = self.event(target)
        environment = {key: value for key, value in self.environment.items()
                       if not key.startswith('UNFERTIG_')}
        environment['UNFERTIG_PUBLISHED_COMMIT'] = target
        try:
            result = subprocess.run(['sh', str(self.root / 'scripts/run_uv.sh'),
                                     str(self.root / 'scripts/request_tool_update.py'),
                                     '--tool', 'unfertig', '--event', event],
                                    cwd=self.root, env=environment, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired as error:
            raise ValueError('Update request outcome is uncertain. Retry the same action to inspect its retained request; do not submit a different update.') from error
        if result.returncode:
            raise ValueError('Host rejected the update request: ' + (result.stderr or result.stdout)[-2000:])
        # Use the durable request, including a previous failed/complete result.
        value = self.retained(target)
        if not value:
            raise ValueError('Host returned without a durable request. Installation is unconfirmed.')
        return value
