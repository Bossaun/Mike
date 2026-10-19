"""Bounded hypothetical one-participant barter cost/limit core projection."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
import hashlib
import json
import re

from . import influencer_metrics as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Проверь сценарий лимита бартера'
PREFIX='barter-limit-preview:'
BASIS='только условные сведения этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Начало периода','Конец периода','Валюта расчета','Полнота сведений','Условный лимит','Количество отгрузок','Основание')
_BOOL={'да':True,'нет':False,'неизвестно':False}
_BARTER={'да':True,'нет':False,'неизвестно':None}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
_MONEY=re.compile(r'(неизвестно|-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?) ([A-Z]{3})')
_REASONS={'incomplete_observations':'сведения неполны','unresolved_attribution':'связь получателя не установлена','unknown_barter':'неизвестен статус бартера',
 'missing_amount':'не передана нужная сумма','currency_conflict':'валюты стоимости не совпадают; пересчет не выполнялся','zero_barter_cost':'нулевая учтённая стоимость'}
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nНачало периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nКонец периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Валюта расчета: [три заглавные латинские буквы]\nПолнота сведений: [полно / неполно / неизвестно]\n'
 'Условный лимит: [число или неизвестно; затем пробел и валюта, например 100.00 RUB]\nКоличество отгрузок: [целое от 1 до 12]\n'
 'Отгрузка 1: [дата со смещением]; [связь: да / нет / неизвестно]; [бартер: да / нет / неизвестно]; [сумма или неизвестно и валюта]\nОснование: '+BASIS)
NOTICE='Проверка не подготовлена. Нужны восемь полей и от 1 до 12 строк «Отгрузка N» подряд без повторов. Каждая строка: дата; подтверждение связи; статус бартера; сумма и валюта. Неизвестная сумма требует явной валюты.\n\n'+TEMPLATE


class LimitUnavailable(ValueError):
    def __init__(self):super().__init__('barter_limit_scenario_unavailable')


def _deny():raise LimitUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if not 10<=len(lines)<=21 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if not set(_LABELS)<=set(fields) or fields['Основание']!=BASIS or not re.fullmatch(r'(?:[1-9]|1[0-2])',fields['Количество отгрузок']):_deny()
    count=int(fields['Количество отгрузок'])
    if set(fields)!=set(_LABELS)|{'Отгрузка '+str(i) for i in range(1,count+1)}:_deny()
    return fields,count


def _date(value):
    if type(value) is not str or len(value)>35 or not _DATE.fullmatch(value):_deny()
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:_deny()
    return result


def _money(value):
    match=_MONEY.fullmatch(value)
    if match is None:_deny()
    amount=None if match[1]=='неизвестно' else Decimal(match[1])
    if amount is not None and amount<0:_deny()
    return core.Money(amount,match[2])


def _valid(result,person,window,ships,bindings,currency):
    if type(result) is not core.InfluencerMetrics or result.influencer_id!=person or result.window!=window:_deny()
    allowed={s.id for s in ships}
    for row in (result.shipment_ids,result.unattributed_shipment_ids,result.unmeasurable_barter_ids):
        if type(row) is not tuple or len(row)>12 or any(type(v) is not str or v not in allowed for v in row) or len(set(row))!=len(row):_deny()
    if set(result.shipment_ids)&set(result.unattributed_shipment_ids) or not set(result.unmeasurable_barter_ids)<=set(result.shipment_ids):_deny()
    for row in (result.click_ids,result.order_ids,result.refund_ids,result.unattributed_order_ids,result.unattributed_click_ids,result.unattributed_refund_ids):
        if type(row) is not tuple or row:_deny()
    if type(result.barter_states) is not tuple or len(result.barter_states)!=len(result.shipment_ids):_deny()
    seen=set()
    for row in result.barter_states:
        if type(row) is not tuple or len(row)!=2 or type(row[0]) is not str or row[0] not in result.shipment_ids or row[0] in seen or type(row[1]) is not core.BarterState:_deny()
        seen.add(row[0])
    sources={s.source_id for s in ships}|{b.source_id for b in bindings}
    if type(result.source_ids) is not tuple or len(result.source_ids)>24 or any(type(v) is not str or v not in sources for v in result.source_ids) or len(set(result.source_ids))!=len(result.source_ids):_deny()
    f=result.financials
    if type(f) is not core.Financials or f.currency!=currency or f.state not in ('known','unknown','zero_denominator') or result.limit_state not in ('unknown','within_limit','exceeded'):_deny()
    if type(f.reasons) is not tuple or len(f.reasons)>6 or any(type(v) is not str or v not in _REASONS for v in f.reasons) or len(set(f.reasons))!=len(f.reasons):_deny()
    cost=f.barter_cost
    if cost is not None and (type(cost) is not Decimal or not cost.is_finite() or cost<0 or len(str(cost))>256):_deny()
    if result.limit_state!='unknown' and cost is None:_deny()


@dataclass(frozen=True)
class LimitPreview:
    text:str
    result:core.InfluencerMetrics


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,count=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        window=core.Window(_date(fields['Начало периода']),_date(fields['Конец периода']))
        if window.end-window.start>timedelta(days=366):_deny()
        complete=fields['Полнота сведений'];currency=fields['Валюта расчета']
        if complete not in ('полно','неполно','неизвестно'):_deny()
        core.Money(None,currency);limit=_money(fields['Условный лимит'])
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        person='scenario-person:'+token;ships=[];bindings=[]
        for ordinal in range(1,count+1):
            row=fields['Отгрузка '+str(ordinal)].split('; ')
            if len(row)!=4 or row[1] not in _BOOL or row[2] not in _BARTER:_deny()
            alias='scenario-alias:'+token+':'+str(ordinal)
            bindings.append(core.Binding(alias,person,_BOOL[row[1]],'supplied-binding:'+token+':'+str(ordinal)))
            ships.append(core.Shipment('scenario-shipment:'+token+':'+str(ordinal),alias,_date(row[0]),_BARTER[row[2]],_money(row[3]),'supplied-shipment:'+token+':'+str(ordinal)))
        result=core.evaluate(influencer_id=person,window=window,currency=currency,aliases=tuple(bindings),promos=(),shipments=tuple(ships),
            clicks=(),orders=(),refunds=(),reports=(),records_complete=complete=='полно',barter_limit=limit)
        _valid(result,person,window,ships,bindings,currency)
        cost=result.financials.barter_cost
        lines=['<b>Лимит бартера · непроверенный сценарий</b>',
            'Только ваши условные сведения для одного анонимного участника. Это не общий итог компании и не согласованный реальный лимит.',
            'Название сценария: '+escape(title),'Период [начало включено, конец исключён): '+escape(window.start.isoformat())+' — '+escape(window.end.isoformat()),
            'Заявленная полнота сведений: '+complete+'.\nПередано отгрузок: '+str(count)+'; отнесено к участнику в периоде: '+str(len(result.shipment_ids))+'; без установленной связи в периоде: '+str(len(result.unattributed_shipment_ids))+'.',
            'Стоимость бартера, учтённая правилом: '+('неизвестно' if cost is None else escape(str(cost))+' '+currency)+'.',
            'Переданный условный лимит: '+('неизвестно' if limit.amount is None else escape(str(limit.amount)))+' '+limit.currency+'.',
            'Сравнение по сценарию: '+{'unknown':'не определено','within_limit':'в пределах переданного лимита','exceeded':'переданный лимит превышен'}[result.limit_state]+'.']
        if cost is None:
            if not result.financials.reasons:_deny()
            lines.append('Почему стоимость не определена: '+'; '.join(_REASONS[r] for r in result.financials.reasons)+'.')
        if limit.amount is None:lines.append('Сумма условного лимита неизвестна; это не означает нулевой лимит.')
        if limit.currency!=currency:lines.append('Валюта условного лимита отличается от валюты расчета; пересчет не выполнялся.')
        lines.append('Реальные отгрузки, суммы и связи не проверены независимо. Результат не разрешает расходы или отправку товара; только ручная проверка для вас. Мониторинг, платежи, складские действия и задания не созданы.')
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return LimitPreview(body,result)
    except LimitUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise LimitUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except LimitUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
