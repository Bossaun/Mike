"""One supplied stock/purchase-plan what-if, never a stock receipt or advice."""
from dataclasses import dataclass
import hashlib
import json

from . import stock_coverage_command as current
from .inventory_metrics import (UNKNOWN, ItemKey, StockKey, StockKind, StockSnapshot,
    SalesWindow, PurchasePlan, Thresholds, CoverageState, procurement_preview,
    coverage, stock_flags)
from .promotion_preview_command import _authorized

MAX_INPUT_CHARS=4000
MAX_OUTPUT_UNITS=3500
_REQUEST='проверь план закупки'
_HEADER='план закупки'
_LABELS=dict(current._LABELS,**{'плановое количество':'plan'})
_ORDER=(*current._ORDER[:7],'plan',*current._ORDER[7:])
_DISPLAY=dict(current._DISPLAY,plan='Плановое количество')
_TEMPLATE='План закупки\n'+'\n'.join(_DISPLAY[key]+': [уточнить]' for key in _ORDER)
_NOTICE=('Сценарий не рассчитан. Нужны явные поля шаблона без повторов: одна и та же '
    'детализация/единица/дата для остатка, плана и продаж. Неизвестное — [уточнить], ноль — 0. '
    'Дни/пороги задайте явно; без подстановок месяца, резерва или спроса.\n\n'+_TEMPLATE)
_LIMITATION=('Только условное добавление количества плана к указанному свободному остатку '
    'на ту же дату, в той же единице и детализации. Продажи, резервы и потери между точками '
    'не моделируются; их фактическое отсутствие не подтверждено. Историческая скорость '
    'условно неизменна, это не прогноз спроса. Данные склада не проверены; приход, наличие '
    'товара и доступный к заказу объём не подтверждены. Это не рекомендация закупать или '
    'отменять закупку. Остатки и заказы не изменены.')


class PurchaseStockUnavailable(ValueError):
    def __init__(self):super().__init__('purchase_stock_preview_unavailable')


@dataclass(frozen=True)
class PurchaseStockDraft:
    text:str
    before_state:str
    after_state:str


def is_purchase_stock_command(text):
    if type(text) is not str:return False
    first=text[:65].split('\n',1)[0]
    return len(first)<=64 and current._normal(first) in (_REQUEST,_HEADER)


def _parse(text):
    if (type(text) is not str or len(text)>MAX_INPUT_CHARS or
        any((ord(c)<32 and c not in '\r\n') or ord(c)==127 for c in text)):
        raise PurchaseStockUnavailable()
    text.encode('utf-8');lines=text.splitlines()
    if not lines or current._normal(lines[0]) not in (_REQUEST,_HEADER) or len(lines)>15:raise PurchaseStockUnavailable()
    values={}
    for line in lines[1:]:
        if ':' not in line:raise PurchaseStockUnavailable()
        label,value=line.split(':',1);key=_LABELS.get(current._normal(label));value=value.strip()
        if key is None or key in values:raise PurchaseStockUnavailable()
        unknown=value.casefold() in ('','[уточнить]','неизвестно','[неизвестно]')
        if not unknown and len(value)>current._LIMITS.get(key,32):raise PurchaseStockUnavailable()
        values[key]=None if unknown else value
    return values


def _coverage_line(label,result):
    if result is not None and result.state is CoverageState.FINITE:
        return label+': '+format(result.months,'f')+' мес. при скорости переданных продаж.'
    if result is not None and result.state is CoverageState.INFINITE:
        return label+': положительный остаток и нулевые продажи; срок исчерпания не определяется, не реальный бесконечный запас.'
    if result is not None and 'zero_stock_and_zero_sales' in result.reasons:
        return label+': неизвестно — остаток0/продажи0 (0/0).'
    return label+': неизвестно — нет достаточных данных/детализации.'


def _flags_line(label,flags):
    return label+': низкий запас — '+current._flag(flags.low_stock)+'; затоваривание — '+current._flag(flags.overstock)+'; отсутствие продаж — '+current._flag(flags.no_sales)+'.'


def build_purchase_stock_preview(text,*,business,event_id):
    try:
        values=_parse(text)
        numbers={key:current._number(values.get(key)) for key in ('stock','plan','sold','month_days','low','over')}
        days,no_sales=current._days(values.get('days')),current._days(values.get('no_sales'))
        observed=current._date(values.get('date'))
        if numbers['month_days'] is not UNKNOWN and not 0<numbers['month_days']<=36600:raise PurchaseStockUnavailable()
        if numbers['low'] is not UNKNOWN and numbers['over'] is not UNKNOWN and numbers['low']>=numbers['over']:raise PurchaseStockUnavailable()
        before=after=None;before_flags=after_flags=None;expected=UNKNOWN
        required=('item','unit','date','days','month_days')
        missing=tuple(key for key in _ORDER if values.get(key) is None)
        missing_thresholds=tuple(key for key in current._THRESHOLDS if values.get(key) is None)
        if all(values.get(key) is not None for key in required):
            if type(business) is not str or not 0<len(business)<=128 or type(event_id) is not str or not 0<len(event_id)<=256:raise PurchaseStockUnavailable()
            key=StockKey(ItemKey(values['item'],values.get('variant') if values.get('variant') is not None else UNKNOWN),
                values.get('location') if values.get('location') is not None else UNKNOWN,
                values.get('batch') if values.get('batch') is not None else UNKNOWN)
            anchor=hashlib.sha256(json.dumps([business,event_id]).encode()).hexdigest()
            stock=StockSnapshot(key,numbers['stock'],values['unit'],observed,'supplied:free:'+anchor,StockKind.FREE)
            plan=PurchasePlan(key,numbers['plan'],values['unit'],observed,'supplied:plan:'+anchor)
            sales=SalesWindow(key,numbers['sold'],days,values['unit'],observed,'supplied:sales:'+anchor)
            expected=procurement_preview(stock,plan).expected_free_units
            hypothetical=StockSnapshot(key,expected,values['unit'],observed,'supplied:hypothetical:'+anchor,StockKind.FREE)
            if missing_thresholds:
                before=coverage(stock,sales,month_days=numbers['month_days'])
                after=coverage(hypothetical,sales,month_days=numbers['month_days'])
            else:
                thresholds=Thresholds(numbers['low'],numbers['over'],no_sales)
                before_flags=stock_flags(stock,sales,month_days=numbers['month_days'],thresholds=thresholds)
                after_flags=stock_flags(hypothetical,sales,month_days=numbers['month_days'],thresholds=thresholds)
                before,after=before_flags.coverage,after_flags.coverage
        lines=['План закупки — моментальный условный сценарий по вашим данным.',
            'Товар: '+current._shown(values.get('item'))+'; вариант: '+current._shown(values.get('variant'))+'.',
            'Склад: '+current._shown(values.get('location'))+'; партия: '+current._shown(values.get('batch'))+'.',
            'Единица: '+current._shown(values.get('unit'))+'; дата по сообщению: '+current._shown(observed)+'.',
            'Свободный остаток по сообщению: '+current._quantity(numbers['stock'])+'.',
            'Условно добавить из плана: '+current._quantity(numbers['plan'])+'.',
            'Условный остаток после добавления: '+current._quantity(expected)+'.',
            'Продано по сообщению: '+current._quantity(numbers['sold'])+' за '+current._shown(days)+' дн.; расчётный месяц: '+current._quantity(numbers['month_days'])+' дн.',
            _coverage_line('Покрытие до',before),_coverage_line('Покрытие после',after)]
        if before_flags is not None:
            lines.extend([_flags_line('По заданным порогам до',before_flags),_flags_line('По заданным порогам после',after_flags),
                'Пороги сценария: низкий < '+current._quantity(numbers['low'])+' мес.; затоваривание > '+current._quantity(numbers['over'])+' мес.; без продаж ≥ '+str(no_sales)+' дн.'])
        else:lines.append('Флаги до/после не рассчитаны; не заданы все необходимые пороги/базовые данные.')
        if missing:lines.append('Нужно уточнить: '+', '.join(_DISPLAY[key] for key in missing)+'.')
        lines.extend(['Условие формулы покрытия: остаток × дней периода / (продано × дней в расчётном месяце).',_LIMITATION])
        output='\n'.join(lines)
        if len(output)>3000 or len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise PurchaseStockUnavailable()
        return PurchaseStockDraft(output,'incomplete' if before is None else before.state.value,'incomplete' if after is None else after.state.value)
    except PurchaseStockUnavailable:raise
    except (ValueError,TypeError,ArithmeticError,UnicodeError):raise PurchaseStockUnavailable() from None


def handle_purchase_stock(engine,event_id,payload):
    if (type(payload) is not dict or not is_purchase_stock_command(payload.get('text'))
        or not engine.s.db.in_transaction):raise PurchaseStockUnavailable()
    intelligence=engine.intelligence
    try:scope=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text=_NOTICE
    try:
        if len(payload['text'])<=64 and current._normal(payload['text']) in (_REQUEST,_HEADER):
            text=_TEMPLATE
        else:text=build_purchase_stock_preview(payload['text'],business=engine.b,event_id=event_id).text
    except PurchaseStockUnavailable:pass
    if _authorized(engine,payload,intelligence)!=scope:raise PurchaseStockUnavailable()
    engine.s.queue(engine.b,payload['sender'],text,'purchase-stock:'+event_id,engine.now(),preferred_transport=payload['channel'])
    if _authorized(engine,payload,intelligence)!=scope:raise PurchaseStockUnavailable()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
