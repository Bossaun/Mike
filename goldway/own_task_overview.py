"""Bounded current own-task view; no task mutation, discovery or model call."""
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from .monthly_task_history import MonthlyHistoryUnavailable
from .presentation import STATUS, clean
from .task_card_command import (_Reader, _bounded_columns, _text, _current, _gate,
    _snapshot, _intelligence_matches, CardUnavailable, MAX_REGISTER)
from .work_context import _deadline

_NOTICE = 'Не удалось показать свои задачи. Проверьте текущий доступ и повторите запрос.'
_ACTIVE = {'confirmed', 'in_progress', 'blocked'}
_FOOTER = 'Показана ограниченная выборка доступных собственных задач.\nПодробности: «Покажи задачу №…».'


class OwnOverviewUnavailable(ValueError):
    def __init__(self):
        super().__init__('own_task_overview_unavailable')


def _mode(text):
    if type(text) is not str or len(text) > 128:
        return None
    normalized = ' '.join(text.casefold().split()).rstrip('?!.' )
    return {'мои задачи': 'all', 'покажи мои задачи': 'all',
            'что у меня на сегодня': 'today'}.get(normalized)


def is_own_task_overview(text):
    return _mode(text) is not None


@dataclass(frozen=True)
class OwnTaskOverview:
    text: str
    evidence_ids: tuple[str, ...]
    snapshot_digest: str


class _PassReader:
    """Cache only within one observation; cumulative original reader budgets.

    Query failures are fatal, unlike a candidate's ordinary permission denial.
    Fresh final observation clears this cache, never the cumulative budgets.
    """
    def __init__(self, store):
        self.reader = _Reader(store)
        self.cache = {}

    def query(self, sql, params=(), maximum=1):
        key = (sql, tuple(params), maximum)
        if key not in self.cache:
            try:
                self.cache[key] = self.reader.query(sql, params, maximum=maximum)
            except Exception:
                raise OwnOverviewUnavailable() from None
        return [dict(row) for row in self.cache[key]]


def _collect(reader, store, config, sender, index, now, mode):
    policy = _gate(config, sender, index, store, now)
    business, zone = config['business'], ZoneInfo(config['timezone'])
    today = datetime.fromtimestamp(now, zone).date()
    ids = reader.query('SELECT '+_bounded_columns({'id': _text('id', 160)})+
                       ' FROM tasks WHERE business=?', (business,), maximum=MAX_REGISTER)
    records, _ = _current(reader, business, tuple(row['id'] for row in ids))
    permitted, refs = [], set()
    for metadata in sorted(records.values(), key=lambda row: row['id']):
        if metadata['owner'] != sender or metadata['status'] not in _ACTIVE:
            continue
        try:
            task, task_refs, fingerprint = _snapshot(reader, store, config, sender, index, now,
                                                     None, metadata['id'])
        except (CardUnavailable, MonthlyHistoryUnavailable):
            continue
        # Admin readability is broader than ownership: still only own active roots.
        if task['owner'] != sender or task['status'] not in _ACTIVE:
            raise OwnOverviewUnavailable()
        canonical, overdue, order = _deadline(task['deadline'], config, now)
        if canonical is None:
            group, label = 2, 'Срок не уточнён'
        else:
            try:
                due_day = date.fromisoformat(canonical)
                label = due_day.strftime('%d.%m.%Y')
            except ValueError:
                moment = datetime.fromisoformat(canonical)
                local = moment.astimezone(zone)
                due_day = local.date()
                label = local.strftime('%d.%m.%Y %H:%M')+' ('+local.tzname()+')'
            group = 0 if overdue else 1 if due_day == today else 3
        refs.update(task_refs)
        if len(refs) > 256:
            raise OwnOverviewUnavailable()
        permitted.append((group, order, task['public_number'], task, label, fingerprint))
    # Include all permitted roots, including unshown/future roots, in both guard and
    # protection. Denied roots contribute neither a count nor display information.
    state = [policy, today.isoformat(), mode, permitted]
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    selected = sorted((row for row in permitted if mode == 'all' or row[0] != 3),
                      key=lambda row: row[:3])[:5]
    return selected, tuple(sorted(refs)), digest


def _render(selected, config, now, mode):
    zone = ZoneInfo(config['timezone'])
    stamp = datetime.fromtimestamp(now, zone)
    heading = 'Мои задачи на сегодня' if mode == 'today' else 'Мои задачи'
    lines = ['<b>'+heading+'</b>', 'Срез на '+stamp.strftime('%d.%m.%Y %H:%M')+' ('+stamp.tzname()+').']
    labels = {0: 'Просроченные', 1: 'На сегодня', 2: 'Срок не уточнён', 3: 'Будущие'}
    previous = None
    for group, _, number, task, deadline, _ in selected:
        if previous != group:
            lines.append('\n<b>'+labels[group]+'</b>')
            previous = group
        lines.append('№'+str(number)+' · '+clean(STATUS[task['status']])+' · '+clean(task['title'], 110))
        lines.append('Срок: '+clean(deadline))
        if task['next_step']:
            lines.append('Следующий шаг: '+clean(task['next_step'], 80))
    if not selected:
        lines.append('В доступных записях нет подходящих собственных задач.')
    lines.extend(['', _FOOTER])
    output = '\n'.join(lines)
    if len(output.encode('utf-16-le'))//2 > 3500:
        raise OwnOverviewUnavailable()
    return output


def build_own_task_overview(store, config, sender, existing_index, *, clock, mode):
    try:
        if mode not in ('all', 'today') or not callable(clock):
            raise OwnOverviewUnavailable()
        now = clock()
        reader = _PassReader(store)
        initial = _collect(reader, store, config, sender, existing_index, now, mode)
        text = _render(initial[0], config, now, mode)
        final_now = clock()
        if type(final_now) not in (int, float) or final_now < now:
            raise OwnOverviewUnavailable()
        reader.cache.clear()
        final = _collect(reader, store, config, sender, existing_index, final_now, mode)
        if final != initial:
            raise OwnOverviewUnavailable()
        return OwnTaskOverview(text, initial[1], initial[2])
    except OwnOverviewUnavailable:
        raise
    except Exception:
        raise OwnOverviewUnavailable() from None


def handle_own_task_overview(engine, event_id, payload):
    mode = _mode(payload.get('text')) if type(payload) is dict else None
    if mode is None or not engine.s.db.in_transaction:
        raise OwnOverviewUnavailable()
    authorized = ('team_intake' in engine.c and engine.c.get('business') == engine.b
        and payload.get('channel') in ('bot', 'business') and engine._closed_team()
        and payload.get('sender') in engine.c['employees'])
    if authorized:
        text, refs, success = _NOTICE, (), False
        intelligence = engine.intelligence
        if _intelligence_matches(engine, intelligence):
            try:
                draft = build_own_task_overview(engine.s, engine.c, payload['sender'], intelligence.index,
                                               clock=engine.now, mode=mode)
                if not _intelligence_matches(engine, intelligence):
                    raise OwnOverviewUnavailable()
                text, refs, success = draft.text, draft.evidence_ids, True
            except OwnOverviewUnavailable:
                pass
        key = 'own-task-overview:'+event_id
        engine.s.queue(engine.b, payload['sender'], text, key, engine.now(), None,
                       parse_mode='HTML' if success else None, preferred_transport=payload['channel'])
        if refs:
            if not _intelligence_matches(engine, intelligence):
                raise OwnOverviewUnavailable()
            row = engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?', (engine.b, key)).fetchone()
            intelligence.protect_outbox(row['id'], refs)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?", (engine.b, event_id))
