"""Independent supplied batch weight/logistics checks; no quote or recommendation."""
from dataclasses import dataclass
from decimal import Decimal
from html import escape
import hashlib
import json
import re

from . import procurement_metrics as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Проверь вес и логистику по сценарию'
PREFIX='procurement-anomaly-preview:'
BASIS='только условные сведения сообщения; независимо не проверены'
_LABELS=('Название сценария','Количество одинаковых единиц','Измеренный вес','Ориентир веса','Допуск отклонения веса — коэффициент',
 'Закупка','Доставка','Допуск доставки к закупке — коэффициент','Закупка и доставка не включают друг друга','Основание')
_DECIMAL=re.compile(r'(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?')
_BASES={'за единицу':'per_unit','за партию':'batch'}
_KINDS={'нетто':'net','брутто':'gross'}
_STATUS={'flag':'выше введённого допуска','within_threshold':'в пределах введённого допуска','unknown':'вывод не определён'}
_WEIGHT_REASONS={'incomparable_net_gross':'нетто и брутто несопоставимы без дополнительных сведений',
 'missing_weight_or_threshold':'неизвестен вес или допуск','zero_expected_weight':'нулевой ориентир не задаёт относительную базу'}
_LOGISTICS_REASONS={'missing_amount_or_threshold':'неизвестна сумма или допуск','zero_purchase_baseline':'нулевая закупочная сумма не задаёт базу отношения'}
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nКоличество одинаковых единиц: [целое от 1 до 1000000000]\n'
 'Измеренный вес: [число или неизвестно]; [g / kg]; [за единицу / за партию]; [нетто / брутто]\n'
 'Ориентир веса: [число или неизвестно]; [g / kg]; [за единицу / за партию]; [нетто / брутто]\n'
 'Допуск отклонения веса — коэффициент: [число или неизвестно]\nЗакупка: [число или неизвестно]; [валюта]; [за единицу / за партию]\n'
 'Доставка: [число или неизвестно]; [валюта]; [за единицу / за партию]\nДопуск доставки к закупке — коэффициент: [число или неизвестно]\n'
 'Закупка и доставка не включают друг друга: [да / нет / неизвестно]\nОснование: '+BASIS)
NOTICE='Проверка не подготовлена. Нужны все десять полей без повторов, явные единицы, базы, валюты и коэффициенты. Для этой проверки требуется явное «да»: закупка и доставка не включают друг друга; валюты должны совпадать.\n\n'+TEMPLATE


class AnomalyUnavailable(ValueError):
    def __init__(self):super().__init__('procurement_anomaly_scenario_unavailable')


def _deny():raise AnomalyUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=11 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key not in _LABELS or key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if set(fields)!=set(_LABELS) or fields['Основание']!=BASIS or fields['Закупка и доставка не включают друг друга']!='да':_deny()
    return fields


def _value(value):
    if value=='неизвестно':return None
    if not _DECIMAL.fullmatch(value):_deny()
    return Decimal(value)


def _weight(value):
    row=value.split('; ')
    if len(row)!=4 or row[1] not in ('g','kg') or row[2] not in _BASES or row[3] not in _KINDS:_deny()
    return core.Weight(_value(row[0]),row[1],_BASES[row[2]],_KINDS[row[3]])


def _charge(value,identifier,kind):
    row=value.split('; ')
    if len(row)!=3 or row[2] not in _BASES:_deny()
    return core.Charge(identifier,kind,core.Money(_value(row[0]),row[1]),_BASES[row[2]],())


def _number(value):
    if type(value) is not Decimal or not value.is_finite() or value<0 or len(str(value))>256:_deny()


def _validated(weight,logistics,weight_threshold,logistics_threshold,quantity,currency):
    if type(weight) is not dict or type(logistics) is not dict:_deny()
    for result in (weight,logistics):
        if type(result.get('status')) is not str or result['status'] not in _STATUS or type(result.get('reason')) is not str:_deny()
    if weight['status']=='unknown':
        if set(weight)!={'status','reason','relative_deviation'} or weight['reason'] not in _WEIGHT_REASONS or weight['relative_deviation'] is not None:_deny()
    else:
        if set(weight)!={'status','reason','actual_batch_kg','expected_batch_kg','relative_deviation','max_relative_deviation'} or weight['reason']!='caller_threshold':_deny()
        for key in ('actual_batch_kg','expected_batch_kg','relative_deviation','max_relative_deviation'):_number(weight[key])
        if weight['max_relative_deviation']!=weight_threshold:_deny()
    if logistics['status']=='unknown':
        if set(logistics)!={'status','reason','ratio'} or logistics['reason'] not in _LOGISTICS_REASONS or logistics['ratio'] is not None:_deny()
    else:
        if set(logistics)!={'status','reason','ratio','max_freight_to_purchase_ratio','currency','quantity_basis','quantity'} or logistics['reason']!='caller_threshold':_deny()
        _number(logistics['ratio']);_number(logistics['max_freight_to_purchase_ratio'])
        if logistics['max_freight_to_purchase_ratio']!=logistics_threshold or logistics['currency']!=currency or logistics['quantity_basis']!='batch' or type(logistics['quantity']) is not int or logistics['quantity']!=quantity:_deny()


@dataclass(frozen=True)
class AnomalyPreview:
    text:str
    weight:dict
    logistics:dict


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        raw=fields['Количество одинаковых единиц']
        if not re.fullmatch(r'[1-9][0-9]{0,9}',raw) or int(raw)>10**9:_deny()
        quantity=int(raw);actual=_weight(fields['Измеренный вес']);expected=_weight(fields['Ориентир веса'])
        weight_threshold=_value(fields['Допуск отклонения веса — коэффициент']);logistics_threshold=_value(fields['Допуск доставки к закупке — коэффициент'])
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        purchase=_charge(fields['Закупка'],'scenario-purchase:'+token,'purchase')
        freight=_charge(fields['Доставка'],'scenario-freight:'+token,'freight')
        weight=core.assess_weight(actual=actual,expected=expected,max_relative_deviation=weight_threshold,quantity=quantity)
        logistics=core.assess_logistics(purchase=purchase,freight=freight,quantity=quantity,max_freight_to_purchase_ratio=logistics_threshold)
        _validated(weight,logistics,weight_threshold,logistics_threshold,quantity,purchase.money.currency)
        def scalar(value):return 'неизвестно' if value is None else escape(str(value))
        bases={v:k for k,v in _BASES.items()};kinds={v:k for k,v in _KINDS.items()}
        def mass(w):return scalar(w.amount)+' '+w.unit+', '+bases[w.basis]+', '+kinds[w.kind]
        def cost(c):return scalar(c.money.amount)+' '+c.money.currency+', '+bases[c.basis]
        output=['<b>Вес и логистика · непроверенный сценарий</b>',
            'Одна условная партия одинаковых единиц. Вес, суммы, ориентир и допуски заданы вами и независимо не проверены.',
            'Название сценария: '+escape(title),'Количество одинаковых единиц: '+str(quantity)+'.',
            '<b>Вес</b>\nИзмеренный по сценарию: '+mass(actual)+'; ориентир: '+mass(expected)+'.\n'
            'Введённый допуск отклонения — коэффициент: '+scalar(weight_threshold)+'.\nРезультат по весу: '+_STATUS[weight['status']]+'.']
        if weight['status']=='unknown':output.append('Причина по весу: '+_WEIGHT_REASONS[weight['reason']]+'.')
        else:output.append('Массы партии по правилу: измеренная '+scalar(weight['actual_batch_kg'])+' kg; ориентир '+scalar(weight['expected_batch_kg'])+' kg.\nОтносительное отклонение — коэффициент: '+scalar(weight['relative_deviation'])+'.')
        output.append('<b>Логистика</b>\nЗакупка: '+cost(purchase)+'; доставка: '+cost(freight)+'.\n'
            'Их независимость — только ваше явное условие сценария.\nВведённый допуск доставки к закупке — коэффициент: '+scalar(logistics_threshold)+'.\nРезультат по логистике: '+_STATUS[logistics['status']]+'.')
        if logistics['status']=='unknown':output.append('Причина по логистике: '+_LOGISTICS_REASONS[logistics['reason']]+'.')
        else:output.append('Доставка / закупка для партии — коэффициент: '+scalar(logistics['ratio'])+'.')
        output.append('Каждый результат относится только к своему введённому допуску; это не норма товара и не рекомендация покупать. Реальные закупки, платежи, складские действия и задания не создавались.')
        body='\n\n'.join(output)
        if _units(body)>3500:_deny()
        return AnomalyPreview(body,weight,logistics)
    except AnomalyUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise AnomalyUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except AnomalyUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
