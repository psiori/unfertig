"""Shared category vocabulary for validation, browser help and agent prompts."""
import json
from pathlib import Path

DEFINITIONS = json.loads(Path(__file__).with_name('categories.json').read_text())
CATEGORIES = DEFINITIONS['categories']


def briefing(todo):
    category = todo.get('category', '')
    definition = CATEGORIES.get(category)
    if not definition:
        return 'Work category: ' + (category or 'Unclassified') + '\n' + DEFINITIONS['boundary']
    return '\n'.join(['Work category: ' + definition['label'], definition['meaning'],
                      'Deliverable: ' + definition['deliverable'],
                      'Complete when: ' + definition['completion'],
                      'Instructions: ' + definition['instructions'], DEFINITIONS['boundary']])


def managed_briefing(todo):
    """Category-aware scope for an already authorized Implement/Retry launch.

    Never use this to authorize idea processing or infer permission from category.
    Keep stored descriptions intact, including historical processing notes.
    """
    advice = json.loads(Path(__file__).with_name('agent_advice.json').read_text())
    lines = [briefing(todo), *advice['managed_stage']]
    if todo.get('category') in ('implementation', 'bugfix', 'refactoring'):
        lines.extend(advice['managed_code'])
    return '\n'.join(lines)
