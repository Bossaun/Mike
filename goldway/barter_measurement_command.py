"""Bounded supplied barter measurement projection; no financial output or lookup."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
import hashlib
import json
import re

from . import influencer_metrics as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Проверь измеримость бартера'
PREFIX='barter-measurement-preview:'
BASIS='только условные сведения этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Начало периода','Конец периода','Дата отгрузки','Связь получателя подтверждена по сценарию',
         'Связь промокода подтверждена по сценарию','Бартер подтвержден по сценарию','Стоимость бартера','Валюта неизвестной стоимости',
         'Дата наблюдения отчета','Результат в отчете','Основание')
_BOOL={'да':True,'нет':False,'неизвестно':False}
_BARTER={'да':True,'нет':False,'неизвестно':None}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nНачало периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Конец периода: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nДата отгрузки: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Связь получателя подтверждена по сценарию: [да / нет / неизвестно]\n'
 'Связь промокода подтверждена по сценарию: [да / нет / неизвестно]\n'
 'Бартер подтвержден по сценарию: [да / нет / неизвестно]\nСтоимость бартера: неизвестно\n'
 'Валюта неизвестной стоимости: [три заглавные латинские буквы]\n'
 'Дата наблюдения отчета: [YYYY-MM-DDTHH:MM:SS+HH:MM / неизвестно]\nРезультат в отчете: [да / нет / неизвестно]\nОснование: '+BASIS)
NOTICE='Проверка не подготовлена. Нужны все двенадцать полей без повторов, явные даты с часовым смещением и условные отметки. Если отчет не передан, оба его поля должны быть «неизвестно».\n\n'+TEMPLATE


class MeasurementUnavailable(ValueError):
    def __init__(self):super().__init__('barter_measurement_scenario_unavailable')


def _deny():raise MeasurementUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>2000 or len(text.encode())>4000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=13 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key not in _LABELS or key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if set(fields)!=set(_LABELS) or fields['Основание']!=BASIS or fields['Стоимость бартера']!='неизвестно':_deny()
    return fields


def _date(value):
    if type(value) is not str or len(value)>35 or not _DATE.fullmatch(value):_deny()
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:_deny()
    return result


def _valid(result,person,window,shipment,sources,currency):
    if type(result) is not core.InfluencerMetrics or result.influencer_id!=person or result.window!=window:_deny()
    groups=(result.shipment_ids,result.unattributed_shipment_ids,result.unmeasurable_barter_ids)
    if any(type(row) is not tuple or len(row)>1 or any(v!=shipment for v in row) for row in groups):_deny()
    selected,unattributed,missing=(set(row) for row in groups)
    if selected&unattributed or selected|unattributed!={shipment} or not missing<=selected:_deny()
    for row in (result.click_ids,result.order_ids,result.refund_ids,result.unattributed_order_ids,result.unattributed_click_ids,result.unattributed_refund_ids):
        if type(row) is not tuple or row:_deny()
    states=result.barter_states
    if type(states) is not tuple or len(states)!=len(selected):_deny()
    for row in states:
        if type(row) is not tuple or len(row)!=2 or row[0]!=shipment or type(row[1]) is not core.BarterState:_deny()
    if type(result.source_ids) is not tuple or len(result.source_ids)>4 or any(type(v) is not str for v in result.source_ids):_deny()
    if len(set(result.source_ids))!=len(result.source_ids) or not set(result.source_ids)<=sources:_deny()
    financial=result.financials
    if type(financial) is not core.Financials or financial.state!='unknown' or financial.currency!=currency:_deny()
    if any(v is not None for v in (financial.net_revenue,financial.margin_after_refunds,financial.barter_cost,financial.net_after_barter,financial.roi)) or result.limit_state!='unknown':_deny()


@dataclass(frozen=True)
class MeasurementPreview:
    text:str
    result:core.InfluencerMetrics


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        window=core.Window(_date(fields['Начало периода']),_date(fields['Конец периода']))
        if window.end-window.start>timedelta(days=366):_deny()
        shipped=_date(fields['Дата отгрузки'])
        if not window.contains(shipped):_deny()
        alias_mark=fields['Связь получателя подтверждена по сценарию'];promo_mark=fields['Связь промокода подтверждена по сценарию'];barter_mark=fields['Бартер подтвержден по сценарию']
        if alias_mark not in _BOOL or promo_mark not in _BOOL or barter_mark not in _BARTER:_deny()
        currency=fields['Валюта неизвестной стоимости']
        money=core.Money(None,currency)
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        person='scenario-person:'+token;alias='scenario-alias:'+token;ship_id='scenario-shipment:'+token
        alias_binding=core.Binding(alias,person,_BOOL[alias_mark],'supplied-alias:'+token)
        promo_binding=core.Binding('scenario-promo:'+token,person,_BOOL[promo_mark],'supplied-promo:'+token)
        shipment=core.Shipment(ship_id,alias,shipped,_BARTER[barter_mark],money,'supplied-shipment:'+token)
        reports=()
        report_date=fields['Дата наблюдения отчета'];report_mark=fields['Результат в отчете']
        if report_date=='неизвестно':
            if report_mark!='неизвестно':_deny()
        else:
            if report_mark not in ('да','нет'):_deny()
            reports=(core.PlacementReport('scenario-report:'+token,ship_id,_date(report_date),report_mark=='да','supplied-report:'+token),)
        result=core.evaluate(influencer_id=person,window=window,currency=currency,aliases=(alias_binding,),promos=(promo_binding,),
            shipments=(shipment,),clicks=(),orders=(),refunds=(),reports=reports,records_complete=False,barter_limit=None)
        sources={alias_binding.source_id,promo_binding.source_id,shipment.source_id}|{r.source_id for r in reports}
        _valid(result,person,window,ship_id,sources,currency)
        lines=['<b>Измеримость бартера · непроверенный сценарий</b>',
            'Только ваши условные сведения. Связи, бартер и отчет не проверены независимо; реальные люди и отгрузки не установлены.',
            'Название сценария: '+escape(title),'Период [начало включено, конец исключён): '+escape(window.start.isoformat())+' — '+escape(window.end.isoformat()),
            'Отгрузка №1: '+escape(shipped.isoformat()),
            'Условные отметки: связь получателя — '+alias_mark+'; связь промокода — '+promo_mark+'; бартер — '+barter_mark+'.']
        if reports:lines.append('Наблюдение отчета по сценарию: '+escape(reports[0].observed_at.isoformat())+'; результат указан — '+report_mark+'. Наблюдения на конце периода и позже не учитываются внутри него.')
        else:lines.append('Отчет в сценарии не передан. Это не доказывает отсутствие реального отчета или публикации.')
        if result.unattributed_shipment_ids:
            lines.append('Отгрузка №1 не отнесена к условному участнику: связь получателя не подтверждена. Вывод об измеримости для участника не сделан.')
        else:
            state=result.barter_states[0][1]
            localized={core.BarterState.CONFIRMED:'бартер подтвержден только по сценарию',core.BarterState.NOT_BARTER:'по сценарию это не бартер',core.BarterState.UNKNOWN:'неизвестно, является ли отгрузка бартером'}[state]
            lines.append('Классификация отгрузки №1: '+localized+'.')
            if result.unmeasurable_barter_ids:
                lines.append('Для отгрузки №1 в переданном сценарии нет подтверждённого способа измерения или наблюдения результата.')
            else:lines.append('Для отгрузки №1 правило не выделило нехватку измерения бартера. Это не доказывает эффективность, достаточность измерения или наличие реальных результатов.')
        lines.extend(['Стоимость явно неизвестна ('+currency+'); сведения о заказах, переходах и возвратах неполны. Денежный итог и эффективность по ним не определены.',
            'Связь промокода сама по себе не подтверждает переходы или заказы; отметка наличия отчета не проверяет его содержание.',
            'Ручная проверка только для вас. Реальные публикации и результаты не установлены; обращения к блогеру, складские действия и задания не созданы.'])
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return MeasurementPreview(body,result)
    except MeasurementUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise MeasurementUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except MeasurementUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
