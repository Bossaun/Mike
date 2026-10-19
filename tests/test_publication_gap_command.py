"""Actual supplied-publication scenario through marketing core, no old suites."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.publication_gap_command as command

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



BLOCK=(command.HEADER+'\nКанал: Сценарий <A> & образец\nМомент сценария: 2030-01-10T12:00:00+03:00\n'
 'Порог перерыва, часов: 48\nМаксимальная давность наблюдений, часов: 24\n'
 'Наблюдено: 2030-01-10T10:00:00+03:00\nНачало наблюдения: 2030-01-01T10:00:00+03:00\n'
 'Конец наблюдения: 2030-01-10T10:00:00+03:00\nПолнота: полное\nПубликации: нет')


def submit(c,text=BLOCK,event='scenario',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0])
 c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def spy_core(monkeypatch):
 actual=command.core.publication_gap;calls=[];results=[]
 def spy(observation,**kw):calls.append((observation,kw));result=actual(observation,**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'publication_gap',spy)
 return calls,results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_core_future_explicit_scenario_and_private_dispatch_no_data_reads(case,monkeypatch,channel):
 calls,results=spy_core(monkeypatch);tables=('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')
 before={t:case.s.rows('SELECT * FROM '+t) for t in tables}
 import sqlite3
 reads=[];writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ:
   reads.append(table)
   if table in tables+('knowledge_chunks','knowledge_readers'):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('initialized index'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert len(calls)==1 and type(calls[0][0]) is command.core.PublicationObservation
 gap=results[0];assert gap.state=='gap' and gap.window_publication_count==0 and gap.review_required and not gap.live_authorized
 assert row and row['task_id'] is None and row['recipient']=='test_operator' and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert 'По переданным полным наблюдениям' in row['text'] and 'независимо не проверены' in row['text']
 assert 'Сценарий &lt;A&gt; &amp; образец' in row['text'] and 'request-supplied-values' not in row['text'] and calls[0][1]['channel_id'] not in row['text']
 assert gap.as_of!=calls[0][1]['now'] and 'Срез наблюдений: 2030-01-10T07:00:00+00:00' in row['text']
 assert 'Напоминания и публикации не запланированы' in row['text'] and len(row['text'].encode('utf-16-le'))//2<=3500
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 assert all(before[t]==case.s.rows('SELECT * FROM '+t) for t in tables)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('change,state,count',[
 ('partial','unknown',None),('unknown','unknown',None),('too_short','unknown',None),('stale_observed','unknown',None),('stale_cutoff','unknown',None),
 ('positive','recent_publication',1),('positive_partial','recent_publication',None),('start_boundary','recent_publication',1),('outside_window','gap',0)])
def test_core_coverage_and_cutoff_drives_state_without_adapter_math(case,monkeypatch,change,state,count):
 text=BLOCK
 if change in ('partial','positive_partial'):text=text.replace('Полнота: полное','Полнота: частичное')
 elif change=='unknown':text=text.replace('Полнота: полное','Полнота: неизвестно')
 elif change=='too_short':text=text.replace('2030-01-01T10:00:00+03:00','2030-01-09T10:00:00+03:00')
 elif change=='stale_observed':text=text.replace('2030-01-10T12:00:00+03:00','2030-01-12T12:00:00+03:00')
 elif change=='stale_cutoff':text=text.replace('Конец наблюдения: 2030-01-10','Конец наблюдения: 2030-01-08')
 if change in ('positive','positive_partial'):text=text.replace('Публикации: нет','Публикации: 2030-01-09T10:00:00+03:00')
 elif change=='start_boundary':text=text.replace('Публикации: нет','Публикации: 2030-01-08T10:00:00+03:00')
 elif change=='outside_window':text=text.replace('Публикации: нет','Публикации: 2030-01-07T10:00:00+03:00')
 calls,results=spy_core(monkeypatch);row=submit(case,text)
 assert results[0].state==state and results[0].window_publication_count==count
 if count is None:assert 'Число публикаций в окне: неизвестно.' in row['text']
 else:assert 'Число публикаций в окне по переданным данным: '+str(count) in row['text']
 if change in ('positive','positive_partial','start_boundary','outside_window'):
  assert type(calls[0][0].publications[0]) is command.core.Publication


@pytest.mark.parametrize('field',['Наблюдено','Начало наблюдения','Конец наблюдения','Полнота','Публикации'])
def test_missing_observation_uses_actual_core_none_not_fake_zero(case,monkeypatch,field):
 text='\n'.join(line for line in BLOCK.split('\n') if not line.startswith(field+': '))
 calls,results=spy_core(monkeypatch);row=submit(case,text)
 assert calls[0][0] is None and results[0].state=='unknown' and results[0].window_publication_count is None
 assert 'Число публикаций в окне: неизвестно' in row['text']


def test_unknown_posts_never_become_empty_complete_observation(case,monkeypatch):
 calls,results=spy_core(monkeypatch);submit(case,BLOCK.replace('Публикации: нет','Публикации: неизвестно'))
 assert calls[0][0] is None and results[0].coverage=='missing'


@pytest.mark.parametrize('old,new',[
 ('Канал: Сценарий <A> & образец','Канал: '+('x'*81)),('Момент сценария: 2030-01-10T12:00:00+03:00','Момент сценария: неизвестно'),
 ('48','0'),('48','8785'),('48','1.5'),('48','True'),('48','-1'),
 ('24','0'),('24','8785'),('2030-01-10T12:00:00+03:00','2030-01-10T12:00:00'),('2030-01-10T12:00:00+03:00','2030-01-10T12:00:00+24:00'),
 ('Наблюдено: 2030-01-10T10:00:00+03:00','Наблюдено: 2030-01-11T10:00:00+03:00'),
 ('Начало наблюдения: 2030-01-01T10:00:00+03:00','Начало наблюдения: 2030-01-10T10:00:00+03:00'),
 ('Конец наблюдения: 2030-01-10T10:00:00+03:00','Конец наблюдения: 2030-01-11T10:00:00+03:00'),
 ('Полнота: полное','Полнота: наверное'),('Публикации: нет','Публикации: 2030-01-10T10:00:00+03:00'),
 ('Публикации: нет','Публикации: 2030-01-09T10:00:00+03:00;2030-01-09T07:00:00Z'),
 ('Публикации: нет','Публикации: 2030-01-09T10:00:00+03:00;'),
 ('Канал:','Неизвестное поле:'),('Канал: Сценарий <A> & образец','Канал: a\nКанал: b'),
 ('Сценарий <A> & образец','Сценарий\tA')])
def test_invalid_supplied_values_fail_fixed_notice_without_sideeffects(case,old,new):
 row=submit(case,BLOCK.replace(old,new));assert row['text']==command.NOTICE
 assert not case.s.rows('SELECT * FROM tasks') and not case.s.rows('SELECT * FROM sources')


@pytest.mark.parametrize('base',['Канал','Момент сценария','Порог перерыва, часов','Максимальная давность наблюдений, часов'])
def test_missing_base_never_calls_core_even_if_other_observation_missing(case,monkeypatch,base):
 calls,_=spy_core(monkeypatch);text='\n'.join(line for line in BLOCK.split('\n') if not line.startswith(base+': ') and not line.startswith('Наблюдено: '))
 assert submit(case,text)['text']==command.NOTICE and not calls


def test_template_roundtrip_and_whole_input_limits(case):
 row=submit(case,command.HEADER,event='template');assert row['text']==command.TEMPLATE
 assert submit(case,row['text'],event='roundtrip')['text']==command.NOTICE
 assert submit(case,BLOCK+'x'*4001,event='chars')['text']==command.NOTICE
 # <=4000 chars, but >8000UTF8; rejection precedes parse/individual field checks.
 text=command.HEADER+'\nКанал: '+'😀'*2100
 assert len(text)<4000 and len(text.encode())>8000
 assert submit(case,text,event='bytes')['text']==command.NOTICE


def test_explicit_duration_endpoints_and_twelve_publications_call_actual_core(case,monkeypatch):
 text=BLOCK.replace('48','8784').replace('24','1').replace('2030-01-10T12:00:00+03:00','2030-01-10T10:30:00+03:00')
 text=text.replace('Публикации: нет','Публикации: '+';'.join('2030-01-09T'+str(i).zfill(2)+':00:00+03:00' for i in range(12)))
 calls,_=spy_core(monkeypatch);submit(case,text)
 assert len(calls[0][0].publications)==12 and calls[0][1]['threshold'].days==366
 assert submit(case,text+';2030-01-09T12:00:00+03:00',event='too-many')['text']==command.NOTICE


@pytest.mark.parametrize('gate',['staff','group','nonclosed','missing_intelligence','config_business','own_uid','multiadmin'])
def test_current_private_soleadmin_gate(case,gate):
 sender='test_operator';channel='bot';source=None
 if gate=='staff':sender='frost';source='telegram:2:10'
 elif gate=='group':channel='group'
 elif gate=='nonclosed':case.c.pop('team_intake')
 elif gate=='missing_intelligence':case.e.intelligence=None
 elif gate=='config_business':case.c['business']='OTHER'
 elif gate=='own_uid':source='telegram:999:10'
 elif gate=='multiadmin':case.c['admins'].append('frost')
 submit(case,sender=sender,channel=channel,source=source)
 assert not case.s.rows('SELECT * FROM outbox')


@pytest.mark.parametrize('phase',['queue','identity','scope','body','transport'])
def test_atomic_queue_host_scope_body_transport_drift_rolls_back(case,monkeypatch,phase):
 real=case.s.queue
 def drift(*a,**kw):
  value=real(*a,**kw)
  if phase=='queue':raise RuntimeError('synthetic queue failure')
  if phase=='identity':case.e.intelligence=None
  elif phase=='scope':case.c['admins']=[]
  elif phase=='body':case.s.execute("UPDATE outbox SET text='tampered'")
  elif phase=='transport':case.s.execute("UPDATE outbox SET preferred_transport='business'")
  return value
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises((ValueError,RuntimeError)):submit(case)
 assert not case.s.rows('SELECT * FROM outbox') and not case.s.rows("SELECT * FROM events WHERE id='scenario'")


def test_duplicate_event_one_reply_no_offer_and_pending_barrier(case):
 row=submit(case);assert row
 assert case.e.reply('scenario','test_operator',BLOCK,source_id='telegram:1:10',channel='bot',message_at=case.clock[0]) is False
 case.e.process('scenario')
 assert len(case.s.rows('SELECT * FROM outbox'))==1
 barrier=case.s.rows("SELECT * FROM answer_citation_turns")
 assert len(barrier)==1 and barrier[0]['phase']=='pending'
 assert not case.s.rows('SELECT * FROM dialogue_memory') if case.s.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dialogue_memory'").fetchone() else True
