"""I48 actual hypothetical touch receipt reconciliation via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.customer_touch_reconciliation_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: План <A> & тест\nМомент сценария: 2026-10-16T12:00:00+03:00\n'
 'Начало отчетного окна: 2026-10-16T09:00:00+03:00\nКонец отчетного окна: 2026-10-16T13:00:00+03:00\n'
 'Событие 1 — основание: 2026-10-16T09:00:00+03:00\nСобытие 1 подтверждено по сценарию: да\n'
 'Начало окна плана: 2026-10-16T10:00:00+03:00\nКонец окна плана: 2026-10-16T14:00:00+03:00\n'
 'Событие 2 — исходящее: 2026-10-16T11:00:00+03:00\nСобытие 2 подтверждено по сценарию: да\n'
 'Запись квитанции A: 1; да\nЗапись квитанции B: не передано\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='reconciliation',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0]);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def changed(text=BLOCK,**fields):
 return '\n'.join(line.split(': ',1)[0]+': '+fields[line.split(': ',1)[0]] if ': ' in line and line.split(': ',1)[0] in fields else line for line in text.splitlines())


def spy_core(monkeypatch):
 actual=command.core.reconcile_touches;calls=[];summaries=[]
 def spy(events,plans,receipts,**kw):
  calls.append((events,plans,receipts,kw));result=actual(events,plans,receipts,**kw);summaries.append(result);return result
 monkeypatch.setattr(command.core,'reconcile_touches',spy)
 return calls,summaries


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_core_summary_conditional_counts_and_own_dispatch(case,monkeypatch,channel):
 calls,summaries=spy_core(monkeypatch)
 snapshots={t:case.s.rows('SELECT * FROM '+t) for t in ('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')}
 import sqlite3
 reads=[];writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ:
   reads.append(table)
   if table in snapshots or table in ('knowledge_chunks','knowledge_readers'):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('new command constructed Index'))
  m.setattr(command.core,'touch_preview',lambda *a,**kw:pytest.fail('reconciliation is not eligibility'))
  m.setattr(command.core,'ContactPermission',lambda *a,**kw:pytest.fail('must not fabricate consent'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(summaries)==1
 events,plans,receipts,kw=calls[0];plan=plans[0];summary=summaries[0]
 assert len(events)==2 and len(plans)==len(receipts)==1
 assert type(plan) is command.core.TouchPlan and type(plan.window) is command.core.Window and type(receipts[0]) is command.core.TouchReceipt
 assert plan.readiness is None and plan.permission_id.startswith('permission-not-checked:') and plan.owner_id.startswith('scenario-owner:') and plan.owner_id not in MEMBERS
 assert events[0].direction=='incoming' and events[1].direction=='outgoing'
 assert receipts[0].event_id==events[1].id and receipts[0].plan_id==plan.id
 assert summary.planned_ids==summary.confirmed_ids==(plan.id,) and summary.pending_ids==summary.uncertain_ids==()
 body=row['text'];assert 'План &lt;A&gt; &amp; тест' in body and 'С условными подтверждёнными отметками: 1' in body
 assert 'не независимые квитанции Telegram' in body and 'Реальная доставка и выполнение этим не проверены' in body
 assert 'согласие, готовность и личности не проверяются' in body
 for identity in (plan.id,plan.owner_id,plan.permission_id,plan.conversation.customer_id,events[1].id,receipts[0].id):assert identity not in body
 assert not any(token in body for token in ('planned_ids','confirmed_ids','pending_ids','uncertain_ids'))
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('no_event',[True,False])
def test_no_receipt_or_outgoing_alone_pending_is_not_nondelivery(case,monkeypatch,no_event):
 calls,summaries=spy_core(monkeypatch)
 updates={'Запись квитанции A':'не передано'}
 if no_event:updates.update({'Событие 2 — исходящее':'не передано','Событие 2 подтверждено по сценарию':'не применяется'})
 row=submit(case,changed(**updates));plan=calls[0][1][0];summary=summaries[0]
 assert summary.pending_ids==(plan.id,) and summary.confirmed_ids==summary.uncertain_ids==()
 assert calls[0][2]==() and len(calls[0][0])==(1 if no_event else 2)
 assert 'Это не доказательство недоставки' in row['text']


@pytest.mark.parametrize('event_mark',['да','нет','неизвестно'])
@pytest.mark.parametrize('receipt_mark',['да','нет','неизвестно'])
def test_actual_confirmation_pair_uncertain_not_resend_permission(case,monkeypatch,event_mark,receipt_mark):
 calls,summaries=spy_core(monkeypatch)
 row=submit(case,changed(**{'Событие 2 подтверждено по сценарию':event_mark,'Запись квитанции A':'1; '+receipt_mark}))
 plan=calls[0][1][0];summary=summaries[0]
 assert bool(summary.confirmed_ids)==(event_mark=='да' and receipt_mark=='да')
 if not summary.confirmed_ids:
  assert summary.uncertain_ids==(plan.id,) and 'Это не разрешение повторить отправку' in row['text']
 assert calls[0][0][1].confirmed==(event_mark=='да') and calls[0][2][0].confirmed==(receipt_mark=='да')


@pytest.mark.parametrize('mark',['нет','неизвестно'])
def test_basis_confirmation_is_not_an_eligibility_or_consent_check(case,monkeypatch,mark):
 calls,summaries=spy_core(monkeypatch)
 row=submit(case,changed(**{'Событие 1 подтверждено по сценарию':mark}))
 assert calls[0][0][0].confirmed is False and summaries[0].confirmed_ids
 assert 'Это не проверка допуска к контакту' in row['text']


@pytest.mark.parametrize('first,second,expected',[
 ('1; да','1; да','confirmed'),('1; нет','1; нет','uncertain'),('1; неизвестно','1; нет','uncertain'),
 ('1; нет','2; да','confirmed'),('2; да','1; нет','confirmed'),('1; да','2; да','confirmed'),('1; неизвестно','2; неизвестно','uncertain')])
def test_actual_dedup_distinct_records_and_confirmed_minus_uncertain(case,monkeypatch,first,second,expected):
 calls,summaries=spy_core(monkeypatch)
 row=submit(case,changed(**{'Запись квитанции A':first,'Запись квитанции B':second}))
 assert row and len(calls[0][2])==2
 summary=summaries[0];plan=calls[0][1][0]
 assert summary.planned_ids==(plan.id,)
 assert summary.confirmed_ids==((plan.id,) if expected=='confirmed' else ())
 assert summary.uncertain_ids==((plan.id,) if expected=='uncertain' else ())
 assert not summary.pending_ids


@pytest.mark.parametrize('first,second',[('1; да','1; нет'),('1; неизвестно','1; да'),('2; нет','2; да')])
def test_actual_core_conflicting_same_identity_receipts_refuse_whole(case,monkeypatch,first,second):
 calls,summaries=spy_core(monkeypatch)
 row=submit(case,changed(**{'Запись квитанции A':first,'Запись квитанции B':second}))
 assert row['text']==command.NOTICE and len(calls)==1 and summaries==[]


@pytest.mark.parametrize('updates,expected',[
 ({'Момент сценария':'2026-10-16T10:00:00+03:00','Событие 2 — исходящее':'2026-10-16T10:00:00+03:00'},'confirmed'),
 ({'Момент сценария':'2026-10-16T10:59:59+03:00'},'pending'),
 ({'Момент сценария':'2026-10-16T15:00:00+03:00','Событие 2 — исходящее':'2026-10-16T13:00:00+03:00'},'pending'),
 ({'Момент сценария':'2026-10-16T15:00:00+03:00','Событие 2 — исходящее':'2026-10-16T12:59:59+03:00'},'confirmed'),
 ({'Момент сценария':'2026-10-16T09:59:59+03:00'},'empty'),
 ({'Конец отчетного окна':'2026-10-16T10:00:00+03:00'},'empty'),
 ({'Начало отчетного окна':'2026-10-16T10:00:01+03:00'},'empty'),
 ({'Момент сценария':'2026-10-16T08:00:00Z'},'confirmed')])
def test_actual_window_start_end_clamp_future_and_empty_selection(case,monkeypatch,updates,expected):
 calls,summaries=spy_core(monkeypatch);case.clock[0]+=500*86400
 row=submit(case,changed(**updates));assert row and len(calls)==1
 summary=summaries[0];plan=calls[0][1][0]
 assert bool(summary.planned_ids)==(expected!='empty')
 assert bool(summary.confirmed_ids)==(expected=='confirmed')
 assert bool(summary.pending_ids)==(expected=='pending')
 assert calls[0][3]['now'].timestamp()!=case.clock[0]
 if expected=='empty':assert 'Это не означает, что реальных планов или работы не было' in row['text']


@pytest.mark.parametrize('at',['2026-10-16T14:00:00+03:00','2026-10-16T09:59:59+03:00','2026-10-16T09:00:00+03:00'])
def test_actual_receipt_plan_window_and_basis_chronology_reject(case,monkeypatch,at):
 calls,summaries=spy_core(monkeypatch)
 assert submit(case,changed(**{'Событие 2 — исходящее':at}))['text']==command.NOTICE
 assert len(calls)==1 and summaries==[]


def test_exact_template_roundtrip_and_no_short_consent_route(case,monkeypatch):
 monkeypatch.setattr(command.core,'reconcile_touches',lambda *a,**kw:pytest.fail('template core call'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and len(row['text'].splitlines())==14
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert command.is_command(text) is False


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_field_no_default_or_lookup(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'reconcile_touches',lambda *a,**kw:pytest.fail('missing input core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Основание':'доставка проверена'}, {'Название сценария':'x'*81}, {'Название сценария':'bad\tname'}, {'Название сценария':'bad\rname'}, {'Название сценария':'bad\x7fname'},
 {'Событие 1 подтверждено по сценарию':'true'}, {'Событие 2 подтверждено по сценарию':'возможно'},
 {'Запись квитанции A':'3; да'}, {'Запись квитанции A':'01; да'}, {'Запись квитанции A':'1; true'}, {'Запись квитанции B':'1; да; нет'},
 {'Событие 2 — исходящее':'не передано'}, {'Событие 2 — исходящее':'не передано','Событие 2 подтверждено по сценарию':'не применяется'},
 {'Событие 2 подтверждено по сценарию':'не применяется'},
 {'Момент сценария':'сейчас'}, {'Момент сценария':'2026-10-16T12:00:00'}, {'Момент сценария':'2026-10-16T12:00:00+03:60'},
 {'Момент сценария':'2026-10-16T12:00:00+24:00'}, {'Момент сценария':'2026-02-30T12:00:00+03:00'},
 {'Начало окна плана':'2026-10-16T08:59:59+03:00'}, {'Конец окна плана':'2026-10-16T10:00:00+03:00'},
 {'Конец отчетного окна':'2026-10-16T09:00:00+03:00'}, {'Конец окна плана':'2027-10-18T14:00:00+03:00'},
 {'Конец отчетного окна':'2027-10-18T13:00:00+03:00'}])
def test_invalid_literals_dates_chronology_and_nonapplicable_flags(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('suffix',['\n','\nЗапись квитанции A: 1; да','\nСогласие: да','\nКлиент: real','x'*4096])
def test_no_extra_fields_receivers_or_partial_truncation(case,suffix):
 assert submit(case,BLOCK+suffix)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['utf8','utf16','output','wrong_type','wrong_tuple','foreign_id','duplicate_id','overlap','missing_partition'])
def test_whole_refusal_caps_and_actual_summary_invariants(case,monkeypatch,mode):
 text=BLOCK
 if mode=='utf8':text=changed(**{'Название сценария':'😀'*1000})
 elif mode=='utf16':text=changed(**{'Название сценария':'Ж'*1900})
 elif mode=='output':
  # Actual core runs; a presentation-size fault must refuse, not trim.
  monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.reconcile_touches
  def core(*a,**kw):
   result=actual(*a,**kw);identifier=result.planned_ids[0]
   if mode=='wrong_type':return SimpleNamespace(planned_ids=())
   if mode=='wrong_tuple':return command.core.TouchSummary([identifier],(),(),())
   if mode=='foreign_id':return command.core.TouchSummary(('foreign',),('foreign',),(),())
   if mode=='duplicate_id':return command.core.TouchSummary((identifier,identifier),(identifier,),(),())
   if mode=='overlap':return command.core.TouchSummary((identifier,),(identifier,),(identifier,),())
   if mode=='missing_partition':return command.core.TouchSummary((identifier,),(),(),())
  monkeypatch.setattr(command.core,'reconcile_touches',core)
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('change',['employee','group','foreign_private','nonclosed','no_intel','business_mismatch','two_admins'])
def test_private_current_admin_host_scope_only(case,change):
 kwargs={}
 if change=='employee':kwargs['sender']='frost';kwargs['source']='telegram:2:10'
 elif change=='group':kwargs['channel']='group';kwargs['source']='telegram:-4:10'
 elif change=='foreign_private':kwargs['source']='telegram:2:10'
 elif change=='nonclosed':case.c['team_roster_verified']=False
 elif change=='no_intel':case.e.intelligence=None
 elif change=='business_mismatch':case.e.b='OTHER'
 elif change=='two_admins':case.c['admins'].append('frost')
 assert submit(case,**kwargs) is None
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'touch-reconciliation-preview:%'")


@pytest.mark.parametrize('change',['config','intelligence','index','policy','recipient','body','transport','queue_failure'])
def test_atomic_host_policy_body_original_transport_race_rollback(case,monkeypatch,change):
 actual=case.s.queue
 def queue(*a,**kw):
  result=actual(*a,**kw)
  if str(a[3]).startswith(command.PREFIX):
   import copy
   if change=='config':case.e.c=copy.deepcopy(case.c)
   elif change=='intelligence':case.e.intelligence=copy.copy(case.e.intelligence)
   elif change=='index':case.e.intelligence.index=copy.copy(case.e.intelligence.index)
   elif change=='policy':case.c['review_mode']=False
   elif change=='recipient':case.s.execute("UPDATE outbox SET recipient='frost' WHERE dedupe=?",(a[3],))
   elif change=='body':case.s.execute("UPDATE outbox SET text='forged' WHERE dedupe=?",(a[3],))
   elif change=='transport':case.s.execute("UPDATE outbox SET preferred_transport='group' WHERE dedupe=?",(a[3],))
   elif change=='queue_failure':raise RuntimeError('synthetic queue failure')
  return result
 monkeypatch.setattr(case.s,'queue',queue)
 with pytest.raises((ValueError,RuntimeError)):submit(case)
 assert not case.s.rows("SELECT * FROM events WHERE id='reconciliation'") and not case.s.rows('SELECT * FROM outbox')


def test_replay_exactly_one_core_call_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
