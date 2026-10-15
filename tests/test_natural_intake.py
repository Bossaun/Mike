"""Natural team proposals, administrator review, and synthetic Telegram routes."""

import copy
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from goldway.model import ScriptedModel, response_schema
from goldway.telegram import Dispatcher, Updates
from test_operator_tasks import result
from test_team_tasks import team, inbound, create, confirm, update


class ObservedModel(ScriptedModel):
    def __init__(self, proposal):
        super().__init__(proposal)
        self.contexts = []

    def propose(self, context):
        self.contexts.append(copy.deepcopy(context))
        return super().propose(context)


def proposal(title, owner=None, owner_lines=None, deadline_lines=None,
             next_step='Подготовить три пункта', criterion='Три пункта сохранены'):
    return {'op': 'create', 'task_id': None, 'title': title, 'owner': owner,
            'owner_lines': owner_lines or [], 'deadline': None,
            'deadline_lines': deadline_lines or [], 'next_step': next_step,
            'criterion': criterion, 'status': 'proposed', 'evidence_lines': [1],
            'set_fields': []}


def start(team, text, event_id, value, sender='test_operator', channel='bot', message_at=None):
    engine, store, config, clock, api = team
    model = ObservedModel(value)
    engine.model = model
    if message_at is None:
        event = inbound(team, text, event_id, sender=sender, channel=channel)
    else:
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
        event = 'update:' + str(event_id)
    return event, model


def deliver(team, text, event_id, value, sender='test_operator', channel='bot', message_at=None,
            expected_model_calls=1):
    engine, store, config, clock, api = team
    event, model = start(team, text, event_id, value, sender, channel, message_at)
    engine.process(event)
    assert len(model.contexts) == expected_model_calls
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    return event, model


def rows_after(store, last_id):
    return store.rows('SELECT * FROM outbox WHERE id>? ORDER BY id', (last_id,))


def last_outbox_id(store):
    return store.execute('SELECT COALESCE(MAX(id),0) FROM outbox').fetchone()[0]


def assert_proposal_context(model, sender):
    context = model.contexts[0]
    assert context['kind'] == 'operator_chat'
    assert context['can_submit_proposals'] is True
    assert context['can_create_tasks'] is (sender == 'test_operator')
    assert context['sender_role'] == ('admin' if sender == 'test_operator' else 'employee')


def test_model_schema_allows_natural_idea_and_meeting_classification():
    schema = response_schema({'kind': 'operator_chat', 'employees': {'alen': 'Алэн'},
                              'can_create_tasks': False, 'can_submit_proposals': True})
    assert set(schema['properties']['intent']['enum']) == {'conversation', 'task_action', 'idea', 'meeting'}


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_short_admin_meeting_uses_one_model_call_and_original_message_date(team, channel):
    engine, store, config, clock, api = team
    text = ('На встрече договорились: Алэн подготовит список, Фрост сверит товары.\n'
            'Оба результата нужны завтра.')
    assert len(text) < 400 and not text.startswith('/')
    actions = [proposal('Подготовить список', 'alen', [1], [2]),
               proposal('Сверить товары', 'frost', [1], [2])]
    value = result(actions, intent='meeting', reply='Подготовлены черновики итогов встречи.')
    original = int(datetime(2026, 10, 14, 23, 59, tzinfo=ZoneInfo('Europe/Moscow')).timestamp())
    clock[0] = original + 120
    event, model = start(team, text, 20, value, channel=channel, message_at=original)
    clock[0] += 12 * 3600
    engine.process(event)
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    assert len(model.contexts) == 1
    assert_proposal_context(model, 'test_operator')
    assert model.contexts[0]['date'] == '2026-10-14T23:59:00+03:00'
    assert store.rows('SELECT kind,state FROM events WHERE id=?', (event,)) == [{'kind': 'reply', 'state': 'done'}]
    tasks = engine.tasks()
    assert {task['title'] for task in tasks} == {'Подготовить список', 'Сверить товары'}
    assert {task['status'] for task in tasks} == {'proposed'}
    assert {task['deadline'] for task in tasks} == {'2026-10-15'}
    reviews = store.rows('SELECT * FROM outbox')
    assert reviews and {row['recipient'] for row in reviews} == {'test_operator'}
    assert all(row['transport'] == channel for row in reviews)
    review_text = '\n'.join(row['text'] for row in reviews)
    assert all(task['title'] in review_text for task in tasks)
    assert 'подтвержд' in review_text.casefold()
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")

    engine.process(event)
    assert len(model.contexts) == 1
    count = len(reviews)
    deliver(team, text, 21, value, channel=channel, expected_model_calls=0)
    assert len(engine.tasks()) == 2
    assert len(store.rows('SELECT id FROM outbox')) == count


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_employee_idea_review_confirmation_and_concrete_result(team, channel):
    engine, store, config, clock, api = team
    text = ('У меня идея: создать памятку для новых сотрудников.\n'
            'Я возьму на себя подготовку.\nСрок завтра.')
    action = proposal('Создать памятку для новых сотрудников', 'alen', [2], [3],
                      next_step='Составить памятку из трёх пунктов',
                      criterion='Памятка из трёх пунктов сохранена')
    value = result([action], intent='idea', reply='Передам идею руководителю на рассмотрение.')
    event, model = deliver(team, text, 20, value, sender='alen', channel=channel)
    assert_proposal_context(model, 'alen')
    task = engine.tasks()[0]
    assert task['status'] == 'proposed' and task['owner'] == 'alen'
    assert task['deadline'] == '2026-10-15'
    reviews = store.rows('SELECT * FROM outbox')
    assert {row['recipient'] for row in reviews} == {'alen', 'test_operator'}
    assert all(row['transport'] == channel for row in reviews if row['recipient'] == 'alen')
    admin_review = '\n'.join(row['text'] for row in reviews if row['recipient'] == 'test_operator')
    assert task['title'] in admin_review
    assert re.search(r'\bподтвер(?:д|жд)', admin_review.casefold())
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")

    count = len(reviews)
    deliver(team, text, 21, value, sender='alen', channel=channel, expected_model_calls=0)
    assert len(engine.tasks()) == 1
    assert len(store.rows('SELECT id FROM outbox')) == count

    deliver(team, 'Задача №1: подтверждаю поручение.', 22,
            result([update(task, status='confirmed')]), channel=channel)
    task = engine.tasks()[0]
    assert task['status'] == 'confirmed'
    assignment = store.rows("SELECT * FROM outbox WHERE dedupe LIKE 'assignment:%'")
    assert len(assignment) == 1 and assignment[0]['recipient'] == 'alen'
    assert assignment[0]['state'] == 'sent' and task['title'] in assignment[0]['text']

    last_id = last_outbox_id(store)
    deliver(team, 'Задача №1: памятка из трёх пунктов подготовлена и сохранена в TEST.txt.', 23,
            result([update(task, status='completed')], reply='Результат зафиксирован.'),
            sender='alen', channel=channel)
    assert engine.tasks()[0]['status'] == 'completed'
    attention = [row for row in rows_after(store, last_id) if row['recipient'] == 'test_operator']
    assert len(attention) == 1 and 'TEST.txt' in attention[0]['text']
    assert all(row['recipient'] in {'alen', 'test_operator'} for row in rows_after(store, last_id))


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_employee_meeting_creates_only_proposals_and_admin_review(team, channel):
    engine, store, config, clock, api = team
    text = ('Записала встречу: Алэн подготовит памятку, Фрост сверит чек-лист.\n'
            'Срок обоих пунктов завтра.')
    value = result([proposal('Подготовить памятку', 'alen', [1], [2]),
                    proposal('Сверить чек-лист', 'frost', [1], [2])],
                   intent='meeting', reply='Итоги переданы руководителю для подтверждения.')
    event, model = deliver(team, text, 20, value, sender='nastya', channel=channel)
    assert_proposal_context(model, 'nastya')
    assert len(engine.tasks()) == 2
    assert {task['status'] for task in engine.tasks()} == {'proposed'}
    assert {row['recipient'] for row in store.rows('SELECT recipient FROM outbox')} == {'nastya', 'test_operator'}
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")
    admin_review = '\n'.join(row['text'] for row in store.rows("SELECT text FROM outbox WHERE recipient='test_operator'"))
    assert all(task['title'] in admin_review for task in engine.tasks())
    assert 'подтвержд' in admin_review.casefold()
    count = len(store.rows('SELECT id FROM outbox'))
    deliver(team, text, 21, value, sender='nastya', channel=channel, expected_model_calls=0)
    assert len(engine.tasks()) == 2
    assert len(store.rows('SELECT id FROM outbox')) == count


@pytest.mark.parametrize('sender', ['test_operator', 'alen'])
@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_long_general_request_receives_actual_answer_without_task_mutation(team, sender, channel):
    engine, store, config, clock, api = team
    task = confirm(team, create(team))
    clock[0] += 3600
    last_id = last_outbox_id(store)
    text = ('Помоги составить приглашение на рабочую встречу. '
            'Хочу понятно описать тему, формат и ожидаемые вопросы, чтобы люди могли подготовиться. ' * 5)
    assert len(text) > 400
    answer = 'Приглашаем вас на встречу: обсудим подготовку памятки и вопросы новых сотрудников.'
    value = result(intent='conversation', reply=answer)
    value['summary'] = 'Сотрудник попросил подготовить приглашение на встречу.'
    event, model = deliver(team, text, 20, value, sender=sender, channel=channel)
    assert engine.tasks() == [task]
    replies = rows_after(store, last_id)
    assert len(replies) == 1 and replies[0]['recipient'] == sender
    assert replies[0]['text'] == answer and replies[0]['transport'] == channel
    assert store.rows('SELECT kind FROM events WHERE id=?', (event,)) == [{'kind': 'reply'}]
    assert model.contexts[0]['kind'] == 'operator_chat'


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_unknown_sender_cannot_submit_ideas_or_reach_model(team, channel):
    engine, store, config, clock, api = team
    model = ObservedModel(result([proposal('Чужая идея')], intent='idea'))
    engine.model = model
    message = {'message_id': 20, 'date': int(clock[0]),
               'chat': {'id': 999999, 'type': 'private'},
               'from': {'id': 999999, 'is_bot': False}, 'text': 'У меня идея: новое поручение.'}
    if channel == 'business':
        message['business_connection_id'] = 'synthetic-connection'
    Updates(engine, api, 900).handle({'update_id': 20,
        'business_message' if channel == 'business' else 'message': message})
    assert not model.contexts
    assert not engine.tasks() and not store.rows('SELECT id FROM events')
    assert not store.rows('SELECT id FROM outbox') and not api.calls
    assert engine.reply('unknown', 'unknown', 'У меня идея: новое поручение.', channel=channel) is False


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_employee_task_action_cannot_create_a_confirmed_assignment(team, channel):
    engine, store, config, clock, api = team
    action = proposal('Самостоятельное поручение', 'alen', [1])
    action['status'] = 'confirmed'
    text = 'Создай задачу: самостоятельное поручение. Ответственный я.'
    event, model = start(team, text, 20, result([action], intent='task_action'),
                         sender='alen', channel=channel)
    with pytest.raises(ValueError):
        engine.process(event)
    assert len(model.contexts) == 1
    assert not engine.tasks()
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")
    assert {row['recipient'] for row in store.rows('SELECT recipient FROM outbox')} <= {'alen'}


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_explicit_admin_task_action_still_requires_review_before_assignment(team, channel):
    engine, store, config, clock, api = team
    action = proposal('Подготовить инструкцию', 'alen', [1])
    action['status'] = 'confirmed'
    event, model = deliver(team, 'Создай задачу: подготовить инструкцию. Ответственный Алэн.', 20,
                           result([action]), channel=channel)
    assert len(engine.tasks()) == 1 and engine.tasks()[0]['status'] == 'proposed'
    assert {row['recipient'] for row in store.rows('SELECT recipient FROM outbox')} == {'test_operator'}
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_foreign_title_dedupe_does_not_expose_another_employees_task(team, channel):
    engine, store, config, clock, api = team
    admin_text = ('Создай задачу: обсудить поставщика. Ответственный Алэн.\n'
                  'Следующий шаг PRIVATE-C04-NEXT, готово когда PRIVATE-C04-CRITERION.\n'
                  'Срок 2026-10-20.')
    foreign_action = proposal('Обсудить поставщика', 'alen', [1], [3],
                             next_step='PRIVATE-C04-NEXT', criterion='PRIVATE-C04-CRITERION')
    deliver(team, admin_text, 10, result([foreign_action]))
    foreign = confirm(team, engine.tasks()[0])
    previous_sources = store.rows('SELECT * FROM task_sources WHERE task_id=?', (foreign['id'],))
    last_id = last_outbox_id(store)
    text = 'У меня идея: обсудить поставщика. Я возьму на себя подготовку вопросов.'
    value = result([proposal('Обсудить поставщика', 'frost', [1],
                             next_step='Подготовить вопросы', criterion='Список вопросов')], intent='idea')
    event, model = deliver(team, text, 20, value, sender='frost', channel=channel)
    assert_proposal_context(model, 'frost')
    assert model.contexts[0]['tasks'] == []
    context_text = json.dumps(model.contexts[0], ensure_ascii=False)
    for secret in (foreign['id'], foreign['next_step'], foreign['criterion'], foreign['deadline']):
        assert secret not in context_text
    assert next(task for task in engine.tasks() if task['id'] == foreign['id']) == foreign
    new_rows = rows_after(store, last_id)
    assert all(row['recipient'] in {'frost', 'test_operator'} for row in new_rows)
    assert all(row['task_id'] is None for row in new_rows if row['recipient'] == 'frost')
    staff_text = '\n'.join(row['text'] for row in new_rows if row['recipient'] == 'frost')
    assert staff_text
    for secret in (foreign['id'], foreign['next_step'], foreign['criterion'], foreign['deadline'], 'Алэн'):
        assert secret not in staff_text
    assert not re.search(r'(?:№|#)\s*' + str(foreign['public_number']) + r'\b', staff_text)
    assert all(task['status'] == 'proposed' for task in engine.tasks() if task['id'] != foreign['id'])
    assert store.rows('SELECT * FROM task_sources WHERE task_id=?', (foreign['id'],)) == previous_sources
    count = len(store.rows('SELECT id FROM outbox'))
    deliver(team, text, 21, value, sender='frost', channel=channel, expected_model_calls=0)
    assert next(task for task in engine.tasks() if task['id'] == foreign['id']) == foreign
    assert store.rows('SELECT * FROM task_sources WHERE task_id=?', (foreign['id'],)) == previous_sources
    assert len(store.rows('SELECT id FROM outbox')) == count


@pytest.mark.parametrize('intent', ['idea', 'meeting'])
@pytest.mark.parametrize('sender', ['alen', 'frost'])
def test_proposal_classification_cannot_smuggle_a_task_update(team, intent, sender):
    engine, store, config, clock, api = team
    task = confirm(team, create(team))
    text = 'У меня предложение: подготовить список иначе.'
    last_id = last_outbox_id(store)
    event, model = start(team, text, 20,
                         result([update(task, next_step='Подмена инструкции')], intent=intent), sender=sender)
    with pytest.raises(ValueError):
        engine.process(event)
    assert len(model.contexts) == 1
    assert engine.tasks() == [task]
    assert not any(row['dedupe'].startswith('assignment:') for row in rows_after(store, last_id))


def test_new_idea_does_not_postpone_an_existing_owned_task_or_cancel_its_reminder(team):
    engine, store, config, clock, api = team
    task = confirm(team, create(team))
    clock[0] += 3600
    store.queue(engine.b, 'alen', 'Синтетическое напоминание',
                'reminder:' + task['id'] + ':natural-intake', clock[0], task['id'])
    text = 'У меня идея: создать памятку для новичков. Я возьму на себя подготовку.'
    event, model = start(team, text, 20,
                         result([proposal('Создать памятку для новичков', 'alen', [1])], intent='idea'),
                         sender='alen')
    engine.process(event)
    assert len(model.contexts) == 1
    unchanged = next(row for row in engine.tasks() if row['id'] == task['id'])
    assert unchanged == task
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'") == [{'state': 'pending'}]


def pending_assignment(team, channel):
    task = create(team, channel=channel)
    deliver(team, 'Как лучше проверить список?', 19,
            result(intent='conversation', reply='Сверьте три пункта по порядку.'),
            sender='alen', channel=channel)
    task = confirm(team, task, flush=False)
    assignment = team[1].rows("SELECT * FROM outbox WHERE dedupe LIKE 'assignment:%'")
    assert len(assignment) == 1 and assignment[0]['state'] == 'pending'
    return task, assignment[0]['id']


@pytest.mark.parametrize('channel', ['bot', 'business'])
@pytest.mark.parametrize('mixed', [False, True])
def test_admin_meeting_updates_active_assignment_and_reviews_only_new_drafts(team, channel, mixed):
    engine, store, config, clock, api = team
    task, old_assignment_id = pending_assignment(team, channel)
    last_id = last_outbox_id(store)
    text = ('На встрече уточнили задачу №1: следующий шаг — сверить три пункта.\n'
            'Новый срок 2026-10-20.')
    actions = [update(task, next_step='Сверить три пункта', deadline=None,
                      deadline_lines=[2], set_fields=['next_step', 'deadline'])]
    if mixed:
        text += '\nФрост подготовит новый чек-лист.\nСрок чек-листа 2026-10-21.'
        draft = proposal('Подготовить новый чек-лист', 'frost', [3], [4])
        draft['evidence_lines'] = [3]
        actions.append(draft)
    event, model = deliver(team, text, 20, result(actions, intent='meeting'), channel=channel)
    updated = next(row for row in engine.tasks() if row['id'] == task['id'])
    assert updated['status'] == 'confirmed'
    assert updated['next_step'] == 'Сверить три пункта'
    assert updated['deadline'] == '2026-10-20'
    assert store.rows('SELECT state FROM outbox WHERE id=?', (old_assignment_id,)) == [{'state': 'cancelled'}]
    new_rows = rows_after(store, last_id)
    assignments = [row for row in new_rows if row['dedupe'].startswith('assignment:')]
    assert len(assignments) == 1 and assignments[0]['recipient'] == 'alen'
    assert assignments[0]['task_id'] == task['id']
    assert assignments[0]['state'] == 'sent' and assignments[0]['transport'] == channel
    assert 'Сверить три пункта' in assignments[0]['text'] and '2026-10-20' in assignments[0]['text']
    assert 'Поручение' in assignments[0]['text'] and 'Черновик' not in assignments[0]['text']
    reviews = [row for row in new_rows if row['dedupe'].startswith('review:')]
    if mixed:
        proposed = next(row for row in engine.tasks() if row['id'] != task['id'])
        assert proposed['status'] == 'proposed' and proposed['owner'] == 'frost'
        assert proposed['deadline'] == '2026-10-21'
        assert reviews and {row['recipient'] for row in reviews} == {'test_operator'}
        review_text = '\n'.join(row['text'] for row in reviews)
        assert proposed['title'] in review_text and 'Черновик' in review_text
        assert task['title'] not in review_text
        assert not any(row['recipient'] == 'frost' for row in new_rows)
    else:
        assert len(engine.tasks()) == 1
        assert not reviews
    count = len(store.rows('SELECT id FROM outbox'))
    engine.process(event)
    assert len(model.contexts) == 1
    assert len(store.rows('SELECT id FROM outbox')) == count


@pytest.mark.parametrize('channel', ['bot', 'business'])
@pytest.mark.parametrize('approval', ['Одобряю', 'Согласовываю'])
def test_admin_can_approve_an_idea_by_exact_title_in_ordinary_language(team, channel, approval):
    engine, store, config, clock, api = team
    text = 'У меня идея: подготовить памятку. Я возьму на себя подготовку.'
    deliver(team, text, 20, result([proposal('Подготовить памятку', 'alen', [1])], intent='idea'),
            sender='alen', channel=channel)
    task = engine.tasks()[0]
    assert task['status'] == 'proposed'
    last_id = last_outbox_id(store)
    deliver(team, approval + ' идею «' + task['title'] + '».', 21,
            result([update(task, status='confirmed')]), channel=channel)
    assert engine.tasks()[0]['status'] == 'confirmed'
    assignments = [row for row in rows_after(store, last_id) if row['dedupe'].startswith('assignment:')]
    assert len(assignments) == 1 and assignments[0]['recipient'] == 'alen'
    assert assignments[0]['state'] == 'sent' and assignments[0]['transport'] == channel
    assert task['title'] in assignments[0]['text'] and 'Черновик' not in assignments[0]['text']


@pytest.mark.parametrize('channel', ['bot', 'business'])
@pytest.mark.parametrize('approval', ['не одобряю', 'не согласовываю'])
def test_negated_ordinary_approval_keeps_the_idea_unassigned(team, channel, approval):
    engine, store, config, clock, api = team
    text = 'У меня идея: подготовить памятку. Я возьму на себя подготовку.'
    deliver(team, text, 20, result([proposal('Подготовить памятку', 'alen', [1])], intent='idea'),
            sender='alen', channel=channel)
    task = engine.tasks()[0]
    last_id = last_outbox_id(store)
    call_count = len(api.calls)
    event, model = start(team, approval + ' идею «' + task['title'] + '».', 21,
                         result([update(task, status='confirmed')]), channel=channel)
    with pytest.raises(ValueError):
        engine.process(event)
    assert len(model.contexts) == 1
    assert engine.tasks() == [task]
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")
    Dispatcher(store, config, api, lambda: clock[0]).flush()
    assert all(row['recipient'] == 'test_operator' for row in rows_after(store, last_id))
    employee_id = str(config['employees']['alen']['telegram_user_id'])
    assert not any(method == 'sendMessage' and str(arguments['chat_id']) == employee_id
                   for method, arguments in api.calls[call_count:])


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_distinct_ideas_with_same_title_each_notify_without_duplicating_the_task(team, channel):
    engine, store, config, clock, api = team
    first = 'У меня идея: подготовить памятку. Я возьму на себя подготовку. Включим примеры.'
    second = ('Предлагаю подготовить памятку. Я возьму на себя подготовку. '
              'Добавим ответы на вопросы новичков.')
    value = result([proposal('Подготовить памятку', 'alen', [1])], intent='idea')
    deliver(team, first, 20, value, sender='alen', channel=channel)
    task = engine.tasks()[0]
    first_notifications = store.rows('SELECT * FROM outbox')
    assert len(first_notifications) == 2
    assert {row['recipient'] for row in first_notifications} == {'alen', 'test_operator'}
    last_id = last_outbox_id(store)
    deliver(team, second, 21, value, sender='alen', channel=channel)
    assert engine.tasks() == [task]
    second_notifications = rows_after(store, last_id)
    assert len(second_notifications) == 2
    assert {row['recipient'] for row in second_notifications} == {'alen', 'test_operator'}
    assert all(row['state'] == 'sent' for row in second_notifications)
    assert all(row['transport'] == channel for row in second_notifications if row['recipient'] == 'alen')
    admin_text = '\n'.join(row['text'] for row in second_notifications if row['recipient'] == 'test_operator')
    assert 'Добавим ответы на вопросы новичков' in admin_text
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")
    count = len(store.rows('SELECT id FROM outbox'))
    sends = len([call for call in api.calls if call[0] == 'sendMessage'])
    deliver(team, first, 22, value, sender='alen', channel=channel, expected_model_calls=0)
    deliver(team, second, 23, value, sender='alen', channel=channel, expected_model_calls=0)
    assert engine.tasks() == [task]
    assert len(store.rows('SELECT id FROM outbox')) == count
    assert len([call for call in api.calls if call[0] == 'sendMessage']) == sends
