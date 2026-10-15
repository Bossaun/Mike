"""Versioned, public feature defaults for the already approved closed team.

No identities, model/budget overrides, network requests or live state are stored
here. Existing explicit private feature configuration always takes precedence.
"""
from copy import deepcopy

from .intake import TEAM_RECIPIENTS, validate_team


PROFILE = 'knowledge-reminders-v1'


def apply_team_rollout(config, profile):
    result = deepcopy(config)
    if profile in ('', 'off', None):
        return result
    if profile != PROFILE:
        raise ValueError('Unknown GOLDWAY feature profile')
    validate_team(result)
    readers = list(TEAM_RECIPIENTS)
    result.setdefault('intelligence', {
        'enabled': True,
        'refresh_seconds': 900,
        'history_messages': 8,
        'history_chars': 6000,
        'knowledge_chars': 7000,
        'sources': [
            {'id': 'goldway-public-site', 'kind': 'website',
             'url': 'https://goldwaybaltic.com',
             'title': 'GOLDWAY — официальный публичный сайт', 'readers': list(readers)},
            {'id': 'goldway-public-catalog', 'kind': 'public_catalog',
             'url': 'https://gw-api.abdulwaheedaziz8.workers.dev?api=catalog',
             'readers': list(readers)},
        ],
    })
    result.setdefault('scheduling', {'enabled': True})
    return result
