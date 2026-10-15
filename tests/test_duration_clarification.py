"""Scripted source-cited duration regressions; no live model or Telegram calls."""

import pytest

from goldway.engine import ClarificationRequired, Engine
from goldway.model import ScriptedModel
from test_operator_tasks import result
from test_staff_dates import receive, strict_update, timestamp
from test_team_tasks import team, create, confirm, process


@pytest.fixture
def assigned(team):
    return team, confirm(team, create(team))


@pytest.mark.parametrize('sender', ['test_operator', 'alen'])
@pytest.mark.parametrize('deadline', [None, '2099-01-01'])
def test_short_duration_uses_original_date_and_literal_citation(assigned, sender, deadline):
    context, task = assigned
    engine, store, config, clock, api = context
    clock[0] = timestamp('2026-10-15T00:01:00')
    original = timestamp('2026-10-14T23:59:30')
    engine.model = ScriptedModel(result([
        strict_update(task, ['deadline'], deadline=deadline, deadline_lines=[1]),
    ]))
    event = receive(context, 'Срок 2 дня', 20, original, sender=sender)
    clock[0] = timestamp('2026-10-18T12:00:00')
    engine.process(event)

    changed = engine.tasks()[0]
    assert changed['deadline'] == '2026-10-16'
    for field in ('title', 'owner', 'next_step', 'criterion', 'status'):
        assert changed[field] == task[field]
    assert store.rows('SELECT state, error FROM events WHERE id=?', (event,)) == [
        {'state': 'done', 'error': None},
    ]
    assert store.rows('SELECT body FROM sources WHERE body=?', ('Срок 2 дня',))
    assert not store.rows('SELECT id FROM outbox WHERE dedupe=?', ('error:' + event,))


def test_null_duration_without_citation_cannot_invent_or_clear_a_deadline(assigned):
    context, task = assigned
    engine, store, config, clock, api = context
    process(context, 'Срок 2 дня', 20, result([
        strict_update(task, ['deadline']),
    ]), sender='alen', flush=False)
    assert engine.tasks()[0]['deadline'] == task['deadline']
    answer = store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    assert 'Срок не изменён' in answer


def test_creation_resolves_a_cited_duration_when_model_deadline_is_null(team):
    engine, store, config, clock, api = team
    original = timestamp('2026-10-14T23:59:30')
    clock[0] = timestamp('2026-10-15T00:01:00')
    text = 'Создай задачу: подготовить список. Ответственный я.\nСрок 2 дня'
    engine.model = ScriptedModel(result([{
        'op': 'create', 'task_id': None, 'title': 'Подготовить список',
        'owner': 'test_operator', 'owner_lines': [1], 'deadline': None,
        'deadline_lines': [2], 'next_step': 'Составить список',
        'criterion': 'Список из трёх пунктов', 'status': 'proposed',
        'evidence_lines': [1], 'set_fields': [],
    }]))
    event = receive(team, text, 20, original)
    clock[0] = timestamp('2026-10-18T12:00:00')
    engine.process(event)
    assert len(engine.tasks()) == 1
    task = engine.tasks()[0]
    assert task['deadline'] == '2026-10-16'
    assert task['owner'] == 'test_operator'
    assert task['status'] == 'proposed'


@pytest.mark.parametrize('phrase', [
    'Срок 2 рабочих дня', 'Срок, наверное, 2 дня', 'Срок не 2 дня',
    'Срок 2 дня или 3 дня', 'Срок 2 дня с момента поставки',
    'С даты оплаты срок 2 дня', 'От даты поставки срок 2 дня',
    'Срок 2 дня (с даты поставки)', 'Срок 2 дня. Отсчёт от даты поставки.',
    'Срок 2 дня или 3',
])
def test_qualified_duration_preserves_deadline_and_requests_clarification(assigned, phrase):
    context, task = assigned
    engine, store, config, clock, api = context
    process(context, phrase, 20, result([
        strict_update(task, ['deadline'], deadline_lines=[1]),
    ]), sender='alen', flush=False)
    assert engine.tasks()[0]['deadline'] == task['deadline']
    assert store.rows("SELECT state FROM events WHERE id='update:20'") == [{'state': 'done'}]
    answer = store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    assert 'Срок не изменён' in answer


def test_duration_for_multiple_contextual_tasks_cannot_choose_a_task(assigned):
    context, first = assigned
    engine, store, config, clock, api = context
    process(context, 'Создай задачу: сверить накладные.', 30, result([{
        'op': 'create', 'title': 'Сверить накладные', 'owner': None,
        'owner_lines': [], 'deadline': None, 'deadline_lines': [],
        'next_step': 'Сверить три накладные', 'criterion': 'Накладные сверены',
        'status': 'proposed', 'evidence_lines': [1], 'set_fields': [],
    }]), flush=False)
    previous = {task['id']: task['deadline'] for task in engine.tasks()}
    with pytest.raises(ClarificationRequired, match='несколько задач'):
        process(context, 'Срок 2 дня', 31, result([
            strict_update(first, ['deadline'], deadline_lines=[1]),
        ]), flush=False)
    assert {task['id']: task['deadline'] for task in engine.tasks()} == previous
    notice = store.rows("SELECT text FROM outbox WHERE dedupe='error:update:31'")[0]['text']
    assert 'Уточните номер задачи' in notice


@pytest.mark.parametrize('phrase', ['Срок 2 дня', 'В течение 2 дней'])
def test_duration_without_known_original_or_receipt_date_requires_an_absolute_date(assigned, phrase):
    context, task = assigned
    engine, store, config, clock, api = context
    store.event(engine.b, 'old-duration', 'reply', {
        'sender': 'alen', 'text': phrase, 'task_id': task['id'],
        'source_id': 'old-duration', 'link': 'old-duration', 'channel': 'bot',
    })
    engine.model = ScriptedModel(result([
        strict_update(task, ['deadline'], deadline_lines=[1]),
    ]))
    engine.process('old-duration')
    assert engine.tasks()[0]['deadline'] == task['deadline']
    answer = store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    assert 'Срок не изменён' in answer


@pytest.mark.parametrize('check_state', ['pending', 'fired'])
def test_deadline_only_duration_preserves_a_persistent_check_and_queued_reminder(assigned, check_state):
    context, task = assigned
    old_engine, store, config, clock, api = context
    config['scheduling'] = {'enabled': True}
    engine = Engine(store, config, old_engine.model, lambda: clock[0])
    engine.scheduler.schedule_check(task['id'], 'alen', clock[0] + 60,
                                    'waiting-check', clock[0])
    if check_state == 'fired':
        clock[0] += 60
        engine.scheduler.tick()
    before = store.rows('SELECT * FROM c07_checks')
    assert before[0]['state'] == check_state
    reminders = store.rows("SELECT id, state FROM outbox WHERE dedupe LIKE 'reminder:c07:%'")
    if check_state == 'fired':
        assert len(reminders) == 1 and reminders[0]['state'] == 'pending'

    clock[0] += 1
    engine.model = ScriptedModel(result([
        strict_update(task, ['deadline'], deadline_lines=[1]),
    ]))
    engine.reply('deadline-only', 'alen', 'Срок 2 дня', task['id'],
                 channel='bot', message_at=int(clock[0]))
    assert store.rows('SELECT * FROM c07_checks') == before
    assert store.rows("SELECT id, state FROM outbox WHERE dedupe LIKE 'reminder:c07:%'") == reminders
    engine.process('deadline-only')

    restarted = Engine(store, config, ScriptedModel({}), lambda: clock[0])
    assert store.rows('SELECT * FROM c07_checks') == before
    assert store.rows("SELECT id, state FROM outbox WHERE dedupe LIKE 'reminder:c07:%'") == reminders
    assert bool(restarted.scheduler.pending(task['id'])) == (check_state == 'pending')
    assert restarted.tasks()[0]['deadline'] == '2026-10-16'
