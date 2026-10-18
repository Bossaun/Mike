"""Prospective own-input meeting decisions/links, never task approval or KB grant.

Consumer-local access receipt only. It is NOT a general Intelligence evidence ID
and authorizes no weekly body dispatch. Canonical text stays in existing sources.
"""
from dataclasses import dataclass
from datetime import date,datetime,timedelta
import json
import math
import re
from zoneinfo import ZoneInfo
from .answer_citations import _hash,_scope
from .task_card_reference import _gate as _host_gate,_barrier
from .task_card_command import _snapshot,CardUnavailable,_gate as _record_gate
from .own_task_overview import _PassReader
from .monthly_task_history import _bounded_columns,_text,_integer,_numeric
from .meeting_review import Source,Span,Agreement,Meeting
from .meeting_week_adapter import TaskBinding

NOTICE='Не удалось зарегистрировать решения. Нужны текущий доставленный разбор, дата, строки решений, номера связанных черновиков, явный доступ только для вас и срок действия. Задачи не изменены.'
_HEADER=re.compile(r'^Регистрация планёрки (MB-[0-9a-f]{16})$')
_REVOKE=re.compile(r'^Отмени регистрацию планёрки (MB-[0-9a-f]{16})[.!]?$')
_DECISION=re.compile(r'^Решение: строки ([1-9][0-9]{0,2})(?:-([1-9][0-9]{0,2}))?; задача №([1-9][0-9]{0,8})$')
_CONSENT='Подтверждаю: решения и связи; задачи остаются черновиками'
_DAY='Дата планёрки: '
_UNTIL='Доступ до конца дня: '
_ACCESS='Доступ: только для меня'
_MAX_WINDOW=128


class BindingUnavailable(ValueError):
 def __init__(self):super().__init__('meeting_binding_unavailable')


def _key(scope,code):return 'meeting-binding:v1:'+_hash([scope,code])


def is_binding_command(text):
 return (type(text) is str and (text.startswith('Регистрация планёрки ') or bool(_REVOKE.fullmatch(text))))


def _gate(engine,payload,intelligence):
 scope,parts=_host_gate(engine,payload,intelligence)
 if not engine._operator_turn(payload,'reply') or engine.c['admins']!=[payload['sender']]:raise BindingUnavailable()
 return scope,_hash(_record_gate(engine.c,payload['sender'],intelligence.index,engine.s,engine.now()))


def _source(reader,business,source_id):
 rows=reader.query('SELECT '+_bounded_columns({'body':_text('body',64000),'deleted':"typeof(deleted)='integer' AND deleted IN (0,1)"})+
  ' FROM sources WHERE business=? AND id=?',(business,source_id))
 if len(rows)!=1 or rows[0]['deleted']!=0:raise BindingUnavailable()
 text=rows[0]['body'];lines=text.split('\n')
 if not 1<=len(lines)<=1000 or len(text.encode())>64000:raise BindingUnavailable()
 return text,lines


def _save(engine,scope,code,doc):
 value=json.dumps(doc,ensure_ascii=False,separators=(',',':'))
 if len(value.encode())>16384:raise BindingUnavailable()
 engine.s.execute('INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(_key(scope,code),value))


def _load(reader,scope,code):
 rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',16384)})+' FROM runtime WHERE key=?',(_key(scope,code),))
 if len(rows)!=1:raise BindingUnavailable()
 doc=json.loads(rows[0]['value'])
 if type(doc) is not dict or doc.get('version')!=1 or doc.get('scope')!=_hash(scope):raise BindingUnavailable()
 return doc


def _template(code,numbers):
 return ('Регистрация планёрки '+code+'\nДата планёрки: [ГГГГ-ММ-ДД]\nДоступ: только для меня\n'
  'Доступ до конца дня: [ГГГГ-ММ-ДД]\nРешение: строки [номер или диапазон исходного сообщения]; задача №[номер черновика]\n'+_CONSENT)


def _capture_plan(engine,event_id,payload,context,result,reviewed,first_outbox_id):
 """Optional NEW accepted private meeting producer, inside existing transaction."""
 try:
  intelligence=engine.intelligence;scope,policy=_gate(engine,payload,intelligence)
  if (result.get('intent')!='meeting' or not 1<=len(reviewed)<=5 or
   any(a.get('op')!='create' for a in result.get('actions',[])) or len(reviewed)!=len(result.get('actions',[]))):return
  reader=_PassReader(engine.s);text=payload['text']
  candidate_tasks=[];refs=set()
  for task in reviewed:
   if task['status']!='proposed' or task['revision']!=1 or task['source_id']!=payload['source_id']:return
   current,taskrefs,fp=_snapshot(reader,engine.s,engine.c,payload['sender'],intelligence.index,engine.now(),None,task['id'])
   if current['status']!='proposed':return
   candidate_tasks.append([current['id'],current['public_number'],current['revision']]);refs.update(taskrefs)
  registered_text,lines=_source(reader,engine.b,payload.get('source_id'))
  if registered_text!=text:return
  prefix='review:meeting:'+payload['sender']+':'+__import__('hashlib').sha256(' '.join(text.casefold().split()).encode()).hexdigest()+':'+payload['sender']+':page:'
  rows=reader.query('SELECT '+_bounded_columns({'id':_integer('id'),'dedupe':_text('dedupe',512),'text':_text('text',16000)})+
   ' FROM outbox WHERE business=? AND recipient=? AND id>? ORDER BY id',(engine.b,payload['sender'],first_outbox_id),maximum=16)
  review=[r['id'] for r in rows if r['dedupe'].startswith(prefix)]
  if not review:return
  code='MB-'+_hash([scope,event_id,_hash(text)])[:16]
  if engine.s.execute('SELECT 1 FROM runtime WHERE key=?',(_key(scope,code),)).fetchone():return
  return intelligence,scope,policy,text,candidate_tasks,refs,review,code,rows
 except (ValueError,TypeError,KeyError,AttributeError):return None


def capture(engine,event_id,payload,context,result,reviewed,first_outbox_id):
 plan=_capture_plan(engine,event_id,payload,context,result,reviewed,first_outbox_id)
 if plan is None:return
 intelligence,scope,policy,text,tasks,refs,review,code,rows=plan
 for identifier in review:engine.s.execute('UPDATE outbox SET preferred_transport=? WHERE id=?',(payload['channel'],identifier))
 template_key='meeting-binding-template:'+event_id;template=_template(code,[t[1] for t in tasks])
 engine.s.queue(engine.b,payload['sender'],template,template_key,engine.now(),preferred_transport=payload['channel'])
 own=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,template_key)).fetchone()[0]
 intelligence.protect_outbox(own,refs)
 seals={str(r['id']):[r['dedupe'],_hash(r['text'])] for r in rows if r['id'] in review}
 seals[str(own)]=[template_key,_hash(template)]
 doc=dict(version=1,scope=_hash(scope),policy=policy,state='candidate',created=engine.now(),
  source=payload['source_id'],source_hash=_hash(text),origin_event=_hash(event_id),anchor_event=_hash(event_id),
  review=review,prompt=[own],seals=seals,tasks=tasks,questions=sorted({i for span in result.get('meeting_question_lines',[]) for i in span}))
 if _gate(engine,payload,intelligence)!=(scope,policy):raise BindingUnavailable()
 _save(engine,scope,code,doc)


def _rows_proof(reader,engine,parts,identifiers,seals,message_at,now):
 if type(identifiers) is not list or not 1<=len(identifiers)<=16 or any(type(i) is not int or i<=0 for i in identifiers):raise BindingUnavailable()
 top=reader.query('SELECT COALESCE(MAX(id),0) AS id FROM outbox')[0]['id'];floor=max(1,top-_MAX_WINDOW+1)
 if min(identifiers)<floor:raise BindingUnavailable()
 spec={'id':_integer('id'),'business':_text('business',128),'recipient':_text('recipient',128),'state':_text('state',16),
  'transport':_text('transport',16,True),'destination_chat':_text('destination_chat',128,True),
  'destination_connection':_text('destination_connection',512,True),'destination_thread':_integer('destination_thread',True),
  'preferred_transport':_text('preferred_transport',16,True),'dedupe':_text('dedupe',512)}
 rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM outbox NOT INDEXED WHERE id BETWEEN ? AND ? '
  'AND business=? AND recipient=? ORDER BY id',(floor,top,engine.b,parts[1]),maximum=_MAX_WINDOW)
 for identifier in identifiers:
  found=[r for r in rows if r['id']==identifier]
  if len(found)!=1:raise BindingUnavailable()
  r=found[0]
  if type(seals.get(str(identifier))) is not list or len(seals[str(identifier)])!=2 or seals[str(identifier)][0]!=r['dedupe']:raise BindingUnavailable()
  if (r['state']!='sent' or r['transport']!=parts[2] or r['destination_chat']!=parts[3] or
   (r['destination_connection'] or None)!=parts[4] or (r['destination_thread'] or None)!=parts[5]):raise BindingUnavailable()
  delivered=reader.query('SELECT '+_bounded_columns({'at':_numeric('at')})+" FROM deliveries WHERE outbox_id=? AND state='sent'",(identifier,))
  if len(delivered)!=1 or delivered[0]['at']>now or math.floor(delivered[0]['at'])>math.floor(message_at):raise BindingUnavailable()
 return rows


def _candidate(engine,event_id,payload,code,intelligence):
 scope,policy=_gate(engine,payload,intelligence);reader=_PassReader(engine.s);doc=_load(reader,scope,code)
 now=engine.now();message_at=payload.get('message_at')
 if (doc.get('state')!='candidate' or doc.get('policy')!=policy or type(doc.get('created')) not in (int,float)
  or not doc['created']<=now<=doc['created']+86400 or type(message_at) not in (int,float) or not 0<message_at<=now):raise BindingUnavailable()
 current=_barrier(reader,engine.b,scope,event_id)
 prior=reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64)})+
  ' FROM answer_citation_turns WHERE id=(SELECT id FROM answer_citation_turns WHERE business=? AND scope_key=? AND id<? ORDER BY id DESC LIMIT 1)',(engine.b,_hash(scope),current))
 if len(prior)!=1 or prior[0]['event_key']!=doc['anchor_event']:raise BindingUnavailable()
 _,parts=_scope(engine.c,payload)
 rows=_rows_proof(reader,engine,parts,list(dict.fromkeys(doc['review']+doc['prompt'])),doc['seals'],message_at,now)
 relevant=[r for r in rows if (r['transport'] is None and r['preferred_transport'] in (None,parts[2])) or
  (r['transport']==parts[2] and r['destination_chat']==parts[3] and (r['destination_connection'] or None)==parts[4] and (r['destination_thread'] or None)==parts[5])]
 if not relevant or relevant[-1]['id']!=max(doc['prompt']):raise BindingUnavailable()
 refs=set()
 if type(doc['tasks']) is not list or not 1<=len(doc['tasks'])<=5:raise BindingUnavailable()
 for tid,number,revision in doc['tasks']:
  task,taskrefs,_=_snapshot(reader,engine.s,engine.c,payload['sender'],intelligence.index,now,None,tid)
  if task['public_number']!=number or task['revision']!=revision or task['status']!='proposed':raise BindingUnavailable()
  link=reader.query('SELECT 1 AS linked FROM task_sources WHERE business=? AND task_id=? AND source_id=?',(engine.b,tid,doc['source']))
  if not link:raise BindingUnavailable()
  refs.update(taskrefs)
 text,lines=_source(reader,engine.b,doc['source'])
 if _hash(text)!=doc['source_hash']:raise BindingUnavailable()
 for identifier in set(doc['review']+doc['prompt']):
  body=reader.query('SELECT '+_bounded_columns({'text':_text('text',16000)})+' FROM outbox WHERE id=? AND business=?',(identifier,engine.b))
  if len(body)!=1 or _hash(body[0]['text'])!=doc['seals'][str(identifier)][1]:raise BindingUnavailable()
 return scope,policy,doc,text,lines,tuple(sorted(refs))


def _parse(text,lines,doc,engine):
 raw=text.splitlines()
 if len(text)>4000 or not 6<=len(raw)<=10 or len(text.encode('utf-16-le'))//2>4000:raise BindingUnavailable()
 if raw[2]!=_ACCESS or raw[-1]!=_CONSENT or not raw[1].startswith(_DAY) or not raw[3].startswith(_UNTIL):raise BindingUnavailable()
 on=date.fromisoformat(raw[1][len(_DAY):]);until=date.fromisoformat(raw[3][len(_UNTIL):]);zone=ZoneInfo(engine.c['timezone'])
 today=datetime.fromtimestamp(engine.now(),zone).date()
 if not today-timedelta(days=31)<=on<=today or not today<=until<=today+timedelta(days=31):raise BindingUnavailable()
 expiry=datetime.combine(until+timedelta(days=1),datetime.min.time(),zone).timestamp()
 decisions=[];used=set();tasks={t[1]:t[0] for t in doc['tasks']};numbers=set()
 for value in raw[4:-1]:
  m=_DECISION.fullmatch(value)
  if not m:raise BindingUnavailable()
  first=int(m[1]);last=int(m[2] or m[1]);number=int(m[3])
  if not 1<=first<=last<=len(lines) or last-first>=3 or number not in tasks or number in numbers or used.intersection(range(first,last+1)) or set(range(first,last+1)).intersection(doc.get('questions',[])):raise BindingUnavailable()
  quote='\n'.join(lines[first-1:last])
  if not quote.strip() or len(quote)>300:raise BindingUnavailable()
  decisions.append(dict(first=first,last=last,number=number,task_id=tasks[number]));used.update(range(first,last+1));numbers.add(number)
 if not 1<=len(decisions)<=5:raise BindingUnavailable()
 return on.isoformat(),expiry,decisions


def handle(engine,event_id,payload):
 if not engine.s.db.in_transaction:raise BindingUnavailable()
 intelligence=engine.intelligence
 try:scope,policy=_gate(engine,payload,intelligence)
 except (ValueError,TypeError,KeyError,AttributeError):
  engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
 text=payload['text'];revoke=_REVOKE.fullmatch(text)
 if revoke:
  try:
   doc=_load(_PassReader(engine.s),scope,revoke[1]);doc['state']='revoked';_save(engine,scope,revoke[1],doc)
   notice='Регистрация отозвана. Задачи не изменены.'
  except Exception:notice=NOTICE
  engine.s.queue(engine.b,payload['sender'],notice,'meeting-binding:'+event_id,engine.now(),preferred_transport=payload['channel'])
 else:
  header=_HEADER.fullmatch(text.splitlines()[0]) if text else None
  current=None
  try:
   if not header:raise BindingUnavailable()
   current=_candidate(engine,event_id,payload,header[1],intelligence)
   on,expiry,decisions=_parse(text,current[4],current[2],engine)
   prospective=dict(current[2],code=header[1],state='registered',on=on,expiry=expiry,decisions=decisions,confirmed_event=_hash(event_id))
   _typed_inputs(engine,prospective,payload,scope,policy,intelligence,_PassReader(engine.s))
  except Exception:
   reply=_template(header[1],[t[1] for t in current[2]['tasks']]) if current else NOTICE
   engine.s.queue(engine.b,payload['sender'],reply,'meeting-binding:'+event_id,engine.now(),preferred_transport=payload['channel'])
   if current:
    doc=current[2];doc['anchor_event']=_hash(event_id)
    row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,'meeting-binding:'+event_id)).fetchone()
    doc['prompt']=[row[0]];doc['seals'][str(row[0])]=['meeting-binding:'+event_id,_hash(reply)];_save(engine,scope,header[1],doc)
  else:
   # Fresh whole candidate check before the atomic metadata write/ack.
   if _candidate(engine,event_id,payload,header[1],intelligence)!=current:raise BindingUnavailable()
   doc=prospective
   _save(engine,scope,header[1],doc)
   notice='Зарегистрированы дата '+on+' и связи решений с черновиками '+', '.join('№'+str(d['number']) for d in decisions)+'. Доступ только для вас. Задачи остаются черновиками; результаты не подтверждены. Код: '+header[1]+'. Для отзыва: «Отмени регистрацию планёрки '+header[1]+'». '
   engine.s.queue(engine.b,payload['sender'],notice,'meeting-binding:'+event_id,engine.now(),preferred_transport=payload['channel'])
   row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,'meeting-binding:'+event_id)).fetchone()
   intelligence.protect_outbox(row[0],current[5])
 if _gate(engine,payload,intelligence)!=(scope,policy):raise BindingUnavailable()
 engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))


@dataclass(frozen=True)
class RegisteredMeeting:
 meeting:Meeting
 task_bindings:tuple
 document_ids:frozenset
 meeting_evidence_ids:frozenset


def _typed_inputs(engine,doc,payload,scope,policy,intelligence,reader):
 # Validate current task/doc/source metadata before transferring any source body.
 refs=set();selected=[]
 for decision in doc['decisions']:
  task,taskrefs,_=_snapshot(reader,engine.s,engine.c,payload['sender'],intelligence.index,engine.now(),None,decision['task_id'])
  if task['public_number']!=decision['number']:raise BindingUnavailable()
  linked=reader.query('SELECT 1 AS linked FROM task_sources WHERE business=? AND task_id=? AND source_id=?',(engine.b,task['id'],doc['source']))
  if not linked:raise BindingUnavailable()
  refs.update(taskrefs);selected.append((decision,task))
 text,lines=_source(reader,engine.b,doc['source'])
 if _hash(text)!=doc['source_hash']:raise BindingUnavailable()
 on=date.fromisoformat(doc['on']);source=Source(doc['source'],engine.b,on,tuple(lines));agreements=[];bindings=[]
 for i,(decision,task) in enumerate(selected,1):
  span=Span(source.id,decision['first'],decision['last'],'\n'.join(lines[decision['first']-1:decision['last']]))
  aid=doc['code']+':'+str(i);agreements.append(Agreement(aid,aid,span.quote,(span,),True))
  bindings.append(TaskBinding(engine.b,aid,task['id'],(span,),True))
 anchor='meeting-access:'+_hash([scope,doc['code'],doc['source_hash'],doc['confirmed_event']])
 docs=frozenset({anchor}|{r for r in refs if not r.startswith('work-task:')})
 meeting=Meeting(doc['code'],engine.b,on,(source,),tuple(agreements))
 if _gate(engine,payload,intelligence)!=(scope,policy):raise BindingUnavailable()
 return RegisteredMeeting(meeting,tuple(bindings),docs,frozenset({anchor}))


def registered_inputs(engine,code,payload):
 """Consumer-local current proof for actual I04; NEVER general evidence/send grant."""
 intelligence=engine.intelligence;scope,policy=_gate(engine,payload,intelligence);reader=_PassReader(engine.s);doc=_load(reader,scope,code)
 if doc.get('state')!='registered' or doc.get('policy')!=policy or not engine.now()<doc['expiry']:raise BindingUnavailable()
 return _typed_inputs(engine,doc,payload,scope,policy,intelligence,reader)
