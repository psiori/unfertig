"""Persisted JSON and API compatibility. See VERSIONING.md before any change."""
import copy
import re

FORMAT_VERSION = '1.10.0'
PROTOCOL_VERSION = '2.0.0'


class VersionError(ValueError):
    pass


def parse(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise VersionError('Version must be a major.minor.build string, for example 1.1.0.')
    return tuple(map(int, value.split('.')))


def inspect(value, label='data', supported=FORMAT_VERSION, field='format_version'):
    if not isinstance(value, dict):
        raise VersionError(f'{label} must be a JSON object.')
    version = value.get(field, '0.0.0')
    found, current = parse(version), parse(supported)
    if found[0] > current[0]:
        raise VersionError(f'{label} uses {field} {version}; supported {supported}. Update Unfertig before opening or changing this data.')
    if found > current:
        level = 'read_only' if found[:2] > current[:2] else 'compatible'
        return level, f'{label} uses newer {field} {version} (supported {supported}). ' + ('Read-only until Unfertig is updated.' if level == 'read_only' else 'Compatible build; version and unknown fields are preserved.')
    if found < current and field == 'format_version' and version not in ('0.0.0', '1.0.0', '1.1.0', '1.2.0', '1.3.0', '1.4.0', '1.5.0', '1.6.0', '1.7.0', '1.8.0', '1.9.0'):
        raise VersionError(f'No migration registered for {label} version {version}. Update Unfertig; data was not changed.')
    return ('legacy' if found < current else 'current'), ''


def introduce_version(value, kind):
    value['format_version'] = '1.0.0'
    return value


def useful_defaults(value, kind):
    if kind == 'todo':
        defaults = dict(group='', tags=[], source_ideas=[], priority='normal', status='open',
                        closed_by='', date_closed='', pr_url='', commit_url='', commit_hash='')
        if 'date_entered' in value:
            defaults['updated_at'] = value['date_entered']
        for field, default in defaults.items():
            value.setdefault(field, default)
    elif kind == 'config':
        value.setdefault('port', 8765)
    value['format_version'] = '1.1.0'
    return value


# Every supported step has one deterministic successor. Never jump over a step.
def aggregation_format(value, kind):
    # Optional routing/provenance fields stay absent on legacy records.
    value['format_version'] = '1.2.0'
    return value


def transport_format(value, kind):
    if kind == 'config' and value.get('mode') == 'aggregation':
        value.setdefault('transports', {'http': True, 'filesystem': False})
    value['format_version'] = '1.3.0'
    return value


def processing_format(value, kind):
    if kind == 'config':
        value.setdefault('processing', {'enabled': True, 'automatic': False, 'idle_seconds': 600, 'closed_seconds': 90})
    # Do not guess a capturing machine for existing ideas.
    value['format_version'] = '1.4.0'
    return value


def workflow_format(value, kind):
    if kind == 'config':
        value.setdefault('workflow', {'automatic': False})
    value['format_version'] = '1.5.0'
    return value


def workflow_switch_format(value, kind):
    if kind == 'config':
        workflow = value.setdefault('workflow', {})
        if isinstance(workflow, dict):
            workflow.setdefault('enabled', False)
    value['format_version'] = '1.6.0'
    return value


def category_format(value, kind):
    # Absence means unclassified; never infer work type from existing records.
    value['format_version'] = '1.7.0'
    return value


def parallel_workflow_format(value, kind):
    if kind == 'config':
        workflow = value.setdefault('workflow', {})
        if isinstance(workflow, dict):
            for key, default in dict(max_workers=2, automatic_merge=False, automatic_publish=False, automatic_deploy=False).items():
                workflow.setdefault(key, default)
    value['format_version'] = '1.8.0'
    return value


def deployment_format(value, kind):
    # New coordinator phases require newer-writer protection; no permission grant.
    value['format_version'] = '1.9.0'
    return value


def discovery_format(value, kind):
    if kind == 'config' and value.get('mode') == 'aggregation':
        value.setdefault('search_paths', [])
    value['format_version'] = '1.10.0'
    return value


MIGRATIONS = {'0.0.0': introduce_version, '1.0.0': useful_defaults,
              '1.1.0': aggregation_format, '1.2.0': transport_format, '1.3.0': processing_format, '1.4.0': workflow_format, '1.5.0': workflow_switch_format, '1.6.0': category_format, '1.7.0': parallel_workflow_format, '1.8.0': deployment_format, '1.9.0': discovery_format}


def migrate(value, kind, label='data'):
    inspect(value, label)
    result = copy.deepcopy(value)
    while parse(result.get('format_version', '0.0.0')) < parse(FORMAT_VERSION):
        previous = result.get('format_version', '0.0.0')
        step = MIGRATIONS.get(previous)
        if step is None:
            raise VersionError(f'No migration from {previous} for {label}.')
        result = step(result, kind)
        if parse(result['format_version']) <= parse(previous):
            raise VersionError('Migration did not advance its version.')
    return result


def semantic(value):
    """Only strip format metadata, never user extensions, for migration audits."""
    result = copy.deepcopy(value)
    result.pop('format_version', None)
    if isinstance(result.get('todos'), list) and isinstance(result.get('ideas'), list):
        for todo in result['todos']:
            todo.pop('format_version', None)
    return result
