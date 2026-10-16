"""Explicit private admin agenda preview, never a scheduled dispatch or action."""
from datetime import datetime, timezone


_COMMANDS = frozenset(('/agenda', 'подготовь повестку следующей планёрки',
                       'подготовь повестку следующей планерки'))


def is_agenda_command(text):
    return type(text) is str and ' '.join(text.casefold().split()) in _COMMANDS


def handle_agenda(engine, event_id, payload):
    if type(payload) is not dict or not is_agenda_command(payload.get('text')):
        raise ValueError('agenda_exact_command_required')
    authorized = ('team_intake' in engine.c and engine.c.get('business') == engine.b
                  and engine._operator_turn(payload, 'reply'))
    if authorized:
        from .operational_agenda_adapter import build_agenda_preview, AgendaUnavailable
        refs = set()
        text = 'Черновик повестки недоступен: источник текущих задач не подключён.'
        if engine.intelligence is not None:
            try:
                draft = build_agenda_preview(engine.s, engine.c, payload['sender'],
                    engine.intelligence.index,
                    as_of=datetime.fromtimestamp(engine.now(), timezone.utc), max_items=5)
                refs = set(draft.digest.sources)
                lines = []
                for line in draft.digest.lines:
                    for ref in refs:
                        suffix = ' · ' + ref
                        if line.endswith(suffix):
                            line = line[:-len(suffix)] + ' · реестр задач'
                            break
                    lines.append(line)
                text = '\n'.join(lines)
                if len(text.encode('utf-16-le')) // 2 > 3500:
                    raise AgendaUnavailable('operational_agenda_unavailable')
            except AgendaUnavailable:
                text = 'Черновик повестки сейчас недоступен: текущую выборку и права не удалось подтвердить. Задачи не изменены.'
                refs = set()
        dedupe = 'agenda-preview:' + event_id
        engine.s.queue(engine.b, payload['sender'], text, dedupe, engine.now(),
                       preferred_transport=payload['channel'])
        if refs:
            row = engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',
                                   (engine.b, dedupe)).fetchone()
            if row:engine.intelligence.protect_outbox(row['id'], refs)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",
                     (engine.b, event_id))
