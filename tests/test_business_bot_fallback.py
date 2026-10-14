"""Synthetic routes only; no polling, external API or model calls."""
import pytest

from goldway.engine import Engine
from goldway.model import ScriptedModel
from goldway.store import Store
from goldway.telegram import DeliveryError, Dispatcher, Updates


class SyntheticAPI:
    def __init__(self):
        self.calls = []
        self.connection = {'user': {'id': 99}, 'is_enabled': True, 'rights': {'can_reply': True}}
        self.lookup_error = None
        self.send_result = {'message_id': 100}
        self.send_error = None

    def call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == 'getBusinessConnection':
            if self.lookup_error:
                raise self.lookup_error
            return self.connection
        assert method == 'sendMessage'
        if self.send_error:
            raise self.send_error
        return self.send_result


@pytest.fixture
def env(tmp_path):
    members = ('test_operator', 'frost', 'alen', 'nastya', 'dasha_it')
    config = {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow',
              'employees': {key: {'name': 'Synthetic ' + key, 'telegram_user_id': i}
                            for i, key in enumerate(members, 1)},
              'admins': ['test_operator'], 'allowed_live_recipients': list(members),
              'business_account_user_id': 99, 'live_send': True, 'recipients_verified': True,
              'review_mode': True, 'sheets_write': False, 'daily_summary': False,
              'allow_onboarding_replies': False, 'allow_registered_replies': False,
              'team_roster_verified': True,
              'team_intake': {'recipients': list(members), 'started_at': 1000}}
    clock = [100000]
    store = Store(str(tmp_path / 'synthetic.sqlite'))
    api = SyntheticAPI()
    engine = Engine(store, config, ScriptedModel({'actions': [], 'summary': 'Synthetic'}), lambda: clock[0])
    dispatcher = Dispatcher(store, config, api, lambda: clock[0])
    return engine, store, config, clock, api, dispatcher


def chat(env, age=0, bot='2', group=None):
    _, store, _, clock, _, _ = env
    store.execute('INSERT INTO chats(business,recipient,business_chat,connection,last_inbound,bot_chat,group_chat) VALUES(?,?,?,?,?,?,?)',
                  ('GOLDWAY', 'frost', '2', 'synthetic-connection', clock[0] - age, bot, group))


def test_existing_auto_selection_and_business_only_preference_are_unchanged(env):
    *_, dispatcher = env
    chat(env, age=86400)
    assert dispatcher.route('frost')[1] == 'bot'
    with pytest.raises(DeliveryError) as error:
        dispatcher.route('frost', 'business')
    assert error.value.state == 'blocked'


@pytest.mark.parametrize('opt_in', [False, 'true', 1])
def test_fallback_requires_explicit_boolean_opt_in(env, opt_in):
    _, _, config, _, _, dispatcher = env
    chat(env, age=86400)
    config['business_bot_fallback'] = opt_in
    with pytest.raises(DeliveryError):
        dispatcher.route('frost', 'business')


def test_open_business_route_stays_preferred_even_when_bot_exists(env):
    _, _, config, _, _, dispatcher = env
    chat(env)
    config['business_bot_fallback'] = True
    route, transport = dispatcher.route('frost', 'business')
    assert transport == 'business'
    assert route == {'chat_id': '2', 'business_connection_id': 'synthetic-connection'}


@pytest.mark.parametrize('reason', ['24h', 'older', 'disabled', 'rights', 'account', 'lookup'])
def test_unavailable_business_can_select_only_verified_bot_before_send(env, reason):
    _, _, config, _, api, dispatcher = env
    chat(env, age=86400 if reason == '24h' else 90000 if reason == 'older' else 0)
    config['business_bot_fallback'] = True
    if reason == 'disabled': api.connection['is_enabled'] = False
    elif reason == 'rights': api.connection['rights']['can_reply'] = False
    elif reason == 'account': api.connection['user']['id'] = 88
    elif reason == 'lookup': api.lookup_error = DeliveryError('unknown', 'Synthetic rights lookup unavailable')
    assert dispatcher.route('frost', 'business') == ({'chat_id': '2'}, 'bot')
    assert not any(method == 'sendMessage' for method, _ in api.calls)


@pytest.mark.parametrize('bot', [None, '22'])
def test_fallback_cannot_use_absent_or_mismatched_private_route_or_group(env, bot):
    _, _, config, _, api, dispatcher = env
    chat(env, age=86400, bot=bot, group='-777')
    config['business_bot_fallback'] = True
    with pytest.raises(DeliveryError) as error:
        dispatcher.route('frost', 'business')
    assert error.value.state == 'blocked'
    assert [method for method, _ in api.calls] == ['getBusinessConnection']


@pytest.mark.parametrize('uid', [None, '2', True, 0])
def test_fallback_requires_confirmed_positive_integer_identity(env, uid):
    _, _, config, _, _, dispatcher = env
    chat(env, age=86400)
    config['business_bot_fallback'] = True
    config['employees']['frost']['telegram_user_id'] = uid
    with pytest.raises(DeliveryError):
        dispatcher.route('frost', 'business')


def test_recipient_allowlist_is_checked_before_any_route_lookup(env):
    _, _, config, _, api, dispatcher = env
    chat(env, age=86400)
    config['business_bot_fallback'] = True
    config['allowed_live_recipients'] = ['test_operator']
    with pytest.raises(DeliveryError, match='allowlist'):
        dispatcher.route('frost', 'business')
    assert not api.calls


def test_known_private_start_establishes_fallback_without_inviting_unknown_sender(env):
    engine, store, config, clock, api, dispatcher = env
    config['business_bot_fallback'] = True
    handler = Updates(engine, api, 900)
    def start(uid, update_id):
        return {'update_id': update_id, 'message': {'message_id': update_id, 'date': clock[0],
                'chat': {'id': uid, 'type': 'private'}, 'from': {'id': uid}, 'text': '/start'}}
    handler.handle(start(77, 1))
    assert not store.rows('SELECT * FROM chats')
    handler.handle(start(2, 2))
    assert dispatcher.route('frost', 'business') == ({'chat_id': '2'}, 'bot')
    assert not any(method == 'sendMessage' for method, _ in api.calls)


def test_pre_send_fallback_performs_one_bot_send_and_records_actual_route(env):
    _, store, config, clock, api, dispatcher = env
    chat(env, age=86400)
    config['business_bot_fallback'] = True
    store.queue('GOLDWAY', 'frost', 'Synthetic reply', 'one', clock[0], preferred_transport='business')
    dispatcher.flush()
    sends = [kwargs for method, kwargs in api.calls if method == 'sendMessage']
    assert sends == [{'chat_id': '2', 'text': 'Synthetic reply'}]
    assert store.rows('SELECT state,attempts,transport,destination_connection FROM outbox') == [
        {'state': 'sent', 'attempts': 1, 'transport': 'bot', 'destination_connection': None}]


@pytest.mark.parametrize('failure', ['unknown', 'malformed', 'unexpected', 'definite_failure'])
def test_no_second_send_or_cross_route_fallback_after_attempt(env, failure):
    _, store, config, clock, api, dispatcher = env
    chat(env)
    config['business_bot_fallback'] = True
    if failure == 'unknown': api.send_error = DeliveryError('unknown', 'Synthetic ambiguous delivery')
    elif failure == 'malformed': api.send_result = {}
    elif failure == 'unexpected': api.send_error = ValueError('Synthetic unexpected response')
    else: api.send_error = DeliveryError('failed', 'Synthetic definite rejection')
    store.queue('GOLDWAY', 'frost', 'Synthetic reply', 'one', clock[0], preferred_transport='business')
    dispatcher.flush()
    clock[0] += 100000  # Business closes; a bot route still exists.
    dispatcher.flush()
    sends = [kwargs for method, kwargs in api.calls if method == 'sendMessage']
    assert len(sends) == 1
    assert sends[0]['business_connection_id'] == 'synthetic-connection'
    row = store.rows('SELECT state,attempts,transport FROM outbox')[0]
    assert row == {'state': 'failed' if failure == 'definite_failure' else 'unknown',
                   'attempts': 1, 'transport': 'business'}
