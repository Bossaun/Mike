"""Explicit private admin values -> existing recount core -> read-only preview.

Supplied book/physical counts are not independently verified observations. No
warehouse action, order gating, external source lookup or stock change exists.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re

from .inventory_metrics import (UNKNOWN, ItemKey, StockKey, StockKind, StockSnapshot,
                                RecountState, recount)

MAX_INPUT_CHARS = 4000
MAX_OUTPUT_UNITS = 1600
_REQUEST = 'сверь пересчёт товара'
_HEADER = 'пересчёт товара'
_LABELS = {'товар': 'item', 'вариант': 'variant', 'склад': 'location', 'партия': 'batch',
           'единица': 'unit', 'дата': 'date', 'учёт': 'book', 'факт': 'physical'}
_ORDER = tuple(_LABELS.values())
_NAMES = {value: key for key, value in _LABELS.items()}
_LIMITS = {'item': 160, 'variant': 120, 'location': 120, 'batch': 120,
           'unit': 40, 'date': 80, 'book': 32, 'physical': 32}
_QUANTITY = re.compile(r'(?:0|[1-9][0-9]{0,11})(?:[.,][0-9]{1,6})?')
_TEMPLATE = ('Пересчёт товара\nТовар: [уточнить]\nВариант: [уточнить]\nСклад: [уточнить]\n'
             'Партия: [уточнить]\nЕдиница: [уточнить]\nДата: [уточнить]\n'
             'Учёт: [уточнить]\nФакт: [уточнить]')
_NOTICE = ('Сверка не подготовлена. Проверьте поля, повторы и размер блока. '
           'Учёт и Факт: неотрицательные числа без разделителей тысяч; неизвестное — [уточнить].')
_LIMITATION = 'Только переданные данные; учёт и подсчёт независимо не проверены. Остатки не изменены.'


class RecountUnavailable(ValueError):
    def __init__(self):
        super().__init__('recount_preview_unavailable')


@dataclass(frozen=True)
class RecountPreview:
    text: str
    state: str
    missing_fields: tuple[str, ...]
    review_required: bool = field(default=True, init=False)
    live_authorized: bool = field(default=False, init=False)


def _normalize(value):
    return ' '.join(value.casefold().replace('ё', 'е').split())


def is_recount_command(text):
    if type(text) is not str:
        return False
    return (_normalize(text) == _normalize(_REQUEST)
            or _normalize(text.split('\n', 1)[0]) == _normalize(_HEADER))


def _parse(text):
    if (type(text) is not str or len(text) > MAX_INPUT_CHARS
            or any((ord(c) < 32 and c not in '\r\n') or ord(c) == 127 for c in text)):
        raise RecountUnavailable()
    lines = text.splitlines()
    if not lines or _normalize(lines[0]) != _normalize(_HEADER) or len(lines) > 9:
        raise RecountUnavailable()
    labels = {_normalize(k): v for k, v in _LABELS.items()}
    result = {}
    for line in lines[1:]:
        if ':' not in line:
            raise RecountUnavailable()
        label, value = line.split(':', 1)
        key = labels.get(_normalize(label))
        if key is None or key in result:
            raise RecountUnavailable()
        value = value.strip()
        if len(value) > _LIMITS[key]:
            raise RecountUnavailable()
        result[key] = None if value.casefold() in ('', '[уточнить]') else value
    return result


def _number(value):
    if value is None:
        return UNKNOWN
    if not _QUANTITY.fullmatch(value):
        raise RecountUnavailable()
    return Decimal(value.replace(',', '.'))


def _date(value):
    if value is None:
        return None
    # Explicit full calendar date only. Never infer a year, relative date or today.
    for pattern, parser in ((r'[0-9]{4}-[0-9]{2}-[0-9]{2}', date.fromisoformat),
                            (r'[0-9]{2}\.[0-9]{2}\.[0-9]{4}', lambda x: datetime.strptime(x, '%d.%m.%Y').date())):
        if re.fullmatch(pattern, value):
            try:
                return parser(value)
            except ValueError:
                pass
    return None


def _shown(value):
    return '[уточнить]' if value is None else value


def _count(value):
    return '[неизвестно]' if value is UNKNOWN else format(value, 'f')


def build_recount_preview(text, *, business, event_id):
    try:
        values = _parse(text)
        book, physical = _number(values.get('book')), _number(values.get('physical'))
        observed = _date(values.get('date'))
        missing = tuple(key for key in _ORDER if values.get(key) is None or (key == 'date' and observed is None))
        state, delta = 'incomplete', UNKNOWN
        if values.get('item') is not None and values.get('unit') is not None and observed is not None:
            key = StockKey(ItemKey(values['item'], values.get('variant') if values.get('variant') is not None else UNKNOWN),
                           values.get('location') if values.get('location') is not None else UNKNOWN,
                           values.get('batch') if values.get('batch') is not None else UNKNOWN)
            anchor = hashlib.sha256(json.dumps([business, event_id]).encode()).hexdigest()
            b = StockSnapshot(key, book, values['unit'], observed, 'supplied:book:'+anchor, StockKind.BOOK)
            p = StockSnapshot(key, physical, values['unit'], observed, 'supplied:physical:'+anchor, StockKind.PHYSICAL)
            result, = recount((b,), (p,))
            state, delta = result.state.value, result.delta
        lines = ['Сверка переданных чисел — черновик.',
                 'Товар: '+_shown(values.get('item')),
                 'Вариант: '+_shown(values.get('variant')),
                 'Склад по сообщению: '+_shown(values.get('location')),
                 'Партия: '+_shown(values.get('batch')),
                 'Единица: '+_shown(values.get('unit')),
                 'Дата по сообщению: '+_shown(values.get('date')),
                 'Учёт по сообщению: '+_count(book),
                 'Факт по сообщению: '+_count(physical)]
        if state == RecountState.COMPARABLE.value:
            lines.append('Разница (факт − учёт): '+('0' if delta == 0 else format(delta, '+f')))
        else:
            lines.append('Разница: неизвестна — недостаточно данных для сопоставления.')
        if missing:
            lines.append('Нужно уточнить: '+', '.join(_NAMES[key] for key in missing)+'.')
        if values.get('date') is not None and observed is None:
            lines.append('Нужна явная дата: ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.')
        lines.append(_LIMITATION)
        output = '\n'.join(lines)
        if len(output.encode('utf-16-le'))//2 > MAX_OUTPUT_UNITS:
            raise RecountUnavailable()
        return RecountPreview(output, state, missing)
    except RecountUnavailable:
        raise
    except (ValueError, TypeError, UnicodeError):
        raise RecountUnavailable() from None


def handle_recount(engine, event_id, payload):
    if (type(payload) is not dict or not is_recount_command(payload.get('text'))
            or not engine.s.db.in_transaction):
        raise RecountUnavailable()
    authorized = ('team_intake' in engine.c and engine.c.get('business') == engine.b
                  and engine._operator_turn(payload, 'reply'))
    if authorized:
        text = _NOTICE
        try:
            if len(payload['text']) > MAX_INPUT_CHARS:
                raise RecountUnavailable()
            if _normalize(payload['text']) == _normalize(_REQUEST):
                text = _TEMPLATE
            else:
                text = build_recount_preview(payload['text'], business=engine.b, event_id=event_id).text
        except RecountUnavailable:
            pass
        engine.s.queue(engine.b, payload['sender'], text, 'recount-preview:'+event_id,
                       engine.now(), preferred_transport=payload['channel'])
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?", (engine.b, event_id))
