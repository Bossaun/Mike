"""One explicit hypothetical financial dataset through actual influencer core."""
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

HEADER='Рассчитай сценарий окупаемости бартера'
PREFIX='influencer-roi-preview:'
BASIS='только условные сведения этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Начало периода','Конец периода','Валюта расчета','Полнота сведений',
 'Связь условного участника подтверждена','Бартер подтвержден','Дата отгрузки','Стоимость бартера',
 'Заказ передан','Дата заказа','Выручка заказа до возвратов','Маржа заказа до возвратов и бартера',
 'Возврат передан','Дата возврата','Уменьшение выручки при возврате','Уменьшение маржи при возврате','Основание')
_BOOL={'да':True,'нет':False,'неизвестно':False}
_BARTER={'да':True,'нет':False,'неизвестно':None}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
_MONEY=re.compile(r'(неизвестно|-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?) ([A-Z]{3})')
_REASONS={'incomplete_observations':'сведения неполны','unresolved_attribution':'связь с условным участником не установлена',
 'unknown_barter':'неизвестен статус бартера','missing_amount':'не передана нужная сумма','currency_conflict':'валюты не совпадают; пересчет не выполнялся',
 'zero_barter_cost':'нулевая стоимость бартера: коэффициент не определён'}
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nНачало периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nКонец периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Валюта расчета: [три заглавные латинские буквы]\nПолнота сведений: [полно / неполно / неизвестно]\n'
 'Связь условного участника подтверждена: [да / нет / неизвестно]\nБартер подтвержден: [да / нет / неизвестно]\n'
 'Дата отгрузки: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nСтоимость бартера: [число или неизвестно; затем пробел и валюта, например 100.00 RUB]\n'
 'Заказ передан: [да / нет / неизвестно]\nДата заказа: [YYYY-MM-DDTHH:MM:SS+HH:MM / не применяется]\n'
 'Выручка заказа до возвратов: [сумма и валюта / не применяется]\nМаржа заказа до возвратов и бартера: [сумма и валюта / не применяется]\n'
 'Возврат передан: [да / нет / неизвестно]\nДата возврата: [YYYY-MM-DDTHH:MM:SS+HH:MM / не применяется]\n'
 'Уменьшение выручки при возврате: [сумма и валюта / не применяется]\nУменьшение маржи при возврате: [сумма и валюта / не применяется]\nОснование: '+BASIS)
NOTICE='Расчет не подготовлен. Нужны все восемнадцать полей без повторов, явные даты и суммы с валютой. Для непереданного заказа или возврата три связанных поля должны быть «не применяется». Неизвестное наличие несовместимо с заявлением полной информации.\n\n'+TEMPLATE


class RoiUnavailable(ValueError):
    def __init__(self):super().__init__('influencer_roi_scenario_unavailable')


def _deny():raise RoiUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=19 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key not in _LABELS or key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if set(fields)!=set(_LABELS) or fields['Основание']!=BASIS:_deny()
    return fields


def _date(value):
    if type(value) is not str or len(value)>35 or not _DATE.fullmatch(value):_deny()
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:_deny()
    return result


def _money(value):
    match=_MONEY.fullmatch(value)
    if match is None:_deny()
    return core.Money(None if match[1]=='неизвестно' else Decimal(match[1]),match[2])


def _valid(result,person,window,shipment,orders,refunds,sources,currency):
    if type(result) is not core.InfluencerMetrics or result.influencer_id!=person or result.window!=window:_deny()
    for selected,unresolved,allowed in ((result.shipment_ids,result.unattributed_shipment_ids,{shipment.id}),
        (result.order_ids,result.unattributed_order_ids,{o.id for o in orders}),
        (result.refund_ids,result.unattributed_refund_ids,{r.id for r in refunds})):
        for row in (selected,unresolved):
            if type(row) is not tuple or len(row)>1 or any(type(v) is not str or v not in allowed for v in row):_deny()
        if set(selected)&set(unresolved):_deny()
    for row in (result.click_ids,result.unattributed_click_ids):
        if type(row) is not tuple or row:_deny()
    if type(result.unmeasurable_barter_ids) is not tuple or len(result.unmeasurable_barter_ids)>1 or not set(result.unmeasurable_barter_ids)<=set(result.shipment_ids):_deny()
    if type(result.barter_states) is not tuple or len(result.barter_states)!=len(result.shipment_ids):_deny()
    for row in result.barter_states:
        if type(row) is not tuple or len(row)!=2 or row[0]!=shipment.id or type(row[1]) is not core.BarterState:_deny()
    if type(result.source_ids) is not tuple or len(result.source_ids)>5 or any(type(v) is not str or v not in sources for v in result.source_ids) or len(set(result.source_ids))!=len(result.source_ids):_deny()
    f=result.financials
    if type(f) is not core.Financials or f.state not in ('known','unknown','zero_denominator') or f.currency!=currency or result.limit_state!='unknown':_deny()
    if type(f.reasons) is not tuple or len(f.reasons)>6 or any(type(v) is not str or v not in _REASONS for v in f.reasons) or len(set(f.reasons))!=len(f.reasons):_deny()
    values=(f.net_revenue,f.margin_after_refunds,f.barter_cost,f.net_after_barter,f.roi)
    for value in values:
        if value is not None and (type(value) is not Decimal or not value.is_finite() or len(str(value))>256):_deny()
    if f.state=='unknown':
        if not f.reasons or any(value is not None for value in (f.net_revenue,f.margin_after_refunds,f.net_after_barter,f.roi)):_deny()
    elif any(value is None for value in values[:4]):_deny()
    elif f.state=='zero_denominator':
        if f.barter_cost!=0 or f.roi is not None:_deny()
    elif f.roi is None or f.barter_cost==0:_deny()


@dataclass(frozen=True)
class RoiPreview:
    text:str
    result:core.InfluencerMetrics


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        window=core.Window(_date(fields['Начало периода']),_date(fields['Конец периода']))
        if window.end-window.start>timedelta(days=366):_deny()
        complete=fields['Полнота сведений'];identity=fields['Связь условного участника подтверждена'];barter=fields['Бартер подтвержден']
        if complete not in ('полно','неполно','неизвестно') or identity not in _BOOL or barter not in _BARTER:_deny()
        currency=fields['Валюта расчета']
        # Validate reporting currency even when no amount uses it.
        core.Money(None,currency)
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        person='scenario-person:'+token;alias='scenario-alias:'+token;promo='scenario-promo:'+token
        aliases=(core.Binding(alias,person,_BOOL[identity],'supplied-alias:'+token),)
        promos=(core.Binding(promo,person,_BOOL[identity],'supplied-promo:'+token),)
        shipment=core.Shipment('scenario-shipment:'+token,alias,_date(fields['Дата отгрузки']),_BARTER[barter],_money(fields['Стоимость бартера']),'supplied-shipment:'+token)
        orders=();refunds=()
        for presence,labels in ((fields['Заказ передан'],('Дата заказа','Выручка заказа до возвратов','Маржа заказа до возвратов и бартера')),
            (fields['Возврат передан'],('Дата возврата','Уменьшение выручки при возврате','Уменьшение маржи при возврате'))):
            if presence not in _BOOL or presence=='неизвестно' and complete=='полно':_deny()
            if presence!='да' and any(fields[label]!='не применяется' for label in labels):_deny()
        if fields['Заказ передан']=='да':
            orders=(core.Order('scenario-order:'+token,(promo,),alias,_date(fields['Дата заказа']),_money(fields['Выручка заказа до возвратов']),_money(fields['Маржа заказа до возвратов и бартера']),'supplied-order:'+token),)
        if fields['Возврат передан']=='да':
            if not orders:_deny()
            refunds=(core.Refund('scenario-refund:'+token,orders[0].id,_date(fields['Дата возврата']),_money(fields['Уменьшение выручки при возврате']),_money(fields['Уменьшение маржи при возврате']),'supplied-refund:'+token),)
        result=core.evaluate(influencer_id=person,window=window,currency=currency,aliases=aliases,promos=promos,shipments=(shipment,),
            clicks=(),orders=orders,refunds=refunds,reports=(),records_complete=complete=='полно',barter_limit=None)
        sources={shipment.source_id,aliases[0].source_id,promos[0].source_id}|{o.source_id for o in orders}|{r.source_id for r in refunds}
        _valid(result,person,window,shipment,orders,refunds,sources,currency)
        f=result.financials
        lines=['<b>Окупаемость бартера · непроверенный сценарий</b>',
            'Расчет только по вашим условным сведениям. Реальные люди, отгрузки, заказы и суммы независимо не проверены.',
            'Название сценария: '+escape(title),'Период [начало включено, конец исключён): '+escape(window.start.isoformat())+' — '+escape(window.end.isoformat()),
            'Заявленная полнота сведений: '+complete+'; связь условного участника: '+identity+'; статус бартера: '+barter+'.',
            'Наличие заказа по сценарию: '+fields['Заказ передан']+'; возврата: '+fields['Возврат передан']+'.',
            'Состояние расчета: '+{'known':'значения определены из переданных предположений','unknown':'недостаточно определённых сведений','zero_denominator':'нулевая стоимость бартера; коэффициент не определён'}[f.state]+'.']
        for label,value in (('Выручка после учтённых возвратов',f.net_revenue),('Маржа после учтённых возвратов, до бартера',f.margin_after_refunds),
            ('Стоимость бартера, учтённая правилом',f.barter_cost),('Маржа после учтённых возвратов и бартера',f.net_after_barter)):
            lines.append(label+': '+('неизвестно' if value is None else escape(str(value))+' '+currency)+'.')
        lines.append('ROI — коэффициент: '+('не определён' if f.roi is None else escape(str(f.roi)))+'.\nФормула: (маржа после возвратов − стоимость бартера) / стоимость бартера.')
        if f.reasons:lines.append('Причины по переданным сведениям: '+'; '.join(_REASONS[r] for r in f.reasons)+'.')
        lines.append('Это не проверенная прибыль или оценка реального блогера. Только ручной сценарий для вас; платежи, согласование расходов, складские действия и задания не созданы.')
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return RoiPreview(body,result)
    except RoiUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise RoiUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except RoiUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
