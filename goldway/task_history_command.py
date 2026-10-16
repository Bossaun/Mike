"""Explicit private admin request for registered changes, not productivity KPI."""
from datetime import date, datetime
import re


_NATURAL = 'покажи статистику задач за этот месяц'
_MONTH = re.compile(r'/task_stats(?: ([0-9]{4}-(?:0[1-9]|1[0-2])))?')
_UNAVAILABLE = ('Отчёт сейчас недоступен: текущую выборку, права или историю '
                'не удалось подтвердить. Задачи не изменены.')


def _request(text):
    return ' '.join(text.casefold().split()).strip(' ?!.') if type(text) is str else ''


def is_history_command(text):
    value = _request(text)
    return value == _NATURAL or _MONTH.fullmatch(value) is not None


def _period(text, now, tz):
    current = datetime.fromtimestamp(now, tz).date()
    match = _MONTH.fullmatch(_request(text))
    value = match.group(1) if match else None
    start = date.fromisoformat(value+'-01') if value else current.replace(day=1)
    if start > current:
        raise ValueError('future_history_period')
    end = date(start.year+1,1,1) if start.month == 12 else date(start.year,start.month+1,1)
    return start, end


def handle_task_history(engine, event_id, payload):
    if type(payload) is not dict or not is_history_command(payload.get('text')):
        raise ValueError('task_history_exact_command_required')
    authorized = ('team_intake' in engine.c and engine.c.get('business') == engine.b
                  and engine._operator_turn(payload, 'reply'))
    if authorized:
        from .monthly_task_history import build_monthly_task_history, MonthlyHistoryUnavailable
        from .operational_reports import Period
        refs, text = set(), _UNAVAILABLE
        if engine.intelligence is not None:
            try:
                now = engine.now()
                start, end = _period(payload['text'], now, engine.tz)
                # Explicit bounded sample, never a count or claim of full coverage.
                ids = tuple(row[0] for row in engine.s.execute(
                    "SELECT CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=160 THEN id END "
                    'FROM tasks WHERE business=? ORDER BY id LIMIT 64', (engine.b,)))
                report = build_monthly_task_history(engine.s, engine.c, payload['sender'],
                    engine.intelligence.index, task_ids=ids, dependency_task_ids=ids,
                    period=Period(start,end), as_of=datetime.fromtimestamp(now,engine.tz))
                text = '\n'.join(report.lines)
                refs = set(report.evidence_ids)
                if len(text.encode('utf-16-le'))//2 > 3500:
                    raise MonthlyHistoryUnavailable()
            except (MonthlyHistoryUnavailable,ValueError,OverflowError):
                refs, text = set(), _UNAVAILABLE
        dedupe = 'task-history:' + event_id
        engine.s.queue(engine.b,payload['sender'],text,dedupe,engine.now(),
                       preferred_transport=payload['channel'])
        if refs:
            row = engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',
                                   (engine.b,dedupe)).fetchone()
            if row:engine.intelligence.protect_outbox(row['id'],refs)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",
                     (engine.b,event_id))
