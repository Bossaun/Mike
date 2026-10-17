"""Explicit bounded catalog sample -> real completeness; no catalog/photo I/O."""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re

from .inventory_metrics import UNKNOWN, ItemKey, CatalogObservation, completeness
from .promotion_preview_command import _authorized

MAX_INPUT_CHARS=4000
MAX_OUTPUT_UNITS=3500
MAX_ROWS=12
_REQUEST='проверь карточки товаров'
_HEADER='проверка карточек товаров'
_LABELS={'товар':'item','вариант':'variant','дата наблюдения':'date','фото':'photo','цена':'price'}
_FIELDS=set(_LABELS.values())
_NUMBER=re.compile(r'(?:0|[1-9][0-9]{0,11})(?:[.,][0-9]{1,6})?')
_UNKNOWN={'','[уточнить]','неизвестно','[неизвестно]'}
_TEMPLATE=('Проверка карточек товаров\nВалюта цен: [уточнить]\n'
 'Товар: [уточнить]; Вариант: [уточнить]; Дата наблюдения: [уточнить]; Фото: неизвестно; Цена: неизвестно')
_LIMITATION=('Проверена только переданная выборка. Фото и цены — ваши указания; '
 'изображения, карточки и цены не загружались и не проверялись независимо. '
 'Это не проверка всего каталога. Ничего не изменено.')
_NOTICE=('Выборка не проверена. После заголовка укажите «Валюта цен: RUB» либо [уточнить], '
 'затем 1–12 строк: пять полей через ;, без повторов. Товар — до 48, вариант — до 32 знаков; эмодзи могут занимать два. '
 'дата — ДД.ММ.ГГГГ или ГГГГ-ММ-ДД, одна дата для выборки. '
 'Фото: да/нет/неизвестно. Цена: число, «отсутствует» или «неизвестно»; '
 '0 — указанная цена, не отсутствие. Для числовых цен нужна одна явная валюта из трёх заглавных латинских букв. '
 'Числа: до 12 цифр до запятой и 6 после, без знаков и степеней.\n\n'+_TEMPLATE)


class CatalogCompletenessUnavailable(ValueError):
    def __init__(self):super().__init__('catalog_completeness_preview_unavailable')


@dataclass(frozen=True)
class CatalogCompletenessDraft:
    text:str
    state:str
    missing_fields:tuple[str,...]=()
    review_required:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


def _normal(value):return ' '.join(value.casefold().replace('ё','е').split())


def is_catalog_completeness_command(text):
    if type(text) is not str:return False
    first=text[:65].split('\n',1)[0]
    return len(first)<=64 and _normal(first) in (_REQUEST,_HEADER)


def _unknown(value):return value.casefold() in _UNKNOWN


def _date(value):
    if _unknown(value):return None
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):return date.fromisoformat(value)
    if re.fullmatch(r'[0-9]{2}\.[0-9]{2}\.[0-9]{4}',value):return datetime.strptime(value,'%d.%m.%Y').date()
    raise CatalogCompletenessUnavailable()


def _photo(value):
    if _unknown(value):return UNKNOWN
    if value.casefold()=='да':return True
    if value.casefold()=='нет':return False
    raise CatalogCompletenessUnavailable()


def _price(value):
    if _unknown(value):return UNKNOWN
    if value.casefold()=='отсутствует':return None
    if _NUMBER.fullmatch(value) is None:raise CatalogCompletenessUnavailable()
    return Decimal(value.replace(',','.'))


def _parse(text):
    if (type(text) is not str or len(text)>MAX_INPUT_CHARS or
        any((ord(c)<32 and c not in '\r\n') or ord(c)==127 for c in text)):
        raise CatalogCompletenessUnavailable()
    text.encode('utf-8')
    lines=text.splitlines()
    if not lines or _normal(lines[0]) not in (_REQUEST,_HEADER) or len(lines)>26:
        raise CatalogCompletenessUnavailable()
    body=[line for line in lines[1:] if line.strip()]
    if len(body)<2 or ':' not in body[0]:raise CatalogCompletenessUnavailable()
    label,currency=body[0].split(':',1);currency=currency.strip()
    if _normal(label)!='валюта цен' or len(currency)>32:raise CatalogCompletenessUnavailable()
    if _unknown(currency):currency=None
    elif re.fullmatch(r'[A-Z]{3}',currency) is None:raise CatalogCompletenessUnavailable()
    if not 1<=len(body[1:])<=MAX_ROWS:raise CatalogCompletenessUnavailable()
    rows=[];seen=set();dates=set();missing=[]
    for number,line in enumerate(body[1:],1):
        pieces=line.split(';')
        if len(pieces)!=5:raise CatalogCompletenessUnavailable()
        values={}
        for part in pieces:
            if ':' not in part:raise CatalogCompletenessUnavailable()
            label,value=part.split(':',1);key=_LABELS.get(_normal(label));value=value.strip()
            if key is None or key in values:raise CatalogCompletenessUnavailable()
            # Identity labels never truncate; UTF16 caps also bound repeated lists.
            cap={'item':48,'variant':32,'date':10,'photo':16,'price':32}[key]
            if not _unknown(value) and len(value.encode('utf-16-le'))//2>cap:raise CatalogCompletenessUnavailable()
            values[key]=value
        if set(values)!=_FIELDS:raise CatalogCompletenessUnavailable()
        observed=_date(values['date']);photo=_photo(values['photo']);price=_price(values['price'])
        item=None if _unknown(values['item']) else values['item']
        variant=None if _unknown(values['variant']) else values['variant']
        for label,value in (('Товар',item),('Вариант',variant),('Дата наблюдения',observed)):
            if value is None:missing.append('строка '+str(number)+': '+label)
        if observed is not None:dates.add(observed)
        if item is not None and variant is not None:
            identity=(item,variant)
            if identity in seen:raise CatalogCompletenessUnavailable()
            seen.add(identity)
        rows.append((item,variant,observed,photo,price))
    if len(dates)>1:raise CatalogCompletenessUnavailable()
    if any(type(row[4]) is Decimal for row in rows) and currency is None:missing.append('Валюта цен')
    return currency,tuple(rows),tuple(missing)


def _label(item):return item.sku+' — '+item.variant


def _listing(title,items):
    return title+' ('+str(len(items))+'): '+('; '.join(_label(item) for item in items) if items else 'нет в этой выборке')+'.'


def build_catalog_completeness_preview(text, *, business, event_id):
    try:
        currency,rows,missing=_parse(text)
        if missing:
            output=('Нужно уточнить: '+', '.join(missing)+'. '
                    'Итоги выборки не рассчитаны; неизвестные поля не заменены нулями.\n\n'+_TEMPLATE)
            if len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise CatalogCompletenessUnavailable()
            return CatalogCompletenessDraft(output,'incomplete_inputs',missing)
        if type(business) is not str or not 0<len(business)<=128 or type(event_id) is not str or not 0<len(event_id)<=256:
            raise CatalogCompletenessUnavailable()
        source=hashlib.sha256(json.dumps([business,event_id]).encode()).hexdigest()
        observations=tuple(CatalogObservation(ItemKey(item,variant),photo,price,observed,
            'supplied:catalog:'+source+':'+str(number))
            for number,(item,variant,observed,photo,price) in enumerate(rows,1))
        result=completeness(observations)  # once, only after the WHOLE block validates.
        zero=tuple(row.item for row in observations if type(row.price) is Decimal and row.price==0)
        known_photos=len(rows)-len(result.missing_photos)-len(result.unknown_photos)
        known_prices=len(rows)-len(result.missing_prices)-len(result.unknown_prices)
        lines=['Проверка переданных карточек — выборка из '+str(len(rows))+' строк.',
            'Дата наблюдения по сообщению: '+rows[0][2].isoformat()+'.',
            ('Валюта числовых цен: '+currency+'.') if currency is not None else
            'Валюта не указана; числовые цены не переданы.',
            'Фото по сообщению: есть '+str(known_photos)+', отсутствует '+str(result.missing_photo_count)+
            ', неизвестно '+str(len(result.unknown_photos))+'.',
            'Цена по сообщению: числовая '+str(known_prices)+' (из них 0: '+str(len(zero))+'), отсутствует '+
            str(result.missing_price_count)+', неизвестна '+str(len(result.unknown_prices))+'.',
            _listing('Без фото',result.missing_photos),_listing('Фото неизвестно',result.unknown_photos),
            _listing('Без цены',result.missing_prices),_listing('Цена неизвестна',result.unknown_prices),
            _listing('Цена 0'+(' '+currency if currency else ''),zero),_LIMITATION]
        output='\n'.join(lines)
        if len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise CatalogCompletenessUnavailable()
        return CatalogCompletenessDraft(output,'sample_checked')
    except CatalogCompletenessUnavailable:raise
    except (ValueError,TypeError,ArithmeticError,UnicodeError):raise CatalogCompletenessUnavailable() from None


def handle_catalog_completeness(engine,event_id,payload):
    if (type(payload) is not dict or not is_catalog_completeness_command(payload.get('text'))
        or not engine.s.db.in_transaction):raise CatalogCompletenessUnavailable()
    intelligence=engine.intelligence
    try:scope=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text=_NOTICE
    try:
        if len(payload['text'])>MAX_INPUT_CHARS:raise CatalogCompletenessUnavailable()
        text=_TEMPLATE if _normal(payload['text'])==_REQUEST else build_catalog_completeness_preview(payload['text'],business=engine.b,event_id=event_id).text
    except CatalogCompletenessUnavailable:pass
    if _authorized(engine,payload,intelligence)!=scope:raise CatalogCompletenessUnavailable()
    engine.s.queue(engine.b,payload['sender'],text,'catalog-completeness:'+event_id,engine.now(),None,
                   preferred_transport=payload['channel'])
    if _authorized(engine,payload,intelligence)!=scope:raise CatalogCompletenessUnavailable()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
