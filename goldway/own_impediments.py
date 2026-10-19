"""Current own blocked-task snapshot; no causal inference or task writes.

One cumulative _PassReader per build (two observations+receipt), one per guard:
2048 queries/8192 rows/4MiB accounting each. Register<=128, refs<=256, visible<=5.
Saved as-of time is sealed; current clock is used only for current authorization.
"""
from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo
from .answer_citations import _hash
from .meeting_binding import _gate as _private_admin
from .monthly_task_history import _current,_bounded_columns,_text,MonthlyHistoryUnavailable
from .own_task_overview import _PassReader
from .task_card_command import _snapshot,CardUnavailable
from .task_card_reference import _barrier,_protected
from .weekly_review_command import _host,_same_host,_outbox,_seal,_accepted
from .presentation import clean

PREFIX='own-impediments:'
NOTICE='Не удалось показать препятствия по собственным задачам. Проверьте текущий доступ и повторите запрос.'
CANCEL='Own impediment snapshot changed; request a fresh view'
_ACTIVE={'confirmed','in_progress','blocked'}
_STATUSES=_ACTIVE|{'proposed','completed'}


def _deny():raise ValueError('own_impediments_unavailable')


def is_command(text):
 return type(text) is str and len(text)<=128 and len(text.encode())<=160 and ' '.join(text.casefold().split()).rstrip('?!.' )=='что мешает моим задачам'


def _gate(engine,payload,host):
 _same_host(engine,host)
 scope,policy=_private_admin(engine,payload,host[3])
 if type(engine.now()) not in (int,float) or not 0<engine.now()<2**53:_deny()
 return scope,policy


def _collect(engine,payload,reader,host):
 scope,policy=_gate(engine,payload,host);now=engine.now()
 ids=reader.query('SELECT '+_bounded_columns({'id':_text('id',160)})+' FROM tasks WHERE business=?',(host[2],),maximum=128)
 records,_=_current(reader,host[2],tuple(row['id'] for row in ids));allowed=[];refs=set();states=[]
 for row in sorted(records.values(),key=lambda r:r['id']):
  if row['status'] not in _STATUSES or (not row['owner_missing'] and row['owner'] is None):_deny()
  if row['owner']!=payload['sender'] or row['status'] not in _ACTIVE:continue
  try:task,taskrefs,proof=_snapshot(reader,host[0],host[1],payload['sender'],host[4],now,None,row['id'])
  except (CardUnavailable,MonthlyHistoryUnavailable):continue
  if task['owner']!=payload['sender'] or task['status'] not in _ACTIVE:_deny()
  # Include all permitted consulted own active roots, not only the visible five.
  states.append([task,proof]);refs.update(taskrefs)
  if len(refs)>256:_deny()
  if task['status']=='blocked':allowed.append(task)
 if _gate(engine,payload,host)!=(scope,policy):_deny()
 return tuple(sorted(allowed,key=lambda t:t['public_number'])[:5]),tuple(sorted(refs)),_hash([scope,policy,states])


def _render(selected,config,at):
 stamp=datetime.fromtimestamp(at,ZoneInfo(config['timezone']))
 lines=['<b>Препятствия по моим задачам</b>','Срез на '+stamp.strftime('%d.%m.%Y %H:%M')+' ('+stamp.tzname()+').']
 for task in selected:
  lines.append('№'+str(task['public_number'])+' · '+clean(task['title'],110)+'\nВ реестре: есть препятствие.\nЗаписанный следующий шаг: '+(clean(task['next_step'],100) if task['next_step'].strip() else 'не указан'))
 if not selected:lines.append('В доступных записях нет собственных задач с отметкой препятствия.')
 lines.append('Показано не более пяти доступных собственных задач. Фактические причины этим срезом не установлены; записанный следующий шаг не является подтверждением причины.')
 body='\n\n'.join(lines)
 if len(body.encode('utf-16-le'))//2>3500:_deny()
 return body


def _key(business,identifier):return PREFIX+'v1:'+_hash([business,identifier])


def _load(reader,business,identifier):
 rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',16384)})+' FROM runtime WHERE key=?',(_key(business,identifier),))
 if len(rows)!=1:_deny()
 doc=json.loads(rows[0]['value'])
 if type(doc) is not dict or set(doc)!={'version','event','request','scope','as_of','proof','refs','seal'}:_deny()
 if type(doc['version']) is not int or doc['version']!=1 or type(doc['event']) is not str or not 0<len(doc['event'].encode())<=1024:_deny()
 if type(doc['as_of']) not in (int,float) or not 0<doc['as_of']<2**53:_deny()
 if type(doc['request']) is not dict or set(doc['request'])!={'sender','channel','source_id'}:_deny()
 if (type(doc['refs']) is not list or len(doc['refs'])>256 or any(type(r) is not str or not 0<len(r)<=160 for r in doc['refs']) or doc['refs']!=sorted(set(doc['refs']))):_deny()
 if any(type(doc[k]) is not str or not re.fullmatch('[0-9a-f]{64}',doc[k]) for k in ('scope','proof','seal')):_deny()
 return doc


def cancel(store,business,identifier):
 store.execute("UPDATE outbox SET state='cancelled',error=? WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",(CANCEL,business,identifier))


def handle(engine,event,payload):
 host=_host(engine);store=host[0]
 if not store.db.in_transaction:_deny()
 store.execute('SAVEPOINT own_impediments')
 try:
  scope,policy=_gate(engine,payload,host)
  if not is_command(payload['text']) or type(event) is not str or not 0<len(event.encode())<=1024:_deny()
  reader=_PassReader(store);_barrier(reader,host[2],scope,event)
  at=engine.now();initial=_collect(engine,payload,reader,host);selected,refs,proof=initial
  body=_render(selected,host[1],at);request={k:payload[k] for k in ('sender','channel','source_id')}
  if len(json.dumps(request).encode())>2048:_deny()
  dedupe=PREFIX+_hash([event,scope])
  if not store.queue(host[2],payload['sender'],body,dedupe,at,parse_mode='HTML',preferred_transport=payload['channel']):_deny()
  _same_host(engine,host)
  identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(host[2],dedupe)).fetchone()[0]
  host[3].protect_outbox(identifier,refs);_same_host(engine,host)
  row=_outbox(reader,host[2],identifier)
  if row['text']!=body or row['state']!='pending' or row['recipient']!=payload['sender'] or row['task_id'] is not None or row['preferred_transport']!=payload['channel'] or row['parse_mode']!='HTML':_deny()
  _protected(reader,host[2],identifier,refs)
  doc=dict(version=1,event=event,request=request,scope=_hash(scope),as_of=at,proof=proof,refs=list(refs),seal=_seal(row))
  value=json.dumps(doc,separators=(',',':'))
  if len(value.encode())>16384:_deny()
  store.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(_key(host[2],identifier),value))
  reader.cache.clear()
  if _collect(engine,payload,reader,host)!=initial or _outbox(reader,host[2],identifier)!=row or _load(reader,host[2],identifier)!=doc:_deny()
  _protected(reader,host[2],identifier,refs);_same_host(engine,host)
 except Exception:
  store.execute('ROLLBACK TO own_impediments');store.execute('RELEASE own_impediments')
  try:_gate(engine,payload,host)
  except Exception:pass
  else:store.queue(host[2],payload['sender'],NOTICE,'own-impediments-notice:'+_hash(event),engine.now(),preferred_transport=payload['channel'])
 else:store.execute('RELEASE own_impediments')
 store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))


def make_delivery_guard(engine):
 host=_host(engine)
 def guard(row,route=None,transport=None):
  if not row['dedupe'].startswith(PREFIX):return False
  try:
   _same_host(engine,host);reader=_PassReader(host[0]);current=_outbox(reader,host[2],row['id']);doc=_load(reader,host[2],row['id'])
   if current['state'] not in ('pending','retry','blocked') or any(current[k]!=row[k] for k in current):_deny()
   if current['parse_mode']!='HTML' or current['task_id'] is not None or doc['seal']!=_seal(current) or len(current['text'].encode('utf-16-le'))//2>3500 or doc['as_of']>engine.now():_deny()
   payload=doc['request'];scope,policy=_gate(engine,payload,host)
   if doc['scope']!=_hash(scope) or current['recipient']!=payload['sender'] or current['preferred_transport']!=payload['channel'] or current['dedupe']!=PREFIX+_hash([doc['event'],scope]):_deny()
   accepted=_accepted(reader,host[2],doc['event'])
   if any(accepted[k]!=payload[k] for k in payload) or not is_command(accepted['text']):_deny()
   selected,refs,proof=_collect(engine,payload,reader,host)
   if refs!=tuple(doc['refs']) or proof!=doc['proof'] or _render(selected,host[1],doc['as_of'])!=current['text']:_deny()
   _protected(reader,host[2],row['id'],refs);_same_host(engine,host)
   if route is not None and (transport!=payload['channel'] or str(route.get('chat_id'))!=str(host[1]['employees'][payload['sender']]['telegram_user_id']) or
    (route.get('business_connection_id') or None)!=(_private_parts(host[1],payload)[4]) or (route.get('message_thread_id') or None)!=_private_parts(host[1],payload)[5]):_deny()
   return True
  except Exception:
   cancel(host[0],host[2],row['id']);return False
 return guard


def _private_parts(config,payload):
 from .answer_citations import _scope
 return _scope(config,payload)[1]
