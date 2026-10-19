"""Prospective administrator-reported outcomes, never task completion/KB grants.

Canonical current input plus a scoped consumer-local receipt; no weekly dispatch.
"""
from dataclasses import dataclass
from datetime import date,datetime,timedelta
import json
import re
from zoneinfo import ZoneInfo
from .answer_citations import _hash
from .meeting_binding import _gate, _load as _meeting_doc, registered_inputs
from .meeting_review import Source,Span,ReviewError
from .meeting_week_adapter import ResultBinding,project_week
from .own_task_overview import _PassReader
from .monthly_task_history import _bounded_columns,_text
from .task_card_command import _snapshot
from .task_result_context import PRIVATE_RESULT_SOURCE_PREFIX

_MB=r'MB-[0-9a-f]{16}'
_RID=r'MR-[0-9a-f]{32}'
_HEADER=re.compile(r'Отчёт по планёрке ('+_MB+r')')
_REVOKE=re.compile(r'Отзови мой отчёт ('+_RID+r') по планёрке ('+_MB+r')')
_CONSENT='Подтверждаю: это мой отчёт, не независимая проверка; без согласования задачи'
_KINDS={'выполнено':'completed','в работе':'in_progress','заблокировано':'blocked'}
MAX_REPORTS=32
NOTICE='Отчёт не зарегистрирован. Нужны текущая доступная регистрация планёрки, точный номер связанной задачи, дата, состояние и ваш явный личный отчёт. Задача не изменена.'

class ReportUnavailable(ValueError):
 def __init__(self):super().__init__('dated_result_unavailable')


def is_dated_result(text):
 return type(text) is str and (text.startswith('Отчёт по планёрке') or text.startswith('Отзови мой отчёт '))


def _key(scope,mb,rid=None):return 'dated-result:v1:'+_hash([scope,mb,rid])


def _read(reader,key,optional=False):
 rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',12000)})+' FROM runtime WHERE key=?',(key,))
 if not rows and optional:return None
 if len(rows)!=1:raise ReportUnavailable()
 return json.loads(rows[0]['value'])


def _save(engine,key,value):
 encoded=json.dumps(value,ensure_ascii=False,separators=(',',':'))
 if len(encoded.encode())>12000:raise ReportUnavailable()
 engine.s.execute('INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,encoded))


def _ids(reader,scope,mb):
 value=_read(reader,_key(scope,mb),True)
 if value is None:return []
 if type(value) is not list or len(value)>MAX_REPORTS or len(set(value))!=len(value) or any(type(x) is not str or not re.fullmatch(_RID,x) for x in value):raise ReportUnavailable()
 return value


def _parent(engine,payload,mb,intelligence):
 scope,policy=_gate(engine,payload,intelligence)
 parent=registered_inputs(engine,mb,payload)
 doc=_meeting_doc(_PassReader(engine.s),scope,mb)
 if doc['policy']!=policy or doc['state']!='registered' or not engine.now()<doc['expiry']:raise ReportUnavailable()
 return scope,policy,parent,doc['expiry']


def _template(mb=None):
 return ('Отчёт по планёрке '+(mb or '[MB-код регистрации]')+'\nЗадача: №[номер]\nДата результата: [ГГГГ-ММ-ДД]\n'
  'Состояние по моему отчёту: [выполнено / в работе / заблокировано]\nДоступ: только для меня\n'
  'Мой отчёт: [короткий дословный текст]\n'+_CONSENT)


def _parse(text,engine):
 if type(text) is not str or len(text)>2000 or len(text.encode())>4000 or len(text.encode('utf-16-le'))//2>2000:raise ReportUnavailable()
 lines=text.split('\n')
 if len(lines)!=7 or any('\r' in line or any(ord(c)<32 for c in line) for line in lines):raise ReportUnavailable()
 header=_HEADER.fullmatch(lines[0]);number=re.fullmatch(r'Задача: №([1-9][0-9]{0,8})',lines[1])
 if not header or not number or lines[4]!='Доступ: только для меня' or lines[6]!=_CONSENT:raise ReportUnavailable()
 if not lines[2].startswith('Дата результата: ') or not lines[3].startswith('Состояние по моему отчёту: ') or not lines[5].startswith('Мой отчёт: '):raise ReportUnavailable()
 literal=lines[2][len('Дата результата: '):];on=date.fromisoformat(literal)
 if literal!=on.isoformat() or on>datetime.fromtimestamp(engine.now(),ZoneInfo(engine.c['timezone'])).date():raise ReportUnavailable()
 outcome=_KINDS.get(lines[3][len('Состояние по моему отчёту: '):]);quote=lines[5][len('Мой отчёт: '):]
 if outcome is None or not quote.strip() or quote!=quote.strip() or len(quote)>300 or quote.startswith('['):raise ReportUnavailable()
 return header[1],int(number[1]),on,outcome,lines


def _selected(engine,payload,parent,number,reader):
 found=[]
 for binding in parent.task_bindings:
  task,refs,_=_snapshot(reader,engine.s,engine.c,payload['sender'],engine.intelligence.index,engine.now(),None,binding.task_id)
  if task['public_number']==number:found.append((task,refs))
 if len(found)!=1:raise ReportUnavailable()
 return found[0]


@dataclass(frozen=True)
class ReportedInputs:
 sources:tuple
 result_bindings:tuple


def reported_inputs(engine,mb,payload):
 """Current owner-only typed reports; no evidence-ID or delivery permission."""
 intelligence=engine.intelligence;scope,policy,parent,expiry=_parent(engine,payload,mb,intelligence)
 reader=_PassReader(engine.s);ids=_ids(reader,scope,mb);sources=[];bindings=[]
 for rid in ids:
  doc=_read(reader,_key(scope,mb,rid))
  if type(doc) is not dict or doc.get('version')!=1 or doc.get('scope')!=_hash(scope) or doc.get('mb')!=mb or doc.get('policy')!=policy:raise ReportUnavailable()
  if doc.get('state')=='revoked':continue
  if doc.get('state')!='reported' or doc['expiry']!=expiry or not engine.now()<doc['expiry']:raise ReportUnavailable()
  if (type(doc.get('inbound_hash')) is not str or not re.fullmatch(r'[0-9a-f]{64}',doc['inbound_hash'])
   or doc.get('source')!=PRIVATE_RESULT_SOURCE_PREFIX+doc['inbound_hash']):raise ReportUnavailable()
  # Current task/dependency/document/source metadata first, before report body.
  task,refs=_selected(engine,payload,parent,doc['number'],reader)
  if task['id']!=doc['task_id']:raise ReportUnavailable()
  link=reader.query('SELECT 1 AS linked FROM task_sources WHERE business=? AND task_id=? AND source_id=?',(engine.b,task['id'],doc['source']))
  if not link:raise ReportUnavailable()
  rows=reader.query('SELECT '+_bounded_columns({'body':_text('body',4000),'deleted':"typeof(deleted)='integer' AND deleted=0"})+' FROM sources WHERE business=? AND id=?',(engine.b,doc['source']))
  if len(rows)!=1 or _hash(rows[0]['body'])!=doc['hash']:raise ReportUnavailable()
  mb2,number,on,outcome,lines=_parse(rows[0]['body'],engine)
  if (mb2,number,on.isoformat(),outcome)!=(mb,doc['number'],doc['on'],doc['outcome']) or on<parent.meeting.on:raise ReportUnavailable()
  source=Source(doc['source'],engine.b,on,tuple(lines));span=Span(source.id,6,6,lines[5])
  sources.append(source);bindings.append(ResultBinding(engine.b,rid,task['id'],on,outcome,(span,),True))
 if _parent(engine,payload,mb,intelligence)!=(scope,policy,parent,expiry):raise ReportUnavailable()
 return ReportedInputs(tuple(sources),tuple(bindings))


def _queue(engine,event_id,payload,text,refs=()):
 engine.s.queue(engine.b,payload['sender'],text,'dated-result:'+event_id,engine.now(),preferred_transport=payload['channel'])
 if refs:
  row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,'dated-result:'+event_id)).fetchone()
  engine.intelligence.protect_outbox(row[0],refs)


def handle(engine,event_id,payload):
 if not engine.s.db.in_transaction:raise ReportUnavailable()
 intelligence=engine.intelligence
 try:scope,policy=_gate(engine,payload,intelligence)
 except (ValueError,KeyError,TypeError,AttributeError):
  engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
 revoke=_REVOKE.fullmatch(payload['text'])
 if revoke:
  rid,mb=revoke.groups()
  try:
   reader=_PassReader(engine.s);doc=_read(reader,_key(scope,mb,rid))
   if type(doc) is not dict or doc.get('scope')!=_hash(scope) or doc.get('mb')!=mb or doc.get('version')!=1:raise ReportUnavailable()
  except (ValueError,TypeError,KeyError):notice=NOTICE
  else:
   doc['state']='revoked';_save(engine,_key(scope,mb,rid),doc);notice='Ваш отчёт отозван. Задача не изменена.'
  _queue(engine,event_id,payload,notice)
 else:
  try:
   mb,number,on,outcome,lines=_parse(payload['text'],engine)
   parent_scope,parent_policy,parent,expiry=_parent(engine,payload,mb,intelligence)
   if (parent_scope,parent_policy)!=(scope,policy) or on<parent.meeting.on:raise ReportUnavailable()
   reader=_PassReader(engine.s);task,refs=_selected(engine,payload,parent,number,reader)
   # The reserved host namespace cannot be supplied as a Telegram source ID.
   # Seal actual admitted transport provenance without exposing it as a grant.
   inbound_hash=_hash([engine.b,scope,payload['source_id']])
   source_id=PRIVATE_RESULT_SOURCE_PREFIX+inbound_hash
   source=Source(source_id,engine.b,on,tuple(lines));span=Span(source.id,6,6,lines[5])
   rid='MR-'+_hash([scope,mb,source.id])[:32]
   binding=ResultBinding(engine.b,rid,task['id'],on,outcome,(span,),True)
   ids=_ids(reader,scope,mb)
   if len(ids)>=MAX_REPORTS or rid in ids or _read(reader,_key(scope,mb,rid),True) is not None:raise ReportUnavailable()
   if reader.query('SELECT 1 AS present FROM sources WHERE business=? AND id=?',(engine.b,source.id)):raise ReportUnavailable()
   # Existing actual transport source identity is not a fresh admitted input.
   if reader.query('SELECT 1 AS present FROM sources WHERE business=? AND id=?',(engine.b,payload['source_id'])):raise ReportUnavailable()
  except (ValueError,KeyError,TypeError,AttributeError):
   first=payload['text'].split('\n',1)[0];header=_HEADER.fullmatch(first)
   _queue(engine,event_id,payload,_template(header[1] if header else None))
  else:
   # All writes remain in Engine's event transaction. DB/write failures propagate.
   engine.s.execute('SAVEPOINT dated_result')
   engine.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',(engine.b,source.id,payload['text'],event_id))
   engine.s.execute('INSERT INTO task_sources VALUES(?,?,?)',(engine.b,task['id'],source.id))
   try:
    # Reuse the reviewed I36 allowlisted view lazily; never forward raw Store.
    from .weekly_review_command import _ReadView
    project_week(_ReadView(engine.s),engine.c,payload['sender'],meeting=parent.meeting,start=on,end=on+timedelta(days=7),task_bindings=parent.task_bindings,result_bindings=(binding,),sources=(source,),document_ids=parent.document_ids,meeting_evidence_ids=parent.meeting_evidence_ids,meeting_anchor_confirmed=True,now=engine.now())
   except ValueError:
    engine.s.execute('ROLLBACK TO dated_result');engine.s.execute('RELEASE dated_result')
    _queue(engine,event_id,payload,NOTICE)
   else:
    engine.s.execute('RELEASE dated_result')
    doc=dict(version=1,state='reported',inbound_hash=inbound_hash,origin_event=_hash(event_id),scope=_hash(scope),policy=policy,mb=mb,number=number,task_id=task['id'],source=source.id,hash=_hash(payload['text']),on=on.isoformat(),outcome=outcome,expiry=expiry)
    _save(engine,_key(scope,mb,rid),doc);_save(engine,_key(scope,mb),ids+[rid])
    notice='По вашему отчёту зарегистрировано состояние «'+next(k for k,v in _KINDS.items() if v==outcome)+'» на '+on.isoformat()+' для задачи №'+str(number)+'. Это не независимая проверка. Задача не изменена. Код отчёта: '+rid+'. Отзыв: Отзови мой отчёт '+rid+' по планёрке '+mb
    _queue(engine,event_id,payload,notice,refs)
    fresh_parent=_parent(engine,payload,mb,intelligence)
    fresh_task,fresh_refs=_selected(engine,payload,parent,number,_PassReader(engine.s))
    if fresh_parent!=(scope,policy,parent,expiry) or fresh_task!=task or fresh_refs!=refs:raise ReportUnavailable()
    # Verify the persisted new source/link/receipt after any host queue callbacks.
    reported_inputs(engine,mb,payload)
 if _gate(engine,payload,intelligence)!=(scope,policy):raise ReportUnavailable()
 engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
