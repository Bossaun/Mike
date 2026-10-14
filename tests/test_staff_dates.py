"""Synthetic source-date and nullable deadline integration regressions."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from goldway.model import ScriptedModel
from goldway.telegram import Updates
from test_operator_tasks import result
from test_team_tasks import team, create, confirm, process, update


MOSCOW = ZoneInfo('Europe/Moscow')


def timestamp(local_time):
    return int(datetime.fromisoformat(local_time).replace(tzinfo=MOSCOW).timestamp())


class ObservedModel(ScriptedModel):
    def __init__(self, proposal):
        super().__init__(proposal)
        self.contexts = []

    def propose(self, context):
        self.contexts.append(dict(context))
        return super().propose(context)


def receive(team, text, event_id, message_at, sender='test_operator', channel='bot'):
    engine, store, config, clock, api = team
    uid = config['employees'][sender]['telegram_user_id']
    message = {'message_id': event_id, 'date': message_at,
               'chat': {'id': uid, 'type': 'private'},
               'from': {'id': uid, 'is_bot': False}, 'text': text}
    if channel == 'business':
        message['business_connection_id'] = 'synthetic-connection'
    Updates(engine, api, 900).handle({
        'update_id': event_id,
        'business_message' if channel == 'business' else 'message': message,
    })
    return 'update:' + str(event_id)


def payload(store, event_id):
    return json.loads(store.rows('SELECT payload FROM events WHERE id=?', (event_id,))[0]['payload'])


def strict_update(task, set_fields, **fields):
    action = update(task, title=None, owner=None, deadline=None, next_step=None,
                    criterion=None, status=None, owner_lines=[], deadline_lines=[],
                    set_fields=set_fields)
    action.update(fields)
    return action


@pytest.fixture
def assigned(team):
    return team, confirm(team, create(team))


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_delayed_employee_receipt_and_processing_use_original_moscow_date(assigned, channel):
    team, task = assigned
    engine, store, config, clock, api = team
    original = timestamp('2026-10-14T23:59:30')
    received = timestamp('2026-10-15T00:01:00')
    clock[0] = received
    text = 'Задача №1: новый срок послезавтра.'
    model = ObservedModel(result([
        strict_update(task, ['deadline'], deadline_lines=[1]),
    ]))
    engine.model = model
    event = receive(team, text, 20, original, sender='alen', channel=channel)
    assert payload(store, event)['message_at'] == original
    assert payload(store, event)['received_at'] == received
    assert engine.tasks()[0]['last_reply'] == received

    clock[0] = timestamp('2026-10-17T12:00:00')
    engine.process(event)
    assert engine.tasks()[0]['deadline'] == '2026-10-16'
    assert engine.tasks()[0]['last_reply'] == received
    assert model.contexts[0]['date'] == '2026-10-14T23:59:30+03:00'
    assert model.contexts[0]['sender_role'] == 'employee'
    assert model.contexts[0]['can_create_tasks'] is False


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_delayed_admin_creation_uses_original_local_date_and_cited_line(team, channel):
    engine, store, config, clock, api = team
    original = timestamp('2026-10-15T00:01:00')
    clock[0] = timestamp('2026-10-16T12:00:00')
    text = 'Создай задачу: подготовить список. Ответственный я.\nСрок сегодня.'
    action = {'op': 'create', 'task_id': None, 'title': 'Подготовить список',
              'owner': 'test_operator', 'owner_lines': [1], 'deadline': None,
              'deadline_lines': [2], 'next_step': 'Составить список',
              'criterion': 'Список из трёх пунктов', 'status': 'proposed',
              'evidence_lines': [1], 'set_fields': []}
    model = ObservedModel(result([action]))
    engine.model = model
    event = receive(team, text, 20, original, channel=channel)
    clock[0] = timestamp('2026-10-18T12:00:00')
    engine.process(event)
    assert engine.tasks()[0]['deadline'] == '2026-10-15'
    assert engine.tasks()[0]['owner'] == 'test_operator'
    assert model.contexts[0]['date'] == '2026-10-15T00:01:00+03:00'
    assert payload(store, event)['received_at'] == timestamp('2026-10-16T12:00:00')


@pytest.mark.parametrize('source_time', [1791979200, 1791979200.5])
def test_reply_accepts_numeric_source_time_and_persists_it(team, source_time):
    engine, store, config, clock, api = team
    clock[0] += 86400
    model = ObservedModel(result(intent='conversation', reply='Обсудим подготовку.'))
    engine.model = model
    assert engine.reply('source', 'alen', 'Обсудим подготовку встречи.',
                        channel='bot', message_at=source_time)
    saved = payload(store, 'source')
    assert saved['message_at'] == source_time
    assert saved['received_at'] == clock[0]
    engine.process('source')
    assert model.contexts[0]['date'] == datetime.fromtimestamp(source_time, MOSCOW).isoformat()


@pytest.mark.parametrize('source_time', [True, False, '1791979200', 0, -1,
                                         float('nan'), float('inf'), float('-inf')])
def test_reply_rejects_invalid_source_time_before_event_is_created(team, source_time):
    engine, store, config, clock, api = team
    with pytest.raises(ValueError, match='original message timestamp'):
        engine.reply('bad-time', 'alen', 'Обсудим подготовку встречи.',
                     channel='bot', message_at=source_time)
    assert not store.rows('SELECT id FROM events')
    assert not store.rows('SELECT id FROM outbox')


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_future_telegram_source_time_does_not_create_an_event(team, channel):
    engine, store, config, clock, api = team
    receive(team, 'Обсудим подготовку встречи.', 20, int(clock[0]) + 1,
            sender='alen', channel=channel)
    assert not store.rows('SELECT id FROM events')
    assert not store.rows('SELECT id FROM outbox')


def test_reply_rejects_source_time_after_receipt(team):
    engine, store, config, clock, api = team
    with pytest.raises(ValueError, match='original message timestamp'):
        engine.reply('future-time', 'alen', 'Обсудим подготовку встречи.',
                     channel='bot', message_at=clock[0] + 1)
    assert not store.rows('SELECT id FROM events')


def test_reply_without_source_timestamp_uses_receipt_date_after_processing_delay(assigned):
    team, task = assigned
    engine, store, config, clock, api = team
    clock[0] = timestamp('2026-10-14T23:59:30')
    model = ObservedModel(result([strict_update(task, ['deadline'], deadline_lines=[1])]))
    engine.model = model
    engine.reply('legacy-reply', 'alen', 'Задача №1: новый срок сегодня.', channel='bot')
    assert 'message_at' not in payload(store, 'legacy-reply')
    clock[0] = timestamp('2026-10-16T12:00:00')
    engine.process('legacy-reply')
    assert engine.tasks()[0]['deadline'] == '2026-10-14'
    assert engine.tasks()[0]['last_reply'] == timestamp('2026-10-14T23:59:30')
    assert model.contexts[0]['date'] == '2026-10-14T23:59:30+03:00'


def test_old_payload_without_either_timestamp_requires_an_absolute_date(assigned):
    team, task = assigned
    engine, store, config, clock, api = team
    text = 'Задача №1: новый срок сегодня.'
    store.event(engine.b, 'old-payload', 'reply', {
        'sender': 'alen', 'text': text, 'task_id': task['id'],
        'source_id': 'old-payload', 'link': 'old-payload', 'channel': 'bot',
    })
    model = ObservedModel(result([strict_update(task, ['deadline'], deadline_lines=[1])]))
    engine.model = model
    clock[0] = timestamp('2026-10-18T12:00:00')
    engine.process('old-payload')
    assert engine.tasks()[0]['deadline'] == task['deadline']
    assert engine.tasks()[0]['last_reply'] == clock[0]
    assert model.contexts[0]['date'] == '2026-10-18T12:00:00+03:00'
    assert model.contexts[0]['date_reference_known'] is False
    assert 'Срок не изменён' in store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    assert store.rows("SELECT state FROM events WHERE id='old-payload'") == [{'state': 'done'}]


@pytest.mark.parametrize('sender', ['test_operator', 'alen'])
@pytest.mark.parametrize('phrase, expected', [
    ('Срок завтра.', '2026-10-16'),
    ('Срок 20.10.2026.', '2026-10-20'),
])
def test_nullable_mask_resolves_only_the_cited_supported_deadline(assigned, sender, phrase, expected):
    team, task = assigned
    engine, store, config, clock, api = team
    clock[0] = timestamp('2026-10-15T10:00:00')
    text = 'Задача №1: уточняю срок.\n' + phrase + '\nПредыдущий срок был 2026-10-22.'
    process(team, text, 20, result([
        strict_update(task, ['deadline'], deadline_lines=[2]),
    ]), sender=sender)
    changed = engine.tasks()[0]
    assert changed['deadline'] == expected
    for field in ('title', 'owner', 'next_step', 'criterion', 'status'):
        assert changed[field] == task[field]


@pytest.mark.parametrize('sender', ['test_operator', 'alen'])
@pytest.mark.parametrize('phrase, deadline_lines', [
    ('Новый срок пока не знаю.', []),
    ('Срок, наверное, завтра.', [2]),
    ('Срок завтра или послезавтра.', [2]),
])
def test_nullable_uncertain_deadline_preserves_previous_fact_and_asks(assigned, sender, phrase, deadline_lines):
    team, task = assigned
    engine, store, config, clock, api = team
    text = 'Задача №1: уточняю срок.\n' + phrase
    process(team, text, 20, result([
        strict_update(task, ['deadline'], deadline_lines=deadline_lines),
    ]), sender=sender)
    changed = engine.tasks()[0]
    assert changed['deadline'] == task['deadline']
    assert changed['status'] == task['status']
    answer = store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    assert 'Срок не изменён' in answer
    assert 'уточните' in answer.casefold()


@pytest.mark.parametrize('sender', ['test_operator', 'alen'])
def test_strict_schema_null_placeholders_do_not_clear_an_unchanged_deadline(assigned, sender):
    team, task = assigned
    engine, store, config, clock, api = team
    text = 'Задача №1: следующий шаг — сверить три пункта.'
    process(team, text, 20, result([
        strict_update(task, ['next_step'], next_step='Сверить три пункта'),
    ]), sender=sender)
    changed = engine.tasks()[0]
    assert changed['next_step'] == 'Сверить три пункта'
    for field in ('title', 'owner', 'deadline', 'criterion', 'status'):
        assert changed[field] == task[field]
    assert 'Срок не изменён' not in store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']


def test_deadline_citation_outside_update_mask_cannot_change_the_deadline(assigned):
    team, task = assigned
    engine, store, config, clock, api = team
    text = 'Задача №1: следующий шаг — сверить три пункта.\nСрок 2026-10-22.'
    process(team, text, 20, result([
        strict_update(task, ['next_step'], next_step='Сверить три пункта',
                      deadline='2026-10-22', deadline_lines=[2]),
    ]), sender='alen')
    assert engine.tasks()[0]['deadline'] == task['deadline']
    assert engine.tasks()[0]['next_step'] == 'Сверить три пункта'


@pytest.mark.parametrize('sender', ['test_operator', 'alen'])
def test_explicit_grounded_clear_can_remove_a_deadline(assigned, sender):
    team, task = assigned
    process(team, 'Задача №1: сними прежний срок.', 20,
            result([strict_update(task, ['deadline'])]), sender=sender)
    assert team[0].tasks()[0]['deadline'] is None


def test_clear_phrase_in_an_uncited_line_does_not_erase_the_deadline(assigned):
    team, task = assigned
    text = 'Задача №1: новый срок пока не знаю.\nСними прежний срок.'
    process(team, text, 20, result([strict_update(task, ['deadline'])]), sender='alen')
    assert team[0].tasks()[0]['deadline'] == task['deadline']


def test_negated_clear_is_not_an_explicit_deadline_removal(assigned):
    team, task = assigned
    process(team, 'Задача №1: только не удали старый срок, новый пока не знаю.', 20,
            result([strict_update(task, ['deadline'])]), sender='alen')
    assert team[0].tasks()[0]['deadline'] == task['deadline']
