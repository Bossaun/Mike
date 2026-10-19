"""One hypothetical plan through actual receipt reconciliation, never delivery.

14 lines <=4096bytes/2048UTF16; one plan, <=2 events/receipt assertions,
explicit windows <=366days; one3500-unit own preview. No eligibility evaluation.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
import hashlib
import json
import re

from . import customer_rules as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Сверь касание по сценарию'
PREFIX='touch-reconciliation-preview:'
BASIS='только условные события и отметки этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Момент сценария','Начало отчетного окна','Конец отчетного окна','Событие 1 — основание',
         'Событие 1 подтверждено по сценарию','Начало окна плана','Конец окна плана','Событие 2 — исходящее',
         'Событие 2 подтверждено по сценарию','Запись квитанции A','Запись квитанции B','Основание')
_BOOL={'да':True,'нет':False,'неизвестно':False}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
_RECEIPT=re.compile(r'([12]); (да|нет|неизвестно)')
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nМомент сценария: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Начало отчетного окна: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nКонец отчетного окна: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Событие 1 — основание: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nСобытие 1 подтверждено по сценарию: [да / нет / неизвестно]\n'
 'Начало окна плана: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nКонец окна плана: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Событие 2 — исходящее: [YYYY-MM-DDTHH:MM:SS+HH:MM / не передано]\n'
 'Событие 2 подтверждено по сценарию: [да / нет / неизвестно / не применяется]\n'
 'Запись квитанции A: [не передано / 1; да / 1; нет / 1; неизвестно / 2; да / 2; нет / 2; неизвестно]\n'
 'Запись квитанции B: [не передано / 1; да / 1; нет / 1; неизвестно / 2; да / 2; нет / 2; неизвестно]\nОснование: '+BASIS)
NOTICE='Сверка не подготовлена. Нужны все тринадцать полей без повторов, явные даты с часовым смещением и условные отметки. Номера 1/2 в записях — только локальные идентификаторы.\n\n'+TEMPLATE


class ReconciliationUnavailable(ValueError):
    def __init__(self):super().__init__('touch_reconciliation_scenario_unavailable')


def _deny():raise ReconciliationUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4096 or _units(text)>2048 or len(text.encode())>4096:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=14 or lines[0]!=HEADER:_deny()
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


def _window(start,end):
    window=core.Window(_date(start),_date(end))
    if window.end-window.start>timedelta(days=366):_deny()
    return window


def _summary_valid(summary,identifier):
    if type(summary) is not core.TouchSummary:_deny()
    rows=(summary.planned_ids,summary.confirmed_ids,summary.pending_ids,summary.uncertain_ids)
    if any(type(row) is not tuple or len(row)>1 or any(value!=identifier for value in row) for row in rows):_deny()
    planned,confirmed,pending,uncertain=(set(row) for row in rows)
    if confirmed&pending or confirmed&uncertain or pending&uncertain or confirmed|pending|uncertain!=planned:_deny()


@dataclass(frozen=True)
class ReconciliationPreview:
    text:str
    summary:core.TouchSummary


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        now=_date(fields['Момент сценария'])
        reporting=_window(fields['Начало отчетного окна'],fields['Конец отчетного окна'])
        planning=_window(fields['Начало окна плана'],fields['Конец окна плана'])
        basis_at=_date(fields['Событие 1 — основание'])
        if planning.start<basis_at or fields['Событие 1 подтверждено по сценарию'] not in _BOOL:_deny()
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        conversation=core.Conversation('scenario:'+token,'supplied-scenario','thread:'+token,'request-supplied-scenario')
        first=core.Event('event:'+token+':01',conversation,basis_at,'supplied-scenario:'+token+':01',
            'incoming',_BOOL[fields['Событие 1 подтверждено по сценарию']])
        # Required schema fields are opaque scenario placeholders, NOT a grant,
        # ContactPermission, real owner or an assertion that contact is ready.
        plan=core.TouchPlan('plan:'+token,conversation,first.id,'scenario-owner:'+token,
            'permission-not-checked:'+token,None,planning,'Условный план для ручной сверки; не сообщение клиенту.')
        events=[first];receipts=[]
        slots=(fields['Запись квитанции A'],fields['Запись квитанции B'])
        if fields['Событие 2 — исходящее']=='не передано':
            if fields['Событие 2 подтверждено по сценарию']!='не применяется' or any(value!='не передано' for value in slots):_deny()
        else:
            mark=fields['Событие 2 подтверждено по сценарию']
            if mark not in _BOOL:_deny()
            second=core.Event('event:'+token+':02',conversation,_date(fields['Событие 2 — исходящее']),
                'supplied-scenario:'+token+':02','outgoing',_BOOL[mark])
            events.append(second)
            for value in slots:
                if value=='не передано':continue
                match=_RECEIPT.fullmatch(value)
                if match is None:_deny()
                receipts.append(core.TouchReceipt('receipt:'+token+':'+match[1],conversation,plan.id,second.id,_BOOL[match[2]]))
        summary=core.reconcile_touches(tuple(events),(plan,),tuple(receipts),conversation=conversation,window=reporting,now=now)
        _summary_valid(summary,plan.id)
        lines=['<b>Сверка касания · непроверенный сценарий</b>',
            'Только ваши условные события и отметки. Это не независимые квитанции Telegram и не подтверждение реальной доставки или выполнения.',
            'Название сценария: '+escape(title),'Момент сценария: '+escape(now.isoformat()),
            'Отчетное окно [начало включено, конец исключён): '+escape(reporting.start.isoformat())+' — '+escape(reporting.end.isoformat()),
            'План №1 · окно: '+escape(planning.start.isoformat())+' — '+escape(planning.end.isoformat()),
            'Событие №1 — основание: '+escape(basis_at.isoformat())+'\nПодтверждено по сценарию: '+fields['Событие 1 подтверждено по сценарию']+'.']
        if len(events)==2:
            lines.append('Событие №2 — исходящее: '+escape(events[1].at.isoformat())+'\nПодтверждено по сценарию: '+fields['Событие 2 подтверждено по сценарию']+'.\n'
                'Условные записи: A — '+escape(slots[0])+'; B — '+escape(slots[1])+'.')
        else:lines.append('Исходящее событие и записи квитанций в сценарии не переданы; это не доказывает отсутствие реальных сообщений.')
        lines.append('По переданному срезу:\nВыбрано планов: '+str(len(summary.planned_ids))+'.\n'
            'С условными подтверждёнными отметками: '+str(len(summary.confirmed_ids))+'.\n'
            'Без учтённой отметки: '+str(len(summary.pending_ids))+'.\n'
            'С неопределённым подтверждением: '+str(len(summary.uncertain_ids))+'.')
        if not summary.planned_ids:lines.append('План №1 не выбран по этому окну и срезу. Это не означает, что реальных планов или работы не было.')
        elif summary.confirmed_ids:lines.append('Для плана №1 правило учло условные подтверждённые отметки. Реальная доставка и выполнение этим не проверены.')
        elif summary.uncertain_ids:lines.append('Для плана №1 подтверждения в сценарии недостаточно. Это не разрешение повторить отправку.')
        else:lines.append('Для плана №1 нет учтённой отметки по условиям сценария. Это не доказательство недоставки.')
        lines.extend(['На конце отчетного окна учет прекращается; события ровно на его конце не учитываются.',
            'Это не проверка допуска к контакту: согласие, готовность и личности не проверяются; отметка основания не разрешает отправку.',
            'Только ручная сверка для вас. Состояние реальной переписки не установлено; вечерний опрос, клиентские сообщения и задания не созданы.'])
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return ReconciliationPreview(body,summary)
    except ReconciliationUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise ReconciliationUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except ReconciliationUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
