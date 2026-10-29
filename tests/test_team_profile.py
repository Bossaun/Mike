"""Synthetic-only closed roster, inbound boundary and existing routing contracts."""
import copy
import json
import os
from pathlib import Path

import pytest

from goldway.engine import Engine
from goldway.intake import TEAM_RECIPIENTS, accepts, validate_team
from goldway.model import ScriptedModel
from goldway.store import Store
from goldway.telegram import DeliveryError, Dispatcher, Updates
import goldway.railway as railway


def raw_profile():
    return {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow',
            'employees': {name: {'name': 'Synthetic ' + name, 'telegram_user_id': i}
                          for i, name in enumerate(TEAM_RECIPIENTS, 1)},
            'admins': ['test_operator'], 'business_account_user_id': 99,
            'team_roster_verified': True, 'bot_user_id': 900, 'bot_user_id_verified': True,
            'model': {'provider': 'openai', 'model': 'synthetic-model', 'daily_budget': 1,
                      'input_per_million': 0.123, 'output_per_million': 0.456},
            'allowed_group_ids': [], 'group_destinations': {}, 'meeting_recipient': None}


@pytest.fixture
def profile(tmp_path):
    path = railway.prepare_team_config(json.dumps(raw_profile()), tmp_path / 'profile', now=lambda: 1000)
    return json.loads(path.read_text())


def text_update(uid=2, business=False, date=1200, update_id=1):
    message = {'message_id': update_id, 'date': date, 'chat': {'id': uid, 'type': 'private'},
               'from': {'id': uid}, 'text': 'Синтетическое сообщение'}
    if business:
        message['business_connection_id'] = 'synthetic-connection'
    return {'update_id': update_id, 'business_message' if business else 'message': message}


def connection_update(uid=99, enabled=True, can_reply=True):
    return {'update_id': 90, 'business_connection': {'id': 'synthetic-connection',
            'user': {'id': uid}, 'date': 1, 'is_enabled': enabled, 'rights': {'can_reply': can_reply}}}


class SyntheticAPI:
    def __init__(self):
        self.calls = []
        self.account = 99
        self.unknown = False

    def call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == 'getBusinessConnection':
            return {'user': {'id': self.account}, 'is_enabled': True, 'rights': {'can_reply': True}}
        assert method == 'sendMessage'
        if self.unknown:
            raise DeliveryError('unknown', 'Synthetic ambiguous delivery')
        return {'message_id': 100}


def runtime(profile, tmp_path):
    clock = [1200]
    store = Store(str(tmp_path / 'synthetic.sqlite'))
    engine = Engine(store, profile, ScriptedModel({'actions': [], 'summary': 'Synthetic'}), lambda: clock[0])
    api = SyntheticAPI()
    return engine, store, api, clock


def test_stage_preserves_budget_restricts_roles_and_keeps_send_disabled(tmp_path):
    raw = raw_profile()
    raw.update(live_send=True, personal_intake={}, controlled_intake={}, ignore_messages_before=99999)
    raw['employees']['unrelated'] = {'telegram_user_id': 88}
    before = copy.deepcopy(raw['model'])
    path = railway.prepare_team_config(json.dumps(raw), tmp_path, now=lambda: 1000)
    c = json.loads(path.read_text())
    assert list(c['employees']) == list(TEAM_RECIPIENTS)
    assert c['admins'] == ['test_operator']
    assert c['model'] == before
    assert c['live_send'] is False
    assert not {'personal_intake', 'controlled_intake', 'ignore_messages_before'} & c.keys()
    assert all(c[k] is False for k in ('sheets_write', 'daily_summary',
                                     'allow_onboarding_replies', 'allow_registered_replies'))
    marker = tmp_path / 'team_start.json'
    assert marker.stat().st_mode & 0o777 == path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('*.sqlite'))
    assert validate_team(c)[2] == 1000


@pytest.mark.parametrize('missing', ['frost', 'dasha_it'])
def test_incomplete_roster_cannot_create_configuration_or_marker(tmp_path, missing):
    raw = raw_profile()
    del raw['employees'][missing]
    with pytest.raises(ValueError, match='roster'):
        railway.prepare_team_config(json.dumps(raw), tmp_path, now=lambda: 1000)
    assert not list(tmp_path.iterdir())


def test_confirmation_required_and_cutoff_reused_with_identity_check(tmp_path):
    raw = raw_profile()
    raw['team_roster_verified'] = False
    with pytest.raises(ValueError):
        railway.prepare_team_config(json.dumps(raw), tmp_path, now=lambda: 1000)
    raw['team_roster_verified'] = True
    path = railway.prepare_team_config(json.dumps(raw), tmp_path, now=lambda: 1000)
    path = railway.prepare_team_config(json.dumps(raw), tmp_path,
                                       now=lambda: pytest.fail('Cannot refresh initial cutoff'))
    assert json.loads(path.read_text())['team_intake']['started_at'] == 1000
    first = path.read_bytes()
    raw['employees']['dasha_it']['telegram_user_id'] = 555
    with pytest.raises(ValueError, match='identity changed') as error:
        railway.prepare_team_config(json.dumps(raw), tmp_path, now=lambda: 2000)
    assert '555' not in str(error.value)
    assert path.read_bytes() == first


@pytest.mark.parametrize('uid', range(1, 6))
@pytest.mark.parametrize('business', [False, True])
def test_new_private_text_from_each_confirmed_teammate(profile, uid, business):
    assert accepts(text_update(uid, business), profile, 1200)


@pytest.mark.parametrize('change', ['old', 'future', 'group', 'unknown', 'forward', 'edit', 'history', 'business_in_bot'])
def test_unapproved_update_rejected_before_persistence(profile, tmp_path, change):
    update = text_update()
    message = update['message']
    if change == 'old': message['date'] = 999
    elif change == 'future': message['date'] = 1201
    elif change == 'group': message['chat']['type'] = 'group'
    elif change == 'unknown': message['from']['id'] = message['chat']['id'] = 77
    elif change == 'forward': message['forward_origin'] = {'type': 'user'}
    elif change == 'edit': update['edited_message'] = update.pop('message')
    elif change == 'history': message['document'] = {'file_id': 'synthetic'}
    elif change == 'business_in_bot': message['business_connection_id'] = 'synthetic-connection'
    engine, store, api, _ = runtime(profile, tmp_path)
    Updates(engine, api, 900).handle(update)
    assert not store.rows('SELECT * FROM events')
    if change=='forward':assert accepts(update,profile,1200)  # Accepted only as data; legacy engine remains untouched.
    else:assert not store.rows('SELECT * FROM runtime')
    assert not api.calls


@pytest.mark.parametrize('enabled,can_reply', [(True, True), (False, False)])
def test_only_configured_account_can_update_explicit_business_rights(profile, enabled, can_reply):
    assert accepts(connection_update(enabled=enabled, can_reply=can_reply), profile, 1200)
    assert not accepts(connection_update(uid=88), profile, 1200)
    assert not accepts(connection_update(can_reply='true'), profile, 1200)


@pytest.mark.parametrize('rights', [None, {}])
def test_connection_revocation_without_optional_reply_right_is_persisted(profile, tmp_path, rights):
    update = connection_update(enabled=False)
    if rights is None:
        update['business_connection'].pop('rights')
    else:
        update['business_connection']['rights'] = rights
    engine, store, api, _ = runtime(profile, tmp_path)
    Updates(engine, api, 900).handle(update)
    assert store.rows('SELECT enabled,can_reply FROM connections') == [{'enabled': 0, 'can_reply': 0}]
    assert not store.rows('SELECT * FROM events')
    assert not api.calls


def test_business_owner_verified_before_message_event_and_personal_route_is_fallback(profile, tmp_path):
    engine, store, api, _ = runtime(profile, tmp_path)
    handler = Updates(engine, api, 900)
    api.account = 88
    handler.handle(text_update(business=True))
    assert not store.rows('SELECT * FROM events')
    api.account = 99
    handler.handle(connection_update())
    handler.handle(text_update(business=True, update_id=2))
    handler.handle(text_update(update_id=3))
    assert len(store.rows('SELECT * FROM events')) == 2
    row = store.rows("SELECT * FROM chats WHERE recipient='frost'")[0]
    assert row['bot_chat'] == row['business_chat'] == '2'
    assert not any(method == 'sendMessage' for method, _ in api.calls)


@pytest.mark.parametrize('parent_kind', ['human', 'bot', 'business_bot'])
def test_replies_require_fresh_same_route_and_confirmed_own_actor(profile, parent_kind):
    business = parent_kind == 'business_bot'
    update = text_update(business=business)
    message = update['business_message' if business else 'message']
    parent = text_update(business=business, date=1100)['business_message' if business else 'message']
    if parent_kind == 'bot': parent['from'] = {'id': 900, 'is_bot': True}
    elif business:
        parent['from'] = {'id': 99}
        parent['sender_business_bot'] = {'id': 900, 'is_bot': True}
    message['reply_to_message'] = parent
    assert accepts(update, profile, 1200)
    parent['date'] = 999
    assert not accepts(update, profile, 1200)


def test_unknown_bot_parent_rejected_but_plain_text_remains_supported(profile):
    update = text_update()
    parent = text_update(date=1100)['message']
    parent['from'] = {'id': 901, 'is_bot': True}
    update['message']['reply_to_message'] = parent
    assert not accepts(update, profile, 1200)
    parent['from']['id'] = 900
    profile.pop('bot_user_id_verified')
    assert not accepts(update, profile, 1200)
    update['message'].pop('reply_to_message')
    assert accepts(update, profile, 1200)


def test_business_window_preferred_route_and_unknown_delivery_contract(profile, tmp_path):
    engine, store, api, clock = runtime(profile, tmp_path)
    profile['live_send'] = True
    store.execute('INSERT INTO chats(business,recipient,business_chat,connection,last_inbound,bot_chat) VALUES(?,?,?,?,?,?)',
                  ('GOLDWAY', 'frost', '2', 'synthetic-connection', 1200, '2'))
    dispatch = Dispatcher(store, profile, api, lambda: clock[0])
    assert dispatch.route('frost', 'business')[1] == 'business'
    clock[0] += 86400
    with pytest.raises(DeliveryError) as error:
        dispatch.route('frost', 'business')
    assert error.value.state == 'blocked'
    assert dispatch.route('frost')[1] == 'bot'
    clock[0] = 1200
    store.queue('GOLDWAY', 'frost', 'Synthetic reply', 'synthetic-key', 1200, preferred_transport='business')
    api.unknown = True
    dispatch.flush()
    clock[0] += 10000
    dispatch.flush()
    sends = [kwargs for method, kwargs in api.calls if method == 'sendMessage']
    assert len(sends) == 1
    assert sends[0]['business_connection_id'] == 'synthetic-connection'
    assert store.rows('SELECT state FROM outbox') == [{'state': 'unknown'}]


def test_team_bootstrap_uses_its_own_database_and_rejects_mixed_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv('GOLDWAY_WORKER_ENABLED', 'true')
    monkeypatch.setenv('GOLDWAY_TEAM_ONLY', 'true')
    monkeypatch.setenv('GOLDWAY_PERSONAL_ONLY', 'true')
    with pytest.raises(ValueError, match='one closed'):
        railway.main()
    monkeypatch.setenv('GOLDWAY_PERSONAL_ONLY', 'false')
    monkeypatch.setenv('GOLDWAY_CONFIG_JSON', json.dumps(raw_profile()))
    monkeypatch.setattr(railway, 'Path', lambda p: tmp_path if p == '/data' else Path(p))
    monkeypatch.setattr(railway.os, 'geteuid', lambda: 10001)
    executed = []
    monkeypatch.setattr(railway.os, 'execvp', lambda command, args: executed.append(args))
    railway.main()
    assert executed[0][-3:] == ['--db', str(tmp_path / 'team.sqlite'), 'run']
    assert not list(tmp_path.glob('*.sqlite'))
    assert 'GOLDWAY_CONFIG_JSON' not in os.environ
