"""Explicit one-item scenario -> existing inventory coverage/flags, no stock I/O."""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re

from .answer_citations import _scope
from .task_card_command import _intelligence_matches
from .inventory_metrics import (UNKNOWN, ItemKey, StockKey, StockKind, StockSnapshot,
    SalesWindow, CoverageState, Thresholds, coverage, stock_flags)

MAX_INPUT_CHARS=4000
MAX_OUTPUT_UNITS=3500
_REQUEST='рассчитай запас товара'
_HEADER='расчет запаса'
_LABELS={'товар':'item','вариант':'variant','склад':'location','партия':'batch','единица':'unit',
    'дата наблюдения':'date','свободный остаток':'stock','продано за период':'sold',
    'длительность периода, дней':'days','дней в расчетном месяце':'month_days',
    'низкий запас, месяцев':'low','затоваривание, месяцев':'over','без продаж, дней':'no_sales'}
_ORDER=tuple(_LABELS.values())
_BASE=_ORDER[:10]
_THRESHOLDS=_ORDER[10:]
_DISPLAY=dict(zip(_ORDER,('Товар','Вариант','Склад','Партия','Единица','Дата наблюдения','Свободный остаток',
    'Продано за период','Длительность периода, дней','Дней в расчётном месяце',
    'Низкий запас, месяцев','Затоваривание, месяцев','Без продаж, дней')))
_LIMITS=dict.fromkeys(_ORDER,32)
_LIMITS.update(item=160,variant=120,location=120,batch=120,unit=40,date=10,days=5,no_sales=5)
_NUMBER=re.compile(r'(?:0|[1-9][0-9]{0,11})(?:[.,][0-9]{1,6})?')
_TEMPLATE='Расчёт запаса\n'+'\n'.join(_DISPLAY[key]+': [уточнить]' for key in _ORDER)
_LIMITATION=('Только сценарий по переданным значениям; данные склада не проверены. '
    'Остаток указан как свободный, продажи — итог одного периода в той же единице и детализации '
    'на одну дату наблюдения. Скорость продаж считается постоянной; будущий спрос не прогнозируется. '
    'Остатки и заказы не изменены.')
_NOTICE=('Расчёт не подготовлен. Проверьте поля, повторы, дату и размер блока. '
    'Числа — неотрицательные, без знаков/степеней, до 12 цифр до запятой и 6 после. '
    'Длительности — целые 1–36600 дней; дней в расчётном месяце — больше 0 и не больше 36600. '
    'Пороги задайте явно; нулевые продажи не заменяют неизвестные.\n\n'+_TEMPLATE)


class StockCoverageUnavailable(ValueError):
    def __init__(self):super().__init__('stock_coverage_preview_unavailable')


@dataclass(frozen=True)
class StockCoverageDraft:
    text:str
    state:str
    missing_fields:tuple[str,...]
    review_required:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


def _normal(value):return ' '.join(value.casefold().replace('ё','е').split())


def is_stock_coverage_command(text):
    if type(text) is not str:return False
    first=text[:65].split('\n',1)[0]
    return len(first)<=64 and _normal(first) in (_REQUEST,_HEADER)


def _parse(text):
    if (type(text) is not str or len(text)>MAX_INPUT_CHARS or
        any((ord(c)<32 and c not in '\r\n') or ord(c)==127 for c in text)):
        raise StockCoverageUnavailable()
    text.encode('utf-8')
    lines=text.splitlines()
    if not lines or _normal(lines[0]) not in (_REQUEST,_HEADER) or len(lines)>14:
        raise StockCoverageUnavailable()
    values={}
    for line in lines[1:]:
        if ':' not in line:raise StockCoverageUnavailable()
        label,value=line.split(':',1);key=_LABELS.get(_normal(label));value=value.strip()
        if key is None or key in values:raise StockCoverageUnavailable()
        unknown=value.casefold() in ('','[уточнить]','неизвестно','[неизвестно]')
        if not unknown and len(value)>_LIMITS[key]:raise StockCoverageUnavailable()
        values[key]=None if unknown else value
    return values


def _number(value):
    if value is None:return UNKNOWN
    if _NUMBER.fullmatch(value) is None:raise StockCoverageUnavailable()
    return Decimal(value.replace(',','.'))


def _days(value):
    if value is None:return None
    if re.fullmatch(r'[1-9][0-9]{0,4}',value) is None or not 1<=int(value)<=36600:
        raise StockCoverageUnavailable()
    return int(value)


def _date(value):
    if value is None:return None
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):return date.fromisoformat(value)
    if re.fullmatch(r'[0-9]{2}\.[0-9]{2}\.[0-9]{4}',value):return datetime.strptime(value,'%d.%m.%Y').date()
    raise StockCoverageUnavailable()


def _shown(value):return '[неизвестно]' if value is None or value is UNKNOWN else str(value)


def _quantity(value):return '[неизвестно]' if value is UNKNOWN else format(value,'f')


def _flag(value):return 'неизвестно' if value is UNKNOWN else 'да' if value else 'нет'


def build_stock_coverage_preview(text, *, business, event_id):
    try:
        values=_parse(text)
        numbers={key:_number(values.get(key)) for key in ('stock','sold','month_days','low','over')}
        days,no_sales=_days(values.get('days')),_days(values.get('no_sales'))
        observed=_date(values.get('date'))
        if numbers['month_days'] is not UNKNOWN and not 0<numbers['month_days']<=36600:
            raise StockCoverageUnavailable()
        if numbers['low'] is not UNKNOWN and numbers['over'] is not UNKNOWN and numbers['low']>=numbers['over']:
            raise StockCoverageUnavailable()
        missing=tuple(key for key in _BASE if values.get(key) is None)
        missing_thresholds=tuple(key for key in _THRESHOLDS if values.get(key) is None)
        result,flags=None,None
        # Required typed constructors receive only explicit item/unit/date/day
        # convention. UNKNOWN quantities/dimensions remain UNKNOWN in real core.
        if all(values.get(key) is not None for key in ('item','unit','date','days','month_days')):
            if type(business) is not str or not 0<len(business)<=128 or type(event_id) is not str or not 0<len(event_id)<=256:
                raise StockCoverageUnavailable()
            key=StockKey(ItemKey(values['item'],values.get('variant') if values.get('variant') is not None else UNKNOWN),
                         values.get('location') if values.get('location') is not None else UNKNOWN,
                         values.get('batch') if values.get('batch') is not None else UNKNOWN)
            ref=hashlib.sha256(json.dumps([business,event_id]).encode()).hexdigest()
            stock=StockSnapshot(key,numbers['stock'],values['unit'],observed,'supplied:free:'+ref,StockKind.FREE)
            sales=SalesWindow(key,numbers['sold'],days,values['unit'],observed,'supplied:sales:'+ref)
            if missing_thresholds:
                result=coverage(stock,sales,month_days=numbers['month_days'])
            else:
                thresholds=Thresholds(numbers['low'],numbers['over'],no_sales)
                flags=stock_flags(stock,sales,month_days=numbers['month_days'],thresholds=thresholds)
                result=flags.coverage
        lines=['Сценарный запас товара — по вашим значениям.',
            'Товар: '+_shown(values.get('item')), 'Вариант: '+_shown(values.get('variant')),
            'Склад: '+_shown(values.get('location')), 'Партия: '+_shown(values.get('batch')),
            'Единица: '+_shown(values.get('unit')), 'Дата наблюдения: '+_shown(observed),
            'Свободный остаток: '+_quantity(numbers['stock']), 'Продано за период: '+_quantity(numbers['sold']),
            'Длительность периода, дней: '+_shown(days), 'Дней в расчётном месяце: '+_quantity(numbers['month_days'])]
        state='incomplete' if result is None else result.state.value
        if result is not None and result.state is CoverageState.FINITE:
            lines.append('Расчётный запас: '+format(result.months,'f')+' мес. по скорости указанных продаж.')
        elif result is not None and result.state is CoverageState.INFINITE:
            lines.append('Положительный остаток и нулевые продажи за указанный период: срок исчерпания не определяется. Это не доказательство бесконечного реального запаса.')
        elif result is not None and 'zero_stock_and_zero_sales' in result.reasons:
            lines.append('Остаток и продажи равны нулю: запас в месяцах неизвестен (0/0).')
        else:lines.append('Запас в месяцах: неизвестен — недостаточно явно переданных данных или детализации.')
        if flags is not None:
            lines.extend(['По заданным порогам: низкий запас — '+_flag(flags.low_stock)+
                '; затоваривание — '+_flag(flags.overstock)+'; отсутствие продаж — '+_flag(flags.no_sales)+'.',
                'Пороги сценария: низкий запас < '+_quantity(numbers['low'])+' мес.; затоваривание > '+
                _quantity(numbers['over'])+' мес.; без продаж ≥ '+str(no_sales)+' дн.'])
        else:
            lines.append('Флаги низкого запаса, затоваривания и длительного отсутствия продаж не рассчитаны.')
            if missing_thresholds:lines.append('Не заданы пороги: '+', '.join(_DISPLAY[key] for key in missing_thresholds)+'.')
        if missing:lines.append('Нужно уточнить: '+', '.join(_DISPLAY[key] for key in missing)+'.')
        lines.extend(['Формула: остаток × дней периода / (продано × дней в расчётном месяце).',_LIMITATION])
        output='\n'.join(lines)
        if len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise StockCoverageUnavailable()
        return StockCoverageDraft(output,state,missing)
    except StockCoverageUnavailable:raise
    except (ValueError,TypeError,ArithmeticError,UnicodeError):raise StockCoverageUnavailable() from None


def _authorized(engine,payload,intelligence):
    if (engine.c.get('business')!=engine.b or 'team_intake' not in engine.c or
        not engine._operator_turn(payload,'reply') or not _intelligence_matches(engine,intelligence)):
        raise StockCoverageUnavailable()
    return _scope(engine.c,payload)[0]


def handle_stock_coverage(engine,event_id,payload):
    if (type(payload) is not dict or not is_stock_coverage_command(payload.get('text'))
        or not engine.s.db.in_transaction):raise StockCoverageUnavailable()
    intelligence=engine.intelligence
    try:scope=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text=_NOTICE
    try:
        if len(payload['text'])>MAX_INPUT_CHARS:raise StockCoverageUnavailable()
        text=_TEMPLATE if _normal(payload['text'])==_REQUEST else build_stock_coverage_preview(payload['text'],business=engine.b,event_id=event_id).text
    except StockCoverageUnavailable:pass
    if _authorized(engine,payload,intelligence)!=scope:raise StockCoverageUnavailable()
    engine.s.queue(engine.b,payload['sender'],text,'stock-coverage:'+event_id,engine.now(),None,
                   preferred_transport=payload['channel'])
    if _authorized(engine,payload,intelligence)!=scope:raise StockCoverageUnavailable()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
