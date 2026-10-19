"""Exact local typed row change draft; no approvals, apply or freshness claim."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
import hashlib
import json
import re

from . import table_change_plan as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Подготовь изменение строки по сценарию'
PREFIX='table-plan-preview:'
BASIS='только условный снимок и изменения этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Количество строк','Количество колонок','Выбранная строка','Количество изменений','Основание')
_INTEGER=re.compile(r'-?(?:0|[1-9][0-9]{0,18})')
_DECIMAL=re.compile(r'-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?')
KEY_COLUMN='__scenario_row_key'
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nКоличество строк: [от 1 до 3]\nКоличество колонок: [от 1 до 3]\n'
 'Выбранная строка: [от 1 до 3]\nКоличество изменений: [от 1 до 3]\nКолонка 1: [название]\n'
 'Строка 1: [значение колонки 1]\nИзменение 1: [номер колонки]; [новое значение]\nОснование: '+BASIS)
NOTICE='Черновик не подготовлен. Укажите все шесть полей и точные строки колонок, данных и изменений. Значения: текст «…», целое 1, десятичное 1.00, логическое да/нет или пусто. Все запрошенные изменения должны отличаться от снимка; запись не выполняется.\n\n'+TEMPLATE


class PlanUnavailable(ValueError):
    def __init__(self):super().__init__('table_plan_scenario_unavailable')


def _deny():raise PlanUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if not 10<=len(lines)<=16 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if not set(_LABELS)<=set(fields) or fields['Основание']!=BASIS:_deny()
    for key in ('Количество строк','Количество колонок','Выбранная строка','Количество изменений'):
        if fields[key] not in ('1','2','3'):_deny()
    rows,columns,changes=(int(fields[key]) for key in ('Количество строк','Количество колонок','Количество изменений'))
    expected=set(_LABELS)|{'Колонка '+str(i) for i in range(1,columns+1)}|{'Строка '+str(i) for i in range(1,rows+1)}|{'Изменение '+str(i) for i in range(1,changes+1)}
    if set(fields)!=expected:_deny()
    return fields,rows,columns,changes


def _scalar(value):
    if value=='пусто':return None
    if value in ('логическое да','логическое нет'):return value=='логическое да'
    if value.startswith('целое '):
        raw=value[6:]
        if not _INTEGER.fullmatch(raw):_deny()
        number=int(raw)
        if abs(number)>10**18:_deny()
        return number
    if value.startswith('десятичное '):
        raw=value[11:]
        if not _DECIMAL.fullmatch(raw):_deny()
        return Decimal(raw)
    if value.startswith('текст «') and value.endswith('»'):
        raw=value[7:-1]
        if _units(raw)>160 or any(c in raw for c in ('«','»',';')):_deny()
        return raw
    _deny()


def _decoded(tag):
    if type(tag) is not list or len(tag)!=2:_deny()
    kind,raw=tag
    if kind=='null' and raw is None:return None
    if kind=='bool' and type(raw) is bool:return raw
    if kind=='str' and type(raw) is str and _units(raw)<=160 and not any(ord(c)<32 or ord(c)==127 for c in raw):return raw
    if kind=='int' and type(raw) is str and _INTEGER.fullmatch(raw):
        value=int(raw)
        if abs(value)<=10**18 and str(value)==raw:return value
    if kind=='decimal' and type(raw) is str and len(raw)<=100:
        value=Decimal(raw)
        if value.is_finite() and len(value.as_tuple().digits)<=50 and abs(value.as_tuple().exponent)<=18 and str(value)==raw:return value
    _deny()


def _same(left,right):
    return type(left) is type(right) and (left.as_tuple()==right.as_tuple() if type(left) is Decimal else left==right)


def _display(value):
    if value is None:return 'пусто (нет значения)'
    if type(value) is bool:return 'логическое '+('да' if value else 'нет')
    if type(value) is int:return 'целое '+str(value)
    if type(value) is Decimal:return 'десятичное '+escape(str(value))
    if type(value) is str:return 'текст «'+escape(value)+'»'
    _deny()


def _validated_preview(plan,preview,book,sheet,identity,desired,columns):
    if type(plan) is not core.ChangePlan or plan.workbook_id!=book.id or plan.workbook_revision!=book.revision or plan.sheet_id!=sheet.id or plan.sheet_revision!=sheet.revision or plan.identity!=identity or plan.baseline not in sheet.rows:_deny()
    if type(plan.changes) is not tuple or len(plan.changes)!=len(desired):_deny()
    actual={}
    for change in plan.changes:
        if type(change) is not core.Change or change.column not in columns or change.column in actual:_deny()
        actual[change.column]=change
    if set(actual)!={cell.column for cell in desired}:_deny()
    for cell in desired:
        if not _same(actual[cell.column].after,cell.value):_deny()
    keys={'format','workbook_id','workbook_revision','sheet_id','sheet_revision','identity','baseline','changes','digest'}
    if type(preview) is not dict or set(preview)!=keys or preview['format']!='fake-workbook-change-plan-v1':_deny()
    for key in ('workbook_id','workbook_revision','sheet_id','sheet_revision'):
        if preview[key]!=getattr(plan,key):_deny()
    if preview['identity']!={'column':identity.column,'value':identity.value} or type(preview['digest']) is not str or not re.fullmatch('[0-9a-f]{64}',preview['digest']):_deny()
    baseline=preview['baseline']
    if type(baseline) is not dict or set(baseline)!={'position','revision','cells'} or type(baseline['position']) is not int or baseline['position']!=plan.baseline.position or baseline['revision']!=plan.baseline.revision:_deny()
    cells=baseline['cells']
    if type(cells) is not list or len(cells)!=len(plan.baseline.cells):_deny()
    for detached,original in zip(cells,plan.baseline.cells):
        if type(detached) is not list or len(detached)!=2 or detached[0]!=original.column or not _same(_decoded(detached[1]),original.value):_deny()
    changes=preview['changes']
    if type(changes) is not list or len(changes)!=len(actual):_deny()
    seen=set();render=[]
    for item in changes:
        if type(item) is not dict or set(item)!={'column','before','after'} or type(item['column']) is not str or item['column'] not in actual or item['column'] in seen:_deny()
        column=item['column'];seen.add(column);change=actual[column]
        before,after=_decoded(item['before']),_decoded(item['after'])
        if not _same(before,change.before) or not _same(after,change.after):_deny()
        render.append((column,before,after))
    return tuple(render)


@dataclass(frozen=True)
class TablePreview:
    text:str
    plan:core.ChangePlan


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,row_count,column_count,change_count=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        labels={}
        for i in range(1,column_count+1):
            label=fields['Колонка '+str(i)]
            if _units(label)>80 or label.startswith('[') or label in labels.values():_deny()
            labels['C'+str(i)]=label
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        rows=[]
        for i in range(1,row_count+1):
            values=fields['Строка '+str(i)].split('; ')
            if len(values)!=column_count:_deny()
            cells=(core.Cell(KEY_COLUMN,'scenario-key:'+token+':'+str(i)),)+tuple(core.Cell('C'+str(j),_scalar(value)) for j,value in enumerate(values,1))
            rows.append(core.Row(i,'scenario-row-v1',cells))
        sheet=core.Sheet('scenario-sheet:'+token,'scenario-sheet-v1',tuple(rows))
        book=core.Workbook('scenario-workbook:'+token,'scenario-workbook-v1',(sheet,))
        selected=fields['Выбранная строка'];identity=core.RowIdentity(KEY_COLUMN,'scenario-key:'+token+':'+selected)
        desired=[]
        for i in range(1,change_count+1):
            pair=fields['Изменение '+str(i)].split('; ')
            if len(pair)!=2 or pair[0] not in ('1','2','3'):_deny()
            desired.append(core.Cell('C'+pair[0],_scalar(pair[1])))
        plan=core.build_plan(book,workbook_id=book.id,sheet_id=sheet.id,identity=identity,desired=tuple(desired))
        detached=core.preview(plan)
        render=_validated_preview(plan,detached,book,sheet,identity,desired,labels)
        output=['<b>Черновик изменения — ничего не записано</b>',
            'Только переданный условный снимок, независимо не проверенный. Реальная рабочая таблица не читалась; актуальность строки и ручные правки не проверялись.',
            'Название сценария: '+escape(title),'Выбрана локальная строка №'+selected+' из '+str(row_count)+'; колонок в снимке: '+str(column_count)+'.']
        for column,before,after in render:
            output.append('<b>Колонка '+column[1:]+' · '+escape(labels[column])+'</b>\nБыло: '+_display(before)+'\nПредлагается: '+_display(after))
        output.append('Это подготовка данных, а не согласование записи. Одобрение и право изменять таблицу не созданы; запись, проверка конфликтов в рабочей таблице, задачи и внешние действия не выполнялись.')
        body='\n\n'.join(output)
        if _units(body)>3500:_deny()
        return TablePreview(body,plan)
    except PlanUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError,InvalidOperation):raise PlanUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except PlanUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
