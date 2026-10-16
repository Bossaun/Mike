"""Exact current permitted task-card read; no model, task mutation or discovery.

Only one selected canonical row's display fields are read, after role/dependency/
current-document/source-state permission checks. Revision stays in fingerprints.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from .intake import validate_team
from .intelligence import reader_key
from .knowledge import KnowledgeIndex
from .monthly_task_history import (_Reader, _bounded_columns, _current, _documents,
                                   _integer, _text)
from .presentation import task_card
from .work_context import WORK_PREFIX, _readable, work_id

MAX_REGISTER = 128
MAX_CLOSURE = 32
MAX_SOURCES = 64
MAX_DOCUMENTS = 64
MAX_OUTPUT_UNITS = 3500
_REQUEST = re.compile(r'покажи задачу(?:\s+№\s*(?P<number>[0-9]{1,9}))?')
_NOTICE = 'Не удалось показать задачу. Проверьте номер и текущий доступ.'
_STATUSES = {'proposed', 'confirmed', 'in_progress', 'blocked', 'completed'}


class CardUnavailable(ValueError):
    def __init__(self):
        super().__init__('task_card_unavailable')


def _fail():
    raise CardUnavailable()


def _request(text):
    if type(text) is not str or len(text) > 128:
        return None
    return _REQUEST.fullmatch(' '.join(text.casefold().split()).rstrip('?!.' ))


def is_task_card_command(text):
    return _request(text) is not None


@dataclass(frozen=True)
class TaskCardPreview:
    text: str
    task_id: str
    evidence_ids: tuple[str, ...]
    snapshot_digest: str


def _identifier(value):
    if (type(value) is not str or not 0 < len(value) <= 160 or value != value.strip()
            or any(ord(c) < 32 for c in value)):
        _fail()
    return value


def _gate(config, sender, index, store, now):
    validate_team(config)
    if (sender not in config['employees'] or type(index) is not KnowledgeIndex or index.store is not store
            or type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now <= 2**53-1):
        _fail()
    ZoneInfo(config['timezone'])
    # Fingerprint only the bounded roster fields used by permission/display,
    # never arbitrary optional profile/employee fields.
    roster = []
    for key, person in sorted(config['employees'].items()):
        name = person.get('name')
        if name is not None and (type(name) is not str or len(name) > 256):
            _fail()
        roster.append([key, person['telegram_user_id'], name])
    return [config['business'], sender, config['timezone'], config['admins'],
            roster, config['allowed_live_recipients'], config['team_intake'],
            config['business_account_user_id']]


def _selected(reader, business, number, trusted_task_id):
    spec = {'id': _text('id', 160), 'public_number': _integer('public_number'),
            'revision': _integer('revision'), 'status': _text('status', 32), 'owner': _text('owner', 128, True)}
    if number is not None:
        if type(number) is not int or not 1 <= number <= 999999999:
            _fail()
        where, params = 'business=? AND public_number=?', (business, number)
    else:
        where, params = 'business=? AND id=?', (business, _identifier(trusted_task_id))
    rows = reader.query('SELECT '+_bounded_columns(spec)+' FROM tasks WHERE '+where, params)
    if len(rows) != 1 or rows[0]['status'] not in _STATUSES:
        _fail()
    return rows[0]


def _snapshot(reader, store, config, sender, index, now, number, trusted_task_id):
    policy = _gate(config, sender, index, store, now)
    business = config['business']
    selected = _selected(reader, business, number, trusted_task_id)
    role = 'admin' if sender in config['admins'] else 'employee'
    if role == 'employee' and (selected['owner'] != sender or selected['status'] == 'proposed'):
        _fail()
    # Whole-business cap precedes archive/owner filters. No display/evidence fields.
    ids = reader.query('SELECT '+_bounded_columns({'id': _text('id', 160)})+
                       ' FROM tasks WHERE business=?', (business,), maximum=MAX_REGISTER)
    records, acl = _current(reader, business, tuple(row['id'] for row in ids))
    selected_ref = work_id(business, selected['id'])
    if selected_ref not in records:
        _fail()
    pending, closure = [selected_ref], set()
    while pending:
        ref = pending.pop()
        if ref in closure:
            continue
        closure.add(ref)
        if len(closure) > MAX_CLOSURE or ref not in records:
            _fail()
        pending.extend(dep for dep in acl.get(ref, ()) if dep.startswith(WORK_PREFIX))
    docs = set().union(*(acl.get(ref, set()) for ref in closure))
    docs = {ref for ref in docs if not ref.startswith(WORK_PREFIX)}
    if len(docs) > MAX_DOCUMENTS:
        _fail()
    doc_state = _documents(reader, docs, reader_key(business, sender), now)
    if selected_ref not in _readable(records, acl, role, sender, docs):
        _fail()
    # Every reachable origin must still exist and be nondeleted. Only IDs/deleted
    # state are read; never source body/link, task evidence or result payload.
    source_ids, source_links = set(), []
    for ref in sorted(closure):
        task_id = records[ref]['id']
        primary = reader.query('SELECT '+_bounded_columns({'source_id': _text('source_id', 256)})+
                               ' FROM tasks WHERE business=? AND id=?', (business, task_id))
        if len(primary) != 1:
            _fail()
        linked = reader.query('SELECT '+_bounded_columns({'source_id': _text('source_id', 256)})+
                              ' FROM task_sources WHERE business=? AND task_id=?',
                              (business, task_id), maximum=MAX_SOURCES)
        found = {row['source_id'] for row in primary+linked}
        source_links.append((ref, sorted(found)))
        source_ids.update(found)
        if len(source_ids) > MAX_SOURCES:
            _fail()
    sources = []
    for source in sorted(source_ids):
        rows = reader.query("SELECT "+_bounded_columns({'deleted': "typeof(deleted)='integer' AND deleted IN (0,1)"})+
                            ' FROM sources WHERE business=? AND id=?', (business, source))
        if len(rows) != 1 or rows[0]['deleted'] != 0:
            _fail()
        sources.append((source, 0))
    # First transfer of selected task display fields, AFTER all permission checks.
    spec = {'id': _text('id', 160), 'public_number': _integer('public_number'),
            'revision': _integer('revision'), 'title': _text('title', 4000),
            'owner': _text('owner', 128, True), 'deadline': _text('deadline', 64, True),
            'next_step': _text('next_step', 4000), 'criterion': _text('criterion', 4000),
            'status': _text('status', 32)}
    content = reader.query('SELECT '+_bounded_columns(spec)+' FROM tasks WHERE business=? AND id=?',
                           (business, selected['id']))
    if len(content) != 1 or any(content[0][key] != selected[key] for key in selected):
        _fail()
    state = [policy, selected, content, {ref: records[ref] for ref in sorted(closure)},
             {ref: sorted(acl.get(ref, ())) for ref in sorted(closure)}, doc_state, source_links, sources]
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return content[0], tuple(sorted(closure | docs)), fingerprint


def build_task_card(store, config, sender, existing_index, *, clock, number=None, trusted_task_id=None):
    try:
        if not callable(clock):
            _fail()
        now = clock()
        reader = _Reader(store)
        initial = _snapshot(reader, store, config, sender, existing_index, now, number, trusted_task_id)
        task, refs, fingerprint = initial
        zone = ZoneInfo(config['timezone'])
        stamp = datetime.fromtimestamp(now, zone)
        label = 'МСК' if zone.key == 'Europe/Moscow' else stamp.tzname()
        output = task_card(task, config)+'\n\nСрез на '+stamp.strftime('%d.%m.%Y %H:%M')+' ('+label+').' \
                 '\nЭто состояние записи задачи; результат независимо не проверен.'
        if len(output.encode('utf-16-le'))//2 > MAX_OUTPUT_UNITS:
            _fail()
        final_now = clock()
        if type(final_now) not in (int, float) or final_now < now:
            _fail()
        final = _snapshot(reader, store, config, sender, existing_index, final_now, number, trusted_task_id)
        if final != initial:
            _fail()
        return TaskCardPreview(output, task['id'], refs, fingerprint)
    except CardUnavailable:
        raise
    except Exception:
        raise CardUnavailable() from None


def _intelligence_matches(engine, intelligence):
    return (intelligence is not None and engine.intelligence is intelligence
            and getattr(intelligence, 'b', None) == engine.b
            and getattr(intelligence, 's', None) is engine.s
            and getattr(intelligence, 'c', None) is engine.c
            and engine.c.get('business') == engine.b)


def handle_task_card(engine, event_id, payload):
    match = _request(payload.get('text')) if type(payload) is dict else None
    if match is None or not engine.s.db.in_transaction:
        _fail()
    authorized = ('team_intake' in engine.c and engine.c.get('business') == engine.b
                  and payload.get('channel') in ('bot', 'business') and engine._closed_team()
                  and payload.get('sender') in engine.c['employees'])
    if authorized:
        text, task_id, refs = _NOTICE, None, ()
        intelligence = engine.intelligence
        if _intelligence_matches(engine, intelligence):
            try:
                digits = match.group('number')
                if digits is not None and digits.startswith('0'):
                    _fail()
                draft = build_task_card(engine.s, engine.c, payload['sender'], intelligence.index,
                    clock=engine.now, number=int(digits) if digits is not None else None,
                    trusted_task_id=payload.get('task_id') if digits is None else None)
                if not _intelligence_matches(engine, intelligence):
                    _fail()
                text, task_id, refs = draft.text, draft.task_id, draft.evidence_ids
            except CardUnavailable:
                pass
        key = 'task-card:'+event_id
        engine.s.queue(engine.b, payload['sender'], text, key, engine.now(), task_id,
                       parse_mode='HTML' if task_id is not None else None, preferred_transport=payload['channel'])
        if refs:
            if not _intelligence_matches(engine, intelligence):
                _fail()  # Caller transaction rolls back any partial private queue.
            row = engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?', (engine.b, key)).fetchone()
            intelligence.protect_outbox(row['id'], refs)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?", (engine.b, event_id))
