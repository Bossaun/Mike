"""Finite synthetic checks of recurrence cancellation; no external clients."""
import pytest

from test_team_tasks import team
from test_scheduling_dialogue import scheduled, setup_task, run


def active_occurrence(scheduled):
    engine, store, config, clock, api = scheduled
    template = setup_task(scheduled)
    run(scheduled, 'Напоминай каждый день в 09:00', 801,
        sender='test_operator', task_id=template['id'])
    series = store.rows('SELECT * FROM c07_series')[0]
    clock[0] = series['next_at']
    engine.tick()
    occurrence_id = store.rows('SELECT task_id FROM c07_occurrences')[0]['task_id']
    occurrence = next(task for task in engine.tasks() if task['id'] == occurrence_id)
    return template, series, occurrence


@pytest.mark.parametrize('target', ['template', 'occurrence'])
def test_admin_pauses_series_from_either_task_without_mutating_tasks(scheduled, target):
    engine, store, config, clock, api = scheduled
    template, series, occurrence = active_occurrence(scheduled)
    task = template if target == 'template' else occurrence
    before_tasks = engine.tasks()
    run(scheduled, f'Задача №{task["public_number"]}: отмени повторение', 802,
        sender='test_operator', task_id=task['id'], flush=False)
    assert store.rows('SELECT state FROM c07_series') == [{'state': 'paused'}]
    assert store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text'] == 'Напоминание отменено.'
    assert engine.tasks() == before_tasks
    before = len(store.rows('SELECT * FROM c07_occurrences'))
    clock[0] = store.rows('SELECT next_at FROM c07_series')[0]['next_at']
    engine.tick()
    assert len(store.rows('SELECT * FROM c07_occurrences')) == before


def test_employee_cannot_pause_parent_series_from_owned_occurrence(scheduled):
    engine, store, config, clock, api = scheduled
    _, _, occurrence = active_occurrence(scheduled)
    run(scheduled, f'Задача №{occurrence["public_number"]}: отмени повторение', 803,
        sender='alen', task_id=occurrence['id'], flush=False)
    assert store.rows('SELECT state FROM c07_series') == [{'state': 'active'}]


def test_revoked_current_task_acl_prevents_cancellation(scheduled, monkeypatch):
    engine, store, config, clock, api = scheduled
    _, _, occurrence = active_occurrence(scheduled)
    monkeypatch.setattr(engine, '_scheduling_permitted', lambda *a: False)
    run(scheduled, f'Задача №{occurrence["public_number"]}: отмени повторение', 804,
        sender='test_operator', task_id=occurrence['id'], flush=False)
    assert store.rows('SELECT state FROM c07_series') == [{'state': 'active'}]


def test_foreign_business_mapping_is_not_used(scheduled):
    engine, store, config, clock, api = scheduled
    _, series, occurrence = active_occurrence(scheduled)
    # The same task ID is deliberately present in another business's mapping.
    store.execute('''INSERT INTO c07_series SELECT 'OTHER',id,task_id,actor,owner,
        rule,snapshot,next_at,source_at,created,state,reason FROM c07_series WHERE business=?''', (engine.b,))
    store.execute('''INSERT INTO c07_occurrences SELECT 'OTHER',series_id,occurrence_key,
        task_id,at,created FROM c07_occurrences WHERE business=?''', (engine.b,))
    run(scheduled, f'Задача №{occurrence["public_number"]}: отмени повторение', 805,
        sender='test_operator', task_id=occurrence['id'], flush=False)
    states = {row['business']: row['state'] for row in store.rows('SELECT business,state FROM c07_series')}
    assert states == {'GOLDWAY': 'paused', 'OTHER': 'active'}


def test_old_occurrence_cannot_pause_a_replacement_series(scheduled):
    engine, store, config, clock, api = scheduled
    template, first, occurrence = active_occurrence(scheduled)
    rule = {'frequency': 'daily', 'weekdays': list(range(7)), 'time': '10:00', 'timezone': config['timezone']}
    replacement = engine.scheduler.register_series(template['id'], 'test_operator', rule, 'replacement', clock[0])
    run(scheduled, f'Задача №{occurrence["public_number"]}: отмени повторение', 806,
        sender='test_operator', task_id=occurrence['id'], flush=False)
    states = {row['id']: row['state'] for row in store.rows('SELECT id,state FROM c07_series')}
    assert states[first['id']] == 'paused' and states[replacement['id']] == 'active'


def test_unrelated_active_series_is_preserved(scheduled):
    engine, store, config, clock, api = scheduled
    template, first, occurrence = active_occurrence(scheduled)
    rule = {'frequency': 'daily', 'weekdays': list(range(7)), 'time': '10:00', 'timezone': config['timezone']}
    unrelated = engine.scheduler.register_series(occurrence['id'], 'test_operator', rule, 'unrelated', clock[0])
    # Cancel the template's own series; the occurrence's separately authorized
    # series is not a child-series wildcard target.
    run(scheduled, f'Задача №{template["public_number"]}: отмени повторение', 807,
        sender='test_operator', task_id=template['id'], flush=False)
    states = {row['id']: row['state'] for row in store.rows('SELECT id,state FROM c07_series')}
    assert states[first['id']] == 'paused' and states[unrelated['id']] == 'active'


def test_ambiguous_multiple_series_fails_closed(scheduled):
    engine, store, config, clock, api = scheduled
    _, _, occurrence = active_occurrence(scheduled)
    rule = {'frequency': 'daily', 'weekdays': list(range(7)), 'time': '10:00', 'timezone': config['timezone']}
    engine.scheduler.register_series(occurrence['id'], 'test_operator', rule, 'another', clock[0])
    run(scheduled, f'Задача №{occurrence["public_number"]}: отмени повторение', 808,
        sender='test_operator', task_id=occurrence['id'], flush=False)
    assert all(row['state'] == 'active' for row in store.rows('SELECT state FROM c07_series'))


def test_repeated_cancellation_is_idempotent(scheduled):
    engine, store, config, clock, api = scheduled
    _, _, occurrence = active_occurrence(scheduled)
    text = f'Задача №{occurrence["public_number"]}: отмени повторение'
    event = run(scheduled, text, 809, sender='test_operator', task_id=occurrence['id'], flush=False)
    first = len(store.rows('SELECT * FROM outbox'))
    engine.process(event)
    assert len(store.rows('SELECT * FROM outbox')) == first
    run(scheduled, text, 810, sender='test_operator', task_id=occurrence['id'], flush=False)
    assert store.rows('SELECT state FROM c07_series') == [{'state': 'paused'}]


@pytest.mark.parametrize('command,series_state,check_pending', [
    ('отмени напоминание, повторение оставь', 'active', False),
    ('отмени повторение, напоминание оставь', 'paused', True),
])
def test_cancel_target_does_not_come_from_the_object_to_keep(scheduled, command, series_state, check_pending):
    engine, store, config, clock, api = scheduled
    _, _, occurrence = active_occurrence(scheduled)
    engine.scheduler.schedule_check(occurrence['id'], 'test_operator', clock[0] + 1800,
                                    'independent-check', clock[0])
    run(scheduled, f'Задача №{occurrence["public_number"]}: {command}', 811,
        sender='test_operator', task_id=occurrence['id'], flush=False)
    assert store.rows('SELECT state FROM c07_series') == [{'state': series_state}]
    assert bool(engine.scheduler.pending(occurrence['id'])) is check_pending
    assert store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text'] == 'Напоминание отменено.'
