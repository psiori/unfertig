"""Small owner-service configuration API, sharing worker capacity persistence.

No configuration document or operator field is returned to the settings client.
UM preferences are overrides, never edits of the supervisor's generated file.
"""
import copy
import json
import subprocess

from storage import Conflict, atomic, encode
from worker_capacity import WorkerSettings, validate_limit
from versions import inspect


FIELDS = {
    'project_name': dict(label='Board title', type='text', maxLength=120,
                         help='Shown beside Unfertig. Empty hides the title. Updates open board pages on their next refresh.'),
    'max_workers': dict(label='Maximum concurrent workers', type='number', min=1, max=8,
                        help='Used at the next dispatch. Lowering the limit lets active work finish and keeps queued jobs.'),
    'idle_seconds': dict(label='Process after inactivity (seconds)', type='number', min=60, max=86400,
                         help='Delay after browser activity stops. Used at the next automatic-processing check, only when automation is already enabled.'),
    'closed_seconds': dict(label='Process after browsers close (seconds)', type='number', min=30, max=86400,
                           help='Delay after all browsers close. Used at the next automatic-processing check, only when automation is already enabled.'),
}


def validate_changes(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - FIELDS.keys():
        raise ValueError('Supply only the documented nonempty settings changes.')
    for key, value in changes.items():
        if key == 'project_name':
            if not isinstance(value, str) or len(value) > 120 or any(ord(c) < 32 for c in value):
                raise ValueError('Board title must be at most 120 characters, without control characters.')
        elif key == 'max_workers':
            validate_limit(value)
        elif type(value) is not int or not FIELDS[key]['min'] <= value <= FIELDS[key]['max']:
            raise ValueError(f"{FIELDS[key]['label']} must be an integer from {FIELDS[key]['min']} to 86400.")


class InstanceSettings(WorkerSettings):
    def __init__(self, workflow, processor):
        super().__init__(workflow)
        self.processor = processor

    def source(self):
        try:
            return super().source()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise ValueError('Cannot edit this configuration. Ask the operator to check host ownership, local overrides and format compatibility.') from error

    def effective(self):
        return dict(project_name=self.store.context.get('project_name', ''),
                    max_workers=self.workflow.options['max_workers'],
                    idle_seconds=self.processor.options['idle_seconds'],
                    closed_seconds=self.processor.options['closed_seconds'])

    def saved(self, value, local):
        # Effective configuration contains host-injected operator settings. Read
        # only allowlisted values, overlaying durable preferences saved this run.
        base = json.loads(self.store.config.read_bytes())
        if local and self.store.config.name == 'machine.local.json':
            shared = self.store.config.with_name('config.json')
            if shared.exists():
                base = json.loads(shared.read_bytes())
                if inspect(base, 'shared configuration')[0] == 'read_only':
                    raise ValueError('Shared configuration is newer; update before editing.')
        result = self.effective()
        for source in (base, value):
            for key in FIELDS:
                section = source if key == 'project_name' else source.get('workflow' if key == 'max_workers' else 'processing', {})
                if not isinstance(section, dict):
                    raise ValueError('Configuration sections must be objects.')
                if key in section:
                    result[key] = section[key]
        return result

    def view(self):
        with self.workflow.lock, self.processor.lock, self.store.lock:
            result = dict(fields=FIELDS, values=self.effective(), editable=False)
            try:
                self.workflow.snapshot()
                _, value, local = self.source()
                result.update(saved_values=self.saved(value, local), revision=self.revision(value),
                              layer='local' if local else 'instance', editable=True)
            except (OSError, ValueError, subprocess.SubprocessError):
                result['error'] = ('Settings are read-only. A supported, writable instance configuration and '
                                   'completed board history are required. Ask the operator to check configuration compatibility and host overrides.')
            return result

    def save(self, body):
        if set(body) != {'revision', 'changes'}:
            raise ValueError('Expected configuration revision and changes only.')
        validate_changes(body['changes'])
        with self.workflow.lock, self.processor.lock, self.store.lock:
            self.workflow.snapshot()
            path, value, local = self.source()
            self.saved(value, local)  # Validate shared compatibility before any write.
            if self.revision(value) != body['revision']:
                raise Conflict('Configuration changed. Compare the latest settings with your retained draft before saving again.')
            updated = copy.deepcopy(value)
            for key, setting in body['changes'].items():
                section = updated if key == 'project_name' else updated.setdefault('workflow' if key == 'max_workers' else 'processing', {})
                section[key] = setting
            if updated != value:
                if local:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic(path, encode(updated))
                    path.chmod(0o600)
                else:
                    self.store.transaction({'@config': updated}, 'Update instance settings')
                    if self.store.pending.exists():
                        raise Conflict('Settings were saved but local Git history is pending. Retry board history, then compare settings; your draft is retained.')
            changes = body['changes']
            if 'project_name' in changes:
                self.store.context['project_name'] = changes['project_name'].strip()
            if 'max_workers' in changes:
                self.workflow.options['max_workers'] = changes['max_workers']
            for key in ('idle_seconds', 'closed_seconds'):
                if key in changes:
                    self.processor.options[key] = changes[key]
            return self.view()
