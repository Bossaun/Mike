"""Actual hypothetical event scenario through unanswered core, no old suites."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.customer_unanswered_command as command

MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600


@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');clock=[NOW]
 c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,
  sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:clock[0]);s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',(c['business'],'test_operator','1','1','conn',NOW))
 class API:
  def __init__(self):self.calls=[]
  def call(self,method,**kw):
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';self.calls.append(kw);return dict(message_id=100+len(self.calls))
 api=API();d=Dispatcher(s,c,api,lambda:clock[0])
 def fail(*a,**kw):pytest.fail('unexpected model/source/context/constructor/network call')
 e.model=SimpleNamespace(propose=fail);e._context=fail;e.intelligence.sources.refresh=fail;e.intelligence.index.search=fail;e.intelligence.memory.get=fail
 import builtins,socket,httpx
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield SimpleNamespace(s=s,c=c,e=e,d=d,api=api,clock=clock)
 s.db.close()




def block(events=None,now='2030-01-10T12:00:00+03:00',start='2030-01-09T00:00:00+03:00',end='2030-01-11T00:00:00+03:00',hours='24'):
 events=events or [('входящее','2030-01-09T10:00:00+03:00','подтверждено','нет')]
 return (command.HEADER+'\nМомент сценария: '+now+'\nНачало окна входящих: '+start+'\nКонец окна входящих: '+end+'\nПорог ожидания, часов: '+hours+
  ''.join('\nСобытие '+str(n)+': '+'; '.join(event) for n,event in enumerate(events,1)))


def submit(c,text=None,event='scenario',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text or block(),channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0])
 c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def spy_core(monkeypatch):
 actual=command.core.unanswered;calls=[];results=[]
 def spy(events,**kw):calls.append((events,kw));result=actual(events,**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'unanswered',spy)
 return calls,results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_typed_candidate_core_and_private_dispatch_no_domain_io(case,monkeypatch,channel):
 calls,results=spy_core(monkeypatch);tables=('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')
 before={t:case.s.rows('SELECT * FROM '+t) for t in tables}
 import sqlite3
 writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ and table in tables+('knowledge_chunks','knowledge_readers'):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('initialized Index'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 events,kw=calls[0];candidate=results[0].candidates[0]
 assert type(events[0]) is command.core.Event and type(events[0].conversation) is command.core.Conversation and type(kw['window']) is command.core.Window
 assert kw['dispositions']==() and candidate.draft_only is True and candidate.kind=='unanswered'
 assert candidate.due_at==datetime.fromisoformat('2030-01-10T07:00:00+00:00')
 assert 'событие №1' in row['text'] and candidate.draft in row['text'] and '2030-01-10T07:00:00+00:00' in row['text']
 assert 'непроверенный сценарий' in row['text'] and 'Прежняя обработка кандидатов' in row['text']
 assert candidate.id not in row['text'] and events[0].id not in row['text'] and events[0].evidence_id not in row['text']
 assert row['task_id'] is None and row['preferred_transport']==channel and row['recipient']=='test_operator' and row['parse_mode']=='HTML'
 assert len(row['text'].encode('utf-16-le'))//2<=3500 and set(writes)<={'events','answer_citation_turns','outbox'}
 assert all(before[t]==case.s.rows('SELECT * FROM '+t) for t in tables)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('kind',['unconfirmed_outgoing','unconfirmed_incoming'])
def test_actual_blocked_not_send_receipt(case,monkeypatch,kind):
 events=[('входящее','2030-01-09T10:00:00+03:00','подтверждено','нет'),
  ('исходящее' if kind=='unconfirmed_outgoing' else 'входящее','2030-01-10T10:00:00+03:00','не подтверждено','1')]
 calls,results=spy_core(monkeypatch);row=submit(case,block(events))
 assert not results[0].candidates and results[0].blocked[0].reason=='unconfirmed_message_state'
 assert calls[0][0][1].confirmed is False and 'кандидат заблокирован' in row['text'] and 'Это не подтверждение ответа или доставки' in row['text']


@pytest.mark.parametrize('relation',['none','target','wrong_target','outside_window'])
def test_real_core_reply_relation_and_after_window_semantics(case,monkeypatch,relation):
 events=[('входящее','2030-01-09T10:00:00+03:00','подтверждено','нет')]
 end='2030-01-11T00:00:00+03:00'
 if relation=='wrong_target':events.append(('входящее','2030-01-09T11:00:00+03:00','подтверждено','нет'))
 events.append(('исходящее','2030-01-10T10:00:00+03:00','подтверждено','нет' if relation=='none' else '1'))
 if relation=='outside_window':end='2030-01-10T00:00:00+03:00'
 calls,results=spy_core(monkeypatch);row=submit(case,block(events,end=end))
 assert len(calls[0][0])==len(events)
 if relation=='wrong_target':assert results[0].candidates and 'событие №2' in row['text']
 else:assert results[0].candidates==() and 'Это не подтверждение ответа, полноты истории или отсутствия проблемы' in row['text']


@pytest.mark.parametrize('kind',['too_recent','outside_window','unconfirmed_only','outgoing_only','future_reply','threshold_equal','start_boundary','end_boundary'])
def test_core_clock_threshold_and_half_open_window_are_not_adapter_math(case,monkeypatch,kind):
 events=[('входящее','2030-01-09T10:00:00+03:00','подтверждено','нет')];kwargs={}
 if kind=='too_recent':kwargs['now']='2030-01-10T09:59:59+03:00'
 elif kind=='outside_window':kwargs['start']='2030-01-09T11:00:00+03:00'
 elif kind=='unconfirmed_only':events[0]=(*events[0][:2],'не подтверждено','нет')
 elif kind=='outgoing_only':events[0]=('исходящее',*events[0][1:])
 elif kind=='future_reply':events.append(('исходящее','2030-01-11T10:00:00+03:00','подтверждено','1'))
 elif kind=='threshold_equal':kwargs['now']='2030-01-10T10:00:00+03:00'
 elif kind=='start_boundary':kwargs['start']=events[0][1]
 elif kind=='end_boundary':kwargs['end']=events[0][1]
 calls,results=spy_core(monkeypatch);row=submit(case,block(events,**kwargs))
 if kind in ('future_reply','threshold_equal','start_boundary'):assert len(results[0].candidates)==1
 else:assert results[0].candidates==() and 'правило не выделило кандидата' in row['text']


@pytest.mark.parametrize('old,new',[
 ('Порог ожидания, часов: 24','Порог ожидания, часов: 0'),('Порог ожидания, часов: 24','Порог ожидания, часов: 8785'),
 ('Порог ожидания, часов: 24','Порог ожидания, часов: 1.5'),('Порог ожидания, часов: 24','Порог ожидания, часов: True'),
 ('2030-01-10T12:00:00+03:00','2030-01-10T12:00:00'),('2030-01-10T12:00:00+03:00','2030-01-10T12:00:00+24:00'),
 ('Начало окна входящих: 2030-01-09','Начало окна входящих: 2031-01-09'),('Начало окна входящих: 2030-01-09','Начало окна входящих: 2028-01-09'),
 ('Событие 1: входящее','Событие 2: входящее'),('Событие 1: входящее','Событие 1: неизвестно'),
 ('; подтверждено; нет','; неизвестно; нет'),('; подтверждено; нет','; подтверждено; 1'),
 ('; подтверждено; нет','; подтверждено; 99'),('; подтверждено; нет','; подтверждено;'),
 ('Момент сценария:','Неизвестное поле:'),('Порог ожидания, часов: 24','Порог ожидания, часов: 24\nПорог ожидания, часов: 24'),
 ('входящее; ','входящее;\t'),('Событие 1:','Клиент Alice:')])
def test_invalid_fields_never_call_core_or_write_domain(case,monkeypatch,old,new):
 calls,_=spy_core(monkeypatch);row=submit(case,block().replace(old,new))
 assert row['text']==command.NOTICE and not calls
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')


@pytest.mark.parametrize('kind',['duplicate_ordinal','same_instant','reverse_time','missing_event','missing_field','outgoing_parent_future'])
def test_local_ordinal_chronology_and_missing_fields_failclosed(case,monkeypatch,kind):
 text=block()
 if kind=='duplicate_ordinal':text+='\nСобытие 1: исходящее; 2030-01-10T10:00:00+03:00; подтверждено; 1'
 elif kind=='same_instant':text+='\nСобытие 2: исходящее; 2030-01-09T07:00:00Z; подтверждено; 1'
 elif kind=='reverse_time':text+='\nСобытие 2: исходящее; 2030-01-08T07:00:00Z; подтверждено; 1'
 elif kind=='missing_event':text='\n'.join(text.split('\n')[:-1])
 elif kind=='missing_field':text='\n'.join(line for line in text.split('\n') if not line.startswith('Момент сценария:'))
 elif kind=='outgoing_parent_future':text+='\nСобытие 2: исходящее; 2030-01-10T10:00:00+03:00; подтверждено; 3'
 calls,_=spy_core(monkeypatch);assert submit(case,text)['text']==command.NOTICE and not calls


def test_twelve_events_duration_caps_and_template_unknown_roundtrip(case,monkeypatch):
 events=[('входящее','2030-01-09T'+str(n).zfill(2)+':00:00+03:00','подтверждено','нет') for n in range(12)]
 calls,_=spy_core(monkeypatch);submit(case,block(events,hours='8784'))
 assert len(calls[0][0])==12 and calls[0][1]['stale_after'].days==366
 assert submit(case,block(events)+ '\nСобытие 13: исходящее; 2030-01-10T00:00:00+03:00; подтверждено; 12',event='too-many')['text']==command.NOTICE
 submit(case,block(hours='1'),event='hour-min');assert calls[-1][1]['stale_after'].seconds==3600
 template=submit(case,command.HEADER,event='template');assert template['text']==command.TEMPLATE
 assert submit(case,template['text'],event='template-roundtrip')['text']==command.NOTICE
 assert submit(case,block()+'x'*4001,event='chars')['text']==command.NOTICE
 text=command.HEADER+'\nСобытие 1: '+'😀'*2100
 assert len(text)<4000 and len(text.encode())>8000 and submit(case,text,event='bytes')['text']==command.NOTICE


@pytest.mark.parametrize('gate',['staff','group','nonclosed','missing_intelligence','business','own_uid','multiadmin'])
def test_current_private_soleadmin_gate(case,gate):
 sender='test_operator';channel='bot';source=None
 if gate=='staff':sender='frost';source='telegram:2:10'
 elif gate=='group':channel='group'
 elif gate=='nonclosed':case.c.pop('team_intake')
 elif gate=='missing_intelligence':case.e.intelligence=None
 elif gate=='business':case.c['business']='OTHER'
 elif gate=='own_uid':source='telegram:999:10'
 elif gate=='multiadmin':case.c['admins'].append('frost')
 submit(case,sender=sender,channel=channel,source=source);assert not case.s.rows('SELECT * FROM outbox')


@pytest.mark.parametrize('phase',['queue','identity','scope','body','transport'])
def test_atomic_queue_captured_host_scope_and_exact_body_rollback(case,monkeypatch,phase):
 real=case.s.queue
 def drift(*a,**kw):
  value=real(*a,**kw)
  if phase=='queue':raise RuntimeError('synthetic failure')
  elif phase=='identity':case.e.intelligence=None
  elif phase=='scope':case.c['admins']=[]
  elif phase=='body':case.s.execute("UPDATE outbox SET text='tampered'")
  elif phase=='transport':case.s.execute("UPDATE outbox SET preferred_transport='business'")
  return value
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises((ValueError,RuntimeError)):submit(case)
 assert not case.s.rows('SELECT * FROM outbox') and not case.s.rows("SELECT * FROM events WHERE id='scenario'")


def test_scope_local_identity_and_dedupe_pending_barrier(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case);first=calls[0][0][0].conversation
 submit(case,event='business',channel='business');second=calls[1][0][0].conversation
 assert first!=second and first.channel=='supplied-scenario'
 assert case.e.reply('scenario','test_operator',block(),source_id='telegram:1:10',channel='bot',message_at=case.clock[0]) is False
 case.e.process('scenario');assert len(case.s.rows('SELECT * FROM outbox'))==2 and len(calls)==2
 assert all(row['phase']=='pending' for row in case.s.rows('SELECT * FROM answer_citation_turns'))
