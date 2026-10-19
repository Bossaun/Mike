"""One hypothetical promise through actual customer_rules; no real reminder.

13 lines <=4096bytes/2048UTF16; one promise, one/two ordinal outgoing events,
zero/one explicitly linked result, NO prior processing history. One3500-unit page.
"""
from dataclasses import dataclass
from datetime import datetime
from html import escape
import hashlib
import json
import re

from . import customer_rules as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Проверь обещание по сценарию'
PREFIX='customer-promise-preview:'
OWNER='только условный исполнитель сценария'
PROCESSING='не проверяется и не учитывается'
BASIS='только условные события из этого сообщения; без независимой проверки'
_LABELS=('Название сценария','Момент сценария','Исходящее событие 1 — обещание','Событие 1 подтверждено по сценарию',
         'Обещание подтверждено по сценарию','Срок обещания','Исполнитель','Исходящее событие 2 — результат',
         'Событие 2 подтверждено по сценарию','Связь результата с обещанием подтверждена','Предыдущая обработка','Основание')
_BOOL={'да':True,'нет':False,'неизвестно':False}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nМомент сценария: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Исходящее событие 1 — обещание: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nСобытие 1 подтверждено по сценарию: [да / нет / неизвестно]\n'
 'Обещание подтверждено по сценарию: [да / нет / неизвестно]\nСрок обещания: [YYYY-MM-DDTHH:MM:SS+HH:MM / неизвестно]\n'
 'Исполнитель: '+OWNER+'\nИсходящее событие 2 — результат: [YYYY-MM-DDTHH:MM:SS+HH:MM / не передано]\n'
 'Событие 2 подтверждено по сценарию: [да / нет / неизвестно / не применяется]\n'
 'Связь результата с обещанием подтверждена: [да / нет / неизвестно / не применяется]\n'
 'Предыдущая обработка: '+PROCESSING+'\nОснование: '+BASIS)
NOTICE='Проверка не подготовлена. Нужны все двенадцать полей без повторов, явные даты с часовым смещением и отметки сценария. При отсутствии события 2 обе его отметки — «не применяется».\n\n'+TEMPLATE


class PromiseUnavailable(ValueError):
    def __init__(self):super().__init__('customer_promise_scenario_unavailable')


def _deny():raise PromiseUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4096 or _units(text)>2048 or len(text.encode())>4096:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=13 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key not in _LABELS or key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if set(fields)!=set(_LABELS) or fields['Исполнитель']!=OWNER or fields['Предыдущая обработка']!=PROCESSING or fields['Основание']!=BASIS:_deny()
    return fields


def _date(value):
    if type(value) is not str or len(value)>35 or not _DATE.fullmatch(value):_deny()
    at=datetime.fromisoformat(value.replace('Z','+00:00'))
    if at.utcoffset() is None:_deny()
    return at


@dataclass(frozen=True)
class PromisePreview:
    text:str
    preview:core.Preview


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        at=_date(fields['Момент сценария']);declaration=_date(fields['Исходящее событие 1 — обещание'])
        due=None if fields['Срок обещания']=='неизвестно' else _date(fields['Срок обещания'])
        if due is not None and due<declaration:_deny()
        marks=('Событие 1 подтверждено по сценарию','Обещание подтверждено по сценарию')
        if any(fields[k] not in _BOOL for k in marks):_deny()
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        conversation=core.Conversation('scenario:'+token,'supplied-scenario','thread:'+token,'request-supplied-scenario')
        owner='scenario-owner:'+token
        first=core.Event('event:'+token+':01',conversation,declaration,'supplied-scenario:'+token+':01','outgoing',_BOOL[fields[marks[0]]])
        promise=core.Promise('promise:'+token,conversation,first.id,owner,due,_BOOL[fields[marks[1]]])
        events=[first];results=[]
        result_marks=('Событие 2 подтверждено по сценарию','Связь результата с обещанием подтверждена')
        if fields['Исходящее событие 2 — результат']=='не передано':
            if any(fields[k]!='не применяется' for k in result_marks):_deny()
        else:
            if any(fields[k] not in _BOOL for k in result_marks):_deny()
            second=core.Event('event:'+token+':02',conversation,_date(fields['Исходящее событие 2 — результат']),
                'supplied-scenario:'+token+':02','outgoing',_BOOL[fields[result_marks[0]]],first.id)
            events.append(second)
            results.append(core.PromiseResult('result:'+token,conversation,promise.id,second.id,_BOOL[fields[result_marks[1]]]))
        # Literal form states processing history is NOT checked. () is not a
        # claim that no real acknowledgement/disposition exists elsewhere.
        preview=core.promise_preview(tuple(events),(promise,),tuple(results),now=at,dispositions=())
        if (type(preview) is not core.Preview or type(preview.candidates) is not tuple or type(preview.blocked) is not tuple or
            len(preview.candidates)+len(preview.blocked)>1):_deny()
        lines=['<b>Проверка обещания · непроверенный сценарий</b>',
            'Только условные события из вашего сообщения. Реальные обещания, переписка, личности и доставка не проверялись.',
            'Название сценария: '+escape(title),'Момент сценария: '+escape(at.isoformat()),
            'Исходящее событие №1 — обещание: '+escape(declaration.isoformat())+'\n'
            'Событие подтверждено по сценарию: '+fields[marks[0]]+'. Обещание подтверждено по сценарию: '+fields[marks[1]]+'.',
            'Срок по сценарию: '+('неизвестно' if due is None else escape(due.isoformat()))+'.',
            'Исполнитель — только условная роль сценария; это не сотрудник и не назначение.',
            'Отметки подтверждения — ваши условия сценария, не независимая проверка и не квитанции доставки.']
        if len(events)==2:
            lines.append('Исходящее событие №2 — сообщённый результат: '+escape(events[1].at.isoformat())+'\n'
                'Событие подтверждено по сценарию: '+fields[result_marks[0]]+'. Связь с обещанием подтверждена: '+fields[result_marks[1]]+'.')
        else:lines.append('Сообщение о результате в сценарии не передано; это не доказывает его отсутствие в реальной переписке.')
        if preview.candidates:
            candidate=preview.candidates[0]
            if (type(candidate) is not core.Candidate or candidate.conversation!=conversation or candidate.target_id!=promise.id or
                candidate.owner_id!=owner or candidate.kind!='promise' or candidate.draft_only is not True or
                type(candidate.draft) is not str or not candidate.draft or len(candidate.draft)>2000 or
                type(candidate.due_at) is not datetime or candidate.due_at.utcoffset() is None or candidate.due_at!=due or
                candidate.evidence_ids!=(first.evidence_id,)):_deny()
            lines.extend(['По условиям сценария получен кандидат на ручную проверку обещания из события №1.',
                'Срок кандидата по правилу: '+escape(candidate.due_at.isoformat()),
                'Черновик правила для этого условного сценария: '+escape(candidate.draft)])
        elif preview.blocked:
            blocked=preview.blocked[0]
            if (type(blocked) is not core.Blocked or blocked.conversation!=conversation or blocked.target_id!=promise.id or
                blocked.reason!='unconfirmed_promise_or_due'):_deny()
            lines.append('Кандидат заблокирован: в сценарии недостаточно подтверждения исходящего события, обещания или срока. Это не подтверждение выполнения.')
        else:
            lines.append('Правило не вернуло кандидата по переданному срезу. Это не подтверждает выполнение или закрытие реального обещания.')
        lines.append('Предыдущая обработка не проверяется и не учитывается. Только ручной просмотр для вас; уведомления и назначения не созданы.')
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return PromisePreview(body,preview)
    except PromiseUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise PromiseUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except PromiseUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
