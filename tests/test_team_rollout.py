"""Synthetic rollout boundaries; temporary configs and in-memory SQLite only."""
from copy import deepcopy
import json
import stat

import pytest

from goldway import railway
from goldway.engine import Engine
from goldway.intake import TEAM_RECIPIENTS, validate_team
from goldway.rollout import PROFILE, apply_team_rollout
from goldway.store import Store


PUBLIC_SOURCES = {
    ('website', 'https://goldwaybaltic.com'),
    ('public_catalog', 'https://gw-api.abdulwaheedaziz8.workers.dev?api=catalog'),
}


def raw_profile():
    return {
        'business': 'GOLDWAY', 'timezone': 'Europe/Moscow',
        'employees': {name: {'name': 'Synthetic ' + name, 'telegram_user_id': number,
                             'preferences': {'language': 'ru', 'tags': ['synthetic']}}
                      for number, name in enumerate(TEAM_RECIPIENTS, 1)},
        'admins': ['test_operator'], 'business_account_user_id': 99,
        'team_roster_verified': True, 'bot_user_id': 900, 'bot_user_id_verified': True,
        'model': {'provider': 'synthetic', 'model': 'synthetic-existing-model',
                  'daily_budget': .7123, 'max_output_tokens': 437,
                  'input_per_million': .123, 'output_per_million': .456,
                  'options': {'nested': [{'preserve': 'Модель и бюджет'}]}},
        'daily_budget': {'private_override': .987},
        'allowed_group_ids': [], 'group_destinations': {}, 'meeting_recipient': None,
        'speaker_aliases': {'Синтетический оператор': 'test_operator'},
        'rules': ['Сохранить существующие правила'],
        'history': {'import': False, 'nested': [{'cutoff': 777, 'routes': []}]},
        'sheet_id': 'synthetic-existing-sheet', 'sheet_verified': False,
        'sheets': {'ranges': ['Synthetic!A1:B9'], 'read_only': True},
        'routing_options': {'business': {'mode': 'existing', 'fallback': ['bot']}},
    }


def closed_profile():
    config = raw_profile()
    config.update(team_intake={'recipients': list(TEAM_RECIPIENTS), 'started_at': 1000},
                  allowed_live_recipients=list(TEAM_RECIPIENTS), live_send=False,
                  recipients_verified=True, review_mode=True, sheets_write=False,
                  daily_summary=False, allow_onboarding_replies=False,
                  allow_registered_replies=False)
    validate_team(config)
    return config


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


@pytest.fixture(autouse=True)
def isolated_feature_environment(monkeypatch):
    monkeypatch.delenv('GOLDWAY_FEATURE_PROFILE', raising=False)


def test_versioned_defaults_enable_only_the_two_features_and_exact_public_readers():
    config = closed_profile()
    before = encoded(config)
    result = apply_team_rollout(config, PROFILE)
    assert PROFILE == 'knowledge-reminders-v1'
    assert result['scheduling'] == {'enabled': True}
    assert result['intelligence']['enabled'] is True
    assert set(result) == set(config) | {'intelligence', 'scheduling'}
    assert encoded({key: value for key, value in result.items()
                    if key not in {'intelligence', 'scheduling'}}) == before
    assert encoded(config) == before
    sources = result['intelligence']['sources']
    assert len(sources) == 2
    assert {(source['kind'], source['url']) for source in sources} == PUBLIC_SOURCES
    assert len({source['id'] for source in sources}) == 2
    for source in sources:
        assert source['readers'] == list(TEAM_RECIPIENTS)
        assert '*' not in source['readers']
        assert set(source) <= {'id', 'kind', 'url', 'title', 'readers'}
    assert validate_team(result) == validate_team(config)


@pytest.mark.parametrize('profile', [None, '', 'off'])
def test_off_is_a_deep_copy_without_enabling_or_validating_a_different_profile(profile):
    config = {'employees': {'synthetic-outsider': {'nested': [1]}},
              'intelligence': {'enabled': False}, 'custom': {'values': [1, 2]}}
    before = encoded(config)
    result = apply_team_rollout(config, profile)
    assert encoded(result) == before
    result['custom']['values'].append(3)
    result['employees']['synthetic-outsider']['nested'].clear()
    assert encoded(config) == before
    assert 'scheduling' not in result


@pytest.mark.parametrize('profile', ['unknown', 'knowledge-reminders-v2', 'OFF',
                                   'knowledge-reminders-v1 ', True, False, 1, [], {}])
def test_unknown_or_malformed_profile_fails_without_mutating_input(profile):
    config = closed_profile()
    before = encoded(config)
    with pytest.raises(ValueError, match='profile'):
        apply_team_rollout(config, profile)
    assert encoded(config) == before


@pytest.mark.parametrize('change', [
    'missing_gate', 'malformed_gate', 'extra_gate_field', 'gate_missing_member',
    'gate_extra_member', 'gate_wrong_order', 'bool_cutoff', 'zero_cutoff',
    'conflicting_personal', 'conflicting_controlled', 'conflicting_test',
    'missing_member', 'extra_member', 'wrong_admin', 'unconfirmed_roster',
    'bool_identity', 'duplicate_identity', 'account_collision', 'wildcard_routes',
    'unverified_routes', 'review_off', 'sheets_on', 'summary_on', 'onboarding_on',
    'registered_on', 'group_route', 'meeting_route', 'bot_collision',
])
def test_enabled_defaults_require_a_valid_existing_closed_team_gate(change):
    config = closed_profile()
    if change == 'missing_gate': del config['team_intake']
    elif change == 'malformed_gate': config['team_intake'] = []
    elif change == 'extra_gate_field': config['team_intake']['extra'] = True
    elif change == 'gate_missing_member': config['team_intake']['recipients'].pop()
    elif change == 'gate_extra_member': config['team_intake']['recipients'].append('outsider')
    elif change == 'gate_wrong_order': config['team_intake']['recipients'].reverse()
    elif change == 'bool_cutoff': config['team_intake']['started_at'] = True
    elif change == 'zero_cutoff': config['team_intake']['started_at'] = 0
    elif change.startswith('conflicting_'): config[change.removeprefix('conflicting_') + '_intake'] = {}
    elif change == 'missing_member': del config['employees']['frost']
    elif change == 'extra_member': config['employees']['outsider'] = {'telegram_user_id': 77}
    elif change == 'wrong_admin': config['admins'] = ['frost']
    elif change == 'unconfirmed_roster': config['team_roster_verified'] = False
    elif change == 'bool_identity': config['employees']['frost']['telegram_user_id'] = True
    elif change == 'duplicate_identity': config['employees']['frost']['telegram_user_id'] = 1
    elif change == 'account_collision': config['business_account_user_id'] = 1
    elif change == 'wildcard_routes': config['allowed_live_recipients'] = ['*']
    elif change == 'unverified_routes': config['recipients_verified'] = False
    elif change == 'review_off': config['review_mode'] = False
    elif change == 'sheets_on': config['sheets_write'] = True
    elif change == 'summary_on': config['daily_summary'] = True
    elif change == 'onboarding_on': config['allow_onboarding_replies'] = True
    elif change == 'registered_on': config['allow_registered_replies'] = True
    elif change == 'group_route': config['allowed_group_ids'] = [-77]
    elif change == 'meeting_route': config['meeting_recipient'] = 'frost'
    elif change == 'bot_collision': config['bot_user_id'] = 1
    before = encoded(config)
    with pytest.raises(ValueError):
        apply_team_rollout(config, PROFILE)
    assert encoded(config) == before
    assert not {'intelligence', 'scheduling'} & config.keys()


@pytest.mark.parametrize('features', [
    {'intelligence': {'enabled': False, 'sources': []}, 'scheduling': {'enabled': False}},
    {'intelligence': False, 'scheduling': False},
    {'intelligence': None, 'scheduling': None},
    {'intelligence': {'enabled': True, 'sources': [], 'history_chars': 1234},
     'scheduling': {'enabled': True}},
    {'intelligence': {'enabled': False, 'sources': [{'kind': 'custom', 'url': 'synthetic'}],
                      'custom': {'keep': [1, 2]}},
     'scheduling': {'enabled': False, 'custom': {'keep': [3, 4]}}},
])
def test_explicit_feature_blocks_are_preserved_even_when_false_or_custom(features):
    config = closed_profile()
    config.update(deepcopy(features))
    before = encoded(config)
    result = apply_team_rollout(config, PROFILE)
    assert encoded(result) == before
    assert encoded(config) == before
    assert result == config and result is not config


@pytest.mark.parametrize('present,missing', [('intelligence', 'scheduling'), ('scheduling', 'intelligence')])
def test_defaults_fill_only_the_missing_feature(present, missing):
    config = closed_profile()
    config[present] = {'enabled': False}
    result = apply_team_rollout(config, PROFILE)
    assert result[present] == {'enabled': False}
    assert result[missing]['enabled'] is True
    assert missing not in config


def test_result_and_default_reader_lists_do_not_alias_original_or_other_results():
    config = closed_profile()
    before = encoded(config)
    first = apply_team_rollout(config, PROFILE)
    second = apply_team_rollout(config, PROFILE)
    first['model']['options']['nested'][0]['preserve'] = 'changed'
    first['employees']['frost']['preferences']['tags'].append('changed')
    first['intelligence']['sources'][0]['readers'].append('outsider')
    assert encoded(config) == before
    assert second['model'] == config['model']
    assert second['employees'] == config['employees']
    assert first['intelligence']['sources'][1]['readers'] == list(TEAM_RECIPIENTS)
    assert all(source['readers'] == list(TEAM_RECIPIENTS)
               for source in second['intelligence']['sources'])


def test_team_bootstrap_applies_profile_without_changing_identity_cutoff_or_unrelated_bytes(tmp_path, monkeypatch):
    raw = raw_profile()
    original = encoded(raw)
    directory = tmp_path / 'synthetic-team'
    path = railway.prepare_team_config(json.dumps(raw), directory, now=lambda: 1000)
    before = json.loads(path.read_text())
    marker = directory / 'team_start.json'
    marker_bytes = marker.read_bytes()
    assert 'intelligence' not in before and 'scheduling' not in before
    monkeypatch.setenv('GOLDWAY_FEATURE_PROFILE', PROFILE)
    for _ in range(2):
        prepared = railway.prepare_team_config(json.dumps(raw), directory,
                     now=lambda: pytest.fail('Restart must preserve the first message cutoff'))
        result = json.loads(prepared.read_text())
        assert encoded({key: value for key, value in result.items()
                        if key not in {'intelligence', 'scheduling'}}) == encoded(before)
        assert result['intelligence']['enabled'] is result['scheduling']['enabled'] is True
        assert result['team_intake']['started_at'] == 1000
        assert validate_team(result) == (set(range(1, 6)), 99, 1000)
        assert marker.read_bytes() == marker_bytes
        assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert encoded(raw) == original
    assert {p.name for p in directory.iterdir()} == {'config.json', 'team_start.json'}


def test_bootstrap_retains_valid_explicit_disabled_features(tmp_path, monkeypatch):
    raw = raw_profile()
    raw.update(intelligence={'enabled': False, 'sources': [], 'history_chars': 2345},
               scheduling={'enabled': False})
    monkeypatch.setenv('GOLDWAY_FEATURE_PROFILE', PROFILE)
    path = railway.prepare_team_config(json.dumps(raw), tmp_path, now=lambda: 1000)
    result = json.loads(path.read_text())
    assert encoded(result['intelligence']) == encoded(raw['intelligence'])
    assert encoded(result['scheduling']) == encoded(raw['scheduling'])


@pytest.mark.parametrize('change', ['unconfirmed', 'missing_member', 'unknown_profile', 'public_marker'])
def test_failed_bootstrap_keeps_existing_private_config_and_marker_bytes(tmp_path, monkeypatch, change):
    raw = raw_profile()
    path = railway.prepare_team_config(json.dumps(raw), tmp_path, now=lambda: 1000)
    marker = tmp_path / 'team_start.json'
    before = path.read_bytes(), marker.read_bytes()
    monkeypatch.setenv('GOLDWAY_FEATURE_PROFILE', PROFILE)
    if change == 'unconfirmed': raw['team_roster_verified'] = False
    elif change == 'missing_member': del raw['employees']['frost']
    elif change == 'unknown_profile': monkeypatch.setenv('GOLDWAY_FEATURE_PROFILE', 'unknown')
    elif change == 'public_marker': marker.chmod(0o644)
    with pytest.raises(ValueError):
        railway.prepare_team_config(json.dumps(raw), tmp_path,
                     now=lambda: pytest.fail('Existing cutoff must not be regenerated'))
    assert (path.read_bytes(), marker.read_bytes()) == before
    assert {p.name for p in tmp_path.iterdir()} == {'config.json', 'team_start.json'}


@pytest.mark.parametrize('prepare', [railway.prepare_personal_config, railway.prepare_browser_test_config])
@pytest.mark.parametrize('explicit', [False, True])
def test_personal_and_browser_bootstrap_are_unchanged_by_team_rollout_profile(tmp_path, monkeypatch, prepare, explicit):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'SYNTHETIC-NO-NETWORK')
    raw = raw_profile()
    if explicit:
        raw.update(intelligence={'enabled': False, 'sources': []}, scheduling={'enabled': False})
    original = encoded(raw)
    off_path = prepare(json.dumps(raw), tmp_path / 'off', now=lambda: 1000)
    monkeypatch.setenv('GOLDWAY_FEATURE_PROFILE', PROFILE)
    on_path = prepare(json.dumps(raw), tmp_path / 'on', now=lambda: 1000)
    assert on_path.read_bytes() == off_path.read_bytes()
    result = json.loads(on_path.read_text())
    assert result['allowed_live_recipients'] == result['admins'] == ['test_operator']
    assert set(result['employees']) == {'test_operator'}
    assert 'team_intake' not in result
    if explicit:
        assert result['intelligence'] == raw['intelligence']
        assert result['scheduling'] == raw['scheduling']
    else:
        assert not {'intelligence', 'scheduling'} & result.keys()
    assert encoded(raw) == original


KNOWLEDGE_TABLES = {'knowledge_documents', 'knowledge_readers', 'knowledge_chunks',
                    'knowledge_facts', 'knowledge_document_observations',
                    'knowledge_source_revocations', 'intelligence_outbox_acl', 'intelligence_task_acl'}
SCHEDULING_TABLES = {'c07_events', 'c07_checks', 'c07_series', 'c07_occurrences'}


@pytest.mark.parametrize('profile,explicit', [
    ('off', None), (PROFILE, None),
    (PROFILE, (False, False)), (PROFILE, (True, False)), (PROFILE, (False, True)),
])
def test_engine_schema_opt_in_uses_only_in_memory_state_without_network_or_model(monkeypatch, profile, explicit):
    import httpx
    from goldway.knowledge_sources import ReadOnlySources

    def forbidden(*args, **kwargs):
        pytest.fail('Rollout/bootstrap must not fetch sources or call a model')

    class NoModel:
        __getattr__ = forbidden

    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(ReadOnlySources, 'refresh', forbidden)
    config = closed_profile()
    if explicit is not None:
        config['intelligence'] = {'enabled': explicit[0], 'sources': []}
        config['scheduling'] = {'enabled': explicit[1]}
    result = apply_team_rollout(config, profile)
    before = encoded(result)
    store = Store(':memory:')
    try:
        def tables():
            return {row['name'] for row in store.rows("SELECT name FROM sqlite_master WHERE type='table'")}

        baseline = tables()
        engine = Engine(store, result, NoModel(), now=lambda: 1000)
        knowledge_on = result.get('intelligence', {}).get('enabled') is True
        scheduling_on = result.get('scheduling', {}).get('enabled') is True
        assert (engine.intelligence is not None) is knowledge_on
        assert (engine.scheduler is not None) is scheduling_on
        assert tables() == baseline | (KNOWLEDGE_TABLES if knowledge_on else set()) | (
            SCHEDULING_TABLES if scheduling_on else set())
        for table in ('events', 'outbox', 'tasks', 'usage', 'runtime'):
            assert store.rows(f'SELECT * FROM {table}') == []
        assert encoded(result) == before
    finally:
        store.db.close()
