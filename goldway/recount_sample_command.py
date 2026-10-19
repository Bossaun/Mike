"""Two explicit bounded recount lists; no padding, stock update or count claim."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape
import hashlib
import json
import re

from . import inventory_metrics as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Сверь выборку пересчёта'
PREFIX='recount-sample-preview:'
BASIS='только два условных списка этого сообщения; независимо не проверены'
_LABELS=('Название сценария','Дата наблюдения','Единица','Учётных строк','Фактических строк','Основание')
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_DECIMAL=re.compile(r'(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,8})?')
_STATES={core.RecountState.COMPARABLE:'числа сопоставимы по сценарию',
 core.RecountState.MISSING_BOOK:'в переданном учётном списке нет строки',
 core.RecountState.MISSING_PHYSICAL:'в переданном списке подсчёта нет строки',
 core.RecountState.UNKNOWN_QUANTITY:'в переданной строке неизвестно количество',
 core.RecountState.UNRESOLVED_KEY:'размерности ключа не уточнены; числовое сопоставление не выполнено'}
TEMPLATE=(HEADER+'\nНазвание сценария: [краткая метка]\nДата наблюдения: [YYYY-MM-DD]\nЕдиница: [явная единица количества]\n'
 'Учётных строк: [от 0 до 5]\nФактических строк: [от 0 до 5; хотя бы один список непустой]\n'
 'Учёт 1: [SKU]; [вариант или неизвестно]; [место или неизвестно]; [партия или неизвестно]; [количество или неизвестно]\n'
 'Факт 1: [SKU]; [вариант или неизвестно]; [место или неизвестно]; [партия или неизвестно]; [количество или неизвестно]\nОснование: '+BASIS)
NOTICE='Выборка не сверена. Нужны все шесть полей и точные количества строк двух списков; при нуле строк они не добавляются. SKU, дата и единица обязательны. До пяти разных точных ключей; повторы внутри списка и частичные строки не допускаются.\n\n'+TEMPLATE


class SampleUnavailable(ValueError):
    def __init__(self):super().__init__('recount_sample_unavailable')


def _deny():raise SampleUnavailable()


def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if not 8<=len(lines)<=17 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if not set(_LABELS)<=set(fields) or fields['Основание']!=BASIS:_deny()
    for key in ('Учётных строк','Фактических строк'):
        if fields[key] not in ('0','1','2','3','4','5'):_deny()
    b,p=int(fields['Учётных строк']),int(fields['Фактических строк'])
    if b+p==0:_deny()
    if set(fields)!=set(_LABELS)|{'Учёт '+str(i) for i in range(1,b+1)}|{'Факт '+str(i) for i in range(1,p+1)}:_deny()
    return fields,b,p


def _literal(value,maximum):
    if not value or value!=value.strip() or _units(value)>maximum or value.startswith('['):_deny()
    return value


def _dimension(value):return core.UNKNOWN if value=='неизвестно' else _literal(value,60)


def _quantity(value):
    if value=='неизвестно':return core.UNKNOWN
    if not _DECIMAL.fullmatch(value):_deny()
    return Decimal(value)


def _same_quantity(left,right):
    if left is core.UNKNOWN or right is core.UNKNOWN:return left is right
    return type(left) is type(right) is Decimal and left.as_tuple()==right.as_tuple()


def _valid(lines,book,physical,unit,observed):
    books={r.key:r for r in book};counts={r.key:r for r in physical};union=set(books)|set(counts)
    if type(lines) is not tuple or len(lines)!=len(union):_deny()
    seen=set()
    for line in lines:
        if type(line) is not core.RecountLine or line.key not in union or line.key in seen or line.unit!=unit or line.observed_on!=observed or type(line.state) is not core.RecountState:_deny()
        seen.add(line.key);b,p=books.get(line.key),counts.get(line.key)
        if not _same_quantity(line.book_units,b.units if b is not None else core.UNKNOWN) or not _same_quantity(line.physical_units,p.units if p is not None else core.UNKNOWN):_deny()
        if line.book_source!=(b.source_id if b is not None else core.UNKNOWN) or line.physical_source!=(p.source_id if p is not None else core.UNKNOWN):_deny()
        if line.state is core.RecountState.COMPARABLE:
            if type(line.book_units) is not Decimal or type(line.physical_units) is not Decimal or any(v is core.UNKNOWN for v in (line.key.item.variant,line.key.location,line.key.batch)):_deny()
            if type(line.delta) is not Decimal or not line.delta.is_finite() or len(str(line.delta))>256:_deny()
        elif line.delta is not core.UNKNOWN:_deny()


@dataclass(frozen=True)
class SamplePreview:
    text:str
    lines:tuple[core.RecountLine,...]


def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,book_count,physical_count=_fields(text);title=_literal(fields['Название сценария'],80);unit=_literal(fields['Единица'],40)
        if unit=='неизвестно' or not _DATE.fullmatch(fields['Дата наблюдения']):_deny()
        observed=date.fromisoformat(fields['Дата наблюдения'])
        token=hashlib.sha256(json.dumps([scope,text],ensure_ascii=False).encode()).hexdigest()[:32]
        def snapshots(label,count,kind):
            rows=[]
            for ordinal in range(1,count+1):
                values=fields[label+' '+str(ordinal)].split('; ')
                if len(values)!=5:_deny()
                sku=_literal(values[0],60)
                if sku=='неизвестно':_deny()
                key=core.StockKey(core.ItemKey(sku,_dimension(values[1])),_dimension(values[2]),_dimension(values[3]))
                rows.append(core.StockSnapshot(key,_quantity(values[4]),unit,observed,'supplied-'+kind.value+':'+token+':'+str(ordinal),kind))
            if len({row.key for row in rows})!=len(rows):_deny()
            return tuple(rows)
        book=snapshots('Учёт',book_count,core.StockKind.BOOK);physical=snapshots('Факт',physical_count,core.StockKind.PHYSICAL)
        if len({row.key for row in (*book,*physical)})>5:_deny()
        lines=core.recount(book,physical)
        _valid(lines,book,physical,unit,observed)
        def shown(value):return 'неизвестно' if value is core.UNKNOWN else escape(str(value))
        output=['<b>Выборка пересчёта · непроверенный сценарий</b>',
            'Только два переданных условных списка. Учёт и подсчёт независимо не проверены; наличие строки не подтверждает реальный пересчёт.',
            'Название сценария: '+escape(title),'Дата по сообщению: '+observed.isoformat()+'; единица: '+escape(unit)+'.\nУчётных строк: '+str(book_count)+'; строк подсчёта: '+str(physical_count)+'.']
        for ordinal,line in enumerate(lines,1):
            output.append('<b>Позиция '+str(ordinal)+' · SKU '+escape(line.key.item.sku)+'</b>\nВариант: '+shown(line.key.item.variant)+'; место: '+shown(line.key.location)+'; партия: '+shown(line.key.batch)+'.\n'
                'Учёт по списку: '+shown(line.book_units)+'; факт по списку: '+shown(line.physical_units)+'.\nРезультат: '+_STATES[line.state]+'.\n'
                'Разница факт − учёт: '+(('0' if line.delta==0 else format(line.delta,'+f')) if line.state is core.RecountState.COMPARABLE else 'не определена')+'.')
        output.append('Отсутствие строки означает только отсутствие в переданном списке. Это не вывод о реальных остатках, потерях или завершении пересчёта. Остатки, складские данные и задания не изменены.')
        body='\n\n'.join(output)
        if _units(body)>3500:_deny()
        return SamplePreview(body,lines)
    except SampleUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise SampleUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except SampleUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
