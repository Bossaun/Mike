"""Natural check-back turns through the existing single-worker task and delivery path."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from goldway.engine import Engine
from goldway.model import ScriptedModel
from goldway.store import Store
from goldway.telegram import Dispatcher
from test_team_tasks import team, create, confirm, inbound, process, update
from test_operator_tasks import result


@pytest.fixture
def scheduled(team):
    e, s, c, clock, api = team
    c['scheduling'] = {'enabled': True}
    e = Engine(s, c, e.model, lambda: clock[0])
    return e, s, c, clock, api


def setup_task(team):
    task = create(team)
    return confirm(team, task)


def run(team, text, number, sender='alen', *, message_at=None, task_id=None, flush=True):
    e, s, c, clock, api = team
    uid = c['employees'][sender]['telegram_user_id']
    event = 'synthetic-reminder:' + str(number)
    e.reply(event, sender, text, task_id, source_id=f'telegram:{uid}:{number}', channel='bot',
            message_at=int(clock[0]) if message_at is None else message_at)
    e.process(event)
    if flush:
        Dispatcher(s, c, api, lambda: clock[0]).flush()
    return event


def test_fresh_reminder_not_done_time_check_result_and_restart(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    original_deadline = task['deadline']
    e.scheduler.schedule_check(task['id'], 'alen', clock[0] + 60, 'first-check', clock[0])
    clock[0] += 60
    e.tick()
    Dispatcher(s, c, api, lambda: clock[0]).flush()
    run(scheduled, 'Пока нет', 30)
    assert 'Когда сможете закончить' in s.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    clock[0] += 1
    run(scheduled, 'Через 30 минут', 31)
    pending = e.scheduler.pending(task['id'])
    assert pending['at'] == clock[0] + 1800
    assert e.tasks()[0]['deadline'] == original_deadline
    e2 = Engine(s, c, ScriptedModel({}), lambda: clock[0])
    clock[0] += 1799
    e2.tick()
    before = s.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'reminder:c07:%'").fetchone()[0]
    clock[0] += 1
    e2.tick(); e2.tick()
    assert s.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'reminder:c07:%'").fetchone()[0] == before + 1
    Dispatcher(s, c, api, lambda: clock[0]).flush()
    e.model = ScriptedModel(result([update(task, status='completed')], reply='Результат сохранён.'))
    run(scheduled, 'Задача №1: список из трёх пунктов подготовлен и сохранён в RESULT.txt.', 32)
    assert e.tasks()[0]['status'] == 'completed'
    clock[0] += 86400
    e2.tick()
    assert not e2.scheduler.pending(task['id'])
    assert {r['recipient'] for r in s.rows('SELECT recipient FROM outbox')} <= {'test_operator', 'alen'}


def test_message_time_not_processing_time_anchors_the_check(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    original = int(clock[0])
    clock[0] += 120
    run(scheduled, 'Напомни через 30 минут', 40, task_id=task['id'], message_at=original)
    assert e.scheduler.pending(task['id'])['at'] == original + 1800


def test_replacement_cancels_old_check_but_does_not_duplicate(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    run(scheduled, 'Напомни через 30 минут', 41, task_id=task['id'])
    clock[0] += 30
    event = run(scheduled, 'Напомни через 1 час', 42, task_id=task['id'])
    assert e.scheduler.pending(task['id'])['at'] == clock[0] + 3600
    before = len(s.rows('SELECT * FROM outbox'))
    e.process(event)
    assert len(s.rows('SELECT * FROM outbox')) == before
    clock[0] += 1770
    e.tick()
    assert not s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:c07:%'")


def test_failed_expired_reschedule_preserves_existing_check(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    e.scheduler.schedule_check(task['id'], 'alen', clock[0] + 3600, 'existing', clock[0] - 7200)
    old = e.scheduler.pending(task['id'])
    run(scheduled, 'Задача №1: сделаю через 30 минут', 43, message_at=int(clock[0] - 3600), task_id=task['id'])
    assert e.scheduler.pending(task['id']) == old
    assert 'не сохранено' in s.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']


@pytest.mark.parametrize('text', [
    'Напомни, как сформулировать ответ «сделаю через 30 минут»',
    'Напомни, какие задачи у меня запланированы через 30 минут',
    'Пожалуйста, напомни, какие задачи у меня запланированы через 30 минут',
    'Напомни, какая встреча у нас через 30 минут',
    'Напомни, который час будет через 30 минут',
    'Можно напомнить через 30 минут?',
    'Расскажи, что значит фраза «через 30 минут»',
])
def test_questions_and_examples_do_not_schedule(scheduled, text):
    e, s, c, clock, api = scheduled
    setup_task(scheduled)
    e.model = ScriptedModel(result([], intent='conversation', reply='Это объяснение, напоминание не создавал.'))
    run(scheduled, text, 44)
    assert not s.rows('SELECT * FROM c07_checks')
    assert s.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text'] == 'Это объяснение, напоминание не создавал.'


def test_unknown_time_asks_once_without_changing_deadline(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    run(scheduled, 'Напомни завтра', 45, task_id=task['id'])
    assert not e.scheduler.pending(task['id'])
    assert 'Когда напомнить?' in s.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    assert e.tasks()[0]['deadline'] == task['deadline']


def test_owner_progress_before_model_failure_cancels_check(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    e.scheduler.schedule_check(task['id'], 'alen', clock[0] + 1800, 'waiting', clock[0])
    clock[0] += 1
    class Failure:
        def propose(self, context):
            raise RuntimeError('synthetic model failure')
    e.model = Failure()
    with pytest.raises(RuntimeError):
        run(scheduled, 'Задача №1: делаю список, проверены две позиции.', 46, task_id=task['id'])
    assert not e.scheduler.pending(task['id'])


def test_question_does_not_cancel_existing_promised_check(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    e.scheduler.schedule_check(task['id'], 'alen', clock[0] + 1800, 'waiting', clock[0])
    e.model = ScriptedModel(result([], intent='conversation', reply='Подтверждённых данных нет.'))
    clock[0] += 1
    run(scheduled, 'Задача №1: какой список нужен?', 47, task_id=task['id'])
    assert e.scheduler.pending(task['id'])


def test_regular_template_gets_new_task_after_completion_and_can_be_paused(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    run(scheduled, 'Напоминай каждый день в 09:00', 48, sender='test_operator', task_id=task['id'])
    series = s.rows('SELECT * FROM c07_series')[0]
    s.execute("UPDATE tasks SET status='completed' WHERE id=?", (task['id'],))
    clock[0] = series['next_at']
    e.tick(); e.tick()
    tasks = e.tasks()
    assert len(tasks) == 2 and tasks[0]['status'] == 'completed'
    assert tasks[1]['status'] == 'confirmed' and tasks[1]['owner'] == 'alen'
    run(scheduled, 'Отмени повторение', 49, sender='test_operator', task_id=task['id'])
    assert s.rows('SELECT state FROM c07_series') == [{'state': 'paused'}]


def test_employee_cannot_enable_recurring_series(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    run(scheduled, 'Напоминай каждый день в 09:00', 50, task_id=task['id'])
    assert not s.rows('SELECT * FROM c07_series')


def test_disabled_feature_creates_no_scheduler_tables(team):
    e, s, c, clock, api = team
    assert e.scheduler is None
    assert not s.rows("SELECT name FROM sqlite_master WHERE name LIKE 'c07_%'")


def test_latest_delivered_prompt_wins_older_dialogue_focus(scheduled):
    from goldway.scheduling_dialogue import handle_scheduling
    e, s, c, clock, api = scheduled
    first = setup_task(scheduled)
    s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
        SELECT business,'second-task','Другая рассылка',owner,deadline,next_step,criterion,status,source_id,evidence,created FROM tasks WHERE id=?''', (first['id'],))
    s.execute('INSERT INTO task_sources SELECT business,?,source_id FROM task_sources WHERE task_id=?', ('second-task', first['id']))
    s.queue(e.b, 'alen', 'Когда закончите другую рассылку?', 'reminder:second', clock[0], 'second-task')
    Dispatcher(s, c, api, lambda: clock[0]).flush()
    clock[0] += 1
    uid = c['employees']['alen']['telegram_user_id']
    event = 'short-time-focus'
    e.reply(event, 'alen', 'Через 30 минут', source_id=f'telegram:{uid}:90', channel='bot', message_at=clock[0])
    payload = json.loads(s.execute('SELECT payload FROM events WHERE id=?', (event,)).fetchone()[0])
    context = {'tasks': e.tasks('alen'), 'dialogue': {'task_id': first['id']}}
    assert handle_scheduling(e, event, payload, context, None)
    assert e.scheduler.pending('second-task') and not e.scheduler.pending(first['id'])


def test_bare_time_does_not_bind_to_prompt_in_another_transport(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    s.queue(e.b, 'alen', 'Когда закончите?', 'reminder:bot-only', clock[0], task['id'])
    Dispatcher(s, c, api, lambda: clock[0]).flush()
    e.model = ScriptedModel(result([], intent='conversation', reply='О какой задаче речь?'))
    uid = c['employees']['alen']['telegram_user_id']
    event = 'other-transport'
    e.reply(event, 'alen', 'Через 30 минут', source_id=f'business:new-connection:{uid}:91', channel='business', message_at=clock[0])
    e.process(event)
    assert not s.rows('SELECT * FROM c07_checks')


def test_regular_creation_requires_confirmation_and_displays_cadence(scheduled):
    e, s, c, clock, api = scheduled
    text = 'Создай регулярную задачу: подготовить список товаров. Ответственный Алэн. Каждый день в 09:00.'
    action = {'op': 'create', 'title': 'Подготовить список товаров', 'owner': 'alen', 'owner_lines': [1],
              'deadline': None, 'deadline_lines': [], 'next_step': 'Составить список',
              'criterion': 'Список из трёх пунктов', 'status': 'confirmed', 'evidence_lines': [1]}
    process(scheduled, text, 92, result([action]))
    task = e.tasks()[0]
    assert task['status'] == 'proposed' and not s.rows('SELECT * FROM c07_series')
    assert 'Повторение после подтверждения' in s.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    confirm(scheduled, task)
    assert s.rows('SELECT state FROM c07_series') == [{'state': 'active'}]


def test_recurring_clone_retains_knowledge_acl_when_intelligence_disabled(scheduled):
    from goldway.intelligence import reader_key, delivery_permitted
    from goldway.knowledge import KnowledgeIndex
    from test_knowledge import document
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    index = KnowledgeIndex(s)
    index.upsert(document(readers=[reader_key(e.b, 'alen'), reader_key(e.b, 'test_operator')],
                          observed_at=clock[0] - 1, permission_expires_at=clock[0] + 14 * 86400))
    ids = sorted(index.allowed_ids(reader_key(e.b, 'alen'), now=clock[0]))
    s.execute('CREATE TABLE intelligence_task_acl(business TEXT,task_id TEXT,evidence_ids TEXT,PRIMARY KEY(business,task_id))')
    s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)', (e.b, task['id'], json.dumps(ids)))
    assert e.intelligence is None and ids
    rule = {'frequency': 'daily', 'weekdays': list(range(7)), 'time': '09:00', 'timezone': c['timezone']}
    series = e.scheduler.register_series(task['id'], 'test_operator', rule, 'private-recurrence', clock[0])
    clock[0] = series['next_at']
    e.tick()
    clone = e.tasks()[1]
    refs = json.loads(s.execute('SELECT evidence_ids FROM intelligence_task_acl WHERE task_id=?', (clone['id'],)).fetchone()[0])
    assert refs == ids
    index.revoke_source('fictional-catalog')
    row = s.rows('SELECT * FROM outbox WHERE task_id=?', (clone['id'],))[0]
    assert not delivery_permitted(s, e.b, 'alen', row['id'], clock[0])


def test_unfinished_report_does_not_complete_task(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    run(scheduled, 'Пока нет', 93, task_id=task['id'])
    assert e.tasks()[0]['status'] == 'confirmed'
    assert not e.scheduler.pending(task['id'])


def test_unknown_owner_does_not_block_task_confirmation(scheduled):
    e, s, c, clock, api = scheduled
    text = 'Создай регулярную задачу: проверить ассортимент каждый день в 09:00.'
    action = {'op': 'create', 'title': 'Проверить ассортимент', 'owner': None, 'owner_lines': [],
              'deadline': None, 'deadline_lines': [], 'next_step': 'Проверить список',
              'criterion': 'Список проверен', 'status': 'proposed', 'evidence_lines': [1]}
    process(scheduled, text, 94, result([action]))
    task = confirm(scheduled, e.tasks()[0])
    assert task['status'] == 'confirmed' and not s.rows('SELECT * FROM c07_series')


def test_late_older_original_report_preserves_newer_queued_promised_reminder(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    original_report = int(clock[0])
    clock[0] += 10
    e.scheduler.schedule_check(task['id'], 'alen', clock[0] + 10, 'new-source-check', clock[0])
    clock[0] += 10
    e.tick()
    row = s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:c07:%'")[0]
    assert row['state'] == 'pending'
    clock[0] += 10
    e.reply('older-progress', 'alen', 'Задача №1: делаю список, проверены две позиции.', task['id'],
            source_id=f'telegram:{c["employees"]["alen"]["telegram_user_id"]}:95', channel='bot', message_at=original_report)
    assert s.rows('SELECT state FROM outbox WHERE id=?', (row['id'],)) == [{'state': 'pending'}]


def test_real_completion_report_preserves_series_and_original_instructions(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    original_evidence = task['evidence']
    run(scheduled, 'Напоминай каждый день в 09:00', 96, sender='test_operator', task_id=task['id'])
    series = s.rows('SELECT * FROM c07_series')[0]
    process(scheduled, 'Задача №1: список из трёх пунктов подготовлен и сохранён в RESULT.txt.', 97,
            result([update(task, status='completed')], reply='Результат сохранён.'), sender='alen')
    assert e.tasks()[0]['status'] == 'completed' and e.tasks()[0]['evidence'] != original_evidence
    clock[0] = series['next_at']
    e.tick()
    assert len(e.tasks()) == 2
    assert e.tasks()[1]['evidence'] == original_evidence
    assert e.tasks()[1]['status'] == 'confirmed'


def test_short_refusal_in_ordinary_conversation_is_not_task_progress(scheduled):
    e, s, c, clock, api = scheduled
    task = setup_task(scheduled)
    e.scheduler.schedule_check(task['id'], 'alen', clock[0] + 1800, 'keep-waiting', clock[0])
    s.queue(e.b, 'alen', 'Рассказать подробнее о брендах?', 'conversation-offer', clock[0])
    Dispatcher(s, c, api, lambda: clock[0]).flush()
    e.model = ScriptedModel(result([], intent='conversation', reply='Хорошо.'))
    clock[0] += 1
    run(scheduled, 'Пока нет', 98)
    assert e.scheduler.pending(task['id'])
    assert s.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text'] == 'Хорошо.'
