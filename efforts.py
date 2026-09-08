"""Single effort contract for records, browser data, planning and execution."""
import json
from pathlib import Path

DEFINITIONS = json.loads(Path(__file__).with_name('efforts.json').read_text())
EFFORTS = DEFINITIONS['values']
DEFAULT_EFFORT = DEFINITIONS['default']


def saved_effort(todo):
    value = todo.get('effort', DEFAULT_EFFORT)
    if not isinstance(value, str) or value not in EFFORTS:
        raise ValueError(f'Unsupported effort {value!r}; choose from {", ".join(EFFORTS)}.')
    return value


def processing_guidance():
    return DEFINITIONS['processing'] + ' Supported effort values: ' + ', '.join(EFFORTS) + '. Default: ' + DEFAULT_EFFORT + '.'


def briefing(todo):
    return 'Agent effort: ' + saved_effort(todo)


def launch_arguments(todo):
    return ['-c', 'model_reasoning_effort=' + json.dumps(saved_effort(todo))]
