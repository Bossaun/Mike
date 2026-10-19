"""Bounded explicit two-week sample through actual Sale/compare_weeks core."""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from html import escape
import hashlib
import json
import re

from . import finance_metrics as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Сравни недели продаж'
PREFIX='weekly-sales-preview:'
BASIS='только переданная условная выборка; независимо не проверена'
_LABELS=('Название сценария','Понедельник текущей недели','Валюта расчета','Количество строк','Основание')
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_AMOUNT=re.compile(r'(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?')
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nПонедельник текущей недели: [YYYY-MM-DD]\nВалюта расчета: [три заглавные латинские буквы]\n'
 'Количество строк: [целое от 1 до 12]\nСтрока 1: [YYYY-MM-DD]; [выручка — сумма операции]; [себестоимость — сумма или неизвестно]; [бартер: да / нет]; [валюта]\nОснование: '+BASIS)
NOTICE='Сравнение не подготовлено. Нужны пять полей и от 1 до 12 строк «Строка N» без повторов и пропусков. Каждая строка: дата; выручка; себестоимость или неизвестно; да/нет для бартера; валюта. Возвраты и отрицательные суммы не поддерживаются.\n\n'+TEMPLATE


class ComparisonUnavailable(ValueError):
    def __init__(self):super().__init__('weekly_sales_sample_unavailable')


def _deny():raise ComparisonUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if not 7<=len(lines)<=18 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if not set(_LABELS)<=set(fields) or fields['Основание']!=BASIS or not re.fullmatch(r'(?:[1-9]|1[0-2])',fields['Количество строк']):_deny()
    count=int(fields['Количество строк'])
    if set(fields)!=set(_LABELS)|{'Строка '+str(i) for i in range(1,count+1)}:_deny()
    return fields,count


def _day(value):
    if type(value) is not str or not _DATE.fullmatch(value):_deny()
    return date.fromisoformat(value)


def _amount(value):
    if not _AMOUNT.fullmatch(value):_deny()
    return Decimal(value)


def _number(value,*,optional=False,nonnegative=False):
    if value is None:
        if not optional:_deny()
    elif type(value) is not Decimal or not value.is_finite() or len(str(value))>256 or nonnegative and value<0:_deny()


def _valid(result,monday,currency,count):
    if type(result) is not core.WeekComparison:_deny()
    for week,start in ((result.current,monday),(result.prior,monday-timedelta(days=7))):
        if type(week) is not core.WeekMetrics or week.start!=start or week.end!=start+timedelta(days=7) or week.currency!=currency:_deny()
        for n in (week.sale_count,week.known_cost_count,week.unknown_cost_count,week.barter_count,week.unknown_barter_cost_count):
            if type(n) is not int or not 0<=n<=count:_deny()
        if week.known_cost_count+week.unknown_cost_count!=week.sale_count or week.unknown_barter_cost_count>week.barter_count:_deny()
        for value in (week.revenue,week.known_cost_revenue,week.known_cogs,week.known_barter_cost):_number(value,nonnegative=True)
        _number(week.cost_coverage,optional=True,nonnegative=True)
        if week.cost_coverage is not None and week.cost_coverage>1:_deny()
        _number(week.gross_profit,optional=True);_number(week.gross_margin_pct,optional=True)
        if (week.cost_coverage is None)!=(week.sale_count==0) or (week.gross_profit is None)!=(week.known_cost_count==0):_deny()
    if result.current.sale_count+result.current.barter_count+result.prior.sale_count+result.prior.barter_count!=count:_deny()
    _number(result.revenue_delta)
    for value in (result.revenue_change_pct,result.known_scope_gross_profit_delta,result.gross_margin_change_pp):_number(value,optional=True)


@dataclass(frozen=True)
class SalesPreview:
    text:str
    result:core.WeekComparison


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,count=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        monday=_day(fields['Понедельник текущей недели'])
        if monday.weekday()!=0:_deny()
        lower=monday-timedelta(days=7);upper=monday+timedelta(days=7);currency=fields['Валюта расчета']
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        records=[]
        for ordinal in range(1,count+1):
            row=fields['Строка '+str(ordinal)].split('; ')
            if len(row)!=5 or row[3] not in ('да','нет'):_deny()
            day=_day(row[0])
            if not lower<=day<upper:_deny()
            records.append(core.Sale('scenario-sale:'+token+':'+str(ordinal),day,row[4],_amount(row[1]),None if row[2]=='неизвестно' else _amount(row[2]),row[3]=='да'))
        result=core.compare_weeks(tuple(records),week_start=monday,currency=currency)
        _valid(result,monday,currency,count)
        def scalar(value,unit=''):return 'не определено' if value is None else escape(str(value))+unit
        def money(value):return 'неизвестно' if value is None else scalar(value,' '+currency)
        lines=['<b>Сравнение недель продаж · условная выборка</b>',
            'Только по переданным строкам. Полнота реальных продаж не установлена; сведения независимо не проверены.',
            'Название сценария: '+escape(title)]
        for label,week in (('Предыдущая неделя',result.prior),('Текущая неделя',result.current)):
            lines.append('<b>'+label+'</b> ['+week.start.isoformat()+', '+week.end.isoformat()+')\n'
                'Обычных продаж в выборке: '+str(week.sale_count)+'; выручка: '+money(week.revenue)+'.\n'
                'Себестоимость известна для '+str(week.known_cost_count)+' из '+str(week.sale_count)+' обычных продаж; неизвестна для '+str(week.unknown_cost_count)+'. Доля по количеству: '+scalar(week.cost_coverage)+'.\n'
                'Выручка строк с известной себестоимостью: '+money(week.known_cost_revenue)+'.\n'
                'Прибыль только этих строк: '+money(week.gross_profit)+'; маржа на их выручке: '+scalar(week.gross_margin_pct,'%')+'.\n'
                'Бартерных строк: '+str(week.barter_count)+'; известный подытог их себестоимости: '+money(week.known_barter_cost)+'; с неизвестной себестоимостью: '+str(week.unknown_barter_cost_count)+'.')
        lines.extend(['<b>Разница: текущая минус предыдущая</b>\nВыручка выборки: '+money(result.revenue_delta)+'; изменение: '+scalar(result.revenue_change_pct,'%')+'.\n'
            'Прибыль только строк с известной себестоимостью: '+money(result.known_scope_gross_profit_delta)+'.\n'
            'Маржа того же известного охвата: '+scalar(result.gross_margin_change_pp,' п.п.')+'. Охваты двух недель могут различаться.',
            'Пустая неделя выборки не доказывает отсутствие реальных продаж. Бартер исключён из обычной выручки; его известная себестоимость — подытог, а не полный итог при неизвестных затратах.',
            'Возвраты этим расчетом не учитываются. Это не проверенная прибыль компании и не рекомендация; только ручное сравнение для вас, без расписания, платежей и заданий.'])
        body='\n\n'.join(lines)
        if _units(body)>3500:_deny()
        return SalesPreview(body,result)
    except ComparisonUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise ComparisonUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except ComparisonUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
