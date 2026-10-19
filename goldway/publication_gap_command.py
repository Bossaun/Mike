"""Manual supplied publication observations -> actual marketing core; no monitor.

All clock/window/coverage assertions are hypothetical inputs, never account facts.
"""
from dataclasses import dataclass
from datetime import datetime,timedelta
from html import escape
import hashlib
import re
from . import marketing_drafts as core
from .customer_product_draft_command import _gate
from .answer_citations import _hash
from .weekly_review_command import _host,_outbox
from .monthly_task_history import _Reader

HEADER='Проверь перерыв в публикациях'
PREFIX='publication-gap-preview:'
MAX_INPUT_CHARS=4000
MAX_INPUT_BYTES=8000
MAX_OUTPUT_UNITS=3500
_CHANNEL='Канал'
_NOW='Момент сценария'
_THRESHOLD='Порог перерыва, часов'
_AGE='Максимальная давность наблюдений, часов'
_OBSERVED='Наблюдено'
_START='Начало наблюдения'
_END='Конец наблюдения'
_COVERAGE='Полнота'
_POSTS='Публикации'
_LABELS=(_CHANNEL,_NOW,_THRESHOLD,_AGE,_OBSERVED,_START,_END,_COVERAGE,_POSTS)
_COVERAGES={'полное':'complete','частичное':'partial','неизвестно':'unknown'}
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
TEMPLATE=(HEADER+'\nКанал: [название сценария]\nМомент сценария: [ISO дата-время с часовым смещением]\n'
 'Порог перерыва, часов: [целое число от 1 до 8784]\nМаксимальная давность наблюдений, часов: [целое число от 1 до 8784]\n'
 'Наблюдено: [ISO дата-время с часовым смещением]\nНачало наблюдения: [ISO дата-время с часовым смещением]\n'
 'Конец наблюдения: [ISO дата-время с часовым смещением]\nПолнота: [полное / частичное / неизвестно]\n'
 'Публикации: [до 12 дат-времён с часовым смещением через ; / нет / неизвестно]')
NOTICE='Проверка не подготовлена. Проверьте поля, размер, даты с часовым смещением и повторения. Неизвестные данные не заменяются нулём.\n\n'+TEMPLATE

class GapUnavailable(ValueError):
 def __init__(self):super().__init__('publication_gap_preview_unavailable')


def _deny():raise GapUnavailable()


def _units(text):
 try:return len(text.encode('utf-16-le'))//2
 except (AttributeError,UnicodeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _unknown(value):return value is None or value=='неизвестно' or value.startswith('[') and value.endswith(']')


def _fields(text):
 if type(text) is not str or len(text)>MAX_INPUT_CHARS or len(text.encode())>MAX_INPUT_BYTES:_deny()
 if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
 lines=text.split('\n')
 if lines[0]!=HEADER or len(lines)>10:_deny()
 fields={}
 for line in lines[1:]:
  if ': ' not in line:_deny()
  name,value=line.split(': ',1)
  if name not in _LABELS or name in fields or not value or value!=value.strip():_deny()
  fields[name]=value
 return fields


def _date(value):
 if type(value) is not str or not _DATE.fullmatch(value):_deny()
 result=datetime.fromisoformat(value.replace('Z','+00:00'))
 if result.utcoffset() is None:_deny()
 return result


def _hours(value):
 if type(value) is not str or not re.fullmatch('[1-9][0-9]{0,3}',value) or not 1<=int(value)<=8784:_deny()
 return timedelta(hours=int(value))


@dataclass(frozen=True)
class GapPreview:
 text:str
 gap:core.PublicationGap


def build_preview(text):
 try:
  fields=_fields(text);label=fields.get(_CHANNEL)
  if _unknown(label) or len(label)>80:_deny()
  now=_date(fields.get(_NOW));threshold=_hours(fields.get(_THRESHOLD));age=_hours(fields.get(_AGE))
  dates={key:None if _unknown(fields.get(key)) else _date(fields[key]) for key in (_OBSERVED,_START,_END)}
  coverage=fields.get(_COVERAGE)
  if coverage not in _COVERAGES and not _unknown(coverage):_deny()
  coverage=_COVERAGES.get(coverage)
  literal=fields.get(_POSTS);posts=None
  if not _unknown(literal):
   if literal=='нет':posts=()
   else:
    parts=literal.split(';')
    if not 1<=len(parts)<=12 or any(not item.strip() for item in parts):_deny()
    posts=tuple(_date(item.strip()) for item in parts)
    if len(set(posts))!=len(posts):_deny()
  token=hashlib.sha256(text.encode()).hexdigest()[:32];channel='scenario-channel:'+token;source='request-supplied-values'
  observation=None
  if coverage is not None and posts is not None and all(value is not None for value in dates.values()):
   publications=tuple(core.Publication('publication:'+token+':'+str(i),channel,at,source) for i,at in enumerate(posts))
   observation=core.PublicationObservation(channel,dates[_OBSERVED],dates[_START],dates[_END],coverage,source,publications)
  gap=core.publication_gap(observation,channel_id=channel,now=now,threshold=threshold,max_observation_age=age)
  if (type(gap) is not core.PublicationGap or gap.review_required is not True or gap.live_authorized is not False or gap.channel_id!=channel or gap.state not in ('gap','recent_publication','unknown')):_deny()
  lines=['<b>Проверка по вашему сценарию · требует проверки</b>',
   'Наблюдения и их полнота указаны вами, независимо не проверены. Это не проверка реального аккаунта.',
   'Канал в сценарии: '+escape(label),'Момент сценария: '+escape(now.isoformat())]
  if gap.as_of is not None:
   lines.append('Срез наблюдений: '+escape(gap.as_of.isoformat())+'\nОкно проверки [включая начало, исключая конец): '+escape(gap.window_start.isoformat())+' — '+escape(gap.as_of.isoformat()))
  else:lines.append('Срез и окно наблюдений: неизвестны; нужны полные поля наблюдения.')
  coverage_labels={'complete':'полное','partial':'частичное','unknown':'неизвестно','missing':'наблюдение не предоставлено полностью'}
  lines.append('Полнота по вашему сообщению: '+coverage_labels[gap.coverage]+'.')
  states={'gap':'По переданным полным наблюдениям в окне есть перерыв: публикации не указаны.',
   'recent_publication':'В переданных наблюдениях есть публикация внутри окна проверки.',
   'unknown':'Нельзя установить перерыв по этим данным: наблюдения неполны, неизвестны, устарели или не покрывают всё окно.'}
  lines.append(states[gap.state])
  if gap.window_publication_count is None:lines.append('Число публикаций в окне: неизвестно.')
  else:lines.append('Число публикаций в окне по переданным данным: '+str(gap.window_publication_count)+'.')
  if gap.last_observed_publication is not None:lines.append('Последняя из переданных публикаций: '+escape(gap.last_observed_publication.isoformat()))
  lines.extend(['Источник сценария: значения из вашего сообщения.',
   'Только ручная проверка сценария для вас. Напоминания и публикации не запланированы; аккаунты, учёт и поручения не изменены.'])
  body='\n\n'.join(lines)
  if _units(body)>MAX_OUTPUT_UNITS:_deny()
  return GapPreview(body,gap)
 except GapUnavailable:raise
 except (ValueError,TypeError,OverflowError,UnicodeError,AttributeError,KeyError):raise GapUnavailable() from None


def handle(engine,event,payload):
 host=_host(engine);store=host[0]
 if not store.db.in_transaction:_deny()
 try:initial=_gate(engine,payload,host)
 except (ValueError,TypeError,KeyError,AttributeError):
  store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
 if payload['text']==HEADER:body=TEMPLATE
 else:
  try:body=build_preview(payload['text']).text
  except GapUnavailable:body=NOTICE
 if _gate(engine,payload,host)!=initial:_deny()
 dedupe=PREFIX+_hash(event)
 if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
 if _gate(engine,payload,host)!=initial:_deny()
 identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
 row=_outbox(_Reader(store),host[2],identifier)
 if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
 if _gate(engine,payload,host)!=initial:_deny()
 store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
