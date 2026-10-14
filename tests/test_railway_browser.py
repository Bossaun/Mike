import copy
import json
from pathlib import Path

import pytest

import goldway.railway as railway


@pytest.fixture
def raw_config(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'SYNTHETIC')
    return {'business': 'GOLDWAY', 'admins': ['test_operator', 'other'],
        'employees': {'test_operator': {'name': 'Canonical', 'telegram_user_id': 77},
                      'other': {'name': 'Other', 'telegram_user_id': 99}},
        'business_account_user_id': 88, 'allowed_live_recipients': ['other'],
        'allowed_group_ids': [-99], 'group_destinations': {'group': {'chat_id': -99}},
        'meeting_recipient': 'other', 'speaker_aliases': {'Other': 'other'},
        'rules': ['Synthetic canonical rules'], 'timezone': 'Europe/Moscow',
        'sheets_write': True, 'daily_summary': True, 'allow_onboarding_replies': True,
        'allow_registered_replies': True, 'review_mode': False,
        'model': {'provider': 'synthetic', 'daily_budget': .75, 'max_output_tokens': 321,
                  'input_per_million': .12, 'output_per_million': .34}}


def test_browser_scope_model_and_saved_paths_are_isolated(raw_config, tmp_path):
    original = copy.deepcopy(raw_config)
    root = tmp_path / 'data'
    root.mkdir()
    preserved = {'config.json': b'CANONICAL CONFIG', 'personal_start.json': b'CANONICAL MARKER',
                 'personal.sqlite': b'CANONICAL DATABASE'}
    for name, value in preserved.items():
        (root / name).write_bytes(value)
    directory = root / 'browser-tests'
    path = railway.prepare_browser_test_config(json.dumps(raw_config), directory, now=lambda: 1000)
    c = json.loads(path.read_text())
    assert c['employees'] == {'test_operator': {'name': 'Browser test operator', 'telegram_user_id': 88}}
    assert c['admins'] == c['allowed_live_recipients'] == ['test_operator']
    assert c['model'] == original['model'] and raw_config == original
    assert c['browser_test'] is True and c['availability'] == 'test_window'
    assert c['speaker_aliases'] == {} and c['rules'] == []
    assert all(c[k] is False for k in ('sheets_write', 'daily_summary', 'allow_onboarding_replies', 'allow_registered_replies'))
    assert all(c[k] is True for k in ('live_send', 'recipients_verified', 'review_mode'))
    assert not any(c[k] for k in ('business_account_user_id', 'allowed_group_ids', 'group_destinations', 'meeting_recipient'))
    assert json.loads((directory / 'personal_start.json').read_text()) == {'telegram_user_id': 88, 'started_at': 1000}
    assert path.stat().st_mode & 0o777 == (directory / 'personal_start.json').stat().st_mode & 0o777 == 0o600
    assert not (directory / 'personal.sqlite').exists()
    assert {name: (root / name).read_bytes() for name in preserved} == preserved


@pytest.mark.parametrize('uid', [None, False, 0, -1, '88', 77])
def test_invalid_or_canonical_account_rejected_without_writes(raw_config, tmp_path, uid):
    raw_config['business_account_user_id'] = uid
    directory = tmp_path / 'browser-tests'
    with pytest.raises(ValueError, match='distinct configured account'):
        railway.prepare_browser_test_config(json.dumps(raw_config), directory)
    assert not directory.exists()


def test_browser_marker_reuses_cutoff_and_rejects_changed_identity(raw_config, tmp_path):
    directory = tmp_path / 'browser-tests'
    path = railway.prepare_browser_test_config(json.dumps(raw_config), directory, now=lambda: 1000)
    marker = directory / 'personal_start.json'
    before = path.read_bytes(), marker.read_bytes()
    railway.prepare_browser_test_config(json.dumps(raw_config), directory,
        now=lambda: pytest.fail('Restart cannot refresh cutoff'))
    assert (path.read_bytes(), marker.read_bytes()) == before
    raw_config['business_account_user_id'] = 89
    with pytest.raises(ValueError, match='identity changed'):
        railway.prepare_browser_test_config(json.dumps(raw_config), directory)
    assert (path.read_bytes(), marker.read_bytes()) == before


@pytest.mark.parametrize('personal,team', [(False, False), (True, True)])
def test_browser_flag_conflicts_fail_before_private_config_or_paths(monkeypatch, personal, team):
    monkeypatch.setenv('GOLDWAY_WORKER_ENABLED', 'true')
    monkeypatch.setenv('GOLDWAY_BROWSER_TEST', 'true')
    monkeypatch.setenv('GOLDWAY_PERSONAL_ONLY', str(personal).lower())
    monkeypatch.setenv('GOLDWAY_TEAM_ONLY', str(team).lower())
    monkeypatch.delenv('GOLDWAY_CONFIG_JSON', raising=False)
    monkeypatch.setattr(railway, 'Path', lambda *_: pytest.fail('Conflict must not create paths'))
    monkeypatch.setattr(railway.os, 'execvp', lambda *_: pytest.fail('Conflict must not launch worker'))
    with pytest.raises(ValueError, match='cannot use team'):
        railway.main()


@pytest.mark.parametrize('browser,relative', [(True, 'browser-tests'), (False, '.')])
def test_bootstrap_selects_one_worker_and_correct_personal_database(raw_config, tmp_path, monkeypatch,
                                                                 browser, relative):
    monkeypatch.setenv('GOLDWAY_WORKER_ENABLED', 'true')
    monkeypatch.setenv('GOLDWAY_PERSONAL_ONLY', 'true')
    monkeypatch.setenv('GOLDWAY_BROWSER_TEST', str(browser).lower())
    monkeypatch.setenv('GOLDWAY_TEAM_ONLY', 'false')
    monkeypatch.setenv('GOLDWAY_CONFIG_JSON', json.dumps(raw_config))
    monkeypatch.setattr(railway, 'Path', lambda p: tmp_path if p == '/data' else Path(p))
    monkeypatch.setattr(railway.os, 'geteuid', lambda: 10001)
    executions = []
    monkeypatch.setattr(railway.os, 'execvp', lambda command, args: executions.append((command, args)))
    railway.main()
    directory = tmp_path / relative
    assert executions == [('goldway', ['goldway', '--config', str(directory / 'config.json'),
                                       '--db', str(directory / 'personal.sqlite'), 'run'])]
    c = json.loads((directory / 'config.json').read_text())
    assert c['employees']['test_operator']['telegram_user_id'] == (88 if browser else 77)
    assert c.get('browser_test', False) is browser
    assert not (directory / 'personal.sqlite').exists()
    if browser:
        assert not (tmp_path / 'config.json').exists()
        assert not (tmp_path / 'personal_start.json').exists()
