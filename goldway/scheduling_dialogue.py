"""Narrow, deterministic scheduling requests; ordinary dialogue stays with the model."""
import re
from datetime import datetime

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


def _recent_prompt(engine, payload, event_id):
    from .scheduling_anchor import resolve
    return resolve(engine, event_id, payload)


def _one_task(engine, payload, context, recent):
    candidates = context.get('tasks', [])
    explicit = payload.get('task_id') or engine.reference_ids(payload['text']) or re.search(r'(?:№|#|задач[ауеи]?\s*)\s*\d+', payload['text'], re.I)
    if explicit:
        return candidates[0]['id'] if len(candidates) == 1 else None
    named = [t for t in candidates if t['title'].casefold() in payload['text'].casefold()]
    if len(named) == 1:
        return named[0]['id']
    ids = {t['id'] for t in candidates}
    if recent in ids:
        return recent
    # A remembered focus or narrowed singleton never authorizes scheduling.
    return None


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
    promise = bool(_PROMISE.match(lead)) or bool(re.match(r'^(?:я\s+)?ещ[её]\s+не\s+сделал[а]?[,.; ]+(?:я\s+)?сделаю\b', lead))
    short_time = bool(_BARE_TIME.match(lead))
    not_done = bool(_NOT_DONE.fullmatch(lead))
    if not (request or cancel or promise or short_time or not_done):
        return False
    direct = _one_task(engine, payload, context, None)
    anchor = _recent_prompt(engine, payload, event_id) if direct is None or short_time else None
    recent = anchor[0] if anchor else None
    short_time = bool(recent and short_time and (direct is None or direct == recent))
    if not (request or cancel or promise or short_time or not_done):
        return False
    if not_done and not (recent or payload.get('task_id') or engine.reference_ids(payload['text'])):
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
        # Recheck the same bounded host/delivery snapshot immediately before use.
        if anchor is not None and task_id == anchor[0] and _recent_prompt(engine, payload, event_id) != anchor:
            task_id, task = None, None
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
        if asking and linked:
            from .scheduling_anchor import capture
            own = engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?', (engine.b, key)).fetchone()
            if own:capture(engine, event_id, payload, linked, own[0])
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?", (engine.b, event_id))
        engine.s.audit(engine.b, 'scheduling_turn', {'event': event_id, 'task': linked, 'asking_time': asking}, engine.now())
    return True


_RETURN_QUESTION='Когда вернуться к вам по этой задаче? Назовите время, например «через 30 минут».'


def _progress_scope(engine,payload,task):
    """Narrow explicit own employee progress, never model text/focus authority."""
    if (not engine.scheduler or not engine.intelligence or payload.get('channel') not in ('bot','business')
        or payload.get('sender') in engine.c.get('admins',()) or task.get('owner')!=payload.get('sender')
        or task.get('status') not in ('confirmed','in_progress') or not engine._task_dialogue_turn(payload,'reply')):return False
    from .task_card_reference import _gate
    from .scheduling import Scheduler
    if (type(engine.scheduler) is not Scheduler or engine.scheduler.s is not engine.s
        or engine.scheduler.c is not engine.c or engine.scheduler.b!=engine.b):return False
    try:_gate(engine,payload,engine.intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):return False
    refs=engine.reference_ids(payload['text']);explicit=payload.get('task_id')
    if (explicit and explicit!=task['id']) or (refs and refs!={task['id']}) or not (explicit==task['id'] or refs=={task['id']}):return False
    text=re.sub(r'^\s*(?:по\s+)?задач[аеуы]?\s*(?:№|#)?\s*\d+\s*[:—,.-]?\s*','',payload['text'].casefold())
    if engine._question_or_draft(text) or not re.match(r'^(?:я\s+)?(?:делаю|работаю|проверяю|продолжаю)\b',text):return False
    return engine._scheduling_permitted(task['id'],payload['sender'],engine.now())


def _future_check(engine,task):
    return engine.s.execute("SELECT 1 FROM c07_checks WHERE business=? AND task_id=? AND owner=? AND source_id=? AND state='pending' AND at>? LIMIT 1",
        (engine.b,task['id'],task['owner'],task['source_id'],engine.now())).fetchone() is not None


def preserve_progress_check(engine,payload,task):
    """Preserve a current agreed check even if a later model action is rejected.

    Scope/ACL/current ownership and explicit narrow phrase are host-validated;
    this only suppresses old ingress cancellation, never creates scheduling.
    """
    current=engine.s.execute('SELECT id,owner,status,source_id FROM tasks WHERE business=? AND id=?',(engine.b,task['id'])).fetchone() if engine.scheduler else None
    return bool(current and _progress_scope(engine,payload,dict(current)) and _future_check(engine,dict(current)))


def queue_progress_question(engine,event_id,payload,context,actions,changed,before):
    """After a validated one-task update, queue/capture one existing C07 prompt."""
    if not engine.s.db.in_transaction:raise ValueError('return_question_transaction')
    if (not engine.scheduler or not engine.intelligence or len(actions)!=1 or len(changed)!=1
        or type(actions[0]) is not dict or actions[0].get('op')!='update'):return False
    action=actions[0]
    allowed={'op','task_id','status','next_step','evidence','evidence_lines','set_fields','title','owner','deadline','criterion','owner_lines','deadline_lines'}
    if not set(action)<=allowed:return False
    # Actual response_schema carries null unchanged fields and unused line arrays.
    # Do not mistake inert placeholders for edits; genuine mixed requests deny.
    if any(action.get(field) is not None for field in ('title','owner','deadline','criterion')):return False
    if any(type(action.get(field,[])) is not list or action.get(field,[]) for field in ('owner_lines','deadline_lines')):return False
    mask=action.get('set_fields')
    if mask is not None:
        if (type(mask) is not list or len(mask)!=len(set(mask)) or 'status' not in mask
            or not set(mask)<={'status','next_step'}
            or ('next_step' not in mask and action.get('next_step') is not None)):return False
    task_id=changed[0];previous=before.get(task_id)
    if (not previous or action.get('task_id')!=task_id or action.get('status')!='in_progress'
        or previous['status'] not in ('confirmed','in_progress')
        or previous['owner']!=payload['sender'] or not _progress_scope(engine,payload,previous)):return False
    row=engine.s.execute('SELECT * FROM tasks WHERE business=? AND id=?',(engine.b,task_id)).fetchone()
    if not row:return False
    task=dict(row)
    if (task['status']!='in_progress' or task['owner']!=previous['owner'] or task['deadline']!=previous['deadline']
        or task['source_id']!=previous['source_id'] or task['revision']!=previous['revision']+1
        or (previous['status']=='in_progress' and all(task[k]==previous[k] for k in ('next_step','evidence')))
        or _future_check(engine,task)):return False
    if engine.s.execute('SELECT 1 FROM c07_checks WHERE business=? AND event_id=? LIMIT 1',(engine.b,event_id)).fetchone():return False
    if engine.s.execute("SELECT 1 FROM outbox WHERE business=? AND recipient=? AND dedupe LIKE 'reminder:c07-prompt:%' AND state IN ('pending','retry','sending','unknown') LIMIT 1",(engine.b,payload['sender'])).fetchone():return False
    source=payload.get('source_id') or event_id
    if not engine.s.execute('SELECT 1 FROM task_sources WHERE business=? AND task_id=? AND source_id=?',(engine.b,task_id,source)).fetchone():return False
    sources=engine.s.rows('SELECT source_id FROM task_sources WHERE business=? AND task_id=? LIMIT 33',(engine.b,task_id))
    source_ids={task['source_id']}|{r['source_id'] for r in sources}
    if len(sources)>32 or any(type(v) is not str or not 0<len(v)<=256 for v in source_ids):return False
    marks=','.join('?' for v in source_ids)
    alive=engine.s.rows('SELECT id,deleted FROM sources WHERE business=? AND id IN ('+marks+')',(engine.b,*sorted(source_ids)))
    if {r['id'] for r in alive}!=source_ids or any(r['deleted']!=0 for r in alive):return False
    # Recheck captured initialized host/current row around queue/protection/capture.
    import json
    from .work_context import work_id
    from .scheduling_anchor import capture
    from .task_card_reference import _gate
    host=(engine.s,engine.s.db,engine.c,engine.intelligence,engine.scheduler)
    policy=json.dumps(engine.c,sort_keys=True,ensure_ascii=False)
    refs=engine.intelligence.task_refs(task_id)|{work_id(engine.b,task_id)}
    def check():
        if (engine.s is not host[0] or engine.s.db is not host[1] or engine.c is not host[2]
            or engine.intelligence is not host[3] or engine.scheduler is not host[4]
            or json.dumps(engine.c,sort_keys=True,ensure_ascii=False)!=policy):raise ValueError('return_question_changed')
        _gate(engine,payload,engine.intelligence)
        current=engine.s.execute('SELECT * FROM tasks WHERE business=? AND id=?',(engine.b,task_id)).fetchone()
        if (not current or dict(current)!=task or not engine._scheduling_permitted(task_id,payload['sender'],engine.now())
            or engine.intelligence.task_refs(task_id)|{work_id(engine.b,task_id)}!=refs or _future_check(engine,task)):
            raise ValueError('return_question_changed')
        bindings=engine.s.rows('SELECT source_id FROM task_sources WHERE business=? AND task_id=? LIMIT 33',(engine.b,task_id))
        if len(bindings)>32 or {r['source_id'] for r in bindings}!={r['source_id'] for r in sources}:raise ValueError('return_question_changed')
        fresh=engine.s.rows('SELECT id,deleted FROM sources WHERE business=? AND id IN ('+marks+')',(engine.b,*sorted(source_ids)))
        if fresh!=alive:raise ValueError('return_question_changed')
    check();key='reminder:c07-prompt:'+event_id
    engine.s.queue(engine.b,payload['sender'],_RETURN_QUESTION,key,engine.now(),task_id,preferred_transport=payload.get('channel'))
    own=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,key)).fetchone()
    if not own:raise ValueError('return_question_missing')
    def queued():
        row=engine.s.execute('SELECT text,recipient,task_id,preferred_transport,dedupe FROM outbox WHERE business=? AND id=?',(engine.b,own[0])).fetchone()
        if (not row or tuple(row)!=(_RETURN_QUESTION,payload['sender'],task_id,payload.get('channel'),key)):
            raise ValueError('return_question_changed')
    check();queued();engine.intelligence.protect_outbox(own[0],refs);check();queued();capture(engine,event_id,payload,task_id,own[0]);check();queued()
    return True
