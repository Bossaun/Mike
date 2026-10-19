"""Two supplied snapshots checked by actual readonly core; no approval or apply."""
from dataclasses import dataclass
from html import escape
import hashlib
import json
import re

from . import table_change_plan as core
from .table_plan_preview_command import _scalar, _display, KEY_COLUMN
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Проверь конфликт строки по сценарию'
PREFIX='table-conflict-preview:'
BASIS='только два условных снимка и план этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Исходных строк','Проверяемых строк','Количество колонок','Выбранный ключ','Количество изменений',
 'Исходная версия книги','Исходная версия листа','Проверяемая версия книги','Проверяемая версия листа','Основание')
_VERSION=re.compile(r'[A-Za-z0-9_-]{1,16}')
_POSITION=re.compile(r'(?:[1-9][0-9]{0,6}|10000000)')
_CONFLICTS={'Target workbook/sheet/row is missing or ambiguous':'книга, лист или строка отсутствуют либо неоднозначны',
 'Baseline changed: manual edit, row move or revision conflict':'снимок изменился: версия, положение или содержимое'}
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nИсходных строк: [от 1 до 3]\nПроверяемых строк: [от 0 до 3]\nКоличество колонок: [от 1 до 3]\n'
 'Выбранный ключ: [локальный номер от 1 до 3]\nКоличество изменений: [от 1 до 3]\nИсходная версия книги: [например v1]\n'
 'Исходная версия листа: [например v1]\nПроверяемая версия книги: [например v1]\nПроверяемая версия листа: [например v1]\n'
 'Колонка 1: [название]\nИсходная строка 1: [ключ 1–3]; [позиция 1–10000000]; [версия строки]; [типизированное значение]\n'
 'Проверяемая строка 1: [ключ 1–3]; [позиция 1–10000000]; [версия строки]; [типизированное значение]\n'
 'Изменение 1: [номер колонки]; [типизированное новое значение]\nОснование: '+BASIS)
NOTICE='Проверка не подготовлена. Нужны все одиннадцать полей, точные количества и полные снимки. Ключи локальные 1–3, позиции не повторяются внутри снимка, версии — короткие латинские метки. Значения: текст «…», целое, десятичное, логическое да/нет или пусто. При нуле проверяемых строк их строки не добавляются.\n\n'+TEMPLATE


class ConflictUnavailable(ValueError):
    def __init__(self):super().__init__('table_conflict_scenario_unavailable')


def _deny():raise ConflictUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if not 15<=len(lines)<=24 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if not set(_LABELS)<=set(fields) or fields['Основание']!=BASIS:_deny()
    for key in ('Исходных строк','Количество колонок','Выбранный ключ','Количество изменений'):
        if fields[key] not in ('1','2','3'):_deny()
    if fields['Проверяемых строк'] not in ('0','1','2','3'):_deny()
    baseline,current,columns,changes=(int(fields[k]) for k in ('Исходных строк','Проверяемых строк','Количество колонок','Количество изменений'))
    expected=set(_LABELS)|{'Колонка '+str(i) for i in range(1,columns+1)}|{'Исходная строка '+str(i) for i in range(1,baseline+1)}|{'Проверяемая строка '+str(i) for i in range(1,current+1)}|{'Изменение '+str(i) for i in range(1,changes+1)}
    if set(fields)!=expected:_deny()
    for key in ('Исходная версия книги','Исходная версия листа','Проверяемая версия книги','Проверяемая версия листа'):
        if not _VERSION.fullmatch(fields[key]):_deny()
    return fields,baseline,current,columns,changes


def _snapshot(fields,prefix,count,columns,token,book_version,sheet_version):
    rows=[]
    for ordinal in range(1,count+1):
        row=fields[prefix+' строка '+str(ordinal)].split('; ')
        if len(row)!=columns+3 or row[0] not in ('1','2','3') or not _POSITION.fullmatch(row[1]) or not _VERSION.fullmatch(row[2]):_deny()
        cells=(core.Cell(KEY_COLUMN,'scenario-key:'+token+':'+row[0]),)+tuple(core.Cell('C'+str(i),_scalar(value)) for i,value in enumerate(row[3:],1))
        rows.append(core.Row(int(row[1]),'scenario-version:'+row[2],cells))
    sheet=core.Sheet('scenario-sheet:'+token,'scenario-version:'+sheet_version,tuple(rows))
    return core.Workbook('scenario-book:'+token,'scenario-version:'+book_version,(sheet,))


@dataclass(frozen=True)
class ConflictPreview:
    text:str
    plan:core.ChangePlan
    compatible:bool
    conflict_class:str|None


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,base_count,current_count,column_count,change_count=_fields(text);title=fields['Название сценария']
        if _units(title)>80 or title.startswith('['):_deny()
        labels={}
        for i in range(1,column_count+1):
            label=fields['Колонка '+str(i)]
            if _units(label)>80 or label.startswith('[') or label in labels.values():_deny()
            labels['C'+str(i)]=label
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        baseline=_snapshot(fields,'Исходная',base_count,column_count,token,fields['Исходная версия книги'],fields['Исходная версия листа'])
        current=_snapshot(fields,'Проверяемая',current_count,column_count,token,fields['Проверяемая версия книги'],fields['Проверяемая версия листа'])
        identity=core.RowIdentity(KEY_COLUMN,'scenario-key:'+token+':'+fields['Выбранный ключ'])
        desired=[]
        for i in range(1,change_count+1):
            pair=fields['Изменение '+str(i)].split('; ')
            if len(pair)!=2 or pair[0] not in ('1','2','3'):_deny()
            desired.append(core.Cell('C'+pair[0],_scalar(pair[1])))
        plan=core.build_plan(baseline,workbook_id=baseline.id,sheet_id=baseline.sheets[0].id,identity=identity,desired=tuple(desired))
        if type(plan) is not core.ChangePlan or plan.identity!=identity or plan.baseline not in baseline.sheets[0].rows or type(plan.changes) is not tuple or len(plan.changes)!=change_count:_deny()
        seen=set()
        for change in plan.changes:
            if type(change) is not core.Change or change.column not in labels or change.column in seen:_deny()
            seen.add(change.column)
        conflict=None
        try:row=core.check_plan_baseline(current,plan)
        except core.ConflictError as error:
            if type(error) is not core.ConflictError or str(error) not in _CONFLICTS:_deny()
            conflict=_CONFLICTS[str(error)]
        else:
            if type(row) is not core.Row or not any(row is candidate for candidate in current.sheets[0].rows) or not any(cell.column==identity.column and type(cell.value) is str and cell.value==identity.value for cell in row.cells):_deny()
        output=['<b>Конфликт строки · непроверенный сценарий</b>',
            'Два переданных условных снимка, независимо не проверенных. Реальная таблица не читалась; это не проверка её актуальности.',
            'Название сценария: '+escape(title),'Выбран локальный ключ №'+fields['Выбранный ключ']+'; исходных строк: '+str(base_count)+'; проверяемых: '+str(current_count)+'.',
            '<b>План по исходному снимку</b>']
        for change in plan.changes:output.append('Колонка '+change.column[1:]+' · '+escape(labels[change.column])+':\nБыло: '+_display(change.before)+'\nПредлагается: '+_display(change.after))
        if conflict is None:output.append('<b>Совместимо с переданным исходным снимком.</b> Проверка не создаёт одобрение или право записи.')
        else:output.append('<b>Конфликт: '+conflict+'.</b> Переносить план на этот снимок нельзя без нового разбора; отдельная причина этим результатом не устанавливается.')
        output.append('Это ручное сравнение предоставленных данных, не разрешение изменять рабочую таблицу. Одобрение, запись, задачи и внешние действия не выполнялись.')
        body='\n\n'.join(output)
        if _units(body)>3500:_deny()
        return ConflictPreview(body,plan,conflict is None,conflict)
    except ConflictUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise ConflictUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except ConflictUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
