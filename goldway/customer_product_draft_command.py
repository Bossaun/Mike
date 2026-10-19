"""One private supplied-scenario product draft through actual customer_rules.

No customer/source lookup, company verification, model, facts admission or send.
Core `confirmed` means the user's explicit scenario claim ONLY. Dates are supplied,
not today's catalogue truth. One4096-byte/2048UTF16 input and one3500-unit output.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape
import hashlib
import re

from . import customer_rules as core
from .answer_citations import _hash
from .mutation_target import _policy
from .promotion_preview_command import _authorized
from .task_card_reference import _gate as _private_gate
from .weekly_review_command import _host, _same_host, _outbox
from .monthly_task_history import _Reader

HEADER='Подготовь ответ о товаре'
BASIS='значения из этого сообщения, без независимой проверки'
_LABELS=('Название для сценария','Наличие','Цена','Валюта','Доставка','Данные действуют с',
         'Данные действуют до','Срез сценария','Актуальность заявлена мной','Основание')
TEMPLATE=(HEADER+'\nНазвание для сценария: [краткое обозначение товара]\nНаличие: [да / нет / неизвестно]\n'
 'Цена: [число / неизвестно]\nВалюта: [AAA / неизвестно]\nДоставка: [краткий текст / неизвестно]\n'
 'Данные действуют с: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nДанные действуют до: [YYYY-MM-DDTHH:MM:SS+HH:MM]\n'
 'Срез сценария: [YYYY-MM-DDTHH:MM:SS+HH:MM]\nАктуальность заявлена мной: [да / нет / неизвестно]\nОснование: '+BASIS)
NOTICE='Черновик не подготовлен. Нужны все десять полей без повторов, явные значения или «неизвестно» и три даты с часовым поясом.\n\n'+TEMPLATE
_MISSING={'availability':'наличие','price':'цена и валюта','delivery':'доставка'}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})')
_PRICE=re.compile(r'(?:0|[1-9][0-9]{0,8})(?:[.,][0-9]{1,2})?')


class ProductDraftUnavailable(ValueError):
    def __init__(self):super().__init__('supplied_product_draft_unavailable')


def _deny():raise ProductDraftUnavailable()


def _units(text):
    try:return len(text.encode('utf-16-le'))//2
    except (AttributeError,UnicodeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _fields(text):
    if type(text) is not str or len(text)>4096 or _units(text)>2048 or len(text.encode())>4096:_deny()
    if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
    lines=text.split('\n')
    if len(lines)!=11 or lines[0]!=HEADER:_deny()
    fields={}
    for line in lines[1:]:
        if ': ' not in line:_deny()
        key,value=line.split(': ',1)
        if key not in _LABELS or key in fields or not value or value!=value.strip():_deny()
        fields[key]=value
    if set(fields)!=set(_LABELS) or fields['Основание']!=BASIS:_deny()
    return fields


def _date(value):
    if not _DATE.fullmatch(value):_deny()
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset() is None:_deny()
    return parsed


@dataclass(frozen=True)
class ProductPreview:
    text:str
    reply:core.ReplyDraft


def build_product_draft(text):
    """Explicit fields -> actual immutable evidence -> actual core reply/missing."""
    try:
        fields=_fields(text);title=fields['Название для сценария']
        if _units(title)>100 or title.startswith('['):_deny()
        booleans={'да':True,'нет':False,'неизвестно':None}
        if fields['Наличие'] not in booleans or fields['Актуальность заявлена мной'] not in booleans:_deny()
        availability=booleans[fields['Наличие']]
        literal=fields['Цена']
        if literal=='неизвестно':price=None
        elif _PRICE.fullmatch(literal):price=Decimal(literal.replace(',','.'))
        else:_deny()
        currency=fields['Валюта']
        if currency=='неизвестно':currency=None
        elif not re.fullmatch('[A-Z]{3}',currency):_deny()
        delivery=fields['Доставка']
        if delivery=='неизвестно':delivery=None
        elif len(delivery)>400 or delivery.startswith('['):_deny()
        start,end,at=(_date(fields[k]) for k in ('Данные действуют с','Данные действуют до','Срез сценария'))
        # Local scenario identities cannot identify a real customer/account or
        # authorize KB evidence. The only source here is this supplied message.
        token=hashlib.sha256(text.encode()).hexdigest()[:32]
        conversation=core.Conversation('scenario:'+token,'supplied-scenario','request:'+token,'request-supplied-values')
        product='product:'+token
        evidence=core.ProductEvidence('scenario:'+token,conversation,product,availability,price,currency,delivery,
            core.Window(start,end),fields['Актуальность заявлена мной']=='да','request-supplied-values')
        reply=core.product_draft(conversation=conversation,product_id=product,evidence=evidence,now=at)
        if (type(reply) is not core.ReplyDraft or reply.draft_only is not True or reply.conversation!=conversation or
            reply.product_id!=product or type(reply.text) is not str or type(reply.missing) is not tuple or
            any(k not in _MISSING for k in reply.missing) or len(set(reply.missing))!=len(reply.missing)):_deny()
        lines=['<b>Черновик по вашим данным · непроверенный сценарий</b>',
            'Только сценарий по значениям из этого сообщения. Наличие, цены, доставка и актуальность независимо не проверены.',
            'Товар в сценарии: '+escape(title),
            'Срез сценария: '+escape(fields['Срез сценария']),
            'Окно данных: '+escape(fields['Данные действуют с'])+' — '+escape(fields['Данные действуют до']),
            'Ваша отметка актуальности: '+fields['Актуальность заявлена мной']+'.',
            '<b>Текст для ручной проверки</b>\n'+escape(reply.text)]
        if reply.missing:lines.append('Нужно уточнить: '+', '.join(_MISSING[k] for k in reply.missing)+'.')
        else:lines.append('По переданному срезу незаполненных полей нет; достоверность значений не подтверждена.')
        lines.append('Только черновик для вас. Клиенту не отправлен; учёт, поручения и условия компании не изменены.')
        output='\n\n'.join(lines)
        if _units(output)>3500:_deny()
        return ProductPreview(output,reply)
    except ProductDraftUnavailable:raise
    except (ValueError,TypeError,OverflowError,UnicodeError):raise ProductDraftUnavailable() from None


def _gate(engine,payload,host):
    _same_host(engine,host)
    scope=_authorized(engine,payload,host[3])
    own,_=_private_gate(engine,payload,host[3])
    if scope!=own or engine.c['admins']!=[payload['sender']]:_deny()
    return scope,_hash([_policy(engine),engine.c.get('review_mode')])


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    try:initial=_gate(engine,payload,host)
    except (ValueError,TypeError,KeyError,AttributeError):
        store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
    if payload['text']==HEADER:body=TEMPLATE
    else:
        try:body=build_product_draft(payload['text']).text
        except ProductDraftUnavailable:body=NOTICE
    if _gate(engine,payload,host)!=initial:_deny()
    dedupe='product-reply-preview:'+_hash(event)
    if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),
        parse_mode='HTML',preferred_transport=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
    row=_outbox(_Reader(store),host[2],identifier)
    if (row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or
        row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']):_deny()
    if _gate(engine,payload,host)!=initial:_deny()
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
