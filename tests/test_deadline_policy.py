"""Default-disabled calendar policy with local time, SQLite and a fake transport."""
import copy
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from goldway.config import load
from goldway.deadline_policy import DeadlinePolicy, PREFIX
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher


class NoModel:
    def propose(self, context):
        pytest.fail('Calendar policy must never call a model')


class API:
    def __init__(self):
        self.calls = []
        self.on_route = None

    def call(self, method, **params):
        self.calls.append(method)
        if method == 'getBusinessConnection':
            if self.on_route:
                self.on_route()
            return {'user': {'id': 99}, 'is_enabled': True, 'rights': {'can_reply': True}}
        assert method == 'sendMessage'
        return {'message_id': len(self.calls)}


@pytest.fixture
def case(tmp_path):
    zone = ZoneInfo('Europe/Moscow')
    clock = [datetime(2026, 10, 15, 8, 59, tzinfo=zone).timestamp()]
    config = {'business': 'GOLDWAY', 'timezone': zone.key, 'admins': ['boss'],
        'employees': {key: {'name': key, 'telegram_user_id': uid} for uid, key in enumerate(('boss', 'owner', 'other'), 1)},
        'allowed_live_recipients': ['boss', 'owner', 'other'], 'live_send': True,
        'recipients_verified': True, 'reminder_hours': 999999,
        'deadline_policy': {'enabled': True, 'owner_hour': 9, 'admin_hour': 10}}
    store = Store(str(tmp_path / 'synthetic.sqlite'))
    store.execute("INSERT INTO sources(business,id,body,link) VALUES('GOLDWAY','source','Synthetic instruction','local')")
    store.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
        VALUES('GOLDWAY','task','Synthetic task','owner','2026-10-16','Check','Three checks','confirmed','source','Synthetic evidence',?)''', (clock[0] - 100,))
    store.execute("INSERT INTO task_sources VALUES('GOLDWAY','task','source')")
    for key, person in config['employees'].items():
        store.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?)', ('GOLDWAY', key, str(person['telegram_user_id'])))
    engine = Engine(store, config, NoModel(), lambda: clock[0])
    api = API()
    yield engine, store, config, clock, api
    store.db.close()


def rows(case):
    return case[1].rows("SELECT * FROM outbox WHERE dedupe LIKE 'deadline-policy:%' ORDER BY id")


def hour(case, day=15, hour=9, minute=0):
    case[3][0] = datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo(case[2]['timezone'])).timestamp()


def enqueue(case, kind='owner'):
    hour(case, day=15 if kind == 'owner' else 16, hour=9 if kind == 'owner' else 10)
    case[0].tick()
    assert len(rows(case)) == 1
    return rows(case)[0]


def flush(case):
    Dispatcher(case[1], case[2], case[4], lambda: case[3][0]).flush()


@pytest.mark.parametrize('setting', [None, {'enabled': False}])
def test_disabled_has_no_schema_queue_or_model_side_effects(tmp_path, setting):
    store = Store(str(tmp_path / 'disabled.sqlite'))
    config = {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow', 'employees': {}, 'admins': []}
    if setting is not None:
        config['deadline_policy'] = setting
    before = store.rows("SELECT name FROM sqlite_master WHERE type='table'")
    engine = Engine(store, config, NoModel(), lambda: 1000)
    engine.tick()
    assert engine.deadline_policy is None and store.rows('SELECT * FROM outbox') == []
    assert store.rows("SELECT name FROM sqlite_master WHERE type='table'") == before
    store.db.close()


@pytest.mark.parametrize('setting', [None, {}, {'enabled': 'yes'}, {'enabled': True},
    {'enabled': True, 'owner_hour': 9}, {'enabled': True, 'owner_hour': True, 'admin_hour': 10},
    {'enabled': True, 'owner_hour': -1, 'admin_hour': 10},
    {'enabled': True, 'owner_hour': 9, 'admin_hour': 24},
    {'enabled': False, 'owner_hour': 9}])
def test_invalid_or_implicit_hours_rejected(case, tmp_path, setting):
    config = copy.deepcopy(case[2]); config['live_send'] = False; config['deadline_policy'] = setting
    path = tmp_path / 'synthetic-config.json'; path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        load(path)


def test_windows_persistent_dedupe_and_task_fields_unchanged(case):
    engine, store, config, clock, api = case
    before = engine.tasks()[0]
    engine.tick(); assert rows(case) == []
    owner = enqueue(case)
    engine.tick(); Engine(store, config, NoModel(), lambda: clock[0]).tick()
    assert len(rows(case)) == 1 and owner['recipient'] == 'owner'
    hour(case, day=16, hour=9); engine.tick()
    assert len(rows(case)) == 1
    hour(case, day=16, hour=10); engine.tick(); engine.tick()
    admin = rows(case)[1]
    assert admin['recipient'] == 'boss' and 'срок сегодня' in admin['text'] and 'просроч' not in admin['text']
    assert engine.tasks()[0]['last_reminder'] == owner['created']
    for field in ('deadline', 'owner', 'status', 'criterion', 'last_reply'):
        assert engine.tasks()[0][field] == before[field]


def test_late_restart_only_handles_current_day(case):
    hour(case, day=16, hour=23)
    case[0].tick()
    assert [row['recipient'] for row in rows(case)] == ['boss']
    hour(case, day=18, hour=12); case[0].tick()
    assert len(rows(case)) == 1


@pytest.mark.parametrize('change', ["status='proposed'", "status='completed'", "owner=NULL",
    "owner='unknown'", "deadline=NULL", "deadline='bad'", "deadline='2026-2-1'"])
def test_ineligible_tasks_enqueue_nothing(case, change):
    case[1].execute('UPDATE tasks SET ' + change)
    hour(case); case[0].tick()
    assert rows(case) == []


@pytest.mark.parametrize('kind', ['owner', 'admin'])
@pytest.mark.parametrize('change', ['completed', 'proposed', 'owner', 'deadline', 'main_source',
                                   'linked_source', 'owner_removed', 'recipient_removed', 'acl', 'disabled'])
def test_direct_presend_rejects_current_stale_state_without_tick(case, kind, change, monkeypatch):
    engine, store, config, clock, api = case
    row = enqueue(case, kind)
    if change in ('completed', 'proposed'):
        store.execute('UPDATE tasks SET status=?', (change,))
    if change == 'owner': store.execute("UPDATE tasks SET owner='other'")
    if change == 'deadline': store.execute("UPDATE tasks SET deadline='2026-10-17'")
    if change == 'main_source': store.execute("UPDATE sources SET deleted=1")
    if change == 'linked_source': store.execute("INSERT INTO task_sources VALUES('GOLDWAY','task','missing')")
    if change == 'owner_removed': config['employees'].pop('owner')
    if change == 'recipient_removed': config['allowed_live_recipients'].remove(row['recipient'])
    if change == 'acl':
        store.execute('CREATE TABLE intelligence_task_acl(business TEXT,task_id TEXT,evidence_ids TEXT)')
        store.execute("INSERT INTO intelligence_task_acl VALUES('GOLDWAY','task','[\"private-ref\"]')")
        monkeypatch.setattr('goldway.intelligence.readable_evidence_ids', lambda *a: set())
    if change == 'disabled': config['deadline_policy'] = {'enabled': False}
    flush(case)
    assert 'sendMessage' not in api.calls
    assert rows(case)[0]['state'] == 'cancelled' and rows(case)[0]['attempts'] == 0


def test_current_admin_change_cancels_old_manager_recipient(case):
    enqueue(case, 'admin'); case[2]['admins'] = ['other']
    flush(case)
    assert not case[4].calls and rows(case)[0]['state'] == 'cancelled'


def test_route_delay_rechecks_before_claim(case):
    engine, store, config, clock, api = case
    enqueue(case)
    store.execute("UPDATE chats SET business_chat='2',connection='synthetic',last_inbound=? WHERE recipient='owner'", (clock[0],))
    config['business_account_user_id'] = 99
    api.on_route = lambda: store.execute("UPDATE tasks SET status='completed'")
    flush(case)
    assert api.calls == ['getBusinessConnection']
    assert rows(case)[0]['state'] == 'cancelled' and rows(case)[0]['attempts'] == 0


@pytest.mark.parametrize('state', ['pending', 'retry', 'blocked'])
def test_local_day_expiry_cancels_unsent_even_direct_flush(case, state):
    enqueue(case); case[1].execute('UPDATE outbox SET state=?', (state,))
    hour(case, day=16, hour=0)
    flush(case)
    assert not case[4].calls and rows(case)[0]['state'] == 'cancelled'
    assert case[1].rows('SELECT state FROM deadline_policy_slots') == [{'state': 'expired'}]


@pytest.mark.parametrize('state', ['sending', 'unknown', 'sent'])
def test_cleanup_preserves_non_unsent_states(case, state):
    enqueue(case); case[1].execute('UPDATE outbox SET state=?', (state,))
    case[1].execute("UPDATE tasks SET status='completed'")
    case[0].tick()
    assert rows(case)[0]['state'] == state


@pytest.mark.parametrize('state', ['sending', 'unknown'])
def test_uncertain_delivery_cannot_be_bypassed_by_new_owner_slot(case, state):
    enqueue(case); case[1].execute('UPDATE outbox SET state=?', (state,))
    case[1].execute("UPDATE tasks SET owner='other'")
    case[0].tick()
    assert len(rows(case)) == 1 and rows(case)[0]['state'] == state


def test_rename_and_date_return_do_not_repeat_sent_slot(case):
    enqueue(case); flush(case)
    assert rows(case)[0]['state'] == 'sent'
    case[1].execute("UPDATE tasks SET title='Renamed',revision=revision+1,deadline='2026-10-17'")
    case[0].tick()
    case[1].execute("UPDATE tasks SET deadline='2026-10-16'")
    case[0].tick(); flush(case)
    assert len(rows(case)) == 1 and case[4].calls == ['sendMessage']


def c07(case, at):
    case[2]['scheduling'] = {'enabled': True}
    engine = Engine(case[1], case[2], NoModel(), lambda: case[3][0])
    engine.scheduler.schedule_check('task', 'owner', at, 'agreed', case[3][0])
    return engine


@pytest.mark.parametrize('due', [False, True])
def test_c07_future_or_same_tick_due_covers_owner_only(case, due):
    hour(case)
    engine = c07(case, case[3][0] + (60 if due else 3600))
    if due: case[3][0] += 60
    engine.tick()
    assert rows(case) == []
    assert case[1].rows('SELECT state FROM deadline_policy_slots') == [{'state': 'covered_by_agreed_check'}]
    hour(case, day=16, hour=10)
    # A new owner promise still cannot suppress the independent due-day signal.
    engine.scheduler.schedule_check('task', 'owner', case[3][0] + 1800, 'tomorrow-agreed', case[3][0])
    before = engine.scheduler.pending('task')
    engine.tick()
    assert [row['recipient'] for row in rows(case)] == ['boss']
    assert engine.scheduler.pending('task') == before
    last_reminder = engine.tasks()[0]['last_reminder']
    engine.scheduler.cancel_check('task', 'owner', 'cancel-c07')
    assert rows(case)[0]['state'] == 'pending'
    assert engine.tasks()[0]['last_reminder'] == last_reminder


def test_c07_created_after_enqueue_prevents_second_owner_send(case):
    enqueue(case)
    c07(case, case[3][0] + 1800)
    flush(case)
    assert not case[4].calls and rows(case)[0]['state'] == 'cancelled'


def test_calendar_owner_queue_suppresses_generic_on_same_tick(case):
    case[2]['reminder_hours'] = 1
    case[1].execute('UPDATE tasks SET created=?', (case[3][0] - 86400,))
    enqueue(case)
    assert len(case[1].rows('SELECT * FROM outbox')) == 1


def test_calendar_owner_replaces_unsent_generic_queued_before_opening(case):
    engine, store, config, clock, api = case
    config['reminder_hours'] = 1
    store.execute('UPDATE tasks SET created=?', (clock[0] - 86400,))
    engine.tick()
    generic = store.rows('SELECT * FROM outbox')[0]
    assert generic['dedupe'].startswith('reminder:task:') and generic['state'] == 'pending'
    hour(case); engine.tick()
    assert store.execute('SELECT state FROM outbox WHERE id=?', (generic['id'],)).fetchone()[0] == 'cancelled'
    assert len(rows(case)) == 1 and rows(case)[0]['state'] == 'pending'
    flush(case)
    assert api.calls == ['sendMessage']


@pytest.mark.parametrize('state', ['pending', 'retry', 'blocked', 'sending', 'unknown', 'sent'])
def test_generic_replacement_preserves_uncertain_and_other_reminders(case, state):
    engine, store, config, clock, api = case
    store.queue('GOLDWAY', 'owner', 'Synthetic generic', 'reminder:task:old', clock[0], 'task')
    generic_id = store.execute('SELECT id FROM outbox').fetchone()[0]
    store.execute('UPDATE outbox SET state=?', (state,))
    protected = [('GOLDWAY', 'owner', 'reminder:c07:check', 'task'),
        ('GOLDWAY', 'owner', 'reminder:c07-prompt:check', 'task'),
        ('GOLDWAY', 'other', 'reminder:task:other-owner', 'task'),
        ('GOLDWAY', 'owner', 'reminder:task:other-task', 'other-task'),
        ('GOLDWAY', 'owner', 'reminder:task-extra:prefix', 'task'),
        ('OTHER', 'owner', 'reminder:task:other-business', 'task')]
    for business, recipient, dedupe, task_id in protected:
        store.queue(business, recipient, 'Synthetic protected', dedupe, clock[0], task_id)
    hour(case); engine.tick()
    expected = 'cancelled' if state in ('pending', 'retry', 'blocked') else state
    assert store.execute('SELECT state FROM outbox WHERE id=?', (generic_id,)).fetchone()[0] == expected
    for business, recipient, dedupe, task_id in protected:
        assert store.execute('SELECT state FROM outbox WHERE business=? AND dedupe=?', (business, dedupe)).fetchone()[0] == 'pending'


def test_active_template_excluded_but_occurrence_gets_its_own_slot(case):
    hour(case, day=14, hour=12)
    case[2]['scheduling'] = {'enabled': True}
    engine = Engine(case[1], case[2], NoModel(), lambda: case[3][0])
    rule = {'frequency': 'daily', 'weekdays': list(range(7)), 'time': '09:00', 'timezone': case[2]['timezone']}
    series = engine.scheduler.register_series('task', 'boss', rule, 'series', case[3][0])
    case[3][0] = series['next_at']; engine.tick()
    policy = rows(case)
    assert len(policy) == 0  # occurrence due today, admin hour is not yet open
    case[3][0] += 3600; engine.tick()
    policy = rows(case)
    assert len(policy) == 1 and policy[0]['task_id'] != 'task' and policy[0]['recipient'] == 'boss'


def test_slot_and_outbox_roll_back_together(case, monkeypatch):
    def broken(*args, **kwargs): raise RuntimeError('Synthetic enqueue failure')
    monkeypatch.setattr(case[1], 'queue', broken)
    hour(case)
    with pytest.raises(RuntimeError): case[0].tick()
    assert not case[1].rows('SELECT * FROM deadline_policy_slots') and rows(case) == []


@pytest.mark.parametrize('zone,instant,deadline,expected', [
    ('Asia/Tokyo', '2026-10-15T09:00:00+09:00', '2026-10-16', 1),
    ('America/New_York', '2026-11-01T01:30:00-04:00', '2026-11-02', 1),
    ('America/New_York', '2026-03-08T03:30:00-04:00', '2026-03-09', 0),
])
def test_business_timezone_fold_and_gap_are_single_explicit_slots(case, zone, instant, deadline, expected):
    case[2]['timezone'] = zone
    case[2]['deadline_policy']['owner_hour'] = 2 if '03-08' in instant else (1 if '11-01' in instant else 9)
    case[1].execute('UPDATE tasks SET deadline=?', (deadline,))
    case[3][0] = datetime.fromisoformat(instant).timestamp()
    engine = Engine(case[1], case[2], NoModel(), lambda: case[3][0])
    engine.tick(); engine.tick()
    if '11-01' in instant:
        case[3][0] += 3600; engine.tick()
    assert len(rows(case)) == expected


def test_business_scoped_reconcile_leaves_foreign_rows_untouched(case):
    row = enqueue(case)
    case[1].execute('''INSERT INTO deadline_policy_slots SELECT 'OTHER',id,task_id,deadline,owner,kind,
        recipient,day,opens,expires,state,outbox_id FROM deadline_policy_slots''')
    case[1].execute("UPDATE tasks SET status='completed'")
    case[0].tick()
    assert case[1].rows("SELECT state FROM deadline_policy_slots WHERE business='OTHER'") == [{'state': 'queued'}]
