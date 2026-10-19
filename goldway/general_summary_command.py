"""Explicit user-labelled hypothetical summary; no classification or group authority."""
from dataclasses import dataclass
from html import escape
from . import group_preview as core
from .customer_product_draft_command import _gate
from .weekly_review_command import _host, _outbox
from .monthly_task_history import _Reader
from .answer_citations import _hash

HEADER='Подготовь общие итоги по сценарию'
PREFIX='general-summary-preview:'
BASIS='условные сведения и мои метки; не подтверждают факты или разрешение отправки'
_TAGS={'обычное':core.Visibility.ROUTINE,'финансовое':core.Visibility.FINANCIAL,'личное':core.Visibility.PRIVATE,'неизвестно':core.Visibility.UNKNOWN}
_STATUS={'черновик':'proposed','подтверждено':'confirmed','в работе':'in_progress','есть препятствие':'blocked','выполнено':'completed'}
_FIELDS=('Заголовок','Статус','Ответственный','Срок','Следующий шаг')
TEMPLATE=(HEADER+'\nКоличество пунктов: [1 / 2 / 3; для 2–3 пунктов повторите пять строк с номерами 2 и 3]\nОснование: '+BASIS+'\n'
 'Пункт 1. Заголовок: [обычное / финансовое / личное / неизвестно]; [текст]\nПункт 1. Статус: [черновик / подтверждено / в работе / есть препятствие / выполнено]\n'
 'Пункт 1. Ответственный: [метка]; [текст] / не указано\nПункт 1. Срок: [метка]; [текст] / не указано\nПункт 1. Следующий шаг: [метка]; [текст] / не указано')
NOTICE='Черновик не подготовлен. Нужны все поля точной формы, явные метки и статусы; до трёх пунктов. Неполные или слишком длинные сведения не выводятся.\n\n'+TEMPLATE
INTRO='Условный черновик по вашим сведениям и меткам; факты и статусы независимо не проверены.'
FOOTER='Метка „обычное“ не подтверждает безопасность содержания. Проверьте текст вручную. В группу ничего не отправлено; разрешение отправки не создано.'

class SummaryUnavailable(ValueError):
    def __init__(self):super().__init__('general_summary_unavailable')

def _deny():raise SummaryUnavailable()

def _units(value):
    try:return len(value.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_deny()

def is_command(text):return type(text) is str and text.startswith(HEADER)

def _fields(text):
    if type(text) is not str or len(text)>4000 or len(text.encode())>8000:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines) not in (8,13,18) or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if fields.get('Количество пунктов') not in ('1','2','3') or fields.get('Основание')!=BASIS:_deny()
    n=int(fields['Количество пунктов'])
    if set(fields)!={'Количество пунктов','Основание'}|{f'Пункт {i}. {label}' for i in range(1,n+1) for label in _FIELDS}:_deny()
    return fields,n

def _tagged(value,limit,optional=False):
    if optional and value=='не указано':return None
    if '; ' not in value:_deny()
    tag,literal=value.split('; ',1)
    if tag not in _TAGS or not literal or literal!=literal.strip() or literal.startswith('[') or _units(literal)>limit:_deny()
    return core.ReviewedText(literal,_TAGS[tag])

@dataclass(frozen=True)
class SummaryPreview:
    text:str
    result:core.GroupPreview

def build_preview(text,*,scope):
    try:
        if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
        fields,n=_fields(text);items=[]
        for i in range(1,n+1):
            def field(label):return fields[f'Пункт {i}. {label}']
            if field('Статус') not in _STATUS:_deny()
            items.append(core.SummaryItem(_tagged(field('Заголовок'),80),_STATUS[field('Статус')],
                _tagged(field('Ответственный'),120,True),_tagged(field('Срок'),120,True),_tagged(field('Следующий шаг'),120,True)))
        result=core.preview(tuple(items),max_items=3)
        if type(result) is not core.GroupPreview or result.requires_review is not True or result.live_send_authorized is not False:_deny()
        if any(type(v) is not int for v in (result.included_items,result.omitted_items,result.omitted_fields)):_deny()
        if not 0<=result.included_items<=n or not 0<=result.omitted_items<=n or result.included_items+result.omitted_items!=n or not 0<=result.omitted_fields<=3*result.included_items:_deny()
        if type(result.text) is not str or len(result.text)>4000 or any(ord(c)<32 and c!='\n' or ord(c)==127 for c in result.text):_deny()
        lines=result.text.split('\n')
        if not 3<=len(lines)<=14 or lines[0]!='Общие итоги планёрки · черновик для проверки' or lines[-1]!='Требуется проверка содержания и отдельное разрешение отправки.':_deny()
        body=escape(INTRO)+'\n\n'+escape(result.text)+'\n\n'+escape(FOOTER)
        if _units(body)>3500:_deny()
        return SummaryPreview(body,result)
    except SummaryUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError,KeyError,AttributeError):raise SummaryUnavailable() from None


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_preview(payload['text'],scope=initial[0]).text
        except SummaryUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe=PREFIX+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
