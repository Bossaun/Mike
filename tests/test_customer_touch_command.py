"""Actual hypothetical touch scenario through accepted core, no old suites."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.customer_touch_command as command

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




def block(**changes):
 fields={command._NOW:'2030-01-10T12:00:00+03:00',command._BASIS:'2030-01-09T10:00:00+03:00',command._BCONF:'да',
  command._OBS:'2030-01-09T11:00:00+03:00',command._PCONF:'да',command._GRANT:'да',command._OPT:'нет',
  command._PSTART:'2030-01-09T12:00:00+03:00',command._PEND:'2030-01-12T00:00:00+03:00',command._READY:'да',
  command._START:'2030-01-10T00:00:00+03:00',command._END:'2030-01-11T00:00:00+03:00'}
 fields.update(changes)
 return command.HEADER+'\n'+'\n'.join(k+': '+fields[k] for k in command._LABELS)


def submit(c,text=None,event='scenario',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text or block(),channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0])
 c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def spy_core(monkeypatch):
 actual=command.core.touch_preview;calls=[];results=[]
 def spy(events,plans,permissions,receipts,**kw):
  calls.append((events,plans,permissions,receipts,kw));result=actual(events,plans,permissions,receipts,**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'touch_preview',spy)
 return calls,results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_core_positive_private_scenario_not_actual_contact_authority(case,monkeypatch,channel):
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
 assert len(calls)==1 and len(results[0].candidates)==1
 events,plans,permissions,receipts,kw=calls[0];candidate=results[0].candidates[0]
 assert len(events)==2 and all(type(e) is command.core.Event and e.direction=='incoming' for e in events)
 assert type(plans[0]) is command.core.TouchPlan and type(permissions[0]) is command.core.ContactPermission and type(plans[0].window) is command.core.Window
 assert permissions[0].granted is True and permissions[0].opt_out is False and plans[0].readiness is True
 assert type(candidate.conversation) is command.core.Conversation and candidate.draft_only and candidate.kind=='touch'
 assert candidate.due_at==datetime.fromisoformat('2030-01-09T21:00:00+00:00') and candidate.draft==command.DRAFT
 assert receipts==() and kw['dispositions']==() and plans[0].owner_id not in MEMBERS
 assert 'Это не разрешение связаться с реальным клиентом' in row['text'] and command.DRAFT in row['text']
 assert 'Прежняя обработка' not in row['text'] and 'Предыдущие касания' in row['text']
 assert 'контакт разрешён' not in row['text'].casefold() and candidate.id not in row['text'] and candidate.owner_id not in row['text']
 assert row['task_id'] is None and row['preferred_transport']==channel and row['recipient']=='test_operator' and row['parse_mode']=='HTML'
 assert len(row['text'].encode('utf-16-le'))//2<=3500 and set(writes)<={'events','answer_citation_turns','outbox'}
 assert all(before[t]==case.s.rows('SELECT * FROM '+t) for t in tables) and not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('changes,reason',[
 ({command._BCONF:'нет'},'unconfirmed_basis'),
 ({command._OPT:'да',command._GRANT:'нет'},'opt_out'),
 ({command._GRANT:'нет'},'unknown_or_declined_consent'),
 ({command._GRANT:'неизвестно'},'unknown_or_declined_consent'),
 ({command._PCONF:'нет'},'unknown_or_declined_consent'),
 ({command._PSTART:'неизвестно',command._PEND:'неизвестно'},'unknown_or_declined_consent'),
 ({command._READY:'нет'},'unknown_or_not_ready'),({command._READY:'неизвестно'},'unknown_or_not_ready'),
 ({command._NOW:'2030-01-12T00:00:00+03:00'},'outside_consent_window'),
 ({command._PSTART:'2030-01-10T01:00:00+03:00'},'outside_consent_window'),
 ({command._PEND:'2030-01-10T23:00:00+03:00'},'outside_consent_window'),
 ({command._NOW:'2030-01-11T00:00:00+03:00'},'outside_plan_window'),
 ({command._NOW:'2030-01-09T23:59:59+03:00'},'outside_plan_window'),
 ({command._OBS:'2030-01-11T11:00:00+03:00'},'unknown_consent')])
def test_actual_core_reason_is_rendered_without_positive_permission(case,monkeypatch,changes,reason):
 calls,results=spy_core(monkeypatch);row=submit(case,block(**changes))
 assert calls and not results[0].candidates and results[0].blocked[0].reason==reason
 assert 'кандидат заблокирован: '+command._REASONS[reason] in row['text']
 assert 'Это не разрешение' in row['text'] and command.DRAFT not in row['text']


def test_completely_unknown_permission_constructs_no_event_or_grant(case,monkeypatch):
 calls,results=spy_core(monkeypatch);row=submit(case,block(**{k:'неизвестно' for k in command._PERMISSION}))
 events,plans,permissions,receipts,kw=calls[0]
 assert len(events)==1 and permissions==() and receipts==() and kw['dispositions']==()
 assert results[0].blocked[0].reason=='unknown_consent' and 'кандидат заблокирован' in row['text']


@pytest.mark.parametrize('field',[command._PCONF,command._GRANT,command._OPT,command._PSTART,command._PEND])
def test_partial_unknown_observation_refuses_without_fabrication(case,monkeypatch,field):
 changes={k:'неизвестно' for k in command._PERMISSION};changes[field]='да' if field in (command._PCONF,command._GRANT,command._OPT) else '2030-01-09T00:00:00+03:00'
 calls,_=spy_core(monkeypatch);assert submit(case,block(**changes))['text']==command.NOTICE and not calls


@pytest.mark.parametrize('changes',[
 {command._OPT:'да'}, # contradictory grant True plus opt-out
 {command._OPT:'неизвестно'}, {command._PCONF:'неизвестно'}, {command._BCONF:'неизвестно'},
 {command._GRANT:'True'}, {command._READY:'1'}, {command._PSTART:'неизвестно'}, {command._PEND:'неизвестно'},
 {command._NOW:'2030-01-10T12:00:00'}, {command._NOW:'2030-01-10T12:00:00+24:00'},
 {command._OBS:'2030-02-30T12:00:00+03:00'}, {command._START:'2030-01-08T12:00:00+03:00'},
 {command._END:'2030-01-10T00:00:00+03:00'}, {command._END:'2032-01-01T00:00:00+03:00'},
 {command._PEND:'2030-01-09T00:00:00+03:00'}, {command._PEND:'2032-01-01T00:00:00+03:00'}])
def test_invalid_or_contradictory_scenario_never_outputs_candidate(case,changes):
 row=submit(case,block(**changes));assert row['text']==command.NOTICE
 assert not case.s.rows('SELECT * FROM tasks') and not case.s.rows('SELECT * FROM sources')


@pytest.mark.parametrize('which',['plan_start','consent_start','plan_equals_consent'])
def test_actual_core_window_start_included_full_containment(case,monkeypatch,which):
 changes={command._NOW:'2030-01-10T00:00:00+03:00'}
 if which=='consent_start':changes[command._PSTART]='2030-01-10T00:00:00+03:00'
 if which=='plan_equals_consent':changes.update({command._START:'2030-01-09T12:00:00+03:00',command._END:'2030-01-12T00:00:00+03:00'})
 calls,results=spy_core(monkeypatch);row=submit(case,block(**changes))
 assert len(results[0].candidates)==1 and command.DRAFT in row['text']


def test_template_roundtrip_missing_duplicate_unknown_control_and_whole_limits(case,monkeypatch):
 calls,_=spy_core(monkeypatch)
 template=submit(case,command.HEADER,event='template');assert template['text']==command.TEMPLATE
 assert submit(case,template['text'],event='roundtrip')['text']==command.NOTICE
 for n,text in enumerate([
  '\n'.join(block().split('\n')[:-1]),
  block().replace(command._READY+': да',command._READY+': да\n'+command._READY+': да'),
  block().replace(command._READY+':','Клиент Alice:'),
  block().replace(command._READY+': да',command._READY+': да\t'),
  block()+'x'*4001,
  command.HEADER+'\n'+('😀'*2100),
 ]):assert submit(case,text,event='bad-'+str(n))['text']==command.NOTICE
 assert not calls


@pytest.mark.parametrize('gate',['staff','group','nonclosed','missing_intelligence','business','own_uid','multiadmin'])
def test_private_current_soleadmin_gate(case,gate):
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
def test_atomic_captured_host_scope_exact_body_transport_rollback(case,monkeypatch,phase):
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


def test_local_scope_identity_dedupe_i14_and_no_real_permissions(case,monkeypatch):
 calls,_=spy_core(monkeypatch);submit(case);first=calls[0][0][0].conversation
 submit(case,event='business',channel='business');second=calls[1][0][0].conversation
 assert first!=second
 assert case.e.reply('scenario','test_operator',block(),source_id='telegram:1:10',channel='bot',message_at=case.clock[0]) is False
 case.e.process('scenario');assert len(case.s.rows('SELECT * FROM outbox'))==2 and len(calls)==2
 assert all(r['phase']=='pending' for r in case.s.rows('SELECT * FROM answer_citation_turns'))
 assert not case.s.rows('SELECT * FROM knowledge_readers') and not case.s.rows('SELECT * FROM runtime')
