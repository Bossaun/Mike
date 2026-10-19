"""Bounded anonymous daily metric/target scenario through actual KPI snapshot."""
from dataclasses import dataclass
from datetime import date
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

HEADER='Проверь дневные показатели по сценарию'
PREFIX='daily-kpi-preview:'
BASIS='условные сведения этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Начало периода','Конец периода','Дата наблюдения полноты','Полнота данных','Количество показателей','Основание')
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_DECIMAL=re.compile(r'(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?')
_DIRECTIONS={'не меньше':'at_least','не больше':'at_most','справочно':'informational'}
_STATUS={'known':'значение определено по сценарию','provisional':'предварительное значение: неполные или ранние данные','unknown':'значение неизвестно'}
_TARGET={'met':'условие сценария выполнено по переданному значению','missed':'условие сценария не выполнено по переданному значению',
 'informational':'справочный показатель; условие не задано','unknown':'вывод по условию не определён'}
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nНачало периода: [YYYY-MM-DD]\nКонец периода: [следующий день YYYY-MM-DD]\n'
 'Дата наблюдения полноты: [YYYY-MM-DD]\nПолнота данных: [да / нет / неизвестно]\nКоличество показателей: [от 1 до 3]\n'
 'Показатель 1: [название]; [количество / деньги RUB]; [не меньше / не больше / справочно]; [цель: число / неизвестно / не применяется для справочно]; [наблюдение: да / нет]; [значение или неизвестно]; [дата наблюдения]\nОснование: '+BASIS)
NOTICE='Проверка не подготовлена. Нужны все семь полей и 1–3 строки показателей за ровно один день. Наличие наблюдения — только да/нет; при отсутствии значение и дата должны быть «не применяется». Для справочного показателя цель — «не применяется».\n\n'+TEMPLATE


class KpiUnavailable(ValueError):
    def __init__(self):super().__init__('daily_kpi_scenario_unavailable')


def _deny():raise KpiUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if not 9<=len(lines)<=11 or lines[0]!=HEADER:_deny()
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


def _value(value):
    if value=='неизвестно':return None
    if not _DECIMAL.fullmatch(value):_deny()
    return Decimal(value)


def _valid(lines,owner,definitions,sources):
    if type(lines) is not tuple or len(lines)!=len(definitions):_deny()
    seen=set()
    for line in lines:
        if type(line) is not core.MetricLine or line.owner!=owner or line.metric_id not in definitions or line.metric_id in seen:_deny()
        seen.add(line.metric_id);definition=definitions[line.metric_id]
        if line.target!=definition.target or line.unit!=definition.unit or line.currency!=definition.currency:_deny()
        if line.status not in _STATUS or line.target_state not in _TARGET:_deny()
        for value in (line.value,line.target):
            if value is not None and (type(value) is not Decimal or not value.is_finite() or value<0 or len(str(value))>256):_deny()
        if line.status!='known' and line.target_state!='unknown':_deny()
        if line.target_state=='informational' and definition.direction!='informational':_deny()
        if line.target_state in ('met','missed') and (definition.direction=='informational' or line.target is None):_deny()
        if type(line.sources) is not tuple or not 1<=len(line.sources)<=2 or any(type(v) is not str or v not in sources for v in line.sources) or len(set(line.sources))!=len(line.sources):_deny()


@dataclass(frozen=True)
class KpiPreview:
    text:str
    lines:tuple[core.MetricLine,...]


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,count=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        period=core.Period(_day(fields['Начало периода']),_day(fields['Конец периода']))
        complete=fields['Полнота данных']
        if complete not in ('да','нет','неизвестно'):_deny()
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32];owner='scenario-owner:'+token
        coverage=core.Coverage(period,complete=='да',_day(fields['Дата наблюдения полноты']),'scenario-coverage:'+token)
        definitions=[];observations=[]
        for ordinal in range(1,count+1):
            row=fields['Показатель '+str(ordinal)].split('; ')
            if len(row)!=7 or not row[0].strip() or row[0]!=row[0].strip() or _units(row[0])>80 or row[0].startswith('['):_deny()
            unit,currency=('count',None) if row[1]=='количество' else ('money',row[1][7:]) if row[1].startswith('деньги ') else ('',None)
            if not unit or row[2] not in _DIRECTIONS or row[4] not in ('да','нет'):_deny()
            direction=_DIRECTIONS[row[2]]
            if direction=='informational':
                if row[3]!='не применяется':_deny()
                target=None
            else:target=_value(row[3])
            definition=core.MetricDefinition('scenario-metric:'+token+':'+str(ordinal),row[0],unit,currency,target,direction)
            definitions.append(definition)
            if row[4]=='нет':
                if row[5]!='не применяется' or row[6]!='не применяется':_deny()
            else:observations.append(core.MetricObservation(owner,definition.id,period,_value(row[5]),unit,currency,_day(row[6]),'scenario-observation:'+token+':'+str(ordinal)))
        lines=core.kpi_snapshot(definitions=tuple(definitions),owners=(owner,),observations=tuple(observations),coverage=coverage,require_daily=True)
        by_id={d.id:d for d in definitions};sources={coverage.source_id}|{r.source_id for r in observations}
        _valid(lines,owner,by_id,sources)
        output=['<b>Дневные показатели · непроверенный сценарий</b>',
            'Один анонимный условный участник. Реальные сотрудники, цели и результаты не проверены независимо; это не оценка их работы.',
            'Название сценария: '+escape(title),'Период ['+period.start.isoformat()+', '+period.end.isoformat()+'); полнота по сценарию: '+complete+'; наблюдение полноты: '+coverage.observed_on.isoformat()+'.']
        def number(value):return 'неизвестно' if value is None else escape(str(value))
        directions={v:k for k,v in _DIRECTIONS.items()}
        for ordinal,line in enumerate(lines,1):
            definition=by_id[line.metric_id];unit=definition.currency if definition.unit=='money' else 'количество'
            target='не применяется' if definition.direction=='informational' else number(line.target)
            output.append('<b>Показатель '+str(ordinal)+' · '+escape(definition.label)+'</b> ('+unit+')\n'
                'Значение: '+number(line.value)+'; цель: '+target+'; правило: '+directions[definition.direction]+'.\n'
                'Данные: '+_STATUS[line.status]+'.\nРезультат: '+_TARGET[line.target_state]+'.')
        output.append('Только ручная проверка переданных предположений. Реальные обязанности, ежедневный контроль, напоминания и задания не назначены.')
        body='\n\n'.join(output)
        if _units(body)>3500:_deny()
        return KpiPreview(body,lines)
    except KpiUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise KpiUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except KpiUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
