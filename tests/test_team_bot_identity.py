"""Synthetic authenticated identity binding; no polling, sends, or live API."""
import copy
from types import SimpleNamespace

import pytest

import goldway.cli as cli
from goldway.intake import accepts_team, validate_team
from test_team_profile import profile, text_update


@pytest.fixture
def unbound_profile(profile):
    profile.pop('bot_user_id')
    profile.pop('bot_user_id_verified')
    return profile


def own_reply(business=False):
    update = text_update(business=business)
    key = 'business_message' if business else 'message'
    parent = text_update(business=business, date=1100)[key]
    parent['from'] = {'id': 99} if business else {'id': 900, 'is_bot': True}
    if business:
        parent['sender_business_bot'] = {'id': 900, 'is_bot': True}
    update[key]['reply_to_message'] = parent
    return update


@pytest.mark.parametrize('business', [False, True])
def test_authenticated_identity_enables_only_own_bot_reply(unbound_profile, business):
    update = own_reply(business)
    assert not accepts_team(update, unbound_profile, 1200)
    cli.register_team_bot_identity(unbound_profile, {'id': 900, 'is_bot': True})
    assert accepts_team(update, unbound_profile, 1200)
    assert validate_team(unbound_profile)[2] == 1000
    assert unbound_profile['live_send'] is False


@pytest.mark.parametrize('business', [False, True])
@pytest.mark.parametrize('change', ['other_bot', 'cross_recipient', 'future', 'old'])
def test_bound_identity_does_not_broaden_reply_gate(unbound_profile, business, change):
    cli.register_team_bot_identity(unbound_profile, {'id': 900, 'is_bot': True})
    update = own_reply(business)
    parent = update['business_message' if business else 'message']['reply_to_message']
    if change == 'other_bot':
        parent['sender_business_bot' if business else 'from']['id'] = 901
    elif change == 'cross_recipient':
        parent['chat']['id'] = 3
    elif change == 'future':
        parent['date'] = 1201
    else:
        parent['date'] = 999
    assert not accepts_team(update, unbound_profile, 1200)


@pytest.mark.parametrize('bot', [None, [], {}, {'id': True, 'is_bot': True},
    {'id': '900', 'is_bot': True}, {'id': 0, 'is_bot': True},
    {'id': -1, 'is_bot': True}, {'id': 2, 'is_bot': True},
    {'id': 99, 'is_bot': True}, {'id': 900}, {'id': 900, 'is_bot': False}])
def test_malformed_or_non_bot_getme_fails_without_mutation(unbound_profile, bot):
    before = copy.deepcopy(unbound_profile)
    with pytest.raises(ValueError):
        cli.register_team_bot_identity(unbound_profile, bot)
    assert unbound_profile == before


@pytest.mark.parametrize('metadata', [
    {'bot_user_id': 901, 'bot_user_id_verified': True},
    {'bot_user_id_verified': True},
    {'bot_user_id': True}, {'bot_user_id': '900'}, {'bot_user_id': None},
    {'bot_user_id': 0}, {'bot_user_id': -1}, {'bot_user_id': 2},
    {'bot_user_id': 99}, {'bot_user_id_verified': 'true'},
    {'bot_user_id_verified': 1},
])
def test_bad_preconfigured_metadata_fails_without_overwrite(unbound_profile, metadata):
    unbound_profile.update(metadata)
    before = copy.deepcopy(unbound_profile)
    with pytest.raises(ValueError):
        cli.register_team_bot_identity(unbound_profile, {'id': 900, 'is_bot': True})
    assert unbound_profile == before


def test_matching_preverified_identity_is_preserved(profile):
    before = copy.deepcopy(profile)
    cli.register_team_bot_identity(profile, {'id': 900, 'is_bot': True})
    assert profile == before


def test_invalid_team_gate_cannot_be_repaired_by_getme(unbound_profile):
    unbound_profile['allowed_live_recipients'].append('unrelated')
    with pytest.raises(ValueError):
        cli.register_team_bot_identity(unbound_profile, {'id': 900, 'is_bot': True})
    assert 'bot_user_id' not in unbound_profile


@pytest.mark.parametrize('config', [{}, {'personal_intake': {'recipient': 'test_operator'}}])
def test_nonteam_profiles_are_unchanged(config):
    before = copy.deepcopy(config)
    cli.register_team_bot_identity(config, None)
    assert config == before


@pytest.mark.parametrize('wrong_identity', [False, True])
def test_startup_binds_before_handlers_and_aborts_mismatch(unbound_profile, monkeypatch, wrong_identity):
    calls = []
    if wrong_identity:
        unbound_profile.update(bot_user_id=901, bot_user_id_verified=True)
    engine = SimpleNamespace(c=unbound_profile, s=object())

    class API:
        client = SimpleNamespace(close=lambda: calls.append('close'))

        def __init__(self, token):
            assert token == 'synthetic-token'

        def call(self, method, **params):
            calls.append(method)
            assert method == 'getMe', 'No polling or live send allowed in this test'
            return {'id': 900, 'is_bot': True, 'username': 'synthetic_bot'}

    def initialized(name):
        assert unbound_profile['bot_user_id'] == 900
        assert unbound_profile['bot_user_id_verified'] is True
        assert accepts_team(own_reply(), unbound_profile, 1200)
        calls.append(name)

    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'synthetic-token')
    monkeypatch.setattr(cli, 'Telegram', API)
    monkeypatch.setattr(cli, 'bootstrap_groups', lambda *args: initialized('bootstrap'))
    monkeypatch.setattr(cli, 'Updates', lambda *args: initialized('updates'))
    monkeypatch.setattr(cli, 'Dispatcher', lambda *args: SimpleNamespace(recover=lambda: initialized('recover')))
    monkeypatch.setattr(cli.signal, 'signal', lambda *args: None)
    if wrong_identity:
        with pytest.raises(ValueError, match='differs'):
            cli.run(engine, run_seconds=0)
        assert calls == ['getMe']
    else:
        cli.run(engine, run_seconds=0)
        assert calls == ['getMe', 'bootstrap', 'updates', 'recover', 'close']
        assert unbound_profile['bot_username'] == 'synthetic_bot'
