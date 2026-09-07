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
