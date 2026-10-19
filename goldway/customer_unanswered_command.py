"""Manual hypothetical events -> actual unanswered rule; no correspondence scan."""
from dataclasses import dataclass
from datetime import datetime,timedelta
from html import escape
import hashlib
import json
import re
from . import customer_rules as core
from .customer_product_draft_command import _gate
from .answer_citations import _hash
from .weekly_review_command import _host,_outbox
from .monthly_task_history import _Reader

HEADER='Проверь сценарий ожидания ответа'
PREFIX='customer-unanswered-preview:'
MAX_INPUT_CHARS=4000
MAX_INPUT_BYTES=8000
MAX_OUTPUT_UNITS=3500
_LABELS=('Момент сценария','Начало окна входящих','Конец окна входящих','Порог ожидания, часов')
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
_EVENT=re.compile(r'Событие ([1-9]|1[0-2])')
_DIRECTION={'входящее':'incoming','исходящее':'outgoing'}
_CONFIRM={'подтверждено':True,'не подтверждено':False}
TEMPLATE=(HEADER+'\nМомент сценария: [ISO дата-время с часовым смещением]\n'
 'Начало окна входящих: [ISO дата-время с часовым смещением]\nКонец окна входящих: [ISO дата-время с часовым смещением]\n'
 'Порог ожидания, часов: [целое число от 1 до 8784]\n'
 'Событие 1: [входящее / исходящее]; [ISO дата-время с часовым смещением]; [подтверждено / не подтверждено]; [нет / номер более раннего события]')
NOTICE='Проверка не подготовлена. Нужны все поля, от 1 до 12 последовательных событий с явными датами, направлением, подтверждением и связью ответа. Неизвестное подтверждение не считается доставкой.\n\n'+TEMPLATE

class PreviewUnavailable(ValueError):
 def __init__(self):super().__init__('customer_unanswered_preview_unavailable')


def _deny():raise PreviewUnavailable()


def _units(text):
 try:return len(text.encode('utf-16-le'))//2
 except (AttributeError,UnicodeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _date(value):
 if type(value) is not str or not _DATE.fullmatch(value):_deny()
 parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
 if parsed.utcoffset() is None:_deny()
 return parsed


def _parse(text):
 if type(text) is not str or len(text)>MAX_INPUT_CHARS or len(text.encode())>MAX_INPUT_BYTES:_deny()
 if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
 lines=text.split('\n')
 if not 6<=len(lines)<=17 or lines[0]!=HEADER:_deny()
 fields={};events=[]
 for line in lines[1:]:
  if ': ' not in line:_deny()
  label,value=line.split(': ',1)
  if not value or value!=value.strip():_deny()
  if label in _LABELS:
   if label in fields:_deny()
   fields[label]=value;continue
  match=_EVENT.fullmatch(label)
  if match is None or int(match[1])!=len(events)+1:_deny()
  parts=value.split('; ')
  if len(parts)!=4 or parts[0] not in _DIRECTION or parts[2] not in _CONFIRM:_deny()
  at=_date(parts[1]);ordinal=int(match[1]);parent=None
  if parts[3]!='нет':
   if not re.fullmatch('[1-9]|1[0-2]',parts[3]) or not 1<=int(parts[3])<ordinal:_deny()
   parent=int(parts[3])
  if events and at<=events[-1][1]:_deny()
  events.append((ordinal,at,_DIRECTION[parts[0]],_CONFIRM[parts[2]],parent))
 if set(fields)!=set(_LABELS) or not 1<=len(events)<=12:_deny()
 now,start,end=(_date(fields[k]) for k in _LABELS[:3]);window=core.Window(start,end)
 if end-start>timedelta(days=366):_deny()
 literal=fields[_LABELS[3]]
 if not re.fullmatch('[1-9][0-9]{0,3}',literal) or not 1<=int(literal)<=8784:_deny()
 return now,window,timedelta(hours=int(literal)),tuple(events)


@dataclass(frozen=True)
class ScenarioPreview:
 text:str
 preview:core.Preview


def build_preview(text,*,scope):
 try:
  if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
  now,window,threshold,supplied=_parse(text)
  token=hashlib.sha256(json.dumps([scope,text]).encode()).hexdigest()[:32]
  conversation=core.Conversation('scenario:'+token,'supplied-scenario','thread:'+token,'request-supplied-events')
  ids={n:'event:'+token+':'+str(n).zfill(2) for n,*_ in supplied};ordinals={v:k for k,v in ids.items()}
  events=tuple(core.Event(ids[n],conversation,at,'supplied-event:'+token+':'+str(n),direction,confirmed,ids[parent] if parent else None)
   for n,at,direction,confirmed,parent in supplied)
  result=core.unanswered(events,now=now,window=window,stale_after=threshold,dispositions=())
  if type(result) is not core.Preview or type(result.candidates) is not tuple or type(result.blocked) is not tuple or len(result.candidates)+len(result.blocked)>1:_deny()
  lines=['<b>Проверка ожидания ответа · непроверенный сценарий</b>',
   'Только условные события из вашего сообщения. Реальная переписка, личности клиентов и доставка не проверялись.',
   'Момент сценария: '+escape(now.isoformat()),
   'Окно входящих [включая начало, исключая конец): '+escape(window.start.isoformat())+' — '+escape(window.end.isoformat()),
   'Отметка «подтверждено» — ваше условие сценария, а не проверенная квитанция доставки.']
  if result.candidates:
   candidate=result.candidates[0]
   if (type(candidate) is not core.Candidate or candidate.conversation!=conversation or candidate.target_id not in ordinals or candidate.kind!='unanswered'
    or candidate.draft_only is not True or type(candidate.draft) is not str or len(candidate.draft)>2000 or candidate.due_at.utcoffset() is None):_deny()
   lines.extend(['По условиям сценария выделен кандидат: входящее событие №'+str(ordinals[candidate.target_id])+'.',
    'Порог ожидания по правилу наступает: '+escape(candidate.due_at.isoformat()),
    'Черновик для ручной проверки: '+escape(candidate.draft)])
  elif result.blocked:
   blocked=result.blocked[0]
   if type(blocked) is not core.Blocked or blocked.conversation!=conversation or blocked.target_id not in ordinals or blocked.reason!='unconfirmed_message_state':_deny()
   lines.append('По событию №'+str(ordinals[blocked.target_id])+' кандидат заблокирован: состояние одного из последующих событий не подтверждено. Это не подтверждение ответа или доставки.')
  else:lines.append('В переданном сценарии правило не выделило кандидата. Это не подтверждение ответа, полноты истории или отсутствия проблемы.')
  lines.extend(['Прежняя обработка кандидатов и отметки «обработано/решено» в этой проверке не рассматриваются.',
   'Только ручная проверка сценария для вас. Клиентские сообщения и напоминания не запланированы, поручения не созданы.'])
  body='\n\n'.join(lines)
  if _units(body)>MAX_OUTPUT_UNITS:_deny()
  return ScenarioPreview(body,result)
 except PreviewUnavailable:raise
 except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,OverflowError):raise PreviewUnavailable() from None


def handle(engine,event,payload):
 host=_host(engine);store=host[0]
 if not store.db.in_transaction:_deny()
 try:initial=_gate(engine,payload,host)
 except (ValueError,TypeError,KeyError,AttributeError):
  store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
 if payload['text']==HEADER:body=TEMPLATE
 else:
  try:body=build_preview(payload['text'],scope=initial[0]).text
  except PreviewUnavailable:body=NOTICE
 if _gate(engine,payload,host)!=initial:_deny()
 dedupe=PREFIX+_hash(event)
 if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
 if _gate(engine,payload,host)!=initial:_deny()
 identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
 row=_outbox(_Reader(store),host[2],identifier)
 if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
 if _gate(engine,payload,host)!=initial:_deny()
 store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
