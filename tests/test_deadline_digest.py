"""Finite digest snapshots with SQLite, synthetic tasks and a fake transport."""
import copy
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from goldway.deadline_policy import DIGEST_PREFIX, PREFIX, validate_config
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher


class NoModel:
    def propose(self, context):
        pytest.fail('Digest must never call the model')


class API:
    def __init__(self):
        self.calls = []
        self.on_route = None

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'getBusinessConnection':
            if self.on_route:
                self.on_route()
            return {'user': {'id': 99}, 'is_enabled': True, 'rights': {'can_reply': True}}
        assert method == 'sendMessage'
        return {'message_id': len(self.calls)}


def add(case, key, *, owner='alice', deadline='2026-10-15', status='confirmed', title=None):
    store, config, clock, engine, api = case
    store.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',
                  (config['business'], key, 'Synthetic instruction', 'local'))
    store.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
        VALUES(?,?,?,?,?,'Check','Three checks',?,?,?,?)''',
        (config['business'], key, title or key.upper(), owner, deadline, status, key, 'Synthetic evidence', clock[0] - 100))
    store.execute('INSERT INTO task_sources VALUES(?,?,?)', (config['business'], key, key))


@pytest.fixture
def case(tmp_path):
    clock = [datetime(2026, 10, 15, 9, 59, tzinfo=ZoneInfo('Europe/Moscow')).timestamp()]
    config = {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow', 'admins': ['boss'],
        'employees': {'boss': {'name': 'Manager', 'telegram_user_id': 1},
            'alice': {'name': 'Alice', 'telegram_user_id': 2},
            'bob': {'name': 'Bob', 'telegram_user_id': 3}},
        'allowed_live_recipients': ['boss', 'alice', 'bob'], 'live_send': True,
        'recipients_verified': True, 'reminder_hours': 999999,
        'deadline_policy': {'enabled': True, 'owner_hour': 9, 'admin_hour': 10, 'admin_mode': 'digest'}}
    store = Store(str(tmp_path / 'synthetic.sqlite'))
    api = API()
    value = (store, config, clock, None, api)
    add(value, 'first')
    add(value, 'second', owner='bob')
    for key, person in config['employees'].items():
        store.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?)', ('GOLDWAY', key, str(person['telegram_user_id'])))
    engine = Engine(store, config, NoModel(), lambda: clock[0])
    yield store, config, clock, engine, api
    store.db.close()


def at(case, day=15, hour=10, minute=0):
    case[2][0] = datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo(case[1]['timezone'])).timestamp()


def digests(case):
    return case[0].rows('SELECT * FROM outbox WHERE business=? AND substr(dedupe,1,?)=? ORDER BY id',
                        (case[1]['business'], len(DIGEST_PREFIX), DIGEST_PREFIX))


def queue(case):
    at(case)
    case[3].tick()
    assert len(digests(case)) == 1
    return digests(case)[0]


def flush(case):
    Dispatcher(case[0], case[1], case[4], lambda: case[2][0]).flush()


def sent(case):
    return [params for method, params in case[4].calls if method == 'sendMessage']


def acl(case, task_id, monkeypatch, allowed):
    case[0].execute('''CREATE TABLE IF NOT EXISTS intelligence_task_acl(
        business TEXT,task_id TEXT,evidence_ids TEXT,PRIMARY KEY(business,task_id))''')
    case[0].execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',
                    ('GOLDWAY', task_id, json.dumps(['synthetic-private-ref'])))
    monkeypatch.setattr('goldway.intelligence.readable_evidence_ids', lambda *args: allowed)


@pytest.mark.parametrize('mode', [None, 'per_task', 'digest'])
def test_validation_preserves_existing_and_explicit_modes(case, mode):
    config = copy.deepcopy(case[1])
    if mode is None:
        config['deadline_policy'].pop('admin_mode')
    else:
        config['deadline_policy']['admin_mode'] = mode
    validate_config(config)


@pytest.mark.parametrize('mode', [None, True, 'daily', [], {}])
def test_invalid_admin_mode_rejected(case, mode):
    case[1]['deadline_policy']['admin_mode'] = mode
    with pytest.raises(ValueError):
        validate_config(case[1])


@pytest.mark.parametrize('setting', [None, {'enabled': False}])
def test_disabled_creates_no_schema_queue_or_model(tmp_path, setting):
    store = Store(str(tmp_path / 'disabled.sqlite'))
    config = {'business': 'GOLDWAY', 'employees': {}, 'admins': []}
    if setting is not None:
        config['deadline_policy'] = setting
    before = store.rows('SELECT name FROM sqlite_master')
    Engine(store, config, NoModel(), lambda: 1000).tick()
    assert store.rows('SELECT name FROM sqlite_master') == before
    assert not store.rows('SELECT * FROM outbox')
    store.db.close()


def test_existing_per_task_mode_stays_two_notices_and_no_digest_schema(tmp_path):
    store = Store(str(tmp_path / 'compat.sqlite'))
    config = {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow', 'admins': ['boss'],
        'employees': {'boss': {}, 'alice': {}}, 'allowed_live_recipients': ['boss', 'alice'],
        'reminder_hours': 999999, 'deadline_policy': {'enabled': True, 'owner_hour': 9, 'admin_hour': 10}}
    clock = [datetime(2026, 10, 15, 10, tzinfo=ZoneInfo('Europe/Moscow')).timestamp()]
    value = (store, config, clock, None, None)
    add(value, 'one'); add(value, 'two')
    Engine(store, config, NoModel(), lambda: clock[0]).tick()
    assert len(store.rows('SELECT * FROM outbox')) == 2
    assert not store.execute("SELECT 1 FROM sqlite_master WHERE name='deadline_policy_digests'").fetchone()
    store.db.close()


def test_one_day_snapshot_restarts_and_late_tasks_never_add_second_delivery(case):
    store, config, clock, engine, api = case
    before = store.rows('SELECT id,last_reminder,last_reply,status FROM tasks ORDER BY id')
    engine.tick(); assert not digests(case)
    row = queue(case)
    assert row['recipient'] == 'boss' and row['task_id'] is None and row['parse_mode'] is None
    assert all(value in row['text'] for value in ('№1', 'FIRST', 'Alice', '№2', 'SECOND', 'Bob', 'сегодня'))
    assert 'просроч' not in row['text']
    engine.tick(); Engine(store, config, NoModel(), lambda: clock[0]).tick()
    assert len(digests(case)) == 1
    add(case, 'late-before-send')
    engine.tick(); flush(case)
    assert len(sent(case)) == 1 and 'LATE-BEFORE-SEND' not in sent(case)[0]['text']
    add(case, 'late-after-send'); engine.tick(); flush(case)
    assert len(digests(case)) == 1 and len(sent(case)) == 1
    assert store.rows("SELECT id,last_reminder,last_reply,status FROM tasks WHERE id IN ('first','second') ORDER BY id") == before
    at(case, day=17); engine.tick(); flush(case)
    assert len(digests(case)) == 1
    add(case, 'next-day', deadline='2026-10-17'); engine.tick(); flush(case)
    assert len(digests(case)) == 2 and len(sent(case)) == 2


@pytest.mark.parametrize('change', ['completed', 'proposed', 'owner', 'deadline', 'source', 'linked_source',
                                   'owner_removed', 'owner_allowlist', 'acl'])
def test_current_item_guard_at_enqueue_and_before_direct_flush(case, change, monkeypatch):
    store, config, clock, engine, api = case
    row = queue(case)
    assert 'SECOND' in row['text']
    if change in ('completed', 'proposed'):
        store.execute("UPDATE tasks SET status=? WHERE id='second'", (change,))
    if change == 'owner': store.execute("UPDATE tasks SET owner='alice' WHERE id='second'")
    if change == 'deadline': store.execute("UPDATE tasks SET deadline='2026-10-16' WHERE id='second'")
    if change == 'source': store.execute("UPDATE sources SET deleted=1 WHERE id='second'")
    if change == 'linked_source': store.execute("INSERT INTO task_sources VALUES('GOLDWAY','second','missing')")
    if change == 'owner_removed': config['employees'].pop('bob')
    if change == 'owner_allowlist': config['allowed_live_recipients'].remove('bob')
    if change == 'acl': acl(case, 'second', monkeypatch, set())
    # No intervening tick: Dispatcher must remove the stale fetched body itself.
    flush(case)
    assert len(sent(case)) == 1
    assert 'FIRST' in sent(case)[0]['text'] and 'SECOND' not in sent(case)[0]['text']
    assert digests(case)[0]['text'] == sent(case)[0]['text']
    assert not store.execute("SELECT 1 FROM sqlite_master WHERE name='intelligence_outbox_acl'").fetchone()


def test_enqueue_filters_acl_proposed_source_and_template_without_hidden_count(case, monkeypatch):
    store, config, clock, engine, api = case
    acl(case, 'second', monkeypatch, set())
    add(case, 'hidden-proposed', status='proposed')
    add(case, 'hidden-source'); store.execute("UPDATE sources SET deleted=1 WHERE id='hidden-source'")
    config['scheduling'] = {'enabled': True}
    engine = Engine(store, config, NoModel(), lambda: clock[0])
    add(case, 'hidden-template')
    store.execute('''INSERT INTO c07_series(business,id,task_id,actor,owner,rule,snapshot,next_at,source_at,created,state)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        ('GOLDWAY', 'series', 'hidden-template', 'boss', 'alice', '{}', '{}', clock[0] + 86400, clock[0], clock[0], 'active'))
    at(case); engine.deadline_policy.tick()
    assert 'FIRST' in digests(case)[0]['text'] and 'SECOND' not in digests(case)[0]['text']
    assert 'HIDDEN' not in digests(case)[0]['text'] and 'Ещё' not in digests(case)[0]['text']


def test_route_delay_acl_revoke_rebuilds_then_reloads_actual_sent_body(case, monkeypatch):
    store, config, clock, engine, api = case
    allowed = {'synthetic-private-ref'}
    acl(case, 'second', monkeypatch, allowed)
    queue(case)
    config['business_account_user_id'] = 99
    store.execute("UPDATE chats SET business_chat='1',connection='synthetic',last_inbound=? WHERE recipient='boss'", (clock[0],))
    api.on_route = allowed.clear
    flush(case)
    assert [method for method, params in api.calls] == ['getBusinessConnection', 'sendMessage']
    assert 'SECOND' not in sent(case)[0]['text'] and 'FIRST' in sent(case)[0]['text']
    assert sent(case)[0]['text'] == digests(case)[0]['text']


def test_provenance_is_rechecked_after_final_guard_reload(case, monkeypatch):
    queue(case)
    calls = []
    def permitted(store, business, recipient, outbox_id, now, config):
        calls.append(store.execute('SELECT text FROM outbox WHERE id=?', (outbox_id,)).fetchone()[0])
        return len(calls) == 1
    monkeypatch.setattr('goldway.intelligence.delivery_permitted', permitted)
    flush(case)
    assert len(calls) == 2 and not sent(case)
    assert digests(case)[0]['state'] == 'blocked' and digests(case)[0]['attempts'] == 0


def test_empty_snapshot_cancels_without_same_day_replacement(case):
    queue(case)
    case[0].execute("UPDATE tasks SET status='completed'")
    flush(case)
    assert not sent(case) and digests(case)[0]['state'] == 'cancelled'
    add(case, 'late'); case[3].tick(); flush(case)
    assert len(digests(case)) == 1 and not sent(case)


@pytest.mark.parametrize('change', ['disabled', 'per_task', 'admin', 'admin_allowlist', 'admin_removed'])
def test_changed_policy_or_manager_cancels_unsent_digest(case, change):
    queue(case)
    config = case[1]
    if change == 'disabled': config['deadline_policy'] = {'enabled': False}
    if change == 'per_task': config['deadline_policy']['admin_mode'] = 'per_task'
    if change == 'admin': config['admins'] = ['alice']
    if change == 'admin_allowlist': config['allowed_live_recipients'].remove('boss')
    if change == 'admin_removed': config['employees'].pop('boss')
    flush(case)
    assert not case[4].calls and digests(case)[0]['state'] == 'cancelled'


@pytest.mark.parametrize('state', ['pending', 'retry', 'blocked'])
def test_day_expiry_before_direct_flush_cancels_definitely_unsent(case, state):
    queue(case); case[0].execute('UPDATE outbox SET state=?', (state,))
    at(case, day=16, hour=0)
    flush(case)
    assert not case[4].calls and digests(case)[0]['state'] == 'cancelled'
    assert case[0].rows('SELECT state FROM deadline_policy_digests') == [{'state': 'expired'}]


@pytest.mark.parametrize('state', ['sending', 'unknown', 'sent'])
def test_uncertain_or_sent_never_replaced_on_restart_new_task_or_admin(case, state):
    queue(case); case[0].execute('UPDATE outbox SET state=?', (state,))
    case[0].execute("UPDATE tasks SET status='completed'")
    add(case, 'late'); case[1]['admins'] = ['alice']
    Engine(case[0], case[1], NoModel(), lambda: case[2][0]).tick(); flush(case)
    assert len(digests(case)) == 1 and digests(case)[0]['state'] == state and not sent(case)
    assert case[0].rows('SELECT state FROM deadline_policy_digests') == [{'state': state}]


def test_blocked_same_row_rebuilds_when_route_becomes_available(case):
    queue(case)
    case[0].execute("DELETE FROM chats WHERE recipient='boss'")
    flush(case)
    assert digests(case)[0]['state'] == 'blocked' and not sent(case)
    case[0].execute("UPDATE tasks SET status='completed' WHERE id='second'")
    case[0].execute("INSERT INTO chats(business,recipient,bot_chat) VALUES('GOLDWAY','boss','1')")
    case[2][0] += 301; flush(case)
    assert len(sent(case)) == 1 and 'SECOND' not in sent(case)[0]['text'] and len(digests(case)) == 1


@pytest.mark.parametrize('prior_state', ['pending', 'sending', 'unknown', 'sent'])
def test_switch_from_per_task_cannot_duplicate_possibly_delivered_manager_notice(case, prior_state):
    case[1]['deadline_policy'].pop('admin_mode')
    at(case); case[3].tick()
    case[0].execute('UPDATE outbox SET state=?', (prior_state,))
    case[1]['deadline_policy']['admin_mode'] = 'digest'
    case[3].tick()
    if prior_state == 'pending':
        assert len(digests(case)) == 1
        assert all(row['state'] == 'cancelled' for row in case[0].rows('SELECT * FROM outbox WHERE task_id IS NOT NULL'))
    else:
        assert not digests(case)


def test_switch_after_sent_digest_does_not_create_per_task_duplicates(case):
    queue(case); flush(case)
    case[1]['deadline_policy']['admin_mode'] = 'per_task'
    case[3].tick(); flush(case)
    assert len(case[0].rows('SELECT * FROM outbox')) == 1 and len(sent(case)) == 1


def test_plain_text_one_message_bound_counts_only_accessible_tasks(case):
    case[1]['employees']['alice']['name'] = 'Owner\n' + '🚀' * 100
    for number in range(90):
        add(case, f'bulk-{number}', title='🚀' * 160 + '\nextra')
    for number in range(7):
        add(case, f'hidden-{number}', title='SHOULD NOT COUNT', status='proposed')
    queue(case); flush(case)
    assert len(sent(case)) == 1 and 'parse_mode' not in sent(case)[0]
    text = sent(case)[0]['text']
    assert ' '.join(case[1]['employees']['alice']['name'].split()) in text
    assert len(text.encode('utf-16-le')) // 2 <= 4000
    displayed = len(re.findall(r'\n№\d+', text))
    omitted = int(re.search(r'Ещё (\d+) доступных задач', text)[1])
    assert displayed + omitted == 92 and 'SHOULD NOT COUNT' not in text


def test_slot_and_outbox_are_atomic(case, monkeypatch):
    monkeypatch.setattr(case[0], 'queue', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('Synthetic failure')))
    at(case)
    with pytest.raises(RuntimeError): case[3].tick()
    assert not digests(case) and not case[0].rows('SELECT * FROM deadline_policy_digests')


def test_reconcile_is_business_scoped(case):
    queue(case)
    case[0].execute('''INSERT INTO deadline_policy_digests SELECT 'OTHER',id,recipient,day,opens,expires,items,state,outbox_id
        FROM deadline_policy_digests''')
    case[0].execute("UPDATE tasks SET status='completed'")
    case[3].tick()
    assert case[0].rows("SELECT state FROM deadline_policy_digests WHERE business='OTHER'") == [{'state': 'queued'}]


def test_owner_day_before_keeps_generic_replacement_with_digest_enabled(case):
    case[0].execute("UPDATE tasks SET deadline='2026-10-16',created=?", (case[2][0] - 86400,))
    case[1]['reminder_hours'] = 1
    at(case, hour=8, minute=59); case[3].tick()
    assert len(case[0].rows("SELECT * FROM outbox WHERE state='pending'")) == 2
    at(case, hour=9); case[3].tick()
    assert not digests(case)
    assert len(case[0].rows("SELECT * FROM outbox WHERE state='cancelled' AND dedupe LIKE 'reminder:%'")) == 2
    assert len(case[0].rows("SELECT * FROM outbox WHERE state='pending' AND task_id IS NOT NULL")) == 2


def test_c07_suppresses_owner_but_not_next_day_digest(case):
    case[0].execute("UPDATE tasks SET deadline='2026-10-16'")
    case[1]['scheduling'] = {'enabled': True}
    engine = Engine(case[0], case[1], NoModel(), lambda: case[2][0])
    engine.scheduler.schedule_check('first', 'alice', case[2][0] + 60, 'agreed', case[2][0])
    engine.deadline_policy.tick()
    assert case[0].rows("SELECT state FROM deadline_policy_slots WHERE task_id='first'") == [{'state': 'covered_by_agreed_check'}]
    at(case, day=16)
    engine.scheduler.schedule_check('first', 'alice', case[2][0] + 1800, 'future', case[2][0])
    before = engine.scheduler.pending('first')
    engine.deadline_policy.tick()
    assert len(digests(case)) == 1 and 'FIRST' in digests(case)[0]['text']
    assert engine.scheduler.pending('first') == before
