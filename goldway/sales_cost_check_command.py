"""Small supplied sales sample -> existing Decimal cost flags, never a ledger.

Each dated row is explicitly a separate operation's totals in one currency and
same volume. No real order IDs, completeness inference, FX, or model call.
"""
from dataclasses import dataclass
from datetime import date
import re

from .finance_metrics import Sale, cost_flags
from .promotion_preview_command import _authorized

MAX_INPUT_CHARS=2500
MAX_ROWS=12
MAX_OUTPUT_UNITS=3500
_HEADERS=('проверь продажи','проверка продаж')
_NUMBER=re.compile(r'(?:0|[1-9][0-9]{0,11})(?:[.,][0-9]{1,6})?')
_TEMPLATE=('Проверь продажи\nВалюта: [уточнить]\n'
           '[ГГГГ-ММ-ДД]; [выручка]; [себестоимость или [неизвестно]]; [бартер да/нет]')
_NOTICE=('Проверка не выполнена. После явной валюты нужны 1–12 строк: '
         'ГГГГ-ММ-ДД; выручка; себестоимость или [неизвестно]; бартер да/нет. '
         'Суммы — итоги одной операции за одинаковый объём; неотрицательные числа '
         'без разделителей тысяч, до 12 цифр до запятой и 6 после. '
         'Ошибочные строки не пропускаются.\n\n'+_TEMPLATE)
_LIMITATION=('Только переданные строки, не проверка всех продаж компании. '
             'Каждая строка считается отдельной операцией; одинаковые строки не объединены. '
             'Даты, бартер и суммы — по вашему сообщению, независимо не проверены. '
             'Прибыль, маржа, налоги и полнота учёта не оценивались; учёт не изменён.')


class SalesCheckUnavailable(ValueError):
    def __init__(self):super().__init__('sales_cost_check_unavailable')


@dataclass(frozen=True)
class SalesCheckDraft:
    text:str
    row_count:int


def _normal(value):return ' '.join(value.casefold().replace('ё','е').split())


def is_sales_cost_check_command(text):
    if type(text) is not str:return False
    first=text[:65].split('\n',1)[0]
    return len(first)<=64 and _normal(first) in _HEADERS


def _amount(value):
    if len(value)>19 or _NUMBER.fullmatch(value) is None:raise SalesCheckUnavailable()
    return value.replace(',','.')


def _records(text):
    if (type(text) is not str or len(text)>MAX_INPUT_CHARS
            or any((ord(c)<32 and c not in '\r\n') or ord(c)==127 for c in text)):
        raise SalesCheckUnavailable()
    lines=text.splitlines()
    if not 3<=len(lines)<=MAX_ROWS+2 or _normal(lines[0]) not in _HEADERS:raise SalesCheckUnavailable()
    label,separator,currency=lines[1].partition(':');currency=currency.strip()
    if not separator or _normal(label)!='валюта' or re.fullmatch(r'[A-Z]{3}',currency) is None:raise SalesCheckUnavailable()
    records=[]
    for number,line in enumerate(lines[2:],1):
        if len(line)>128:raise SalesCheckUnavailable()
        pieces=[part.strip() for part in line.split(';')]
        if len(pieces)!=4:raise SalesCheckUnavailable()
        day,revenue,cogs,barter=pieces
        if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',day) is None:raise SalesCheckUnavailable()
        occurred=date.fromisoformat(day)
        revenue=_amount(revenue)
        cogs=None if cogs=='[неизвестно]' else _amount(cogs)
        barter=_normal(barter)
        if barter not in ('да','нет'):raise SalesCheckUnavailable()
        # Local ephemeral identity only; no customer/order ID is accepted,
        # inferred or displayed, and identical supplied rows remain separate.
        records.append(Sale('row:'+str(number),occurred,currency,revenue,cogs,barter=='да'))
    return tuple(records),currency


def build_sales_cost_check(text):
    try:
        if type(text) is str and len(text)<=64 and _normal(text) in _HEADERS:
            return SalesCheckDraft(_NOTICE,0)
        records,currency=_records(text)
        # Whole input must validate before the accepted core sees any row.
        flags=cost_flags(records,currency=currency)
        by_row={}
        for flag in flags:
            if flag.sale_id not in {record.id for record in records} or flag.kind not in ('missing_cost','under_cost') or flag.sale_id in by_row:
                raise SalesCheckUnavailable()
            by_row[flag.sale_id]=flag.kind
        lines=['Проверка переданных продаж — по вашим данным.',
               'Строк: '+str(len(records))+'. Валюта: '+currency+'.',
               'Выручка и себестоимость каждой строки — итоги за одинаковый объём одной операции.']
        for number,record in enumerate(records,1):
            flag=by_row.get(record.id)
            if flag=='missing_cost':label='себестоимость неизвестна; сравнение с выручкой невозможно.'
            elif flag=='under_cost':label='выручка ниже указанной себестоимости.'
            elif record.barter:label='бартер заявлен; сравнение выручки с себестоимостью не применялось.'
            else:label='флагов для переданной выручки и известной себестоимости нет.'
            if record.barter and flag=='missing_cost':label+=' Бартер заявлен.'
            lines.append('Строка '+str(number)+': '+label)
        lines.append(_LIMITATION);output='\n'.join(lines)
        if len(output)>3000 or len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise SalesCheckUnavailable()
        return SalesCheckDraft(output,len(records))
    except SalesCheckUnavailable:raise
    except (ValueError,TypeError,ArithmeticError,UnicodeError,AttributeError):raise SalesCheckUnavailable() from None


def handle_sales_cost_check(engine,event_id,payload):
    if (type(payload) is not dict or not is_sales_cost_check_command(payload.get('text'))
            or not engine.s.db.in_transaction):raise SalesCheckUnavailable()
    intelligence=engine.intelligence
    try:scope=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text=_NOTICE
    try:text=build_sales_cost_check(payload['text']).text
    except SalesCheckUnavailable:pass
    if _authorized(engine,payload,intelligence)!=scope:raise SalesCheckUnavailable()
    engine.s.queue(engine.b,payload['sender'],text,'sales-cost-check:'+event_id,engine.now(),preferred_transport=payload['channel'])
    if _authorized(engine,payload,intelligence)!=scope:raise SalesCheckUnavailable()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
