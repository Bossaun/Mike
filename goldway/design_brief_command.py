"""Private administrator supplied fields -> existing review-only design brief.

No NLP, knowledge lookup, assets retrieval, task creation or designer delivery.
Material descriptions remain unverified text; no external Asset is fabricated.
"""
from datetime import datetime
import hashlib
import json
import re

from .marketing_drafts import DesignBrief, DraftError, EditableDraft, render_design_brief

MAX_INPUT_CHARS = 4000
MAX_OUTPUT_UNITS = 3500
_REQUESTS = frozenset(('подготовь тз дизайнеру', 'подготовь тз для дизайнера'))
_HEADER = 'тз дизайнеру'
_LABELS = {'цель': 'goal', 'аудитория': 'audience', 'формат': 'format',
           'срок': 'deadline', 'материалы': 'materials'}
_LIMITS = {'goal': 1000, 'audience': 1000, 'format': 100, 'deadline': 200, 'materials': 1000}
_UNKNOWN = frozenset(('', '[уточнить]'))
_TEMPLATE = ('ТЗ дизайнеру\nЦель: [уточнить]\nАудитория: [уточнить]\n'
             'Формат: [уточнить]\nСрок: [уточнить]\nМатериалы: [уточнить]')
_NOTICE = ('Черновик не подготовлен. Проверьте названия полей, повторы и размер блока: '
           'Цель, Аудитория, Формат, Срок, Материалы.')
_AWARE_ISO = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'
                        r'(?::[0-9]{2})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')


class BriefUnavailable(ValueError):
    def __init__(self):
        super().__init__('design_brief_unavailable')


def _normalize(value):
    return ' '.join(value.casefold().split())


def is_design_brief_command(text):
    if type(text) is not str:
        return False
    if _normalize(text) in _REQUESTS:
        return True
    return _normalize(text.split('\n', 1)[0]) == _HEADER


def _fields(text):
    if (type(text) is not str or len(text) > MAX_INPUT_CHARS
            or any((ord(c) < 32 and c not in '\r\n') or ord(c) == 127 for c in text)):
        raise BriefUnavailable()
    lines = text.splitlines()
    if not lines or _normalize(lines[0]) != _HEADER or len(lines) > 6:
        raise BriefUnavailable()
    result = {}
    for line in lines[1:]:
        if ':' not in line:
            raise BriefUnavailable()
        label, value = line.split(':', 1)
        field = _LABELS.get(_normalize(label))
        if field is None or field in result:
            raise BriefUnavailable()
        value = value.strip()
        if len(value) > _LIMITS[field]:
            raise BriefUnavailable()
        result[field] = None if value.casefold() in _UNKNOWN else value
    return result


def build_brief(text, *, business, event_id):
    """Literal field parsing only; absent/placeholder values remain unknown.

    Only a complete explicit ISO datetime with offset can become a typed deadline.
    Other deadline text is preserved separately for clarification, never guessed.
    No listed material is treated as an available/confirmed file or source.
    """
    try:
        fields = _fields(text)
        deadline, supplied_deadline = None, fields.get('deadline')
        if supplied_deadline is not None and _AWARE_ISO.fullmatch(supplied_deadline):
            try:
                deadline = datetime.fromisoformat(supplied_deadline.replace('Z', '+00:00'))
            except ValueError:
                pass
        unknown = frozenset({'assets'} | {field for field in ('goal', 'audience', 'format')
                             if fields.get(field) is None} | ({'deadline'} if deadline is None else set()))
        identifier = 'brief:'+hashlib.sha256(json.dumps([business, event_id]).encode()).hexdigest()
        brief = DesignBrief(identifier, fields.get('goal'), fields.get('audience'), fields.get('format'),
                            deadline, (), False, unknown)
        rendered = render_design_brief(brief)
        lines = []
        for line in rendered.text.splitlines():
            if line == 'Материалы:':
                line = 'Подтверждённые материалы:'
            elif line == '[не предоставлены]':
                line = '[доступность не подтверждена]'
            elif line.startswith('Неизвестные поля: '):
                labels = {'goal': 'цель', 'audience': 'аудитория', 'format': 'формат',
                          'deadline': 'срок', 'assets': 'доступность материалов'}
                line = 'Нужно уточнить: '+', '.join(labels[field] for field in sorted(unknown))
            lines.append(line)
        if deadline is None and supplied_deadline is not None:
            lines.append('Срок из сообщения (требует уточнения): '+supplied_deadline)
        if fields.get('materials') is not None:
            lines.append('Описания материалов из сообщения (не проверены): '+fields['materials'])
        result = EditableDraft('\n'.join(lines), ())
        if len(result.text.encode('utf-16-le'))//2 > MAX_OUTPUT_UNITS:
            raise BriefUnavailable()
        return result
    except BriefUnavailable:
        raise
    except (DraftError, ValueError, TypeError, UnicodeError, OverflowError):
        raise BriefUnavailable() from None


def handle_design_brief(engine, event_id, payload):
    if (type(payload) is not dict or not is_design_brief_command(payload.get('text'))
            or not engine.s.db.in_transaction):
        raise BriefUnavailable()
    authorized = ('team_intake' in engine.c and engine.c.get('business') == engine.b
                  and engine._operator_turn(payload, 'reply'))
    if authorized:
        text = _NOTICE
        try:
            if len(payload['text']) > MAX_INPUT_CHARS:
                raise BriefUnavailable()
            if _normalize(payload['text']) in _REQUESTS:
                text = _TEMPLATE
            else:
                draft = build_brief(payload['text'], business=engine.b, event_id=event_id)
                text = draft.text
        except BriefUnavailable:
            pass
        engine.s.queue(engine.b, payload['sender'], text, 'design-brief:'+event_id,
                       engine.now(), preferred_transport=payload['channel'])
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",
                     (engine.b, event_id))
