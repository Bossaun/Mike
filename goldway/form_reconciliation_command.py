"""Supplied hypothetical form/account cells through actual reconciliation core."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import escape
import hashlib
import json
import re

from . import operational_reports as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Сверь бланк по сценарию'
PREFIX='form-reconciliation-preview:'
BASIS='только условные сведения этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Начало периода','Конец периода','Понедельник сценария','Часовой пояс IANA','Момент сценария',
 'Дата наблюдения полноты','Полнота данных','Количество показателей','Основание')
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_INSTANT=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
_DECIMAL=re.compile(r'-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?')
_COMPARISON={'provisional':'предварительно: данные ещё не позволяют итоговую разницу','missing_form':'бланк в сценарии не передан',
 'missing_account':'учётное значение в сценарии не передано','unknown':'сведений недостаточно для разницы','match':'значения совпали по сценарию','discrepancy':'значения различаются по сценарию'}
_SUBMISSION={'pending':'по сценарию время ещё не наступило; бланк не передан','missing_after_cutoff':'к условному сроку нет подтверждённого бланка',
 'unknown_time':'время передачи неизвестно','on_time':'передан не позже условного срока','late':'передан после условного срока'}
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nНачало периода: [YYYY-MM-DD]\nКонец периода: [YYYY-MM-DD]\n'
 'Понедельник сценария: [YYYY-MM-DD]\nЧасовой пояс IANA: [например Europe/Moscow]\nМомент сценария: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Дата наблюдения полноты: [YYYY-MM-DD]\nПолнота данных: [да / нет / неизвестно]\nКоличество показателей: [от 1 до 3]\n'
 'Показатель 1: [название]; [единицы / деньги RUB]; [бланк: да / нет]; [значение или неизвестно]; [дата наблюдения]; [время передачи со смещением или неизвестно]; [учёт: да / нет]; [значение или неизвестно]; [дата наблюдения]\nОснование: '+BASIS)
NOTICE='Сверка не подготовлена. Нужны все десять полей и 1–3 строки показателей. Наличие бланка и учётного значения — только да/нет; неизвестное наличие требует уточнения. При отсутствии бланка три его поля, при отсутствии учёта два его поля должны быть «не применяется».\n\n'+TEMPLATE


class FormUnavailable(ValueError):
    def __init__(self):super().__init__('form_reconciliation_scenario_unavailable')


def _deny():raise FormUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if not 12<=len(lines)<=14 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if not set(_LABELS)<=set(fields) or fields['Основание']!=BASIS or fields['Количество показателей'] not in ('1','2','3'):_deny()
    count=int(fields['Количество показателей'])
    if set(fields)!=set(_LABELS)|{'Показатель '+str(i) for i in range(1,count+1)}:_deny()
    return fields,count


def _day(value):
    if type(value) is not str or not _DATE.fullmatch(value):_deny()
    return date.fromisoformat(value)


def _instant(value):
    if type(value) is not str or len(value)>35 or not _INSTANT.fullmatch(value):_deny()
    # Numeric offsets express absolute instants; IANA applies to cutoff/local date.
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:_deny()
    return result


def _value(value):
    if value=='неизвестно':return None
    if not _DECIMAL.fullmatch(value):_deny()
    return Decimal(value)


def _valid(lines,owner,definitions,sources):
    if type(lines) is not tuple or len(lines)!=len(definitions):_deny()
    seen=set()
    for line in lines:
        if type(line) is not core.FormLine or line.owner!=owner or line.metric_id not in definitions or line.metric_id in seen:_deny()
        seen.add(line.metric_id)
        if line.comparison not in _COMPARISON or line.submission_state not in _SUBMISSION:_deny()
        for value in (line.reported,line.accounted,line.difference):
            if value is not None and (type(value) is not Decimal or not value.is_finite() or len(str(value))>256):_deny()
        if line.question is not None and (type(line.question) is not str or not line.question or _units(line.question)>512 or any(ord(c)<32 or ord(c)==127 for c in line.question)):_deny()
        if type(line.sources) is not tuple or not 1<=len(line.sources)<=3 or any(type(v) is not str or v not in sources for v in line.sources) or len(set(line.sources))!=len(line.sources):_deny()


@dataclass(frozen=True)
class FormPreview:
    text:str
    lines:tuple[core.FormLine,...]
    due_at:datetime


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,count=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        period=core.Period(_day(fields['Начало периода']),_day(fields['Конец периода']))
        zone=fields['Часовой пояс IANA']
        if len(zone)>64 or not re.fullmatch(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*',zone):_deny()
        due=core.monday_form_cutoff(monday=_day(fields['Понедельник сценария']),timezone_name=zone)
        asof=_instant(fields['Момент сценария']);complete=fields['Полнота данных']
        if complete not in ('да','нет','неизвестно'):_deny()
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        owner='scenario-owner:'+token
        coverage=core.Coverage(period,complete=='да',_day(fields['Дата наблюдения полноты']),'scenario-coverage:'+token)
        definitions=[];forms=[];account=[]
        for ordinal in range(1,count+1):
            row=fields['Показатель '+str(ordinal)].split('; ')
            if len(row)!=9 or not row[0].strip() or row[0]!=row[0].strip() or _units(row[0])>80 or row[0].startswith('['):_deny()
            unit,currency=('units',None) if row[1]=='единицы' else ('money',row[1][7:]) if row[1].startswith('деньги ') else ('',None)
            if not unit or row[2] not in ('да','нет') or row[6] not in ('да','нет'):_deny()
            definition=core.MetricDefinition('scenario-metric:'+token+':'+str(ordinal),row[0],unit,currency,None,'informational')
            definitions.append(definition)
            if row[2]=='нет':
                if any(v!='не применяется' for v in row[3:6]):_deny()
            else:
                observation=core.MetricObservation(owner,definition.id,period,_value(row[3]),unit,currency,_day(row[4]),'scenario-form:'+token+':'+str(ordinal))
                forms.append(core.FormValue(observation,None if row[5]=='неизвестно' else _instant(row[5])))
            if row[6]=='нет':
                if any(v!='не применяется' for v in row[7:9]):_deny()
            else:account.append(core.MetricObservation(owner,definition.id,period,_value(row[7]),unit,currency,_day(row[8]),'scenario-account:'+token+':'+str(ordinal)))
        lines=core.reconcile_forms(definitions=tuple(definitions),owners=(owner,),forms=tuple(forms),account=tuple(account),coverage=coverage,due_at=due,as_of=asof)
        by_id={d.id:d for d in definitions};sources={coverage.source_id}|{f.observation.source_id for f in forms}|{a.source_id for a in account}
        _valid(lines,owner,by_id,sources)
        output=['<b>Сверка бланка · непроверенный сценарий</b>',
            'Только условная выборка для одного анонимного участника. Реальные бланки, учёт и сотрудник не проверены независимо.',
            'Название сценария: '+escape(title),'Период ['+period.start.isoformat()+', '+period.end.isoformat()+'); момент: '+escape(asof.isoformat())+'.',
            'Условное правило понедельника: 15:00, '+escape(zone)+'; срок по правилу: '+escape(due.isoformat())+'. Это не согласование реального расписания.',
            'Полнота по сценарию: '+complete+'; наблюдение полноты: '+coverage.observed_on.isoformat()+'.']
        def number(value):return 'неизвестно' if value is None else escape(str(value))
        for ordinal,line in enumerate(lines,1):
            definition=by_id[line.metric_id];unit=definition.currency if definition.unit=='money' else 'ед.'
            output.append('<b>Показатель '+str(ordinal)+' · '+escape(definition.label)+'</b> ('+unit+')\n'
                'Бланк: '+number(line.reported)+'; учёт: '+number(line.accounted)+'.\n'
                'Сравнение: '+_COMPARISON[line.comparison]+'.\nВремя по сценарию: '+_SUBMISSION[line.submission_state]+'.\n'
                'Разница бланк − учёт: '+number(line.difference)+'.')
            if line.question:output.append('<b>Черновик вопроса — не отправлен</b>\n'+escape(line.question))
        output.append('Это не вывод о реальном отсутствии бланка, опоздании сотрудника или расхождении в компании. Вопрос можно отредактировать вручную; сбор бланков, вопросы сотрудникам, напоминания и задания не запускались.')
        body='\n\n'.join(output)
        if _units(body)>3500:_deny()
        return FormPreview(body,lines,due)
    except FormUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise FormUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except FormUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
