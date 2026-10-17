"""New engine rewrite boundary only, actual synthetic dispatcher delivery."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
from goldway.task_card_reference import is_task_reference,reference_request,ReferenceUnavailable,NOTICE
import goldway.task_card_reference as command
MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600;TAIL='ESSENTIAL-FINAL-CONDITION'

@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:NOW);e.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',NOW))
 class API:
  def call(self,method,**kw):
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';return dict(message_id=11)
 d=Dispatcher(s,c,API(),lambda:NOW)
 import builtins,socket,httpx
 def fail(*a,**kw):pytest.fail('external I/O')
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield SimpleNamespace(e=e,s=s,c=c,d=d)
 s.db.close()



def setup(c):
 c.clock=[NOW];c.e.now=lambda:c.clock[0];c.d.now=lambda:c.clock[0]
 for task_id,title,owner in [('A','ALPHA','test_operator'),('B','BETA','frost')]:
  c.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',('GOLDWAY','source-'+task_id,'PRIVATE SOURCE NEVER READ','synthetic'))
  c.s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created)
    VALUES(?,?,?,?,?,?,?,?,?,?)''',('GOLDWAY',task_id,title,owner,'2026-10-20','next','done criterion','confirmed','source-'+task_id,NOW-10))
 def fail(*a,**kw):pytest.fail('unexpected model/history/retrieval/context')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail
 c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e.intelligence.memory.get=fail


def card(c,number=2,event='card',send=True,sender='test_operator',channel='bot'):
 uid=c.c['employees'][sender]['telegram_user_id']
 c.e.reply(event,sender,'Покажи задачу №'+str(number),channel=channel,source_id=('telegram:'+str(uid)+':1' if channel=='bot' else 'business:conn:'+str(uid)+':1'),message_at=c.clock[0])
 row=c.s.execute('SELECT * FROM outbox WHERE dedupe=?',('task-card:'+event,)).fetchone()
 if send:c.d.flush()
 return row


def question(c,text='Кто отвечает по этой задаче?',event='question',sender='test_operator',channel='bot',source=None,message_at='current',task_id=None):
 uid=c.c['employees'].get(sender,{}).get('telegram_user_id',999)
 at=c.clock[0] if message_at=='current' else message_at
 c.e.reply(event,sender,text,task_id=task_id,channel=channel,source_id=source or ('telegram:'+str(uid)+':2' if channel=='bot' else 'business:conn:'+str(uid)+':2'),message_at=at)
 c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE dedupe=?',('task-reference:'+event,)).fetchone()

@pytest.mark.parametrize('text',['Кто отвечает по этой задаче?','Какой срок у этой задачи?','Покажи эту задачу'])
@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_latest_delivered_i15_card_current_fields_chain(case,text,channel):
 setup(case);original=card(case,channel=channel);before=case.s.rows('SELECT * FROM tasks')
 receipt=case.s.rows('SELECT * FROM task_card_references');assert len(receipt)==1 and receipt[0]['task_id']=='B'
 assert 'text' not in receipt[0] and 'PRIVATE' not in json.dumps(receipt)
 row=question(case,text,channel=channel)
 assert row['task_id']=='B' and row['parse_mode']=='HTML' and 'BETA' in row['text'] and 'frost' in row['text'] and '2026-10-20' in row['text']
 assert 'ALPHA' not in row['text'] and 'PRIVATE' not in row['text'] and 'source-B' not in row['text']
 assert before==case.s.rows('SELECT * FROM tasks') and not case.s.rows('SELECT id FROM usage')
 assert len(case.s.rows('SELECT * FROM task_card_references'))==2
 case.d.flush();row=question(case,'Какой срок у этой задачи?',event='chain',channel=channel)
 assert row['task_id']=='B' and '2026-10-20' in row['text']


def test_old_model_focus_not_used(case):
 setup(case);card(case)
 # The actual code never reads this legacy value; even memory.get is forbidden.
 case.s.execute("INSERT INTO runtime(key,value) VALUES('legacy-model-focus','A')")
 assert question(case)['task_id']=='B'


def test_own_staff_scope_current_permissions(case):
 setup(case);case.s.execute('INSERT INTO chats(business,recipient,bot_chat,last_inbound) VALUES(?,?,?,?)',('GOLDWAY','frost','2',NOW))
 card(case,sender='frost');row=question(case,sender='frost');assert row['task_id']=='B' and 'BETA' in row['text']

@pytest.mark.parametrize('state',['pending','retry','unknown','failed','sending','blocked'])
def test_latest_unsent_no_older_delivered_fallback(case,state):
 setup(case);card(case,1,event='old');latest=card(case,2,event='new',send=False)
 case.s.execute('UPDATE outbox SET state=? WHERE id=?',(state,latest['id']))
 row=question(case);assert row['text']==NOTICE and row['task_id'] is None

@pytest.mark.parametrize('change',['legacy','ordinary','sourcefollowup','body','dedupe','deletedreceipt','missingdelivery','duplicatedelivery'])
def test_no_fallback_when_prior_unavailable_or_not_canonical(case,change):
 setup(case);card(case,1,event='old');latest=card(case,2,event='new')
 if change in ('legacy','deletedreceipt'):case.s.execute('DELETE FROM task_card_references WHERE outbox_id=?',(latest['id'],))
 elif change=='ordinary':
  from goldway.answer_citations import begin_answer
  begin_answer(case.s,case.c,'ordinary',dict(sender='test_operator',channel='bot',source_id='telegram:1:9'),NOW)
 elif change=='sourcefollowup':case.e.reply('source','test_operator','Откуда ты это знаешь?',source_id='telegram:1:9',channel='bot',message_at=NOW)
 elif change=='body':case.s.execute("UPDATE outbox SET text='forged BETA private' WHERE id=?",(latest['id'],))
 elif change=='dedupe':case.s.execute("UPDATE outbox SET dedupe='reminder:forged' WHERE id=?",(latest['id'],))
 elif change=='missingdelivery':case.s.execute('DELETE FROM deliveries WHERE outbox_id=?',(latest['id'],))
 else:case.s.execute("INSERT INTO deliveries(outbox_id,at,state) VALUES(?,?,'sent')",(latest['id'],NOW))
 assert question(case)['text']==NOTICE

@pytest.mark.parametrize('channel,source',[('business','business:conn:1:2'),('bot','telegram:777:2'),('business','business:other:1:2')])
def test_scope_no_cross_transport_chat_connection(case,channel,source):
 setup(case);card(case);row=question(case,channel=channel,source=source)
 assert row is None or row['text']==NOTICE

@pytest.mark.parametrize('change',['revision','owner','deleted','source','acl','protect','config'])
def test_current_revision_acl_and_profile_fail_closed(case,change):
 setup(case);card(case)
 if change=='revision':case.s.execute("UPDATE tasks SET revision=revision+1 WHERE id='B'")
 elif change=='owner':case.s.execute("UPDATE tasks SET owner='alen',revision=revision+1 WHERE id='B'")
 elif change=='deleted':case.s.execute("DELETE FROM tasks WHERE id='B'")
 elif change=='source':case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-B'")
 elif change=='acl':case.s.execute("INSERT INTO intelligence_task_acl VALUES('GOLDWAY','B','[\"revoked-document\"]')")
 elif change=='protect':case.s.execute('DELETE FROM intelligence_outbox_acl')
 else:case.c['team_roster_verified']=False
 row=question(case);assert row is None or row['text']==NOTICE


def test_staff_cannot_use_admin_or_hidden_proposed_card(case):
 setup(case);card(case)
 case.s.execute('INSERT INTO chats(business,recipient,bot_chat,last_inbound) VALUES(?,?,?,?)',('GOLDWAY','frost','2',NOW))
 assert question(case,sender='frost')['text']==NOTICE
 case.s.execute("UPDATE tasks SET status='proposed',revision=revision+1 WHERE id='B'")
 card(case,event='staff-draft',sender='frost')
 assert question(case,event='staff-after',sender='frost')['text']==NOTICE

@pytest.mark.parametrize('state',['pending','retry','sent','unknown','cancelled'])
def test_newer_relevant_outbound_is_barrier_even_without_inbound(case,state):
 setup(case);card(case)
 case.s.queue('GOLDWAY','test_operator','Other host output','reminder:later',NOW,'A',preferred_transport='bot')
 row=case.s.execute("SELECT id FROM outbox WHERE dedupe='reminder:later'").fetchone()
 case.s.execute('UPDATE outbox SET state=? WHERE id=?',(state,row['id']))
 assert question(case)['text']==NOTICE


def test_known_other_transport_pending_does_not_mix_scopes(case):
 setup(case);card(case)
 case.s.queue('GOLDWAY','test_operator','Business only','different',NOW,preferred_transport='business')
 assert question(case)['task_id']=='B'

@pytest.mark.parametrize('message_at',[None,NOW-1])
def test_missing_or_original_old_message_time_clarifies(case,message_at):
 setup(case);card(case);assert question(case,message_at=message_at)['text']==NOTICE


def test_late_question_before_actual_delivery_floor_denied(case):
 setup(case);card(case,send=False);case.clock[0]=NOW+1.1;case.d.flush();case.clock[0]=NOW+2
 assert question(case,message_at=NOW)['text']==NOTICE


def test_delivery_same_floor_second_as_original_question_allowed(case):
 setup(case);card(case,send=False);case.clock[0]=NOW+0.9;case.d.flush();case.clock[0]=NOW+1.2
 assert question(case,message_at=NOW)['task_id']=='B'

@pytest.mark.parametrize('column,value',[
 ('task_revision','X'*10000),('body_hash','X'*10000),('task_id','X'*10000),('outbox_id','X'*10000),('snapshot_digest','X'*10000)])
def test_malformed_metadata_sql_guard_no_text_leak(case,column,value):
 setup(case);card(case);case.s.execute('UPDATE task_card_references SET '+column+'=?',(value,))
 row=question(case);assert row['text']==NOTICE and value not in row['text']


def test_giant_old_body_and_nonnumeric_delivery_denied(case):
 setup(case);latest=card(case);case.s.execute('UPDATE outbox SET text=? WHERE id=?',('X'*20000,latest['id']))
 assert question(case)['text']==NOTICE
 setup_clock=case.clock[0]
 # A fresh valid producer gives a separate latest prospective anchor.
 card(case,event='next');case.s.execute("UPDATE deliveries SET at='PRIVATE MALFORMED' WHERE outbox_id=(SELECT MAX(id) FROM outbox)")
 assert question(case,event='after')['text']==NOTICE


def test_revocation_checked_before_old_body_read(case,monkeypatch):
 setup(case);card(case);case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-B'")
 execute=case.s.execute
 def guard(sql,args=()):
  if 'FROM outbox WHERE id=?' in sql and "'text'" not in sql and 'AS text' in sql:pytest.fail('old body read before current permission')
  return execute(sql,args)
 monkeypatch.setattr(case.s,'execute',guard)
 assert question(case)['text']==NOTICE


def test_global_window_bound_no_archive_fallback(case):
 setup(case);card(case)
 for i in range(129):case.s.queue('OTHER','foreign','unrelated','foreign:'+str(i),NOW)
 assert question(case)['text']==NOTICE
 plan=case.s.rows('EXPLAIN QUERY PLAN SELECT id FROM outbox NOT INDEXED WHERE id BETWEEN ? AND ? AND business=? AND recipient=? ORDER BY id',(1,128,'GOLDWAY','test_operator'))
 assert any('INTEGER PRIMARY KEY' in str(row) for row in plan)


def test_prospective_atomic_metadata_no_existing_card_backfill(case):
 setup(case);case.s.queue('GOLDWAY','test_operator','legacy card','legacy',NOW,'B',parse_mode='HTML',preferred_transport='bot');case.d.flush()
 assert not case.s.rows('SELECT * FROM task_card_references') and question(case)['text']==NOTICE


def test_replay_no_duplicate_derivative_or_metadata(case):
 setup(case);card(case);question(case);question(case)
 assert len(case.s.rows("SELECT id FROM outbox WHERE dedupe='task-reference:question'"))==1
 assert len(case.s.rows('SELECT * FROM task_card_references'))==2

@pytest.mark.parametrize('change',['business','intelligence','roster'])
def test_queue_drift_rolls_back_derivative(case,monkeypatch,change):
 setup(case);card(case);queue=case.s.queue;before=case.s.rows('SELECT * FROM task_card_references')
 def drift(*a,**kw):
  result=queue(*a,**kw)
  if a[3]=='task-reference:question':
   if change=='business':case.c['business']='OTHER'
   elif change=='intelligence':case.e.intelligence=None
   else:case.c['team_roster_verified']=False
  return result
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises(ValueError):question(case)
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='task-reference:question'") and before==case.s.rows('SELECT * FROM task_card_references')


def test_newer_accepted_turn_during_builder_blocks_final_queue(case,monkeypatch):
 setup(case);card(case);build=command.build_task_card
 def change(*a,**kw):
  result=build(*a,**kw)
  from goldway.answer_citations import begin_answer
  begin_answer(case.s,case.c,'newer',dict(sender='test_operator',channel='bot',source_id='telegram:1:99'),NOW)
  return result
 monkeypatch.setattr(command,'build_task_card',change)
 assert question(case)['text']==NOTICE

@pytest.mark.parametrize('text',['да','Давай','Подтверждаю задачу №2','Перенеси срок этой задачи','Напомни по этой задаче','Покажи задачу №2','Какой срок у задачи №2?','Расскажи о втором бренде'])
def test_no_confirmation_edits_schedule_explicit_number_interception(text):assert not is_task_reference(text)


def test_actual_telegram_reply_task_keeps_original_route(case):
 setup(case);card(case)
 class ExistingRoute(Exception):pass
 case.e._context=lambda *a,**kw:(_ for _ in ()).throw(ExistingRoute())
 with pytest.raises(ExistingRoute):question(case,task_id='A')
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='task-reference:question'")


def test_store_restart_keeps_prospective_metadata(case,tmp_path):
 # SQLite backup API uses no Python file/HTTP operation and copies only synthetic DB.
 from goldway.store import Store
 setup(case);card(case);path=str(tmp_path/'synthetic.sqlite');other=Store(path);case.s.db.backup(other.db);other.db.close()
 reopened=Store(path)
 from goldway.engine import Engine
 old=case.s;case.s=reopened;case.e=Engine(reopened,case.c,None,lambda:case.clock[0])
 case.e.model=SimpleNamespace(propose=lambda *a:pytest.fail('model after restart'))
 case.e._context=lambda *a,**kw:pytest.fail('context after restart')
 assert question(case)['task_id']=='B'
 reopened.db.close();case.s=old


def test_constructor_does_not_backfill_and_direct_invalid_boundaries(case):
 setup(case)
 assert not case.s.rows('SELECT * FROM task_card_references')
 assert not reference_request(dict(text='Покажи эту задачу',task_id='trusted-reply'))
 for value in [None,True,1,{},[]]:assert not is_task_reference(value)
 with pytest.raises(ReferenceUnavailable):command.handle_task_reference(case.e,'bad',dict(text='Покажи эту задачу'))


def test_optional_receipt_bounds_failure_preserves_normal_i15_card(case,monkeypatch):
 setup(case)
 def unavailable(*a,**kw):raise ValueError('synthetic metadata bound')
 monkeypatch.setattr(command,'_snapshot',unavailable)
 row=card(case)
 assert 'BETA' in row['text'] and not case.s.rows('SELECT * FROM task_card_references')
 assert case.s.execute('SELECT state FROM outbox WHERE id=?',(row['id'],)).fetchone()[0]=='sent'
 assert question(case)['text']==NOTICE


def test_i15_producer_identity_drift_after_protect_rolls_back(case,monkeypatch):
 setup(case);protect=case.e.intelligence.protect_outbox
 def drift(*a,**kw):
  result=protect(*a,**kw);case.e.intelligence=None;return result
 monkeypatch.setattr(case.e.intelligence,'protect_outbox',drift)
 with pytest.raises(ValueError):card(case,send=False)
 assert not case.s.rows('SELECT id FROM outbox') and not case.s.rows('SELECT * FROM task_card_references')
 assert not case.s.rows('SELECT id FROM events')
