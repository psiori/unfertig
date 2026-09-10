"""Persisted JSON and API compatibility. See VERSIONING.md before any change."""
import copy
import re
from efforts import DEFAULT_EFFORT

FORMAT_VERSION = '1.22.0'
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
    if found < current and field == 'format_version' and version not in MIGRATIONS:
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
            for key, default in dict(max_workers=4, automatic_merge=False, automatic_publish=False, automatic_deploy=False).items():
                workflow.setdefault(key, default)
    value['format_version'] = '1.8.0'
    return value


def deployment_format(value, kind):
    # New coordinator phases require newer-writer protection; no permission grant.
    value['format_version'] = '1.9.0'
    return value


def completion_format(value, kind):
    # Missing summaries remain missing: never fabricate historical outcomes.
    value['format_version'] = '1.10.0'
    return value


def discovery_format(value, kind):
    if kind == 'config' and value.get('mode') == 'aggregation':
        value.setdefault('search_paths', [])
    value['format_version'] = '1.11.0'
    return value


def effort_format(value, kind):
    if kind == 'todo':
        value.setdefault('effort', DEFAULT_EFFORT)
    value['format_version'] = '1.12.0'
    return value


def integration_queue_format(value, kind):
    # Existing claims already express delivery failures. Preserve them verbatim;
    # the new coordinator treats outstanding failures as durable queue barriers.
    value['format_version'] = '1.13.0'
    return value


def automatic_startup_format(value, kind):
    # Preserve historical deployment drivers; new runs declare startup ownership.
    value['format_version'] = '1.14.0'
    return value


def external_completion_format(value, kind):
    # Preserve old failures and attribution; manual closure is no evidence.
    value['format_version'] = '1.15.0'
    return value


def managed_completion_format(value, kind):
    # No inferred success, publication permission or rewritten historical reports.
    value['format_version'] = '1.16.0'
    return value


def context_repositories_format(value, kind):
    # Historical single-repository claims retain their original authorization.
    # Only a new owner-authorized run receives a context repository manifest.
    value['format_version'] = '1.17.0'
    return value


def worker_capacity_format(value, kind):
    if kind == 'config':
        workflow = value.setdefault('workflow', {})
        if isinstance(workflow, dict):
            workflow.setdefault('max_workers', 4)
    value['format_version'] = '1.18.0'
    return value


def bugfix_category_format(value, kind):
    # Retain existing missing-config defaults; never recategorize.
    value = worker_capacity_format(value, kind)
    # Protect the expanded vocabulary from older writers.
    value['format_version'] = '1.19.0'
    return value


def publication_hooks_format(value, kind):
    # No automatic replay of historical publication or conversion of restart
    # recipes into executable hooks. Preserve explicit values and old receipts.
    value = worker_capacity_format(value, kind)
    if kind == 'config' and isinstance(value.get('workflow'), dict):
        value['workflow'].setdefault('after_publish', [])
    value['format_version'] = '1.20.0'
    return value


def execution_profiles_format(value, kind):
    # Old effort hints were defaulted by writers; do not invent manual choices.
    value = publication_hooks_format(value, kind)
    if kind == 'todo':
        value.setdefault('execution_profile', 'auto')
    if kind == 'config':
        processing = value.setdefault('processing', {})
        if isinstance(processing, dict):
            processing.setdefault('context_sources', {})
    value['format_version'] = '1.21.0'
    return value


def relaxed_integration_format(value, kind):
    value = execution_profiles_format(value, kind)
    # No inferred repair/publication permission or fabricated historical evidence.
    if kind == 'config':
        workflow = value.setdefault('workflow', {})
        if isinstance(workflow, dict):
            integration = workflow.setdefault('integration', {})
            if isinstance(integration, dict):
                for key, default in dict(mode='strict', automatic_repair=False, max_attempts=3, reuse_board_metadata=False).items():
                    integration.setdefault(key, default)
    value['format_version'] = '1.22.0'
    return value


MIGRATIONS = {'0.0.0': introduce_version, '1.0.0': useful_defaults,
              '1.1.0': aggregation_format, '1.2.0': transport_format, '1.3.0': processing_format,
              '1.4.0': workflow_format, '1.5.0': workflow_switch_format, '1.6.0': category_format,
              '1.7.0': parallel_workflow_format, '1.8.0': deployment_format,
              '1.9.0': completion_format, '1.10.0': discovery_format, '1.11.0': effort_format,
              '1.12.0': integration_queue_format, '1.13.0': automatic_startup_format,
              '1.14.0': external_completion_format, '1.15.0': managed_completion_format, '1.16.0': context_repositories_format,
              '1.17.0': worker_capacity_format, '1.18.0': bugfix_category_format, '1.19.0': publication_hooks_format, '1.20.0': execution_profiles_format, '1.21.0': relaxed_integration_format}


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
