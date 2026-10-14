import copy
import json

import pytest

from goldway.config import load
from goldway.intake import FORBIDDEN_FIELDS, accepts
from goldway.telegram import Updates
from test_telegram import API


def profile(c, clock):
    c['employees'] = {'test_operator': {'name': 'Synthetic operator', 'telegram_user_id': 77}}
    c['admins'] = ['test_operator']
    c['personal_intake'] = {'recipient': 'test_operator', 'started_at': int(clock[0]) - 10}
    c['allowed_live_recipients'] = ['test_operator']
    c['live_send'] = False
    c['review_mode'] = True
    for flag in ('sheets_write', 'allow_onboarding_replies', 'allow_registered_replies', 'daily_summary'):
        c[flag] = False
    for field in ('meeting_recipient', 'allowed_group_ids', 'group_destinations', 'business_account_user_id'):
        c.pop(field, None)


def update(now):
    return {'update_id': 1, 'message': {'message_id': 1, 'date': int(now),
            'chat': {'id': 77, 'type': 'private'}, 'from': {'id': 77},
            'text': 'Как разделить синтетический проект на три шага?'}}


def test_permanent_private_gate_has_no_end_and_preserves_duplicate_protection(env):
    e, s, c, clock = env
    profile(c, clock)
    clock[0] += 86400
    u = update(clock[0])
    api = API()
    handler = Updates(e, api, 999)
    handler.handle(u)
    handler.handle(u)
    assert len(s.rows('SELECT * FROM events')) == 1
    assert s.rows('SELECT recipient,bot_chat FROM chats') == [{'recipient': 'test_operator', 'bot_chat': '77'}]
    assert not api.calls


@pytest.mark.parametrize('field', sorted(FORBIDDEN_FIELDS | {'reply_to_message', 'via_bot', 'unknown_attachment'}))
def test_unsafe_or_unconfirmed_metadata_never_persists(env, field):
    e, s, c, clock = env
    profile(c, clock)
    u = update(clock[0])
    u['message'][field] = {}
    api = API()
    Updates(e, api, 999).handle(u)
    for table in ('events', 'sources', 'chats', 'runtime', 'outbox', 'pending_members', 'connections'):
        assert not s.rows('SELECT * FROM ' + table)
    assert not api.calls


@pytest.mark.parametrize('change', ['old', 'future', 'group', 'sender', 'chat', 'bot', 'empty', 'bool_date', 'bad_chat'])
def test_wrong_sender_date_and_non_plain_input_fail_closed(env, change):
    e, s, c, clock = env
    profile(c, clock)
    u = update(clock[0])
    m = u['message']
    if change == 'old': m['date'] = c['personal_intake']['started_at'] - 1
    if change == 'future': m['date'] = int(clock[0]) + 1
    if change == 'group': m['chat']['type'] = 'group'
    if change == 'sender': m['from']['id'] = 88
    if change == 'chat': m['chat']['id'] = 88
    if change == 'bot': m['from']['is_bot'] = True
    if change == 'empty': m['text'] = '  '
    if change == 'bool_date': m['date'] = True
    if change == 'bad_chat': m['chat'] = None
    Updates(e, API(), 999).handle(u)
    assert not s.rows('SELECT * FROM events')
    assert not s.rows('SELECT * FROM runtime')


@pytest.mark.parametrize('kind', ['edited_message', 'business_message', 'business_connection', 'deleted_business_messages'])
def test_non_original_update_kinds_are_rejected(env, kind):
    e, s, c, clock = env
    profile(c, clock)
    u = update(clock[0])
    u[kind] = u.pop('message')
    Updates(e, API(), 999).handle(u)
    assert not s.rows('SELECT * FROM runtime')


@pytest.mark.parametrize('gate', [None, {}, {'recipient': 'other', 'started_at': 1},
    {'recipient': 'test_operator', 'started_at': True},
    {'recipient': 'test_operator', 'started_at': 0},
    {'recipient': 'test_operator', 'started_at': 1, 'ends_at': 300}])
def test_malformed_personal_gate_rejected_by_filter_and_config(env, tmp_path, gate):
    e, s, c, clock = env
    profile(c, clock)
    c['personal_intake'] = gate
    assert not accepts(update(clock[0]), c, clock[0])
    p = tmp_path / 'synthetic.json'
    p.write_text(json.dumps(c))
    with pytest.raises(ValueError): load(p)


@pytest.mark.parametrize('change', [
    {'employees': {'test_operator': {'telegram_user_id': 77}, 'other': {'telegram_user_id': 88}}},
    {'admins': ['test_operator', 'other']}, {'allowed_live_recipients': []},
    {'allowed_live_recipients': ['test_operator', 'other']}, {'sheets_write': True},
    {'daily_summary': True}, {'allow_registered_replies': True}, {'allow_onboarding_replies': True},
    {'review_mode': False}, {'allowed_group_ids': [-77]}, {'meeting_recipient': 'test_operator'},
    {'group_destinations': {'synthetic': {}}}, {'business_account_user_id': 88},
    {'employees': {'test_operator': {'telegram_user_id': True}}},
    {'controlled_intake': {'recipient': 'test_operator', 'started_at': 1, 'ends_at': 300}},
])
def test_scope_expansion_or_conflicting_gate_is_rejected(env, tmp_path, change):
    e, s, c, clock = env
    profile(c, clock)
    c.update(change)
    assert not accepts(update(clock[0]), c, clock[0])
    p = tmp_path / 'synthetic.json'
    p.write_text(json.dumps(c))
    with pytest.raises(ValueError): load(p)


def test_live_operator_only_config_loads_without_changing_model_budget(env, tmp_path, monkeypatch):
    e, s, c, clock = env
    profile(c, clock)
    model_before = copy.deepcopy(c['model'])
    c['live_send'] = True
    c['recipients_verified'] = True
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'SYNTHETIC')
    p = tmp_path / 'synthetic.json'
    p.write_text(json.dumps(c))
    assert load(p) == c
    assert c['model'] == model_before


def test_live_send_requires_verification_and_existing_credential(env, tmp_path, monkeypatch):
    e, s, c, clock = env
    profile(c, clock)
    c['live_send'] = True
    c['recipients_verified'] = False
    p = tmp_path / 'synthetic.json'
    p.write_text(json.dumps(c))
    with pytest.raises(ValueError): load(p)
    c['recipients_verified'] = True
    p.write_text(json.dumps(c))
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    with pytest.raises(ValueError): load(p)


def test_personal_start_is_an_input_boundary_not_a_renewed_window(env):
    e, s, c, clock = env
    profile(c, clock)
    u = update(clock[0])
    c['personal_intake']['started_at'] = int(clock[0]) + 1
    assert not accepts(u, c, clock[0])
    c['personal_intake']['started_at'] = int(clock[0])
    u['message']['entities'] = []
    assert accepts(u, c, clock[0])


@pytest.mark.parametrize('header', ['chat', 'from'])
def test_boolean_identity_does_not_match_numeric_operator(env, header):
    e, s, c, clock = env
    profile(c, clock)
    c['employees']['test_operator']['telegram_user_id'] = 1
    u = update(clock[0])
    u['message']['chat']['id'] = u['message']['from']['id'] = 1
    u['message'][header]['id'] = True
    assert not accepts(u, c, clock[0])
