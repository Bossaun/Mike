"""One supplied hypothetical shipment through actual fixed168h report-due core."""
from dataclasses import dataclass
from datetime import datetime
from html import escape
import hashlib
import json
import re

from . import influencer_metrics as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Проверь срок отчёта по отгрузке'
PREFIX='placement-report-due-preview:'
BASIS='только условные сведения этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Дата отгрузки','Момент сценария','Интервал сценария, часов','Стоимость и статус бартера',
 'Валюта неизвестной стоимости','Дата наблюдения отчета A','Результат в отчете A','Дата наблюдения отчета B','Результат в отчете B',
 'Дата наблюдения отчета C','Результат в отчете C','Основание')
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nДата отгрузки: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Момент сценария: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nИнтервал сценария, часов: [введите 168 явно — единственное поддерживаемое значение]\n'
 'Стоимость и статус бартера: неизвестны\nВалюта неизвестной стоимости: [три заглавные латинские буквы]\n'
 'Дата наблюдения отчета A: [YYYY-MM-DDTHH:MM:SS+HH:MM / неизвестно]\nРезультат в отчете A: [да / нет / неизвестно]\n'
 'Дата наблюдения отчета B: [YYYY-MM-DDTHH:MM:SS+HH:MM / неизвестно]\nРезультат в отчете B: [да / нет / неизвестно]\n'
 'Дата наблюдения отчета C: [YYYY-MM-DDTHH:MM:SS+HH:MM / неизвестно]\nРезультат в отчете C: [да / нет / неизвестно]\nОснование: '+BASIS)
NOTICE='Проверка не подготовлена. Нужны все тринадцать полей без повторов и даты с часовым смещением. Поддерживается только явно введённый интервал 168 часов. Для непереданного наблюдения оба его поля должны быть «неизвестно».\n\n'+TEMPLATE


class DueUnavailable(ValueError):
    def __init__(self):super().__init__('placement_report_due_scenario_unavailable')


def _deny():raise DueUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>2000 or len(text.encode())>4000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=14 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key not in _LABELS or key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if set(fields)!=set(_LABELS) or fields['Основание']!=BASIS or fields['Стоимость и статус бартера']!='неизвестны' or fields['Интервал сценария, часов']!='168':_deny()
    return fields


def _date(value):
    if type(value) is not str or len(value)>35 or not _DATE.fullmatch(value):_deny()
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:_deny()
    return result


def _valid(candidates,shipment):
    if type(candidates) is not tuple or len(candidates)>1:_deny()
    for candidate in candidates:
        if type(candidate) is not core.DueCandidate or candidate.shipment_id!=shipment.id or candidate.recipient_alias!=shipment.recipient_alias or candidate.source_id!=shipment.source_id:_deny()
        if type(candidate.due_at) is not datetime or candidate.due_at.utcoffset() is None:_deny()


@dataclass(frozen=True)
class DuePreview:
    text:str
    candidates:tuple[core.DueCandidate,...]


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        shipped=_date(fields['Дата отгрузки']);now=_date(fields['Момент сценария'])
        currency=fields['Валюта неизвестной стоимости'];money=core.Money(None,currency)
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        shipment=core.Shipment('scenario-shipment:'+token,'scenario-alias:'+token,shipped,None,money,'supplied-shipment:'+token)
        reports=[];observations=[]
        for ordinal,slot in enumerate(('A','B','C'),1):
            date=fields['Дата наблюдения отчета '+slot];mark=fields['Результат в отчете '+slot]
            if date=='неизвестно':
                if mark!='неизвестно':_deny()
            else:
                if mark not in ('да','нет'):_deny()
                report=core.PlacementReport('scenario-report:'+token+':'+slot,shipment.id,_date(date),mark=='да','supplied-report:'+token+':'+slot)
                reports.append(report);observations.append((ordinal,report,mark))
        # Canonical chronology lets the core see every known simultaneous
        # conflict before a newer observation becomes its latest accumulator.
        ordered=tuple(sorted(reports,key=lambda report:report.observed_at))
        candidates=core.report_due_candidates((shipment,),ordered,now=now)
        _valid(candidates,shipment)
        lines=['<b>Срок отчёта по отгрузке · непроверенный сценарий</b>',
            'Только ваши условные сведения. Реальная отгрузка, отчет и публикация независимо не проверены.',
            'Название сценария: '+escape(title),'Условная отгрузка №1: '+escape(shipped.isoformat()),
            'Момент сценария: '+escape(now.isoformat()),
            'Выбранный интервал для этой проверки: 168 часов, по прошедшему времени в UTC. Это не согласование реального графика напоминаний.']
        if observations:
            lines.append('Переданные наблюдения:\n'+'\n'.join('№'+str(ordinal)+': '+escape(report.observed_at.isoformat())+'; результат указан — '+mark+'.' for ordinal,report,mark in observations))
        else:lines.append('Наблюдения отчета не переданы; это не доказывает отсутствие реального отчета.')
        lines.append('Учитывается последнее переданное наблюдение не позже момента сценария, включая точное совпадение; более поздние наблюдения пока не учитываются.')
        if candidates:
            lines.extend(['В этом сценарии наступил срок проверки для отгрузки №1. Дата по правилу: '+escape(candidates[0].due_at.isoformat())+'.',
                'В переданных наблюдениях на этот момент нет актуального положительного результата. Это не подтверждение отсутствия реальной публикации или отчета; возможна только ручная проверка.'])
        else:lines.append('Правило не вернуло повод для проверки на этот момент: интервал мог ещё не пройти или последнее учтённое наблюдение указывает результат. Это не подтверждает реальную доставку отчета, публикацию или завершение работы.')
        lines.extend(['Стоимость и статус бартера неизвестны ('+currency+'); эта проверка их не устанавливает.',
            'Проверка только для вас. Таймер, запрос отчета блогеру, складские действия и задания не созданы.'])
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return DuePreview(body,candidates)
    except DueUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise DueUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except DueUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
