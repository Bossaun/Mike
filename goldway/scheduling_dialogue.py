"""Narrow, deterministic scheduling requests; ordinary dialogue stays with the model."""
import json
import re
from datetime import datetime

from .dialogue import scope_from_payload
from .reminder_times import resolve_recurrence, resolve_reminder_at


_REQUEST = re.compile(r'^(?:пожалуйста[, ]+)?(?:напомни(?:те)?|напоминай(?:те)?|спроси(?:те)?|вернись)\b')
_PROMISE = re.compile(r'^(?:я\s+)?(?:сделаю|закончу|завершу|отправлю|подготовлю|выполню)\b')
_NOT_DONE = re.compile(r'^(?:я\s+)?(?:(?:пока|ещ[её])\s+)?(?:нет|не\s+(?:сделал[а]?|готов[оа]?|закончил[а]?|успел[а]?))\s*[.!]?$', re.I)
_CANCEL = re.compile(r'^(?:пожалуйста[, ]+)?(?:отмени(?:те)?|убери(?:те)?|сними(?:те)?)\s+(?:это\s+)?(?P<target>напоминание|регулярную\s+задачу|повторение)\b')
_BARE_TIME = re.compile(r'^(?:через\b|(?:сегодня|завтра|послезавтра)\s+в\b|\d{1,2}:\d{2}\b)')


def recurrence_request(engine, payload):
    """Only an explicit administrator request can propose a recurring template."""
    if payload['sender'] not in engine.c['admins']:
        return None
    text = payload['text'].casefold().strip()
    if not re.match(r'^(?:пожалуйста[, ]+)?(?:создай(?:те)?|поставь(?:те)?|напоминай(?:те)?|повторяй(?:те)?)\b', text):
        return None
    return resolve_recurrence(text, datetime.fromtimestamp(engine._message_time(payload), engine.tz))


def _recent_prompt(engine, payload):
    """Short answers can bind only to a delivered prompt in this exact chat."""
    scope = scope_from_payload(payload, engine.b)
    if not scope:
        return None
    _, _, channel, chat, connection, thread = json.loads(scope.split(':', 2)[2])
    rows = engine.s.rows("""SELECT * FROM outbox WHERE business=? AND recipient=?
        AND state='sent' AND transport=? AND destination_chat=?
        AND destination_connection IS ? AND destination_thread IS ?
        ORDER BY id DESC LIMIT 1""", (engine.b, payload['sender'], channel, chat, connection, thread))
    if not rows:
        return None
    row = rows[0]
    if (row['created'] > engine._message_time(payload) or
            engine._message_time(payload) - row['created'] > 24 * 3600 or
            not row['dedupe'].startswith('reminder:')):
        return None
    return row['task_id']


def _one_task(engine, payload, context, recent):
    candidates = context.get('tasks', [])
    explicit = payload.get('task_id') or engine.reference_ids(payload['text']) or re.search(r'(?:№|#|задач[ауеи]?\s*)\s*\d+', payload['text'], re.I)
    if explicit:
        return candidates[0]['id'] if len(candidates) == 1 else None
    named = [t for t in candidates if t['title'].casefold() in payload['text'].casefold()]
    if len(named) == 1:
        return named[0]['id']
    ids = {t['id'] for t in candidates}
    focus = context.get('dialogue', {}).get('task_id')
    if recent in ids:
        return recent
    if focus in ids:
        return focus
    return candidates[0]['id'] if len(candidates) == 1 else None


def _active_series(engine, task_id):
    # An occurrence card has its own task ID; retain the original series link.
    rows = engine.s.rows("""SELECT id FROM c07_series
        WHERE business=? AND state='active' AND (task_id=? OR id IN (
        SELECT series_id FROM c07_occurrences WHERE business=? AND task_id=?))""",
        (engine.b, task_id, engine.b, task_id))
    if len(rows) > 1:
        raise ValueError('Recurring cancellation target is ambiguous')
    return rows[0] if rows else None


def handle_scheduling(engine, event_id, payload, context, scope):
    """Return true only for a recognized scheduling turn, persisted atomically."""
    if not engine.scheduler or not engine._task_dialogue_turn(payload, 'reply'):
        return False
    low = payload['text'].casefold().strip()
    # Remove a leading explicit task reference without making ordinary questions commands.
    lead = re.sub(r'^(?:по\s+)?задач[аеуы]?\s*(?:№|#)?\s*\d+\s*[:—,.-]?\s*', '', low)
    lead = re.sub(r'^пожалуйста[, ]+', '', lead)
    if '?' in lead or re.match(r'^напомни(?:те)?[, ]+(?:как[а-яё]*|котор[а-яё]*|что|когда|почему|зачем|кто|сколько|чем|пример)\b', lead):
        return False
    request = bool(_REQUEST.match(lead))
    cancel_match = _CANCEL.match(lead)
    cancel = bool(cancel_match)
    cancel_series = cancel and cancel_match.group('target') != 'напоминание'
    recent = _recent_prompt(engine, payload)
    promise = bool(_PROMISE.match(lead)) or bool(re.match(r'^(?:я\s+)?ещ[её]\s+не\s+сделал[а]?[,.; ]+(?:я\s+)?сделаю\b', lead))
    short_time = bool(recent and _BARE_TIME.match(lead))
    not_done = bool(_NOT_DONE.fullmatch(lead))
    if not_done and not (recent or payload.get('task_id') or engine.reference_ids(payload['text'])):
        return False
    if not (request or cancel or promise or short_time or not_done):
        return False
    reference = datetime.fromtimestamp(engine._message_time(payload), engine.tz)
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"', '', payload['text'])
    when = resolve_reminder_at(unquoted, reference)
    rule = resolve_recurrence(unquoted, reference) if request else None
    # A normal work report without a time still goes through existing task logic.
    if promise and not when and not request:
        return False
    task_id = _one_task(engine, payload, context, recent)
    task = engine.s.execute('SELECT * FROM tasks WHERE business=? AND id=?', (engine.b, task_id)).fetchone() if task_id else None
    notice, asking = None, False
    with engine.s.transaction():
        source = payload.get('source_id') or event_id
        engine.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?) ON CONFLICT(business,id) DO NOTHING',
                         (engine.b, source, payload['text'], payload.get('link', event_id)))
        first = engine.s.execute('SELECT COALESCE(MAX(id),0) FROM outbox').fetchone()[0]
        if not task:
            notice = 'О какой задаче напомнить? Назовите её или ответьте на её карточку.'
        elif task['status'] in ('completed', 'proposed') and not cancel_series:
            notice = 'Для напоминания нужна подтверждённая незавершённая задача.'
        elif not task['owner']:
            notice = 'Кто отвечает за эту задачу?'
        elif not engine._scheduling_permitted(task_id, payload['sender'], engine.now()):
            notice = 'Для этой задачи сейчас нет доступного маршрута напоминания.'
        else:
            engine.s.execute('SAVEPOINT c07_dialogue_action')
            try:
                if cancel:
                    if cancel_series:
                        row = _active_series(engine, task_id)
                        changed = engine.scheduler.pause_series(row['id'], payload['sender'], event_id) if row else False
                    else:
                        changed = engine.scheduler.cancel_check(task_id, payload['sender'], event_id)
                    notice = 'Напоминание отменено.' if changed else 'Активного напоминания для этой задачи нет.'
                elif rule:
                    result = engine.scheduler.register_series(task_id, payload['sender'], rule, event_id, engine._message_time(payload))
                    due = datetime.fromtimestamp(result['next_at'], engine.tz)
                    notice = f'Повторение сохранено. Следующая задача появится {due:%d.%m.%Y в %H:%M} ({engine.tz.key}).'
                elif when:
                    if promise and task['owner'] != payload['sender']:
                        notice = 'Вы берёте эту задачу на себя или просите напомнить её исполнителю?'
                    else:
                        engine.scheduler.progress(task_id, payload['sender'], payload.get('received_at', engine.now()), message_at=engine._message_time(payload))
                        engine._record_response(task_id, payload['sender'], payload.get('received_at', engine.now()))
                        result = engine.scheduler.schedule_check(task_id, payload['sender'], when.timestamp(), event_id, engine._message_time(payload))
                        due = datetime.fromtimestamp(result['at'], engine.tz)
                        who = 'вам' if task['owner'] == payload['sender'] else engine.c['employees'][task['owner']]['name']
                        notice = f'Сохранено: {due:%d.%m.%Y в %H:%M} ({engine.tz.key}) напомню {who} по задаче №{task["public_number"]} и спрошу о результате.'
                elif not_done:
                    if task['owner'] != payload['sender']:
                        notice = 'Когда исполнитель сможет закончить эту задачу?'
                    else:
                        engine.scheduler.progress(task_id, payload['sender'], payload.get('received_at', engine.now()), message_at=engine._message_time(payload))
                        engine._record_response(task_id, payload['sender'], payload.get('received_at', engine.now()))
                        notice, asking = 'Когда сможете закончить? Назовите время, например «через 30 минут».', True
                else:
                    notice = 'Когда напомнить? Укажите время, например «через 30 минут» или «завтра в 10:00».'
                    asking = True
            except ValueError:
                engine.s.execute('ROLLBACK TO c07_dialogue_action')
                notice = 'Напоминание не сохранено. Уточните будущее время и подтвердите задачу с ответственным; регулярные поручения настраивает руководитель.'
            finally:
                engine.s.execute('RELEASE c07_dialogue_action')
        key = ('reminder:c07-prompt:' if asking else 'c07-ack:') + event_id
        linked = task_id if task and engine._scheduling_permitted(task_id, payload['sender'], engine.now()) else None
        engine.s.queue(engine.b, payload['sender'], notice, key, engine.now(), linked, preferred_transport=payload.get('channel'))
        if engine.intelligence:
            dialogue = {k: context['dialogue'][k] for k in ('topic', 'entities', 'pending_offer', 'task_id')}
            dialogue['task_id'] = linked
            engine.intelligence.remember(event_id, payload, context,
                {'dialogue_state': dialogue, 'reply_text': notice}, scope, first, [], engine.now())
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?", (engine.b, event_id))
        engine.s.audit(engine.b, 'scheduling_turn', {'event': event_id, 'task': linked, 'asking_time': asking}, engine.now())
    return True
