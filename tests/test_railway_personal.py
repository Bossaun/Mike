import copy
import json
import os
from pathlib import Path

import pytest

import goldway.railway as railway


@pytest.fixture
def raw_config(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'synthetic-token')
    c = json.loads(Path('examples/telegram-only.json').read_text())
    c['employees']['test_operator'] = {'name': 'Synthetic operator', 'telegram_user_id': 77}
    c.update(business_account_user_id=88, allowed_group_ids=[-99],
             group_destinations={'old': {'chat_id': -99}}, meeting_recipient='boss',
             sheets_write=True, daily_summary=True, review_mode=False)
    c['model']['daily_budget'] = 1
    c['model']['synthetic_pricing'] = {'input': 0.123, 'output': 0.456}
    return c


def test_profile_preserves_model_and_keeps_databases_private_and_separate(raw_config, tmp_path):
    original = copy.deepcopy(raw_config)
    raw_config.update(controlled_intake={'started_at': 1, 'ends_at': 2},
                      test_intake={}, personal_intake={'started_at': 999999},
                      ignore_messages_before=999999)
    production = tmp_path / 'goldway.sqlite'
    production.write_bytes(b'synthetic existing production database')
    path = railway.prepare_personal_config(json.dumps(raw_config), tmp_path, now=lambda: 1000)
    c = json.loads(path.read_text())
    assert c['employees'] == {'test_operator': original['employees']['test_operator']}
    assert c['admins'] == c['allowed_live_recipients'] == ['test_operator']
    assert c['model'] == original['model']
    assert all(c[k] is True for k in ('live_send', 'recipients_verified', 'review_mode'))
    assert all(c[k] is False for k in ('sheets_write', 'daily_summary',
                                     'allow_onboarding_replies', 'allow_registered_replies'))
    assert not any(c[k] for k in ('business_account_user_id', 'allowed_group_ids',
                                 'group_destinations', 'meeting_recipient'))
    assert not {'controlled_intake', 'test_intake', 'ignore_messages_before'} & c.keys()
    assert c['personal_intake'] == {'recipient': 'test_operator', 'started_at': 1000}
    marker = tmp_path / 'personal_start.json'
    assert json.loads(marker.read_text()) == {'telegram_user_id': 77, 'started_at': 1000}
    assert marker.stat().st_mode & 0o777 == path.stat().st_mode & 0o777 == 0o600
    assert marker.stat().st_uid == os.geteuid()
    assert production.read_bytes() == b'synthetic existing production database'
    assert not (tmp_path / 'personal.sqlite').exists()
    assert not list(tmp_path.glob('.config-*')) + list(tmp_path.glob('.personal-start-*'))


def test_restart_keeps_initial_cutoff_and_marker(raw_config, tmp_path):
    raw = json.dumps(raw_config)
    railway.prepare_personal_config(raw, tmp_path, now=lambda: 1000)
    marker = tmp_path / 'personal_start.json'
    first = marker.read_bytes()
    path = railway.prepare_personal_config(raw, tmp_path,
                                          now=lambda: pytest.fail('Restart must not refresh cutoff'))
    assert json.loads(path.read_text())['personal_intake']['started_at'] == 1000
    assert marker.read_bytes() == first


def test_changed_identity_rejected_without_replacing_private_files(raw_config, tmp_path):
    path = railway.prepare_personal_config(json.dumps(raw_config), tmp_path, now=lambda: 1000)
    first_config, first_marker = path.read_bytes(), (tmp_path / 'personal_start.json').read_bytes()
    raw_config['employees']['test_operator']['telegram_user_id'] = 987654321
    with pytest.raises(ValueError, match='identity changed') as error:
        railway.prepare_personal_config(json.dumps(raw_config), tmp_path, now=lambda: 2000)
    assert '987654321' not in str(error.value)
    assert path.read_bytes() == first_config
    assert (tmp_path / 'personal_start.json').read_bytes() == first_marker


@pytest.mark.parametrize('value', [None, 0, True, '77'])
def test_invalid_operator_cannot_create_marker(raw_config, tmp_path, value):
    raw_config['employees']['test_operator']['telegram_user_id'] = value
    with pytest.raises(ValueError):
        railway.prepare_personal_config(json.dumps(raw_config), tmp_path, now=lambda: 1000)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('start', [0, True, '1000'])
def test_corrupt_cutoff_rejected_without_refresh(raw_config, tmp_path, start):
    marker = tmp_path / 'personal_start.json'
    marker.write_text(json.dumps({'telegram_user_id': 77, 'started_at': start}))
    marker.chmod(0o600)
    first = marker.read_bytes()
    with pytest.raises(ValueError, match='start marker'):
        railway.prepare_personal_config(json.dumps(raw_config), tmp_path, now=lambda: 2000)
    assert marker.read_bytes() == first
    assert not (tmp_path / 'config.json').exists()


def test_full_validation_precedes_marker_creation(raw_config, tmp_path):
    previous = tmp_path / 'config.json'
    previous.write_text('synthetic previous config')
    raw_config['business'] = 'other'
    with pytest.raises(ValueError):
        railway.prepare_personal_config(json.dumps(raw_config), tmp_path, now=lambda: 1000)
    assert previous.read_text() == 'synthetic previous config'
    assert not (tmp_path / 'personal_start.json').exists()
    assert not list(tmp_path.glob('.config-*'))


def test_marker_atomic_creation_failure_preserves_previous_config(raw_config, tmp_path, monkeypatch):
    previous = tmp_path / 'config.json'
    previous.write_text('synthetic previous config')
    def occupied(*args):
        raise FileExistsError('Synthetic concurrent creation')
    monkeypatch.setattr(railway.os, 'link', occupied)
    with pytest.raises(FileExistsError):
        railway.prepare_personal_config(json.dumps(raw_config), tmp_path, now=lambda: 1000)
    assert previous.read_text() == 'synthetic previous config'
    assert not list(tmp_path.glob('.config-*')) + list(tmp_path.glob('.personal-start-*'))


@pytest.mark.parametrize('personal,db_name', [(True, 'personal.sqlite'), (False, 'goldway.sqlite')])
def test_bootstrap_chooses_personal_database_only_when_enabled(raw_config, tmp_path, monkeypatch,
                                                             personal, db_name):
    # Default mode retains its original configuration and database path.
    raw_config['live_send'] = False
    raw_config['sheets_write'] = False
    monkeypatch.setenv('GOLDWAY_WORKER_ENABLED', 'true')
    monkeypatch.setenv('GOLDWAY_PERSONAL_ONLY', str(personal).lower())
    monkeypatch.setenv('GOLDWAY_CONFIG_JSON', json.dumps(raw_config))
    monkeypatch.setattr(railway, 'Path', lambda p: tmp_path if p == '/data' else Path(p))
    monkeypatch.setattr(railway.os, 'geteuid', lambda: 10001)
    executions = []
    monkeypatch.setattr(railway.os, 'execvp', lambda command, args: executions.append((command, args)))
    railway.main()
    assert executions == [('goldway', ['goldway', '--config', str(tmp_path / 'config.json'),
                                      '--db', str(tmp_path / db_name), 'run'])]
    assert 'GOLDWAY_CONFIG_JSON' not in os.environ
    assert not (tmp_path / db_name).exists()
    c = json.loads((tmp_path / 'config.json').read_text())
    assert ('personal_intake' in c) is personal
    assert c['model'] == raw_config['model']


def test_root_privileges_dropped_before_personal_preparation(tmp_path, monkeypatch):
    monkeypatch.setenv('GOLDWAY_WORKER_ENABLED', 'true')
    monkeypatch.setenv('GOLDWAY_PERSONAL_ONLY', 'true')
    monkeypatch.setenv('GOLDWAY_CONFIG_JSON', 'synthetic raw')
    monkeypatch.setattr(railway, 'Path', lambda p: tmp_path if p == '/data' else Path(p))
    monkeypatch.setattr(railway.os, 'geteuid', lambda: 0)
    calls = []
    for name in ('chown', 'setgroups', 'setgid', 'setuid'):
        monkeypatch.setattr(railway.os, name,
                            lambda *args, name=name: calls.append((name, args)))
    def prepare(raw, directory):
        assert calls[-1] == ('setuid', (10001,))
        calls.append(('prepare', ()))
        return directory / 'config.json'
    monkeypatch.setattr(railway, 'prepare_personal_config', prepare)
    monkeypatch.setattr(railway.os, 'execvp', lambda *args: calls.append(('exec', ())))
    railway.main()
    assert [name for name, _ in calls] == ['chown', 'setgroups', 'setgid', 'setuid', 'prepare', 'exec']


def test_disabled_worker_does_not_read_private_config_or_write(monkeypatch, capsys):
    monkeypatch.setenv('GOLDWAY_WORKER_ENABLED', 'false')
    monkeypatch.setenv('GOLDWAY_PERSONAL_ONLY', 'true')
    reads = []
    original_get = os.environ.get
    def get(key, default=None):
        reads.append(key)
        assert key == 'GOLDWAY_WORKER_ENABLED'
        return original_get(key, default)
    monkeypatch.setattr(os.environ, 'get', get)
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled worker must have no preparation or filesystem side effects')
    monkeypatch.setattr(railway, 'Path', forbidden)
    monkeypatch.setattr(railway, 'prepare_config', forbidden)
    monkeypatch.setattr(railway, 'prepare_personal_config', forbidden)
    monkeypatch.setattr(railway.os, 'geteuid', forbidden)
    monkeypatch.setattr(railway.signal, 'signal', lambda signal, stop: stop())
    railway.main()
    assert reads == ['GOLDWAY_WORKER_ENABLED']
    assert 'worker disabled' in capsys.readouterr().out
