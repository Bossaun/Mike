"""Finite C07 scheduling with fictional tasks, a local clock and SQLite only."""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from goldway.scheduling import Scheduler
from goldway.store import Store


DAY = 86400
DAILY = dict(frequency='daily', weekdays=list(range(7)), time='12:00',
             timezone='Europe/Moscow')


@pytest.fixture
def case(tmp_path):
    store = Store(str(tmp_path / 'fictional.sqlite'))
    config = dict(business='GOLDWAY', admins=['boss'],
                  employees={'boss': {}, 'alen': {}, 'frost': {}},
                  allowed_live_recipients=['boss', 'alen', 'frost'])
    clock = [datetime(2026, 10, 15, 9, tzinfo=timezone.utc).timestamp()]
    denied, calls = set(), []
    store.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',
                  ('GOLDWAY', 'fictional-source', 'Вымышленное поручение', 'local'))
    store.execute('''INSERT INTO tasks
        (business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        ('GOLDWAY', 'task', 'Вымышленный отчёт', 'alen', '2026-10-20',
         'Сверить три пункта', 'Три пункта проверены', 'confirmed',
         'fictional-source', 'Алэн подготовит отчёт', clock[0] - DAY))
    store.execute('INSERT INTO task_sources VALUES(?,?,?)',
                  ('GOLDWAY', 'task', 'fictional-source'))

    def permitted(task_id, recipient, now):
        calls.append((task_id, recipient, now))
        return (task_id, recipient) not in denied

    def spawn(template_id, key, due_at):
        template = store.rows('SELECT * FROM tasks WHERE business=? AND id=?',
                              ('GOLDWAY', template_id))[0]
        task_id = 'occ-' + hashlib.sha256(key.encode()).hexdigest()
        store.execute('''INSERT INTO tasks
            (business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            ('GOLDWAY', task_id, template['title'], template['owner'],
             datetime.fromtimestamp(due_at, timezone.utc).date().isoformat(),
             template['next_step'], template['criterion'], 'confirmed',
             template['source_id'], template['evidence'], clock[0]))
        store.execute('INSERT INTO task_sources VALUES(?,?,?)',
                      ('GOLDWAY', task_id, template['source_id']))
        store.queue('GOLDWAY', template['owner'], 'Новое вымышленное поручение',
                    'assignment:' + key, clock[0], task_id)
        return task_id

    scheduler = Scheduler(store, config, lambda: clock[0], permitted=permitted, spawn=spawn)
    yield scheduler, store, config, clock, denied, calls, permitted, spawn
    store.db.close()


def schedule(case, *, event='check-1', delay=1800, sender='alen'):
    scheduler, _, _, clock, *_ = case
    return scheduler.schedule_check('task', sender, clock[0] + delay, event, clock[0])


def register(case, *, event='series-1', sender='boss', rule=None):
    scheduler, _, _, clock, *_ = case
    return scheduler.register_series('task', sender, DAILY if rule is None else rule,
                                     event, clock[0])


def reminder_rows(store):
    return store.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%' ORDER BY id")


def test_tables_are_optional_and_constructor_does_not_commit_caller(tmp_path):
    store = Store(str(tmp_path / 'optional.sqlite'))
    assert not store.rows("SELECT name FROM sqlite_master WHERE name LIKE 'c07_%'")
    with pytest.raises(RuntimeError):
        with store.transaction():
            Scheduler(store, {'business': 'GOLDWAY'}, permitted=lambda *args: True,
                      spawn=lambda *args: 'unused')
            assert store.db.in_transaction
            raise RuntimeError('caller rolls back')
    assert not store.rows("SELECT name FROM sqlite_master WHERE name LIKE 'c07_%'")
    store.db.close()


@pytest.mark.parametrize('bad', [True, False, None, '1800', float('nan'),
                               float('inf'), float('-inf'), 10 ** 1000])
def test_invalid_numeric_check_time_is_rejected(case, bad):
    scheduler, store, _, clock, *_ = case
    with pytest.raises(ValueError):
        scheduler.schedule_check('task', 'alen', bad, 'bad', clock[0])
    assert not scheduler.pending('task') and not reminder_rows(store)


@pytest.mark.parametrize('offset', [-1, 0, 7 * DAY + 1])
def test_check_window_has_strict_future_and_seven_day_bound(case, offset):
    with pytest.raises(ValueError):
        schedule(case, delay=offset)


def test_exact_seven_day_bound_is_allowed(case):
    result = schedule(case, delay=7 * DAY)
    assert result == {'task_id': 'task', 'at': case[3][0] + 7 * DAY}


@pytest.mark.parametrize('bad', [True, None, 'now', float('nan'), float('inf'), 'future'])
def test_invalid_or_future_source_time_is_rejected(case, bad):
    scheduler, _, _, clock, *_ = case
    source = clock[0] + 1 if bad == 'future' else bad
    with pytest.raises(ValueError):
        scheduler.schedule_check('task', 'alen', clock[0] + 10, 'bad-source', source)


@pytest.mark.parametrize('sender', ['frost', 'outsider'])
def test_nonowner_cannot_schedule_or_cancel(case, sender):
    scheduler, _, _, clock, *_ = case
    schedule(case)
    with pytest.raises(ValueError):
        scheduler.schedule_check('task', sender, clock[0] + 20, 'other', clock[0])
    with pytest.raises(ValueError):
        scheduler.cancel_check('task', sender, 'cancel-other')
    assert scheduler.pending('task')


@pytest.mark.parametrize('status', ['proposed', 'completed'])
def test_checks_never_use_unconfirmed_or_completed_tasks(case, status):
    scheduler, store, _, clock, *_ = case
    store.execute('UPDATE tasks SET status=?', (status,))
    with pytest.raises(ValueError):
        schedule(case)
    with pytest.raises(ValueError):
        scheduler.cancel_check('task', 'alen', 'cancel')
    assert not store.rows('SELECT * FROM c07_events')


@pytest.mark.parametrize('status', ['confirmed', 'in_progress', 'blocked'])
def test_admin_can_schedule_for_actual_current_owner(case, status):
    scheduler, store, _, _, _, calls, *_ = case
    store.execute('UPDATE tasks SET status=?', (status,))
    schedule(case, sender='boss')
    assert [call[1] for call in calls] == ['boss', 'alen']
    assert scheduler.pending('task')['owner'] == 'alen'


@pytest.mark.parametrize('change', ['actor_acl', 'owner_acl', 'allowed', 'owner',
                                  'deleted_source', 'missing_source', 'linked_source'])
def test_schedule_rejects_unknown_recipients_and_unavailable_sources(case, change):
    _, store, config, _, denied, *_ = case
    if change == 'actor_acl':
        denied.add(('task', 'boss'))
    elif change == 'owner_acl':
        denied.add(('task', 'alen'))
    elif change == 'allowed':
        config['allowed_live_recipients'] = ['boss']
    elif change == 'owner':
        store.execute('UPDATE tasks SET owner=NULL')
    elif change == 'deleted_source':
        store.execute('UPDATE sources SET deleted=1')
    elif change == 'missing_source':
        store.execute('DELETE FROM sources')
    else:
        store.execute('INSERT INTO task_sources VALUES(?,?,?)',
                      ('GOLDWAY', 'task', 'removed-secondary'))
    with pytest.raises(ValueError):
        schedule(case, sender='boss')
    assert not reminder_rows(store)


def test_event_replay_survives_restart_and_changed_task(case, tmp_path):
    scheduler, store, config, clock, _, _, permitted, spawn = case
    source_at, at = clock[0], clock[0] + 1800
    result = schedule(case)
    clock[0] += 8 * DAY
    store.execute("UPDATE tasks SET status='completed'")
    other_store = Store(str(tmp_path / 'fictional.sqlite'))
    restarted = Scheduler(other_store, config, lambda: clock[0], permitted=permitted, spawn=spawn)
    assert restarted.schedule_check('task', 'alen', at, 'check-1', source_at) == result
    with pytest.raises(ValueError, match='different payload'):
        restarted.schedule_check('task', 'alen', at + 1, 'check-1', source_at)
    assert len(store.rows('SELECT * FROM c07_checks')) == 1
    other_store.db.close()


@pytest.mark.parametrize('state', ['pending', 'retry', 'blocked', 'unknown', 'sending', 'sent'])
def test_replace_cancels_only_unsent_older_reminders(case, state):
    scheduler, store, _, clock, *_ = case
    schedule(case)
    store.queue('GOLDWAY', 'alen', 'Старое напоминание', 'reminder:old', clock[0], 'task')
    store.execute('UPDATE outbox SET state=?', (state,))
    result = schedule(case, event='replacement', delay=3600)
    expected = 'cancelled' if state in ('pending', 'retry', 'blocked') else state
    assert reminder_rows(store)[0]['state'] == expected
    assert scheduler.pending('task')['at'] == result['at']
    assert [row['state'] for row in store.rows('SELECT state FROM c07_checks ORDER BY rowid')] == ['cancelled', 'pending']


def test_schedule_joins_existing_transaction_and_rolls_back_together(case):
    scheduler, store, _, clock, *_ = case
    store.queue('GOLDWAY', 'alen', 'Старое', 'reminder:old', clock[0], 'task')
    with pytest.raises(RuntimeError):
        with store.transaction():
            schedule(case)
            assert store.db.in_transaction
            raise RuntimeError('caller rollback')
    assert scheduler.pending('task') is None
    assert reminder_rows(store)[0]['state'] == 'pending'
    assert not store.rows('SELECT * FROM c07_events')


def test_due_check_queues_once_updates_reminder_and_keeps_calendar_deadline(case):
    scheduler, store, config, clock, _, _, permitted, spawn = case
    result = schedule(case)
    assert scheduler.suppress_generic('task')
    assert scheduler.tick()['checks'] == [] and not reminder_rows(store)
    clock[0] = result['at']
    effects = scheduler.tick()
    assert effects['checks'] == [result]
    row = reminder_rows(store)[0]
    assert row['recipient'] == 'alen' and row['task_id'] == 'task'
    assert row['dedupe'].startswith('reminder:c07:')
    assert 'Какой результат' in row['text'] and 'Вымышленный отчёт' in row['text']
    assert scheduler.pending('task') is None and not scheduler.suppress_generic('task')
    task = store.rows('SELECT * FROM tasks')[0]
    assert task['last_reminder'] == clock[0]
    assert task['status'] == 'confirmed' and task['deadline'] == '2026-10-20'
    restarted = Scheduler(store, config, lambda: clock[0], permitted=permitted, spawn=spawn)
    assert restarted.tick()['checks'] == []
    assert len(reminder_rows(store)) == 1


@pytest.mark.parametrize('change', ['completed', 'proposed', 'reassigned', 'deleted_task',
                                  'actor_acl', 'owner_acl', 'allowed', 'member',
                                  'removed_source', 'deleted_source', 'changed_source'])
def test_due_check_cancels_when_current_task_becomes_ineligible(case, change):
    scheduler, store, config, clock, denied, *_ = case
    result = schedule(case, sender='boss')
    if change in ('completed', 'proposed'):
        store.execute('UPDATE tasks SET status=?', (change,))
    elif change == 'reassigned':
        store.execute("UPDATE tasks SET owner='frost'")
    elif change == 'deleted_task':
        store.execute('DELETE FROM tasks')
    elif change == 'actor_acl':
        denied.add(('task', 'boss'))
    elif change == 'owner_acl':
        denied.add(('task', 'alen'))
    elif change == 'allowed':
        config['allowed_live_recipients'] = ['boss']
    elif change == 'member':
        del config['employees']['alen']
    elif change == 'removed_source':
        store.execute('DELETE FROM sources')
    elif change == 'deleted_source':
        store.execute('UPDATE sources SET deleted=1')
    else:
        store.execute("UPDATE tasks SET source_id='different-source'")
    clock[0] = result['at']
    assert scheduler.tick()['cancelled'] == ['task']
    assert not reminder_rows(store) and scheduler.pending('task') is None
    assert not scheduler.suppress_generic('task')


def test_future_invalid_check_no_longer_suppresses_generic(case):
    scheduler, store, *_ = case
    schedule(case)
    store.execute("UPDATE tasks SET owner='frost'")
    assert not scheduler.suppress_generic('task')
    assert scheduler.pending('task') is None


def test_queued_unsent_check_is_cancelled_on_later_revocation(case):
    scheduler, store, _, clock, denied, *_ = case
    result = schedule(case)
    clock[0] = result['at']
    scheduler.tick()
    denied.add(('task', 'alen'))
    scheduler.tick()
    assert reminder_rows(store)[0]['state'] == 'cancelled'


def test_cancel_is_durable_idempotent_and_conflicting_event_rejects(case):
    scheduler, store, config, clock, _, _, permitted, spawn = case
    schedule(case)
    assert scheduler.cancel_check('task', 'boss', 'cancel') is True
    restarted = Scheduler(store, config, lambda: clock[0], permitted=permitted, spawn=spawn)
    assert restarted.cancel_check('task', 'boss', 'cancel') is True
    assert restarted.cancel_check('task', 'boss', 'new-cancel') is False
    assert restarted.cancel_check('task', 'boss', 'new-cancel') is False
    with pytest.raises(ValueError, match='different payload'):
        restarted.cancel_check('task', 'alen', 'cancel')


def test_progress_cancels_own_check_and_old_unsent_reminders(case):
    scheduler, store, _, clock, *_ = case
    schedule(case)
    store.queue('GOLDWAY', 'alen', 'Старое', 'reminder:old', clock[0], 'task')
    clock[0] += 1
    scheduler.progress('task', 'alen', clock[0])
    assert scheduler.pending('task') is None
    assert reminder_rows(store)[0]['state'] == 'cancelled'


def test_older_progress_cannot_cancel_a_newer_scheduled_check(case):
    scheduler, store, _, clock, *_ = case
    old_receipt = clock[0]
    clock[0] += 10
    # Even evidence predating progress must not let an old receipt cancel a
    # scheduling decision that was made later.
    scheduler.schedule_check('task', 'alen', clock[0] + 60, 'new', old_receipt - 1)
    store.queue('GOLDWAY', 'alen', 'Новое', 'reminder:new', clock[0], 'task')
    scheduler.progress('task', 'alen', old_receipt)
    assert scheduler.pending('task')
    assert reminder_rows(store)[0]['state'] == 'pending'


def test_delayed_progress_cancels_earlier_promise_committed_before_actual_receipt(case):
    scheduler, store, _, clock, *_ = case
    original_promise = clock[0]
    original_progress = original_promise + 10
    clock[0] = original_promise + 20
    scheduler.schedule_check('task', 'alen', clock[0] + 1800, 'delayed-promise',
                             original_promise)
    store.queue('GOLDWAY', 'alen', 'Недавнее напоминание', 'reminder:recent',
                clock[0], 'task')
    clock[0] += 10
    scheduler.progress('task', 'alen', clock[0], message_at=original_progress)
    assert scheduler.pending('task') is None
    assert reminder_rows(store)[0]['state'] == 'cancelled'


def test_older_original_progress_received_later_preserves_newer_source_promise(case):
    scheduler, store, _, clock, *_ = case
    original_progress = clock[0]
    clock[0] += 10
    scheduler.schedule_check('task', 'alen', clock[0] + 1800, 'newer-promise',
                             clock[0])
    store.queue('GOLDWAY', 'alen', 'Недавнее напоминание', 'reminder:recent',
                clock[0], 'task')
    clock[0] += 10
    scheduler.progress('task', 'alen', clock[0], message_at=original_progress)
    assert scheduler.pending('task') is not None
    # Receipt time still governs cancellation of already queued, unsent rows.
    assert reminder_rows(store)[0]['state'] == 'cancelled'


def test_older_original_progress_preserves_fired_pending_newer_promise(case):
    scheduler, store, _, clock, *_ = case
    original_progress = clock[0]
    clock[0] += 10
    result = schedule(case, event='newer-promise', delay=10)
    clock[0] = result['at']
    scheduler.tick()
    store.queue('GOLDWAY', 'alen', 'Обычное напоминание', 'reminder:generic',
                clock[0], 'task')
    clock[0] += 10
    scheduler.progress('task', 'alen', clock[0], message_at=original_progress)
    rows = {row['dedupe']: row for row in reminder_rows(store)}
    scheduled = next(row for key, row in rows.items() if key.startswith('reminder:c07:'))
    assert scheduled['state'] == 'pending'
    assert rows['reminder:generic']['state'] == 'cancelled'
    # Later actual progress can cancel the queued promise using the same receipt
    # bound once its original message also postdates that promise's source.
    scheduler.progress('task', 'alen', clock[0], message_at=clock[0] - 1)
    assert next(row for row in reminder_rows(store)
                if row['dedupe'].startswith('reminder:c07:'))['state'] == 'cancelled'


@pytest.mark.parametrize('state', ['pending', 'fired', 'cancelled'])
def test_stale_replacement_preserves_latest_source_history_in_every_check_state(case, state):
    scheduler, store, _, clock, *_ = case
    earlier_source = clock[0]
    clock[0] += 10
    result = schedule(case, event='newer-promise', delay=10)
    if state == 'fired':
        clock[0] = result['at']
        scheduler.tick()
    elif state == 'cancelled':
        scheduler.cancel_check('task', 'alen', 'cancel-newer')
    before_checks = store.rows('SELECT * FROM c07_checks')
    before_outbox = reminder_rows(store)
    with pytest.raises(ValueError, match='older scheduling source'):
        scheduler.schedule_check('task', 'alen', clock[0] + 1800,
                                 'late-old-message', earlier_source)
    assert store.rows('SELECT * FROM c07_checks') == before_checks
    assert reminder_rows(store) == before_outbox
    assert not store.rows("SELECT * FROM c07_events WHERE event_id='late-old-message'")


@pytest.mark.parametrize('sender', ['boss', 'frost', 'outsider'])
def test_progress_requires_actual_owner_even_for_admin(case, sender):
    scheduler, _, _, clock, *_ = case
    schedule(case)
    scheduler.progress('task', sender, clock[0])
    assert scheduler.pending('task')


def test_recurring_registration_requires_admin_confirmed_template_and_future_evidence(case):
    scheduler, store, _, clock, *_ = case
    with pytest.raises(ValueError):
        register(case, sender='alen')
    store.execute("UPDATE tasks SET status='in_progress'")
    with pytest.raises(ValueError):
        register(case)
    store.execute("UPDATE tasks SET status='confirmed'")
    with pytest.raises(ValueError):
        scheduler.register_series('task', 'boss', DAILY, 'future', clock[0] + 1)
    assert not store.rows('SELECT * FROM c07_series')


@pytest.mark.parametrize('change', [dict(frequency='monthly'), dict(weekdays=[]),
                                  dict(weekdays=[True]), dict(weekdays=[0, 0]),
                                  dict(weekdays=[0]), dict(time='25:00'),
                                  dict(timezone='Invalid/Nowhere'), dict(extra=True)])
def test_invalid_rules_are_rejected_without_partial_series(case, change):
    rule = dict(DAILY, **change)
    with pytest.raises((ValueError, KeyError)):
        register(case, rule=rule)
    assert not case[1].rows('SELECT * FROM c07_series')


def test_series_is_strictly_future_and_registration_replay_is_durable(case):
    scheduler, store, config, clock, _, _, permitted, spawn = case
    source_at = clock[0]
    result = register(case)
    # The clock is exactly 12:00 Moscow: inclusive=False skips today's slot.
    assert result['next_at'] == clock[0] + DAY
    clock[0] += 10 * DAY
    restarted = Scheduler(store, config, lambda: clock[0], permitted=permitted, spawn=spawn)
    assert restarted.register_series('task', 'boss', DAILY, 'series-1', source_at) == result
    with pytest.raises(ValueError, match='different payload'):
        restarted.register_series('task', 'boss', dict(DAILY, time='13:00'), 'series-1', source_at)
    assert len(store.rows('SELECT * FROM c07_series')) == 1


def test_recurring_slots_spawn_distinct_tasks_preserving_completed_template(case):
    scheduler, store, _, clock, *_ = case
    first = register(case)
    store.execute("UPDATE tasks SET status='completed',last_reply=?", (clock[0],))
    original = store.rows("SELECT * FROM tasks WHERE id='task'")[0]
    clock[0] = first['next_at']
    one = scheduler.tick()['occurrences']
    assert len(one) == 1 and one[0]['task_id'] != 'task'
    assert scheduler.tick()['occurrences'] == []
    clock[0] += DAY
    two = scheduler.tick()['occurrences']
    assert len(two) == 1 and two[0]['task_id'] not in {'task', one[0]['task_id']}
    assert store.rows("SELECT * FROM tasks WHERE id='task'")[0] == original
    assert len(store.rows('SELECT * FROM c07_occurrences')) == 2
    assert len(store.rows("SELECT * FROM outbox WHERE dedupe LIKE 'assignment:%'")) == 2


@pytest.mark.parametrize('change', ['evidence', 'progress_source', 'completion'])
def test_series_allows_lifecycle_evidence_and_appended_sources_without_changing_snapshot(case, change):
    scheduler, store, _, clock, *_ = case
    first = register(case)
    frozen = store.rows('SELECT snapshot FROM c07_series')[0]['snapshot']
    if change in ('evidence', 'completion'):
        store.execute("UPDATE tasks SET evidence='Отчёт завершён, три пункта сверены'")
    if change in ('progress_source', 'completion'):
        store.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                      ('GOLDWAY', 'progress-source', 'Три пункта сверены', 'local', 0))
        store.execute('INSERT INTO task_sources VALUES(?,?,?)',
                      ('GOLDWAY', 'task', 'progress-source'))
    if change == 'completion':
        store.execute("UPDATE tasks SET status='completed',last_reply=?", (clock[0],))
    original = store.rows("SELECT * FROM tasks WHERE id='task'")[0]
    clock[0] = first['next_at']
    effects = scheduler.tick()
    assert effects['paused'] == [] and len(effects['occurrences']) == 1
    assert effects['occurrences'][0]['task_id'] != 'task'
    assert store.rows("SELECT * FROM tasks WHERE id='task'")[0] == original
    assert store.rows('SELECT snapshot FROM c07_series')[0]['snapshot'] == frozen
    assert json.loads(frozen)['evidence'] == 'Алэн подготовит отчёт'
    assert json.loads(frozen)['source_ids'] == ['fictional-source']


def test_restart_consumes_only_latest_overdue_slot_without_backlog_burst(case):
    scheduler, store, config, clock, _, _, permitted, spawn = case
    result = register(case)
    clock[0] = result['next_at'] + 2000 * DAY + 3600
    restarted = Scheduler(store, config, lambda: clock[0], permitted=permitted, spawn=spawn)
    effects = restarted.tick()['occurrences']
    assert len(effects) == 1
    assert effects[0]['at'] == clock[0] - 3600
    assert restarted.tick()['occurrences'] == []
    assert store.rows('SELECT next_at FROM c07_series')[0]['next_at'] > clock[0]
    assert len(store.rows('SELECT * FROM tasks')) == 2


def test_weekly_recurrence_uses_explicit_weekday_and_timezone(case):
    scheduler, _, _, clock, *_ = case
    rule = dict(frequency='weekly', weekdays=[0], time='10:00', timezone='Asia/Tokyo')
    result = register(case, rule=rule)
    assert result['next_at'] == datetime(2026, 10, 19, 1, tzinfo=timezone.utc).timestamp()
    clock[0] = result['next_at'] + 90 * DAY
    assert len(scheduler.tick()['occurrences']) == 1
    assert scheduler.tick()['occurrences'] == []


@pytest.mark.parametrize('change', ['owner', 'title', 'deadline', 'next_step', 'criterion',
                                  'source_id', 'removed_linked_source', 'deleted_linked_source',
                                  'deleted_task', 'deleted_source', 'removed_source',
                                  'actor_acl', 'owner_acl', 'allowed', 'admin'])
def test_changed_or_unavailable_series_pauses_for_review_without_spawning(case, change):
    scheduler, store, config, clock, denied, *_ = case
    if change in ('removed_linked_source', 'deleted_linked_source'):
        store.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                      ('GOLDWAY', 'other', 'Другой источник', 'local', 0))
        store.execute('INSERT INTO task_sources VALUES(?,?,?)', ('GOLDWAY', 'task', 'other'))
    result = register(case)
    if change == 'owner':
        store.execute("UPDATE tasks SET owner='frost'")
    elif change in ('title', 'deadline', 'next_step', 'criterion', 'source_id'):
        store.execute(f'UPDATE tasks SET {change}=?', ('changed',))
    elif change == 'removed_linked_source':
        store.execute("DELETE FROM task_sources WHERE source_id='other'")
    elif change == 'deleted_linked_source':
        store.execute("UPDATE sources SET deleted=1 WHERE id='other'")
    elif change == 'deleted_task':
        store.execute('DELETE FROM tasks')
    elif change == 'deleted_source':
        store.execute('UPDATE sources SET deleted=1')
    elif change == 'removed_source':
        store.execute('DELETE FROM sources')
    elif change == 'actor_acl':
        denied.add(('task', 'boss'))
    elif change == 'owner_acl':
        denied.add(('task', 'alen'))
    elif change == 'allowed':
        config['allowed_live_recipients'] = ['boss']
    else:
        config['admins'] = ['frost']
    clock[0] = result['next_at']
    effects = scheduler.tick()
    assert effects['paused'] == [result['id']] and effects['occurrences'] == []
    assert store.rows('SELECT state,reason FROM c07_series') == [
        {'state': 'paused', 'reason': 'review_required'}]
    assert not store.rows('SELECT * FROM c07_occurrences') and not reminder_rows(store)


def test_one_invalidated_series_does_not_stop_another_series(case):
    scheduler, store, _, clock, *_ = case
    first = register(case)
    store.execute('''INSERT INTO tasks
        (business,id,title,owner,next_step,criterion,status,source_id,created)
        VALUES(?,?,?,?,?,?,?,?,?)''',
        ('GOLDWAY', 'other-task', 'Другой вымышленный отчёт', 'frost',
         'Сверить', 'Сверено', 'confirmed', 'fictional-source', clock[0]))
    second = scheduler.register_series('other-task', 'boss', DAILY, 'other-series', clock[0])
    store.execute("UPDATE tasks SET title='changed' WHERE id='task'")
    clock[0] = first['next_at']
    effects = scheduler.tick()
    assert effects['paused'] == [first['id']]
    assert [row['series_id'] for row in effects['occurrences']] == [second['id']]


def test_pause_requires_admin_and_replays_saved_result(case):
    scheduler, store, config, clock, _, _, permitted, spawn = case
    result = register(case)
    for sender in ('alen', 'outsider'):
        with pytest.raises(ValueError):
            scheduler.pause_series(result['id'], sender, 'bad-' + sender)
    assert scheduler.pause_series(result['id'], 'boss', 'pause') is True
    restarted = Scheduler(store, config, lambda: clock[0], permitted=permitted, spawn=spawn)
    assert restarted.pause_series(result['id'], 'boss', 'pause') is True
    assert restarted.pause_series(result['id'], 'boss', 'pause-again') is False
    with pytest.raises(ValueError, match='different payload'):
        restarted.pause_series('different-id', 'boss', 'pause')
    clock[0] = result['next_at']
    assert restarted.tick()['occurrences'] == []


def test_registering_replacement_rule_pauses_previous_series(case):
    scheduler, store, *_ = case
    first = register(case)
    second = register(case, event='replacement', rule=dict(DAILY, time='13:00'))
    assert first['id'] != second['id']
    assert store.rows('SELECT state,reason FROM c07_series WHERE id=?', (first['id'],)) == [
        {'state': 'paused', 'reason': 'replaced'}]


@pytest.mark.parametrize('outer_transaction', [False, True])
def test_spawn_failure_rolls_back_task_outbox_and_occurrence_even_in_caller_transaction(case, outer_transaction):
    scheduler, store, config, clock, _, _, permitted, spawn = case
    result = register(case)
    clock[0] = result['next_at']

    def broken(template, key, at):
        spawn(template, key, at)
        raise RuntimeError('fictional clone failed')

    failing = Scheduler(store, config, lambda: clock[0], permitted=permitted, spawn=broken)
    if outer_transaction:
        with store.transaction():
            with pytest.raises(RuntimeError, match='clone failed'):
                failing.tick()
            assert store.db.in_transaction
            assert not store.rows('SELECT * FROM c07_occurrences')
    else:
        with pytest.raises(RuntimeError, match='clone failed'):
            failing.tick()
    assert len(store.rows('SELECT * FROM tasks')) == 1
    assert not store.rows('SELECT * FROM outbox')
    assert store.rows('SELECT next_at FROM c07_series')[0]['next_at'] == result['next_at']
    assert len(scheduler.tick()['occurrences']) == 1


def test_callback_cannot_reset_template_in_place(case):
    _, store, config, clock, _, _, permitted, _ = case
    scheduler = Scheduler(store, config, lambda: clock[0], permitted=permitted,
                          spawn=lambda task, key, at: task)
    result = scheduler.register_series('task', 'boss', DAILY, 'register', clock[0])
    clock[0] = result['next_at']
    with pytest.raises(ValueError, match='distinct authorized task'):
        scheduler.tick()
    assert not store.rows('SELECT * FROM c07_occurrences')
    assert len(store.rows('SELECT * FROM tasks')) == 1
