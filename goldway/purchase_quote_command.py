"""Explicit private purchase scenario -> existing bounded Decimal quote core.

No price/FX lookup, financial/stock records, model, task or purchase execution.
Quantity is the explicit count of identical individual units in one batch.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from .procurement_metrics import Charge, Money, FXRate, quote_purchase
from .promotion_preview_command import _authorized

MAX_INPUT_CHARS=3000
MAX_OUTPUT_UNITS=3500
_REQUEST='рассчитай закупку'
_HEADER='расчет закупки'
_LABELS={'количество':'quantity','валюта итога':'currency','закупка':'purchase',
         'доставка':'freight','комиссия':'commission','прочие сборы':'fee',
         'все сборы перечислены':'fees_complete','затраты не включают друг друга':'separate',
         'курсы':'rates','дата курсов':'rate_date'}
_NAMES=dict(zip(_LABELS.values(),('Количество','Валюта итога','Закупка','Доставка','Комиссия',
    'Прочие сборы','Все сборы перечислены','Затраты не включают друг друга','Курсы','Дата курсов')))
_ROLES=('purchase','freight','commission','fee')
_LITERAL=re.compile(r'(?:0|[1-9][0-9]{0,11})(?:[.,][0-9]{1,6})?')
_BASES={'за штуку':'per_unit','за партию':'batch'}
_TEMPLATE=('Расчёт закупки\nКоличество: [уточнить]\nВалюта итога: [уточнить]\n'
 'Закупка: [уточнить] [валюта]; [база]\nДоставка: [уточнить] [валюта]; [база]\n'
 'Комиссия: [уточнить] [валюта]; [база]\nПрочие сборы: [уточнить] [валюта]; [база]\n'
 'Все сборы перечислены: [уточнить]\nЗатраты не включают друг друга: [уточнить]\n'
 'Курсы: [уточнить]\nДата курсов: [уточнить]')
_NOTICE=('Расчёт не подготовлен. Нужны десять полей без повторов. Количество — целое число '
 'одинаковых штук одной партии. Каждая сумма: число или [уточнить], валюта и «за штуку»/«за партию»; '
 'нули вводятся явно. Сборы перечислены: да/нет; затраты не включают друг друга: да. '
 'Курсы: например USD->RUB=90; при нескольких — через ;. Нужна дата ГГГГ-ММ-ДД. '
 'Для одной валюты: Курсы: нет; Дата курсов: не применяется.\n\n'+_TEMPLATE)
_LIMITS=('Сценарий только по переданным суммам и курсам; цены, расходы и дата курса независимо не проверены. '
 'Полнота и раздельность затрат — ваше указание. Неуказанные налоги и другие расходы не добавлены. '
 'Это не отчёт о фактической себестоимости, не чистая прибыль и не оформленная закупка; учёт не изменён.')


class PurchaseQuoteUnavailable(ValueError):
    def __init__(self):super().__init__('purchase_quote_unavailable')


@dataclass(frozen=True)
class PurchaseDraft:
    text:str
    status:str


def _normal(value):return ' '.join(value.casefold().replace('ё','е').split())


def is_purchase_quote_command(text):
    if type(text) is not str:return False
    first=text[:65].split('\n',1)[0]
    return len(first)<=64 and _normal(first) in (_REQUEST,_HEADER)


def _fields(text):
    if (type(text) is not str or len(text)>MAX_INPUT_CHARS or
            any((ord(c)<32 and c not in '\r\n') or ord(c)==127 for c in text)):
        raise PurchaseQuoteUnavailable()
    lines=text.splitlines()
    if not lines or _normal(lines[0]) not in (_REQUEST,_HEADER) or len(lines)>11:raise PurchaseQuoteUnavailable()
    values={}
    for line in lines[1:]:
        if ':' not in line:raise PurchaseQuoteUnavailable()
        label,value=line.split(':',1);key=_LABELS.get(_normal(label));value=value.strip()
        if key is None or key in values or len(value)>256:raise PurchaseQuoteUnavailable()
        values[key]=None if value in ('','[уточнить]') else value
    return values


def _literal(value,*,positive=False):
    if type(value) is not str or len(value)>19 or _LITERAL.fullmatch(value) is None:raise PurchaseQuoteUnavailable()
    result=value.replace(',','.')
    if positive and Decimal(result)==0:raise PurchaseQuoteUnavailable()
    return result


def _charge(value,kind):
    pieces=value.split(';')
    if len(pieces)!=2:raise PurchaseQuoteUnavailable()
    pair=pieces[0].split();basis=_BASES.get(_normal(pieces[1]))
    if len(pair)!=2 or re.fullmatch(r'[A-Z]{3}',pair[1]) is None or basis is None:raise PurchaseQuoteUnavailable()
    amount=None if pair[0]=='[уточнить]' else _literal(pair[0])
    # Fixed unique economic roles plus explicit non-overlap declaration. No
    # guessed bundling/commission percentage/FX is introduced from free text.
    return Charge(kind,kind,Money(amount,pair[1]),basis)


def _rates(value,output,foreign):
    if _normal(value)=='нет':
        if foreign:raise PurchaseQuoteUnavailable()
        return ()
    parts=value.split(';')
    if not foreign or len(parts)>4:raise PurchaseQuoteUnavailable()
    result=[];seen=set()
    for part in parts:
        match=re.fullmatch(r'([A-Z]{3})->([A-Z]{3})=([^\s=]+)',part.strip())
        if not match:raise PurchaseQuoteUnavailable()
        source,target,rate=match.groups()
        if source not in foreign or target!=output or source in seen:raise PurchaseQuoteUnavailable()
        seen.add(source);result.append(FXRate(source,target,_literal(rate,positive=True)))
    if seen!=foreign:raise PurchaseQuoteUnavailable()
    return tuple(result)


def _shown(value):
    if value is None:return 'неизвестно'
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def build_purchase_quote(text):
    try:
        values=_fields(text)
        missing=tuple(_NAMES[key] for key in _NAMES if values.get(key) is None)
        if missing:return PurchaseDraft('Нужно уточнить: '+', '.join(missing)+'. Нули не подставлены.\n\n'+_TEMPLATE,'incomplete')
        quantity=values['quantity']
        if re.fullmatch(r'[1-9][0-9]{0,8}|1000000000',quantity) is None:raise PurchaseQuoteUnavailable()
        currency=values['currency']
        if re.fullmatch(r'[A-Z]{3}',currency) is None:raise PurchaseQuoteUnavailable()
        complete=_normal(values['fees_complete'])
        if complete not in ('да','нет') or _normal(values['separate'])!='да':raise PurchaseQuoteUnavailable()
        charges=tuple(_charge(values[kind],kind) for kind in _ROLES)
        foreign={charge.money.currency for charge in charges if charge.money.currency!=currency}
        rates=_rates(values['rates'],currency,foreign)
        supplied_date=values['rate_date']
        if rates:
            if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',supplied_date) is None:raise PurchaseQuoteUnavailable()
            date.fromisoformat(supplied_date)  # Supplied date, never today/market verification.
        elif _normal(supplied_date)!='не применяется':raise PurchaseQuoteUnavailable()
        result=quote_purchase(quantity=int(quantity),currency=currency,charges=charges,
                              fx_rates=rates,fees_complete=complete=='да',transaction_kind='purchase')
        lines=['Сценарный расчёт закупки — по вашим данным.',
               'Количество: '+quantity+' одинаковых штук одной партии.',
               'Валюта итога: '+currency+'.',
               'Все сборы перечислены по сообщению: '+complete+'. Раздельность затрат заявлена вами.']
        for component in result['components']:
            basis='за штуку' if component['source_basis']=='per_unit' else 'за партию'
            lines.append(_NAMES[component['kind']]+': '+_shown(component['source_amount'])+' '+component['source_currency']+
                         ', '+basis+' → '+_shown(component['batch_amount'])+' '+currency+' за партию.')
        if rates:
            lines.append('Курсы по сообщению на '+supplied_date+': '+ '; '.join('1 '+r.from_currency+' = '+_shown(r.target_per_source_unit)+' '+r.to_currency for r in rates)+'.')
        else:lines.append('Пересчёт валют не применяется: все компоненты в '+currency+'.')
        if result['status']=='known':
            lines+=['Итого по указанным затратам за партию: '+_shown(result['total_batch'])+' '+currency+'.',
                    'На одну одинаковую штуку: '+_shown(result['per_unit'])+' '+currency+'.']
        else:
            reasons=['сумма '+_NAMES[reason.split(':',1)[1]].lower() if reason.startswith('amount:')
                     else 'полнота перечисления сборов' for reason in result['missing']]
            subtotal=('Сумма известных компонентов (не итог закупки): '+_shown(result['known_components_subtotal'])+' '+currency+'.'
                      if any(component['batch_amount'] is not None for component in result['components'])
                      else 'Сумма известных компонентов: неизвестна — ни одна сумма не указана.')
            lines+=[subtotal,'Итог партии и стоимость штуки: неизвестны; нужно уточнить: '+', '.join(reasons)+'.']
        lines.append(_LIMITS);output='\n'.join(lines)
        if len(output)>3000 or len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise PurchaseQuoteUnavailable()
        return PurchaseDraft(output,result['status'])
    except PurchaseQuoteUnavailable:raise
    except (ValueError,TypeError,ArithmeticError,UnicodeError,KeyError):raise PurchaseQuoteUnavailable() from None


def handle_purchase_quote(engine,event_id,payload):
    if (type(payload) is not dict or not is_purchase_quote_command(payload.get('text'))
            or not engine.s.db.in_transaction):raise PurchaseQuoteUnavailable()
    intelligence=engine.intelligence
    try:scope=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text=_NOTICE
    try:text=build_purchase_quote(payload['text']).text
    except PurchaseQuoteUnavailable:pass
    if _authorized(engine,payload,intelligence)!=scope:raise PurchaseQuoteUnavailable()
    engine.s.queue(engine.b,payload['sender'],text,'purchase-quote:'+event_id,engine.now(),preferred_transport=payload['channel'])
    if _authorized(engine,payload,intelligence)!=scope:raise PurchaseQuoteUnavailable()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
