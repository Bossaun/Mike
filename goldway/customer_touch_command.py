"""Hypothetical contact-plan conditions -> actual touch core, never a permission."""
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

HEADER='Проверь сценарий касания'
PREFIX='customer-touch-preview:'
MAX_INPUT_CHARS=4000
MAX_INPUT_BYTES=8000
MAX_OUTPUT_UNITS=3500
DRAFT='Проверьте условия условного контакта перед любыми действиями.'
_NOW='Момент сценария'
_BASIS='Время входящего события-основания'
_BCONF='Входящее основание подтверждено в сценарии'
_OBS='Входящее наблюдение разрешения'
_PCONF='Событие разрешения подтверждено'
_GRANT='Разрешение в сценарии'
_OPT='Отказ от контакта в сценарии'
_PSTART='Разрешение действует с'
_PEND='Разрешение действует до'
_READY='Готовность в сценарии'
_START='Плановое окно с'
_END='Плановое окно до'
_PERMISSION=(_OBS,_PCONF,_GRANT,_OPT,_PSTART,_PEND)
_LABELS=(_NOW,_BASIS,_BCONF)+_PERMISSION+(_READY,_START,_END)
_DATE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
_BOOL={'да':True,'нет':False}
_UNKNOWN_BOOL={'да':True,'нет':False,'неизвестно':None}
TEMPLATE=(HEADER+'\n'+ '\n'.join(label+': '+('[да / нет]' if label==_BCONF else
 '[да / нет; неизвестно только при полностью неизвестном наблюдении]' if label in (_PCONF,_OPT) else
 '[да / нет / неизвестно]' if label in (_GRANT,_READY) else
 '[ISO дата-время с часовым смещением / неизвестно]' if label in (_OBS,_PSTART,_PEND) else
 '[ISO дата-время с часовым смещением]') for label in _LABELS))
NOTICE='Проверка не подготовлена. Нужны все поля без повторов и явные даты/отметки. Если дата наблюдения разрешения неизвестна, все шесть полей разрешения должны быть «неизвестно». При известной дате подтверждение события и отказ задаются только «да» или «нет». Противоречивые поля не дают положительного результата.\n\n'+TEMPLATE
_REASONS={
 'unconfirmed_basis':'событие-основание не подтверждено в сценарии',
 'unknown_consent':'наблюдение разрешения не установлено на момент сценария',
 'opt_out':'в сценарии указан отказ от контакта',
 'unknown_or_declined_consent':'разрешение не подтверждено, отклонено или его окно неизвестно',
 'stale_permission_reference':'план ссылается на неактуальное наблюдение разрешения',
 'outside_consent_window':'момент сценария или всё плановое окно не покрыты окном разрешения',
 'unknown_or_not_ready':'готовность неизвестна или не подтверждена',
 'outside_plan_window':'момент сценария вне планового окна',
 'unknown_delivery':'состояние предыдущей доставки неизвестно',
}

class TouchUnavailable(ValueError):
 def __init__(self):super().__init__('customer_touch_preview_unavailable')


def _deny():raise TouchUnavailable()


def _units(text):
 try:return len(text.encode('utf-16-le'))//2
 except (AttributeError,UnicodeError):_deny()


def is_command(text):return type(text) is str and text.startswith(HEADER)


def _date(value):
 if type(value) is not str or not _DATE.fullmatch(value):_deny()
 parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
 if parsed.utcoffset() is None:_deny()
 return parsed


def _window(start,end):
 value=core.Window(_date(start),_date(end))
 if value.end-value.start>timedelta(days=366):_deny()
 return value


def _fields(text):
 if type(text) is not str or len(text)>MAX_INPUT_CHARS or len(text.encode())>MAX_INPUT_BYTES:_deny()
 if any(ord(c)<32 and c!='\n' or ord(c)==127 for c in text):_deny()
 lines=text.split('\n')
 if len(lines)!=13 or lines[0]!=HEADER:_deny()
 fields={}
 for line in lines[1:]:
  if ': ' not in line:_deny()
  label,value=line.split(': ',1)
  if label not in _LABELS or label in fields or not value or value!=value.strip():_deny()
  fields[label]=value
 if set(fields)!=set(_LABELS):_deny()
 return fields


@dataclass(frozen=True)
class TouchScenario:
 text:str
 preview:core.Preview


def build_preview(text,*,scope):
 try:
  if type(scope) is not str or not scope or len(scope.encode())>4096:_deny()
  fields=_fields(text);now=_date(fields[_NOW]);basis_at=_date(fields[_BASIS]);window=_window(fields[_START],fields[_END])
  if fields[_BCONF] not in _BOOL or fields[_READY] not in _UNKNOWN_BOOL:_deny()
  token=hashlib.sha256(json.dumps([scope,text]).encode()).hexdigest()[:32]
  conversation=core.Conversation('scenario:'+token,'supplied-scenario','thread:'+token,'request-supplied-values')
  owner='scenario-owner:'+token;permission_id='permission:'+token;plan_id='plan:'+token
  basis=core.Event('basis:'+token,conversation,basis_at,'supplied-basis:'+token,'incoming',_BOOL[fields[_BCONF]])
  events=(basis,);permissions=()
  if fields[_OBS]=='неизвестно':
   if any(fields[k]!='неизвестно' for k in _PERMISSION):_deny()
  else:
   observed=_date(fields[_OBS])
   if fields[_PCONF] not in _BOOL or fields[_OPT] not in _BOOL or fields[_GRANT] not in _UNKNOWN_BOOL:_deny()
   if fields[_PSTART]=='неизвестно' and fields[_PEND]=='неизвестно':permission_window=None
   else:permission_window=_window(fields[_PSTART],fields[_PEND])
   event=core.Event('permission-event:'+token,conversation,observed,'supplied-permission:'+token,'incoming',_BOOL[fields[_PCONF]])
   permission=core.ContactPermission(permission_id,conversation,event.id,_UNKNOWN_BOOL[fields[_GRANT]],_BOOL[fields[_OPT]],permission_window)
   events+=(event,);permissions=(permission,)
  plan=core.TouchPlan(plan_id,conversation,basis.id,owner,permission_id,_UNKNOWN_BOOL[fields[_READY]],window,DRAFT)
  result=core.touch_preview(events,(plan,),permissions,(),now=now,dispositions=())
  if type(result) is not core.Preview or type(result.candidates) is not tuple or type(result.blocked) is not tuple or len(result.candidates)+len(result.blocked)>1:_deny()
  lines=['<b>Проверка касания · непроверенный условный сценарий</b>',
   'Это не разрешение связаться с реальным клиентом. Личности, согласие, переписка и доставка независимо не проверены.',
   'Момент сценария: '+escape(now.isoformat()),
   'Плановое окно [включая начало, исключая конец): '+escape(window.start.isoformat())+' — '+escape(window.end.isoformat())]
  if result.candidates:
   candidate=result.candidates[0]
   if (type(candidate) is not core.Candidate or candidate.conversation!=conversation or candidate.target_id!=plan.id or candidate.owner_id!=owner
    or candidate.kind!='touch' or candidate.draft_only is not True or candidate.draft!=DRAFT or candidate.due_at.utcoffset() is None):_deny()
   lines.extend(['По условиям сценария правило выделило только черновик для ручной проверки.',
    'Время по условному плану: '+escape(candidate.due_at.isoformat()),escape(candidate.draft)])
  elif result.blocked:
   blocked=result.blocked[0]
   if type(blocked) is not core.Blocked or blocked.conversation!=conversation or blocked.target_id!=plan.id or blocked.reason not in _REASONS:_deny()
   lines.append('По условиям сценария кандидат заблокирован: '+_REASONS[blocked.reason]+'.')
  else:lines.append('По этим условиям правило не выделило кандидата. Это не подтверждение доставки или выполнения реального плана.')
  lines.extend(['Отметки разрешения и готовности — только ваши условия сценария, не права на контакт с кем-либо.',
   'Предыдущие касания, доставка и отметки «обработано/решено» в этой проверке не рассматриваются.',
   'Только ручная проверка для вас. Клиентские сообщения и напоминания не запланированы; права, поручения и аккаунты не изменены.'])
  body='\n\n'.join(lines)
  if _units(body)>MAX_OUTPUT_UNITS:_deny()
  return TouchScenario(body,result)
 except TouchUnavailable:raise
 except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,OverflowError):raise TouchUnavailable() from None


def handle(engine,event,payload):
 host=_host(engine);store=host[0]
 if not store.db.in_transaction:_deny()
 try:initial=_gate(engine,payload,host)
 except (ValueError,TypeError,KeyError,AttributeError):
  store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event));return
 if payload['text']==HEADER:body=TEMPLATE
 else:
  try:body=build_preview(payload['text'],scope=initial[0]).text
  except TouchUnavailable:body=NOTICE
 if _gate(engine,payload,host)!=initial:_deny()
 dedupe=PREFIX+_hash(event)
 if not store.queue(host[2],payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
 if _gate(engine,payload,host)!=initial:_deny()
 identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
 row=_outbox(_Reader(store),host[2],identifier)
 if row['text']!=body or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel']:_deny()
 if _gate(engine,payload,host)!=initial:_deny()
 store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))
