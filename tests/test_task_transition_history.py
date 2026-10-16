"""Actual authorized local writer boundaries; synthetic-only prospective history."""
import copy
from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest
import goldway.engine as engine_module
import goldway.importer as importer_module
import goldway.sheets as sheets_module
from goldway.engine import Engine
from goldway.importer import LEGACY_HEADERS, import_tasks
from goldway.intelligence import reader_key
from goldway.model import ScriptedModel
from goldway.scheduling import Scheduler
from goldway.sheets import Sheets
from goldway.store import Store
from goldway.task_transition_history import (HistoryError, append_transition,
                                              snapshot, start_observing)
from goldway.work_context import work_id

WHEN = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo('Europe/Moscow')).timestamp()


@pytest.fixture
def case(monkeypatch):
    import builtins, socket, httpx
    store = Store(':memory:')
    clock = [WHEN]
    config = {'business': 'GOLDWAY', 'admins': ['admin'], 'timezone': 'Europe/Moscow',
              'employees': {'admin': {'name': 'Admin'}, 'alice': {'name': 'Alice'}, 'bob': {'name': 'Bob'}},
              'review_mode': False, 'reminder_hours': 24, 'rules': []}
    engine = Engine(store, config, ScriptedModel({'actions': [], 'summary': 'Synthetic'}), lambda: clock[0])
    def forbidden(*a, **k):
        pytest.fail('history boundary attempted external I/O')
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    yield engine, store, config, clock
    store.db.close()


def history(case):
    return case[1].rows('SELECT * FROM task_status_history ORDER BY task_number,revision')


def create(case, title='Synthetic private title', *, status='confirmed', extra_actions=()):
    engine, store, _, _ = case
    text = f'Alice will prepare {title} by 2026-10-21. PRIVATE-SOURCE-TEXT'
    action = dict(op='create', title=title, owner='alice', owner_evidence='Alice',
                  deadline='2026-10-21', deadline_evidence='2026-10-21', next_step='PRIVATE-NEXT',
                  criterion='PRIVATE-CRITERION', status=status, evidence=text)
    engine.model = ScriptedModel({'actions': [action, *extra_actions], 'summary': 'Synthetic'})
    event = engine.transcript(text, link='PRIVATE-SOURCE-ID')
    engine.process(event)
    return store.rows('SELECT * FROM tasks WHERE business=? ORDER BY public_number', ('GOLDWAY',))[-1], event


def update(case, task, *, sender='admin', event='synthetic-update', evidence=None, **fields):
    engine, _, _, _ = case
    text = evidence or 'Concrete synthetic output produced and saved successfully by Alice and Bob on 2026-10-22.'
    action = dict(op='update', task_id=task['id'], evidence=text, **fields)
    if 'owner' in fields and fields['owner']:
        action['owner_evidence'] = 'Bob' if fields['owner'] == 'bob' else 'Alice'
    if fields.get('deadline'):
        action['deadline_evidence'] = fields['deadline']
    engine.model = ScriptedModel({'actions': [action], 'summary': 'Synthetic result'})
    engine.reply(event, sender, text, task_id=task['id'], channel='bot')
    engine.process(event)
    return case[1].rows('SELECT * FROM tasks WHERE business=? AND id=?', ('GOLDWAY', task['id']))[0]


def raw_task(case, *, id='PRIVATE-TASK-ID', status='completed', **fields):
    _, store, _, _ = case
    data = dict(business='GOLDWAY', id=id, title='PRIVATE-OLD-TITLE', owner='alice',
                deadline='2026-10-19', next_step='PRIVATE-NEXT', criterion='PRIVATE-CRITERION',
                status=status, source_id='PRIVATE-OLD-SOURCE', evidence='PRIVATE-OLD-RESULT', created=1)
    data.update(fields)
    store.execute(f"INSERT INTO tasks({','.join(data)}) VALUES({','.join('?' for _ in data)})", tuple(data.values()))
    return store.rows('SELECT * FROM tasks WHERE business=? AND id=?', (data['business'], id))[0]


def legacy_rows():
    return [LEGACY_HEADERS, ['1', 'PRIVATE-IMPORTED-TITLE', 'Alice', '', 'PRIVATE-NEXT',
                            'PRIVATE-CRITERION', 'Выполнено', '2026-10-19',
                            'PRIVATE-IMPORTED-RESULT concrete output', 'PRIVATE-COMMENT']]


def recurring_setup(case):
    engine, store, config, clock = case
    task, _ = create(case)
    # Existing scheduling authorization is supplied synthetically; this tests
    # the actual Engine cloning writer without activating a poller or provider.
    Scheduler(store, config, lambda: clock[0], permitted=engine._scheduling_permitted, spawn=engine._spawn_recurring)
    snap = {key: task[key] for key in ('title', 'owner', 'deadline', 'next_step', 'criterion', 'source_id', 'evidence')}
    snap['source_ids'] = [task['source_id']]
    store.execute('INSERT INTO c07_series VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  ('GOLDWAY', 'synthetic-series', task['id'], 'admin', 'alice', '{}', json.dumps(snap),
                   WHEN, WHEN, WHEN, 'active', None))
    return task


def fail_after_append(monkeypatch, target):
    real = target.append_transition
    def fail(*a, **k):
        real(*a, **k)
        raise HistoryError('synthetic_ledger_failure')
    monkeypatch.setattr(target, 'append_transition', fail)


def test_engine_actual_create_update_capture_server_time_and_safe_metadata(case):
    task, _ = create(case)
    first = history(case)[0]
    assert first['task_ref'] == work_id('GOLDWAY', task['id'])
    assert first['owner_ref'] == reader_key('GOLDWAY', 'alice')
    assert first['task_number'] == task['public_number'] and first['revision'] == 1
    assert first['task_origin'] == 'new' and first['history_quality'] == 'from_creation'
    assert first['source_kind'] == 'transcript' and first['before_status'] is None
    assert first['deadline'] == '2026-10-21' and first['is_completion_transition'] == 0
    case[3][0] += 100
    update(case, task, status='completed')
    second = history(case)[1]
    assert second['before_status'] == 'confirmed' and second['status'] == 'completed'
    assert second['observed_at'] == case[3][0] and second['is_completion_transition'] == 1
    assert second['source_kind'] == 'reply_bot' and second['history_quality'] == 'from_creation'
    dump = json.dumps(history(case))
    for secret in (task['id'], 'alice', 'admin', 'PRIVATE', 'Concrete synthetic', 'Alice'):
        assert secret not in dump


@pytest.mark.parametrize('field,value', [('deadline', '2026-10-22'), ('owner', 'bob'), ('status', 'blocked')])
def test_metadata_changes_are_journaled_even_without_status_change(case, field, value):
    task, _ = create(case)
    update(case, task, **{field: value})
    second = history(case)[1]
    assert len(history(case)) == 2
    if field == 'deadline':
        assert second['before_deadline'] == '2026-10-21' and second['deadline'] == value
        assert second['before_status'] == second['status'] == 'confirmed'
    if field == 'owner':
        assert second['before_owner_ref'] == reader_key('GOLDWAY', 'alice')
        assert second['owner_ref'] == reader_key('GOLDWAY', 'bob')


def test_event_replay_and_duplicate_title_create_append_nothing(case):
    task, event = create(case)
    case[0].process(event)
    create(case, title=task['title']+' ')  # fingerprint normalizes whitespace
    assert len(history(case)) == 1
    assert len(case[0].tasks()) == 1


@pytest.mark.parametrize('fields', [dict(status='confirmed'), dict(next_step='Changed text only'), {}])
def test_no_semantic_metadata_change_appends_no_row(case, fields):
    task, _ = create(case)
    update(case, task, **fields)
    assert len(history(case)) == 1


def test_untracked_revision_gaps_from_text_edits_do_not_fabricate_a_history_gap(case):
    task, _ = create(case)
    update(case, task, next_step='New next text', event='text-only')
    update(case, task, status='blocked', event='block')
    assert [r['revision'] for r in history(case)] == [1, 3]
    assert history(case)[1]['history_quality'] == 'from_creation'


def test_actual_multi_action_transaction_rolls_back_first_history_when_later_action_rejected(case):
    with pytest.raises(ValueError):
        create(case, extra_actions=({'op': 'INVALID'},))
    assert history(case) == [] and case[0].tasks() == []


def test_engine_ledger_failure_rolls_back_create(case, monkeypatch):
    fail_after_append(monkeypatch, engine_module)
    with pytest.raises(HistoryError):
        create(case)
    assert history(case) == [] and case[0].tasks() == []
    assert case[1].rows('SELECT * FROM task_sources') == []


def test_engine_ledger_failure_rolls_back_update(case, monkeypatch):
    task, _ = create(case)
    fail_after_append(monkeypatch, engine_module)
    with pytest.raises(HistoryError):
        update(case, task, status='completed')
    row = case[0].tasks()[0]
    assert row['status'] == 'confirmed' and row['revision'] == 1 and len(history(case)) == 1


def test_employee_cannot_mutate_foreign_task_and_no_ledger_is_added(case):
    task, _ = create(case)
    update(case, task, sender='bob', status='completed')
    assert len(history(case)) == 1 and case[0].tasks()[0]['status'] == 'confirmed'


def test_fresh_engine_before_image_and_out_of_band_gap_are_explicit(case):
    engine, store, _, _ = case
    task, _ = create(case)
    text = 'Concrete synthetic work update registered.'
    class ConcurrentSyntheticModel:
        def propose(self, context):
            assert context['tasks'][0]['status'] == 'confirmed'
            store.execute("UPDATE tasks SET status='blocked',revision=revision+1 WHERE id=?", (task['id'],))
            return {'actions': [dict(op='update', task_id=task['id'], status='in_progress', evidence=text)], 'summary': 'Synthetic'}
    engine.model = ConcurrentSyntheticModel()
    engine.reply('concurrent-event', 'admin', text, task_id=task['id'])
    engine.process('concurrent-event')
    second = history(case)[1]
    assert second['before_status'] == 'blocked' and second['status'] == 'in_progress'
    assert second['before_revision'] == 2 and second['revision'] == 3
    assert second['history_quality'] == 'gap'
    update(case, task, status='completed', event='after-gap')
    assert history(case)[-1]['history_quality'] == 'gap'


def test_preexisting_completed_first_observation_is_not_a_completion(case):
    task = raw_task(case)
    assert history(case) == []
    update(case, task, owner='bob')
    row = history(case)[0]
    assert row['task_origin'] == 'existing' and row['history_quality'] == 'unknown_prior'
    assert row['before_status'] == row['status'] == 'completed'
    assert row['is_completion_transition'] == 0 and row['observed_at'] == WHEN


def test_preexisting_unfinished_to_completed_is_a_registered_transition_with_unknown_prior(case):
    task = raw_task(case, status='in_progress')
    update(case, task, status='completed')
    row = history(case)[0]
    assert row['is_completion_transition'] == 1 and row['history_quality'] == 'unknown_prior'
    assert row['observed_at'] == WHEN  # not old task.created=1 or inferred result time


def test_sheet_actual_manual_change_is_recorded_and_no_change_is_skipped(case):
    task, _ = create(case)
    sheets = Sheets(case[0], None)
    sheets._manual(task, {})
    sheets._manual(task, {'status': 'confirmed'})
    assert len(history(case)) == 1
    sheets._manual(task, {'deadline': '2026-10-22'})
    row = history(case)[1]
    assert row['source_kind'] == 'sheet' and row['deadline'] == '2026-10-22'
    assert row['before_status'] == row['status'] == 'confirmed'


def test_sheet_completion_is_observed_only_after_existing_result_guard(case):
    task, _ = create(case)
    sheets = Sheets(case[0], None)
    with pytest.raises(ValueError):
        sheets._manual(task, {'status': 'completed'})
    assert len(history(case)) == 1
    sheets._manual(task, {'status': 'completed', 'evidence': 'Concrete synthetic finished result'})
    assert history(case)[1]['is_completion_transition'] == 1


def test_sheet_fresh_before_not_stale_sheet_row_and_atomic_failure(case, monkeypatch):
    task, _ = create(case)
    case[1].execute("UPDATE tasks SET status='blocked',revision=revision+1")
    sheets = Sheets(case[0], None)
    sheets._manual(task, {'deadline': '2026-10-22'})
    assert history(case)[1]['before_status'] == 'blocked' and history(case)[1]['history_quality'] == 'gap'
    fail_after_append(monkeypatch, sheets_module)
    with pytest.raises(HistoryError):
        sheets._manual(task, {'status': 'in_progress'})
    assert case[0].tasks()[0]['status'] == 'blocked' and len(history(case)) == 2


def test_actual_import_is_unknown_prior_baseline_never_historical_completion(case):
    result = import_tasks(case[0], legacy_rows(), 'PRIVATE-EXPORT-PATH', apply=True)
    assert result['added'] == 1
    row = history(case)[0]
    assert row['source_kind'] == row['task_origin'] == 'import'
    assert row['history_quality'] == 'unknown_prior' and row['is_completion_transition'] == 0
    assert row['status'] == 'completed' and row['before_status'] is None
    assert row['observed_at'] == WHEN and 'PRIVATE' not in json.dumps(row)
    assert import_tasks(case[0], legacy_rows(), 'PRIVATE-EXPORT-PATH', apply=True)['skipped'] == 1
    assert len(history(case)) == 1


def test_import_preview_and_import_atomic_failure(case, monkeypatch):
    import_tasks(case[0], legacy_rows(), 'PRIVATE-EXPORT-PATH')
    assert history(case) == [] and case[0].tasks() == []
    fail_after_append(monkeypatch, importer_module)
    with pytest.raises(HistoryError):
        import_tasks(case[0], legacy_rows(), 'PRIVATE-EXPORT-PATH', apply=True)
    assert history(case) == [] and case[0].tasks() == []
    assert case[1].rows('SELECT * FROM sources') == []


def test_actual_recurring_clone_uses_own_record_and_duplicate_is_skipped(case):
    engine, store, _, _ = case
    template = recurring_setup(case)
    with store.transaction():
        task_id = engine._spawn_recurring(template['id'], 'synthetic-series:slot', WHEN+3600)
    rows = history(case)
    spawned = next(r for r in rows if r['task_ref'] == work_id('GOLDWAY', task_id))
    assert spawned['task_origin'] == 'recurring' and spawned['source_kind'] == 'recurring'
    assert spawned['history_quality'] == 'from_creation' and spawned['is_completion_transition'] == 0
    with store.transaction():
        assert engine._spawn_recurring(template['id'], 'synthetic-series:slot', WHEN+3600) == task_id
    assert len(history(case)) == 2


def test_recurring_ledger_failure_rolls_back_clone(case, monkeypatch):
    engine, store, _, _ = case
    template = recurring_setup(case)
    fail_after_append(monkeypatch, engine_module)
    with pytest.raises(HistoryError):
        with store.transaction():
            engine._spawn_recurring(template['id'], 'synthetic-series:slot', WHEN+3600)
    assert len(history(case)) == 1 and len(engine.tasks()) == 1


def test_cutoff_is_prospective_immutable_and_survives_engine_reconstruction(case):
    engine, store, config, clock = case
    raw_task(case)
    clock[0] += 86400
    Engine(store, config, None, lambda: clock[0])
    cutoff = store.rows('SELECT * FROM task_history_observation')
    assert cutoff == [{'business': 'GOLDWAY', 'schema_version': 1, 'started_at': WHEN, 'timezone_name': 'Europe/Moscow'}]
    assert history(case) == []
    assert len(engine.tasks()) == 1


def test_same_revision_exact_duplicate_is_idempotent_but_conflict_rejected(case):
    _, store, _, _ = case
    task = raw_task(case, status='confirmed')
    with store.transaction():
        before = snapshot(store, 'GOLDWAY', task['id'])
        store.execute("UPDATE tasks SET status='blocked',revision=revision+1")
        assert append_transition(store, 'GOLDWAY', task['id'], before, at=WHEN, source='sheet')
        assert not append_transition(store, 'GOLDWAY', task['id'], before, at=WHEN, source='sheet')
        with pytest.raises(HistoryError):
            append_transition(store, 'GOLDWAY', task['id'], before, at=WHEN+1, source='sheet')
    assert len(history(case)) == 1


@pytest.mark.parametrize('operation', ['update_history', 'delete_history', 'update_cutoff', 'delete_cutoff'])
def test_sql_append_only_tables_reject_rewrite_and_delete(case, operation):
    create(case)
    sql = {'update_history': "UPDATE task_status_history SET status='completed'",
           'delete_history': 'DELETE FROM task_status_history',
           'update_cutoff': 'UPDATE task_history_observation SET started_at=0',
           'delete_cutoff': 'DELETE FROM task_history_observation'}[operation]
    with pytest.raises(sqlite3.IntegrityError, match='task_history_append_only'):
        with case[1].transaction():
            case[1].execute(sql)
    assert len(history(case)) == 1


@pytest.mark.parametrize('old_deadline', ['SYNTHETIC-PRIVATE-KEY', 'SYNTHETIC-PRIVATE-KEY'*10000, '20261019', None])
def test_bad_old_deadline_is_unknown_or_missing_without_unsafe_plaintext(case, old_deadline):
    task = raw_task(case, status='confirmed', deadline=old_deadline)
    Sheets(case[0], None)._manual(task, {'deadline': '2026-10-22'})
    row = history(case)[0]
    assert row['before_deadline'] is None and row['deadline'] == '2026-10-22'
    assert row['before_deadline_state'] == ('missing' if old_deadline is None else 'unknown')
    if old_deadline is not None and len(old_deadline) <= 64:
        assert len(row['before_deadline_hash']) == 64
    assert 'PRIVATE' not in json.dumps(row) and '20261019' not in json.dumps(row)


def test_clock_regression_cannot_create_fake_chronology_and_update_rolls_back(case):
    task, _ = create(case)
    case[3][0] += 100
    update(case, task, status='blocked')
    case[3][0] -= 1
    with pytest.raises(HistoryError):
        update(case, task, status='completed', event='clock-regression')
    assert len(history(case)) == 2 and case[0].tasks()[0]['status'] == 'blocked'


def test_helpers_require_caller_transaction_and_never_commit_it(case):
    task = raw_task(case, status='confirmed')
    with pytest.raises(HistoryError):
        snapshot(case[1], 'GOLDWAY', task['id'])
    with pytest.raises(HistoryError):
        start_observing(case[1], 'OTHER', at=WHEN, timezone_name='UTC')
    with pytest.raises(RuntimeError):
        with case[1].transaction():
            before = snapshot(case[1], 'GOLDWAY', task['id'])
            case[1].execute("UPDATE tasks SET status='blocked',revision=revision+1")
            append_transition(case[1], 'GOLDWAY', task['id'], before, at=WHEN, source='sheet')
            assert case[1].db.in_transaction
            raise RuntimeError('synthetic outer failure')
    assert history(case) == [] and case[0].tasks()[0]['status'] == 'confirmed'


def test_business_namespace_is_separate_and_no_foreign_snapshot_can_be_used(case):
    task = raw_task(case, status='confirmed')
    with case[1].transaction():
        start_observing(case[1], 'OTHER', at=WHEN, timezone_name='UTC')
        assert snapshot(case[1], 'OTHER', task['id']) is None
        with pytest.raises(HistoryError):
            append_transition(case[1], 'OTHER', task['id'], None, at=WHEN, source='import', creation='import')
    assert history(case) == []


def test_fixed_business_timezone_epoch_rejects_changed_calendar_at_startup(case):
    _, store, config, clock = case
    changed = dict(config, timezone='UTC')
    with pytest.raises(HistoryError, match='^task_history_unavailable$'):
        Engine(store, changed, None, lambda: clock[0])
    assert store.rows('SELECT * FROM task_history_observation')[0]['timezone_name'] == 'Europe/Moscow'
    assert history(case) == []


def test_completed_update_event_replay_is_idempotent(case):
    task, _ = create(case)
    update(case, task, status='completed', event='completion-event')
    before = history(case)
    case[0].process('completion-event')
    assert history(case) == before and len(before) == 2
