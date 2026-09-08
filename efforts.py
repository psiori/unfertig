"""Shared task complexity hints and explicit model/reasoning execution profiles."""
import json
import re
from pathlib import Path

DEFINITIONS = json.loads(Path(__file__).with_name('efforts.json').read_text())
EFFORTS = DEFINITIONS['values']
DEFAULT_EFFORT = DEFINITIONS['default']
PROFILES = DEFINITIONS['profiles']
DEFAULT_PROFILE = 'auto'


def saved_effort(todo):
    value = todo.get('effort', DEFAULT_EFFORT)
    if not isinstance(value, str) or value not in EFFORTS:
        raise ValueError('Unsupported effort ' + repr(value) + '. Choose a supported effort on the owning board.')
    return value


def saved_profile(todo):
    value = todo.get('execution_profile', DEFAULT_PROFILE)
    if not isinstance(value, str) or value not in (DEFAULT_PROFILE, *PROFILES):
        raise ValueError('Unsupported execution profile ' + repr(value) + '. Choose Automatic or a supported profile on the owning board.')
    return value


def resolve(todo, role='implementation'):
    effort, requested = saved_effort(todo), saved_profile(todo)
    profile, reason = requested, 'Manual selection'
    if requested == DEFAULT_PROFILE:
        profile, reason = DEFINITIONS['automatic_default'], 'Ordinary multi-file work or unspecified scope'
        text = '\n'.join(str(todo.get(k, '')) for k in ('name', 'description'))
        for rule in DEFINITIONS['automatic']:
            if (effort in rule.get('efforts', []) or todo.get('category') in rule.get('categories', [])
                    or role in rule.get('roles', []) or (rule.get('pattern') and re.search(rule['pattern'], text, re.I))):
                profile, reason = rule['profile'], rule['reason']
                break
    return dict(requested=requested, profile=profile, **PROFILES[profile], reason=reason)


def processing_guidance():
    return DEFINITIONS['processing'] + ' Supported effort values: ' + ', '.join(EFFORTS) + '. Default: ' + DEFAULT_EFFORT + '.'


def briefing(todo, role='implementation'):
    selected = resolve(todo, role)
    return ('Agent effort: ' + selected['label'] + ' (' + selected['requested'] + '). '
            + selected['model'] + ', reasoning ' + selected['reasoning_effort'] + '. ' + selected['reason']
            + '. Use this explicit profile for a new agent session; never silently substitute another model.')


def launch_arguments(todo, role='implementation'):
    selected = resolve(todo, role)
    return ['-m', selected['model'], '-c', 'model_reasoning_effort=' + json.dumps(selected['reasoning_effort'])]
