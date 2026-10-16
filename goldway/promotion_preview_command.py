"""Private administrator's explicit scenario -> existing Decimal promotion core.

No financial records, sources, lookup, model call, task write or launch action.
Amounts are totals for one supplied operation in one explicit currency.
"""
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import re

from .finance_metrics import preview_promotion
from .answer_citations import _scope

MAX_INPUT_CHARS=2000
MAX_OUTPUT_UNITS=3500
_REQUEST='рассчитай акцию'
_HEADER='расчет акции'
_LABELS={'валюта':'currency','выручка без скидки':'original_revenue','себестоимость':'cogs',
         'скидка %':'discount_pct','комиссия %':'fee_pct','фиксированная комиссия':'fixed_fee'}
_ORDER=tuple(_LABELS.values())
_DISPLAY=dict(zip(_ORDER,('Валюта','Выручка без скидки','Себестоимость','Скидка %','Комиссия %','Фиксированная комиссия')))
_LITERAL=re.compile(r'(?:0|[1-9][0-9]{0,11})(?:[.,][0-9]{1,6})?')
_TEMPLATE=('Расчёт акции\nВалюта: [уточнить]\nВыручка без скидки: [уточнить]\n'
           'Себестоимость: [уточнить]\nСкидка %: [уточнить]\nКомиссия %: [уточнить]\n'
           'Фиксированная комиссия: [уточнить]')
_NOTICE=('Расчёт не выполнен. Нужны ровно шесть полей шаблона без повторов. '
         'Все суммы — итоги одной операции в указанной валюте; нули укажите явно. '
         'Числа: без знаков, валютных суффиксов и разделителей тысяч; до 12 цифр до '
         'запятой и 6 после, проценты 0–100.\n\n'+_TEMPLATE)
_LIMITATION=('Сценарий только по переданным числам; не финансовый отчёт компании. '
             'Все суммы — за одну операцию и один объём в одной валюте. '
             'Налоги, логистика и другие неуказанные затраты не учтены; это не чистая прибыль '
             'и не рекомендация запускать акцию.')


class PromotionUnavailable(ValueError):
    def __init__(self):super().__init__('promotion_preview_unavailable')


@dataclass(frozen=True)
class PromotionDraft:
    text:str
    missing_fields:tuple[str,...]=()


def _normal(value):return ' '.join(value.casefold().replace('ё','е').split())


def is_promotion_command(text):
    if type(text) is not str:return False
    # Only the first line is examined before the handler's hard input bound.
    # No free-form financial question or task command is classified here.
    first=text[:65].split('\n',1)[0]
    return len(first)<=64 and _normal(first) in (_REQUEST,_HEADER)


def _fields(text):
    if (type(text) is not str or len(text)>MAX_INPUT_CHARS
            or any((ord(c)<32 and c not in '\r\n') or ord(c)==127 for c in text)):
        raise PromotionUnavailable()
    lines=text.splitlines()
    if not lines or _normal(lines[0]) not in (_REQUEST,_HEADER) or len(lines)>7:raise PromotionUnavailable()
    values={}
    for line in lines[1:]:
        if ':' not in line:raise PromotionUnavailable()
        label,value=line.split(':',1);key=_LABELS.get(_normal(label));value=value.strip()
        if key is None or key in values or len(value)>32:raise PromotionUnavailable()
        values[key]=None if value in ('','[уточнить]') else value
    return values


def _number(value):
    if type(value) is not str or _LITERAL.fullmatch(value) is None:raise PromotionUnavailable()
    return value.replace(',','.')


def _shown(value):
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def build_promotion_preview(text):
    """Bounded explicit fields only; no silently supplied cost/fee/currency."""
    try:
        values=_fields(text)
        # Validate every supplied value even when another field is missing.
        if values.get('currency') is not None and re.fullmatch(r'[A-Z]{3}',values['currency']) is None:raise PromotionUnavailable()
        numbers={key:_number(value) for key,value in values.items() if key!='currency' and value is not None}
        for key in ('discount_pct','fee_pct'):
            if key in numbers and Decimal(numbers[key])>100:raise PromotionUnavailable()
        missing=tuple(key for key in _ORDER if values.get(key) is None)
        if missing:
            return PromotionDraft('Нужно уточнить: '+', '.join(_DISPLAY[key] for key in missing)+
                '. Нули укажите явно; суммы — итоги одной операции в одной валюте.\n\n'+_TEMPLATE,missing)
        result=preview_promotion(currency=values['currency'],**numbers)
        currency=result.currency
        def amount(value):return _shown(value)+' '+currency
        ratio='неизвестна: выручка после скидки равна нулю'
        if result.contribution_margin_pct is not None:
            with localcontext(Context(prec=100,rounding=ROUND_HALF_EVEN)):
                ratio=_shown(result.contribution_margin_pct.quantize(Decimal('0.000001')))+'% (округлено до 6 знаков)'
        lines=['Сценарный расчёт акции — по вашим данным.',
            'Выручка без скидки: '+amount(result.original_revenue),
            'Себестоимость операции: '+amount(result.cogs),
            'Скидка: '+numbers['discount_pct']+'% = '+amount(result.discount_amount),
            'Выручка после скидки: '+amount(result.discounted_revenue),
            'Комиссии: '+amount(result.fee_amount)+' ('+numbers['fee_pct']+'% от выручки после скидки + '+amount(Decimal(numbers['fixed_fee']))+')',
            'Остаток после указанных затрат: '+amount(result.contribution),
            'Доля остатка в выручке после скидки: '+ratio+'.',_LIMITATION]
        output='\n'.join(lines)
        if len(output)>3000 or len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise PromotionUnavailable()
        return PromotionDraft(output)
    except PromotionUnavailable:raise
    except (ValueError,TypeError,ArithmeticError,UnicodeError):raise PromotionUnavailable() from None


def _authorized(engine,payload,intelligence):
    if (engine.c.get('business')!=engine.b or 'team_intake' not in engine.c
            or not engine._operator_turn(payload,'reply') or intelligence is None
            or engine.intelligence is not intelligence or intelligence.b!=engine.b
            or intelligence.s is not engine.s or intelligence.c is not engine.c):
        raise PromotionUnavailable()
    return _scope(engine.c,payload)[0]


def handle_promotion_preview(engine,event_id,payload):
    if (type(payload) is not dict or not is_promotion_command(payload.get('text'))
            or not engine.s.db.in_transaction):raise PromotionUnavailable()
    intelligence=engine.intelligence
    try:scope=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text=_NOTICE
    try:text=build_promotion_preview(payload['text']).text
    except PromotionUnavailable:pass
    if _authorized(engine,payload,intelligence)!=scope:raise PromotionUnavailable()
    engine.s.queue(engine.b,payload['sender'],text,'promotion-preview:'+event_id,engine.now(),
                   preferred_transport=payload['channel'])
    # Same host/config/current TEAM authority after a mutable queue callback;
    # failure aborts caller transaction, including any partially queued answer.
    if _authorized(engine,payload,intelligence)!=scope:raise PromotionUnavailable()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
