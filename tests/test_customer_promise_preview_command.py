"""I46 actual hypothetical Promise evaluation via private Engine; no old suites."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.customer_promise_preview_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: Обещание <A> & тест\nМомент сценария: 2026-10-16T12:00:00+03:00\n'
 'Исходящее событие 1 — обещание: 2026-10-16T09:00:00+03:00\nСобытие 1 подтверждено по сценарию: да\n'
 'Обещание подтверждено по сценарию: да\nСрок обещания: 2026-10-16T11:00:00+03:00\nИсполнитель: '+command.OWNER+
 '\nИсходящее событие 2 — результат: не передано\nСобытие 2 подтверждено по сценарию: не применяется\n'
 'Связь результата с обещанием подтверждена: не применяется\nПредыдущая обработка: '+command.PROCESSING+'\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='promise',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0]);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def with_result(text=BLOCK,at='2026-10-16T10:00:00+03:00',event_mark='да',link='да'):
 return text.replace('Исходящее событие 2 — результат: не передано','Исходящее событие 2 — результат: '+at).replace('Событие 2 подтверждено по сценарию: не применяется','Событие 2 подтверждено по сценарию: '+event_mark).replace('Связь результата с обещанием подтверждена: не применяется','Связь результата с обещанием подтверждена: '+link)


def spy_core(monkeypatch):
 actual=command.core.promise_preview;calls=[];results=[]
 def spy(events,promises,records,**kw):
  calls.append((events,promises,records,kw));result=actual(events,promises,records,**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'promise_preview',spy)
 return calls,results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_core_positive_candidate_opaque_owner_and_own_dispatch(case,monkeypatch,channel):
 calls,results=spy_core(monkeypatch)
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
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(results)==1
 events,promises,records,kw=calls[0];promise=promises[0]
 assert len(events)==len(promises)==1 and records==() and kw['dispositions']==()
 assert type(events[0]) is command.core.Event and type(promise) is command.core.Promise and type(promise.conversation) is command.core.Conversation
 assert events[0].direction=='outgoing' and events[0].confirmed is True and promise.confirmed is True and promise.event_id==events[0].id
 assert promise.owner_id.startswith('scenario-owner:') and promise.owner_id not in MEMBERS
 assert len(results[0].candidates)==1 and not results[0].blocked
 candidate=results[0].candidates[0];assert candidate.kind=='promise' and candidate.draft_only is True and candidate.owner_id==promise.owner_id
 body=row['text'];assert 'Обещание &lt;A&gt; &amp; тест' in body and candidate.draft in body and 'получен кандидат на ручную проверку' in body
 assert 'Предыдущая обработка не проверяется и не учитывается' in body and 'непроверенный сценарий' in body
 assert 'условная роль сценария' in body
 for identity in (candidate.id,promise.id,promise.owner_id,events[0].id,events[0].evidence_id,promise.conversation.customer_id):assert identity not in body
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('label,value',[
 ('Срок обещания','неизвестно'),('Событие 1 подтверждено по сценарию','нет'),('Событие 1 подтверждено по сценарию','неизвестно'),
 ('Обещание подтверждено по сценарию','нет'),('Обещание подтверждено по сценарию','неизвестно')])
def test_explicit_unknown_deadline_confirmation_actual_blocked_no_guesses(case,monkeypatch,label,value):
 calls,results=spy_core(monkeypatch)
 text='\n'.join(label+': '+value if line.startswith(label+': ') else line for line in BLOCK.splitlines())
 row=submit(case,text)
 assert not results[0].candidates and len(results[0].blocked)==1 and results[0].blocked[0].reason=='unconfirmed_promise_or_due'
 assert 'Кандидат заблокирован' in row['text'] and 'unconfirmed_promise_or_due' not in row['text']
 if label=='Срок обещания':assert calls[0][1][0].due_at is None and 'Срок по сценарию: неизвестно' in row['text']
 elif label.startswith('Событие'):assert calls[0][0][0].confirmed is False
 else:assert calls[0][1][0].confirmed is False


@pytest.mark.parametrize('slice,has_candidate',[
 ('2026-10-16T10:59:59+03:00',False),('2026-10-16T11:00:00+03:00',True),
 ('2026-10-16T12:00:00+03:00',True),('2026-10-16T08:59:59+03:00',False),('2026-10-16T08:00:00Z',True)])
def test_actual_due_boundary_future_declaration_and_supplied_slice_not_server_clock(case,monkeypatch,slice,has_candidate):
 calls,results=spy_core(monkeypatch);case.clock[0]+=500*86400
 text=BLOCK.replace('Момент сценария: 2026-10-16T12:00:00+03:00','Момент сценария: '+slice)
 row=submit(case,text);assert bool(results[0].candidates)==has_candidate
 assert calls[0][3]['now']==datetime.fromisoformat(slice.replace('Z','+00:00'))
 if not has_candidate:
  assert not results[0].blocked and 'Правило не вернуло кандидата' in row['text']
  assert 'не подтверждает выполнение или закрытие реального обещания' in row['text']
 assert 'не проверяется и не учитывается' in row['text']


@pytest.mark.parametrize('event_mark',['да','нет','неизвестно'])
@pytest.mark.parametrize('link',['да','нет','неизвестно'])
def test_linked_later_result_requires_both_explicit_confirmations(case,monkeypatch,event_mark,link):
 calls,results=spy_core(monkeypatch);row=submit(case,with_result(event_mark=event_mark,link=link))
 events,promises,records,kw=calls[0]
 assert len(events)==2 and len(records)==1 and type(records[0]) is command.core.PromiseResult
 assert events[1].reply_to==events[0].id and records[0].promise_id==promises[0].id and records[0].event_id==events[1].id
 assert events[1].confirmed==(event_mark=='да') and records[0].confirmed==(link=='да')
 assert bool(results[0].candidates)!=(event_mark=='да' and link=='да')
 assert 'событие №2' in row['text'] and events[1].id not in row['text']
 if not results[0].candidates:
  assert 'Правило не вернуло кандидата' in row['text'] and 'не подтверждает выполнение или закрытие' in row['text']


def test_future_linked_result_does_not_suppress_current_candidate(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,with_result(at='2026-10-16T13:00:00+03:00'))
 assert row and len(results[0].candidates)==1 and calls[0][0][1].at>calls[0][3]['now']


def test_unknown_marks_remain_blocked_even_with_claimed_result(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,with_result(BLOCK.replace('Обещание подтверждено по сценарию: да','Обещание подтверждено по сценарию: неизвестно')))
 assert len(results[0].blocked)==1 and not results[0].candidates and 'Кандидат заблокирован' in row['text']


def test_actual_core_draft_text_used_escaped_not_wrapper_recreated(case,monkeypatch):
 from dataclasses import replace
 actual=command.core.promise_preview
 def core(*a,**kw):
  result=actual(*a,**kw)
  return command.core.Preview((replace(result.candidates[0],draft='Actual <CORE> & draft'),),())
 monkeypatch.setattr(command.core,'promise_preview',core)
 assert 'Actual &lt;CORE&gt; &amp; draft' in submit(case)['text']


def test_exact_template_roundtrip_and_short_consent_exclusion(case,monkeypatch):
 monkeypatch.setattr(command.core,'promise_preview',lambda *a,**kw:pytest.fail('template core call'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and len(row['text'].splitlines())==13
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for phrase in ('да','согласен','отправь','нет'):assert command.is_command(phrase) is False


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_field_no_default_or_prior_processing_inference(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'promise_preview',lambda *a,**kw:pytest.fail('missing input core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('mutation',[
 lambda b:b+'\n',lambda b:b+'\nСрок обещания: неизвестно',lambda b:b+'\nКлиент: real-customer',
 lambda b:b.replace(command.OWNER,'test_operator'),lambda b:b.replace(command.PROCESSING,'выполнено'),
 lambda b:b.replace(command.BASIS,'проверенные сообщения'),lambda b:b.replace('Событие 1 подтверждено по сценарию: да','Событие 1 подтверждено по сценарию: true'),
 lambda b:b.replace('Обещание подтверждено по сценарию: да','Обещание подтверждено по сценарию: возможно'),
 lambda b:b.replace('Срок обещания: 2026-10-16T11:00:00+03:00','Срок обещания: завтра'),
 lambda b:b.replace('Срок обещания: 2026-10-16T11:00:00+03:00','Срок обещания: 2026-10-16T08:59:59+03:00'),
 lambda b:b.replace('Событие 2 подтверждено по сценарию: не применяется','Событие 2 подтверждено по сценарию: да'),
 lambda b:b.replace('Связь результата с обещанием подтверждена: не применяется','Связь результата с обещанием подтверждена: неизвестно'),
 lambda b:b.replace('Исходящее событие 2 — результат: не передано','Исходящее событие 2 — результат: неизвестно'),
 lambda b:b.replace('2026-10-16T09:00:00+03:00','2026-10-16T09:00:00'),lambda b:b.replace('2026-10-16T09:00:00+03:00','2026-02-30T09:00:00+03:00'),
 lambda b:b.replace('2026-10-16T12:00:00+03:00','сейчас'),lambda b:b.replace('2026-10-16T12:00:00+03:00','20261016T120000+0300'),
 lambda b:b.replace('2026-10-16T12:00:00+03:00','2026-10-16T12:00:00+03:60'),lambda b:b.replace('2026-10-16T12:00:00+03:00','2026-10-16T12:00:00+24:00'),
 lambda b:b.replace('Обещание <A> & тест','x'*81),lambda b:b.replace('Обещание <A> & тест','x\tbad'),
 lambda b:b.replace('Обещание <A> & тест','x\rbad'),lambda b:b.replace('Обещание <A> & тест','x\x7fbad'),lambda b:b+'x'*4096])
def test_invalid_fields_no_guessed_identity_date_or_claim(case,mutation):
 assert submit(case,mutation(BLOCK))['text']==command.NOTICE


@pytest.mark.parametrize('at',['2026-10-16T08:59:59+03:00','2026-10-16T09:00:00+03:00'])
def test_actual_ordinal_result_chronology_must_follow_declaration(case,at):
 assert submit(case,with_result(at=at))['text']==command.NOTICE


@pytest.mark.parametrize('label',['Событие 2 подтверждено по сценарию','Связь результата с обещанием подтверждена'])
def test_supplied_result_cannot_silently_ignore_nonapplicable_mark(case,label):
 text=with_result().replace(label+': да',label+': не применяется')
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['utf8','utf16','overflow','wrong_preview','wrong_owner','wrong_target','wrong_scope','wrong_kind','draft_flag','bad_reason','too_many'])
def test_whole_refusal_limits_and_actual_core_result_identity(case,monkeypatch,mode):
 text=BLOCK
 if mode=='utf8':text=text.replace('Обещание <A> & тест','😀'*1000)
 elif mode=='utf16':text=text.replace('Обещание <A> & тест','Ж'*1900)
 else:
  from dataclasses import replace
  actual=command.core.promise_preview
  def core(*a,**kw):
   result=actual(*a,**kw)
   if mode=='wrong_preview':return SimpleNamespace(candidates=())
   candidate=result.candidates[0]
   if mode=='overflow':candidate=replace(candidate,draft='&'*1000)
   elif mode=='wrong_owner':candidate=replace(candidate,owner_id='test_operator')
   elif mode=='wrong_target':candidate=replace(candidate,target_id='other-promise')
   elif mode=='wrong_scope':candidate=replace(candidate,conversation=replace(candidate.conversation,thread_id='other'))
   elif mode=='wrong_kind':candidate=replace(candidate,kind='unanswered')
   elif mode=='draft_flag':object.__setattr__(candidate,'draft_only',False)
   elif mode=='bad_reason':return command.core.Preview((),(command.core.Blocked(candidate.conversation,candidate.target_id,'other-reason'),))
   elif mode=='too_many':return command.core.Preview((candidate,candidate),())
   return command.core.Preview((candidate,),())
  monkeypatch.setattr(command.core,'promise_preview',core)
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('change',['employee','group','foreign_private','nonclosed','no_intel','business_mismatch','two_admins'])
def test_private_current_admin_scope_only(case,change):
 kwargs={}
 if change=='employee':kwargs['sender']='frost';kwargs['source']='telegram:2:10'
 elif change=='group':kwargs['channel']='group';kwargs['source']='telegram:-4:10'
 elif change=='foreign_private':kwargs['source']='telegram:2:10'
 elif change=='nonclosed':case.c['team_roster_verified']=False
 elif change=='no_intel':case.e.intelligence=None
 elif change=='business_mismatch':case.e.b='OTHER'
 elif change=='two_admins':case.c['admins'].append('frost')
 assert submit(case,**kwargs) is None
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'customer-promise-preview:%'")


@pytest.mark.parametrize('change',['config','intelligence','index','policy','recipient','body','transport','queue_failure'])
def test_captured_host_original_transport_exact_queue_race_rollback(case,monkeypatch,change):
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
 assert not case.s.rows("SELECT * FROM events WHERE id='promise'") and not case.s.rows('SELECT * FROM outbox')


def test_replay_once_no_prior_processing_or_source_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert calls[0][3]['dispositions']==()
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
