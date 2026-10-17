"""I29 real Engine and synthetic Dispatcher; no injected scheduling context/target."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600;TAIL='ESSENTIAL-FINAL-CONDITION'

@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]),scheduling=dict(enabled=True))
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
 c.clock=[NOW];c.e.now=lambda:c.clock[0];c.e.scheduler.now=lambda:c.clock[0];c.d.now=lambda:c.clock[0]
 for tid,title in [('task-alpha','ALPHA'),('task-beta','BETA')]:
  c.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',('GOLDWAY','source-'+tid,'synthetic task source','local'))
  c.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)',('GOLDWAY',tid,title,'test_operator','2026-10-20','next','criterion','confirmed','source-'+tid,NOW-10))
 class NoModel:
  def propose(self,*a,**kw):pytest.fail('scheduling called model')
 c.e.model=NoModel()


def turn(c,text,event,*,send=True,task_id=None,channel='bot',at=None,sender='test_operator'):
 uid=c.c['employees'][sender]['telegram_user_id']
 c.e.reply(event,sender,text,task_id=task_id,channel=channel,source_id=(f'telegram:{uid}:20' if channel=='bot' else f'business:conn:{uid}:20'),message_at=c.clock[0] if at is None else at)
 c.e.process(event)
 assert c.s.rows('SELECT state FROM events WHERE id=?',(event,))[0]['state']=='done'
 if send:c.d.flush()
 return c.s.rows('SELECT * FROM outbox ORDER BY id DESC LIMIT 1')[0]


def state(c):
 return (c.s.rows('SELECT * FROM c07_checks'),c.s.rows('SELECT * FROM c07_events'),c.s.rows('SELECT id,last_reply FROM tasks ORDER BY id'))


def old_a(c):
 setup(c)
 turn(c,'Задача №1: напомни через 1 час','old-A')
 assert c.e.scheduler.pending('task-alpha')
 c.clock[0]+=1


def test_actual_a_history_new_b_card_plain_reminder_never_mutates_old_a(case):
 old_a(case)
 turn(case,'Покажи задачу №2','new-B')
 before=state(case);case.clock[0]+=1
 row=turn(case,'Напомни через 30 минут','ambiguous')
 assert state(case)==before
 assert row['text']=='О какой задаче напомнить? Назовите её или ответьте на её карточку.'
 assert row['task_id'] is None


def prompt(c,*,channel='bot',send=True):
 setup(c)
 row=turn(c,'Задача №1: напомни','prompt',channel=channel,send=send)
 assert row['dedupe']=='reminder:c07-prompt:prompt' and row['task_id']=='task-alpha'
 return row


@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('text',['Через 30 минут','Напомни через 30 минут'])
def test_immediate_host_prompt_success_after_restart(case,channel,text):
 prompt(case,channel=channel);case.clock[0]+=1
 case.e=Engine(case.s,case.c,case.e.model,lambda:case.clock[0])
 ack=turn(case,text,'answer',channel=channel)
 assert case.e.scheduler.pending('task-alpha')['at']==case.clock[0]+1800
 assert ack['task_id']=='task-alpha' and 'Сохранено:' in ack['text']
 assert not case.e.scheduler.pending('task-beta')


@pytest.mark.parametrize('kind',['help','unsupported','card_pending','card_sent','unavailable'])
def test_newer_accepted_turn_never_revives_old_focus(case,kind):
 old_a(case)
 if kind=='help':turn(case,'Что умеешь?','new')
 elif kind=='card_pending':turn(case,'Покажи задачу №2','new',send=False)
 elif kind=='card_sent':turn(case,'Покажи задачу №2','new')
 elif kind=='unavailable':turn(case,'Покажи задачу №999','new')
 else:case.e.reply('new','test_operator','Неподдержанная команда',source_id='telegram:1:21',channel='bot',message_at=case.clock[0])
 before=state(case);case.clock[0]+=1
 row=turn(case,'Напомни через 30 минут','answer')
 assert state(case)==before and row['task_id'] is None and 'О какой задаче' in row['text']


@pytest.mark.parametrize('change',['pending','unknown','sending','failed','legacy','missing_delivery','duplicate_delivery',
 'revision','revoked_source','wrong_chat','wrong_connection','wrong_transport','missing_time','old_time','expired','window','forged_model','forged_card'])
def test_invalid_prompt_proof_no_scheduling_mutation(case,change):
 row=prompt(case,send=change not in ('pending','unknown','sending','failed'))
 if change in ('pending','unknown','sending','failed'):case.s.execute('UPDATE outbox SET state=? WHERE id=?',(change,row['id']))
 elif change=='legacy':case.s.execute("DELETE FROM runtime WHERE key LIKE 'scheduling-prompt:%'")
 elif change=='missing_delivery':case.s.execute('DELETE FROM deliveries WHERE outbox_id=?',(row['id'],))
 elif change=='duplicate_delivery':case.s.execute("INSERT INTO deliveries(outbox_id,at,state) VALUES(?,?,'sent')",(row['id'],case.clock[0]))
 elif change=='revision':case.s.execute('UPDATE tasks SET revision=revision+1 WHERE id=?',('task-alpha',))
 elif change=='revoked_source':case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-task-alpha'")
 elif change=='role':case.c['admins']=[];case.s.execute("UPDATE tasks SET owner='frost' WHERE id='task-alpha'")
 elif change=='wrong_chat':case.s.execute("UPDATE outbox SET destination_chat='999' WHERE id=?",(row['id'],))
 elif change=='wrong_connection':case.s.execute("UPDATE outbox SET destination_connection='other' WHERE id=?",(row['id'],))
 elif change=='wrong_transport':case.s.execute("UPDATE outbox SET transport='business' WHERE id=?",(row['id'],))
 elif change=='expired':case.clock[0]+=86401
 elif change=='window':
  for i in range(129):case.s.queue('GOLDWAY','frost','synthetic unrelated',f'noise:{i}',case.clock[0])
 elif change=='forged_model':case.s.execute("UPDATE outbox SET dedupe='clarify:prompt' WHERE id=?",(row['id'],))
 elif change=='forged_card':case.s.execute("UPDATE outbox SET dedupe='task-card:prompt' WHERE id=?",(row['id'],))
 case.clock[0]+=1;before=state(case)
 if change=='missing_time':
  case.e.reply('answer','test_operator','Напомни через 30 минут',source_id='telegram:1:22',channel='bot')
  case.e.process('answer');ack=case.s.rows('SELECT * FROM outbox ORDER BY id DESC LIMIT 1')[0]
 else:ack=turn(case,'Напомни через 30 минут','answer',at=case.clock[0]-10 if change=='old_time' else None,send=False)
 assert state(case)==before
 assert ack['task_id'] is None and 'О какой задаче' in ack['text']


@pytest.mark.parametrize('target',['number','title','reply'])
def test_explicit_current_target_after_intervening_help(case,target):
 old_a(case);turn(case,'Что умеешь?','help');case.clock[0]+=1
 texts={'number':'Задача №2: напомни через 30 минут','title':'Напомни BETA через 30 минут','reply':'Напомни через 30 минут'}
 turn(case,texts[target],'explicit',task_id='task-beta' if target=='reply' else None)
 assert case.e.scheduler.pending('task-beta')['at']==case.clock[0]+1800
 assert case.e.scheduler.pending('task-alpha')['at']==NOW+3600


def test_singleton_never_implicit_authority(case):
 old_a(case);case.s.execute("UPDATE tasks SET status='completed' WHERE id='task-beta'")
 before=state(case);row=turn(case,'Напомни через 30 минут','answer')
 assert state(case)==before and row['task_id'] is None


def test_late_message_must_follow_actual_delivery_not_queue_created(case):
 setup(case);turn(case,'Задача №1: напомни','prompt',send=False)
 original=case.clock[0];case.clock[0]+=10;case.d.flush();case.clock[0]+=1
 before=state(case);row=turn(case,'Напомни через 30 минут','late',at=original)
 assert state(case)==before and row['task_id'] is None


def test_same_second_delivery_and_message_allowed(case):
 row=prompt(case);case.s.execute('UPDATE deliveries SET at=? WHERE outbox_id=?',(NOW+0.8,row['id']))
 case.clock[0]=NOW+0.9;turn(case,'Напомни через 30 минут','same-second',at=NOW)
 assert case.e.scheduler.pending('task-alpha')['at']==NOW+1800


def test_duplicate_event_restart_no_extra_check_or_queue(case):
 prompt(case);case.clock[0]+=1;turn(case,'Напомни через 30 минут','answer')
 before=state(case);out=case.s.rows('SELECT id FROM outbox')
 case.e=Engine(case.s,case.c,case.e.model,lambda:case.clock[0])
 assert case.e.reply('answer','test_operator','Напомни через 30 минут',source_id='telegram:1:20',channel='bot',message_at=case.clock[0]) is False
 case.e.process('answer')
 assert state(case)==before and case.s.rows('SELECT id FROM outbox')==out


def test_receipt_only_metadata(case):
 prompt(case);rows=case.s.rows("SELECT value FROM runtime WHERE key LIKE 'scheduling-prompt:%'")
 assert len(rows)==1
 assert set(json.loads(rows[0]['value']))=={'version','kind','event_key','producer_key','task_id','revision','outbox_id'}
 assert 'ALPHA' not in rows[0]['value'] and 'synthetic task source' not in rows[0]['value']


def test_cross_transport_no_prompt_authority(case):
 prompt(case,channel='bot');before=state(case);case.clock[0]+=1
 row=turn(case,'Напомни через 30 минут','business',channel='business')
 assert state(case)==before and row['task_id'] is None


def test_plain_time_without_host_prompt_keeps_normal_model_path(case):
 setup(case);calls=[]
 class Model:
  def propose(self,context):
   calls.append(context)
   return dict(intent='conversation',actions=[],summary='Уточните задачу и срок.',reply_text='Уточните задачу и срок.',knowledge_mode='not_needed',evidence_ids=[],dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None))
 case.e.model=Model()
 row=turn(case,'Через 30 минут','ordinary-time')
 assert len(calls)==1 and not case.s.rows('SELECT * FROM c07_checks')
 assert row['text']=='Уточните задачу и срок.' and not row['dedupe'].startswith('c07-ack:')


def test_ordinary_chat_does_not_run_new_metadata_resolver(case,monkeypatch):
 setup(case)
 import goldway.scheduling_anchor as anchor
 def fail(*a,**kw):pytest.fail('ordinary chat ran scheduling metadata reads')
 monkeypatch.setattr(anchor,'resolve',fail)
 class Model:
  def propose(self,context):
   return dict(intent='conversation',actions=[],summary='Синтетический ответ.',reply_text='Синтетический ответ.',knowledge_mode='not_needed',evidence_ids=[],dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None))
 case.e.model=Model();row=turn(case,'Обсудим рабочую идею','chat')
 assert row['text']=='Синтетический ответ.' and not case.s.rows('SELECT * FROM c07_checks')


def test_explicit_target_does_not_consult_unrelated_old_anchor(case,monkeypatch):
 prompt(case)
 import goldway.scheduling_anchor as anchor
 def fail(*a,**kw):pytest.fail('explicit target consulted old anchor')
 monkeypatch.setattr(anchor,'resolve',fail)
 turn(case,'Задача №2: напомни через 30 минут','explicit-B')
 assert case.e.scheduler.pending('task-beta') and not case.e.scheduler.pending('task-alpha')


def test_revoked_source_not_done_never_records_progress_or_creates_prompt(case):
 prompt(case);case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-task-alpha'")
 calls=[]
 class Model:
  def propose(self,context):
   calls.append(True)
   return dict(intent='conversation',actions=[],summary='Уточните задачу.',reply_text='Уточните задачу.',knowledge_mode='not_needed',evidence_ids=[],dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None))
 case.e.model=Model();before=state(case);out=case.s.rows('SELECT id FROM outbox')
 row=turn(case,'Нет','not-done',send=False)
 assert state(case)==before and len(calls)==1
 assert not row['dedupe'].startswith('reminder:c07-prompt:')


@pytest.mark.parametrize('corruption',['oversized','invalid_json','extra_key','wrong_revision','wrong_event'])
def test_malformed_or_oversized_receipt_never_falls_back(case,corruption):
 prompt(case)
 row=case.s.rows("SELECT key,value FROM runtime WHERE key LIKE 'scheduling-prompt:%'")[0]
 doc=json.loads(row['value'])
 if corruption=='oversized':value='x'*2049
 elif corruption=='invalid_json':value='{' 
 else:
  if corruption=='extra_key':doc['unexpected']='A'
  elif corruption=='wrong_revision':doc['revision']=False
  elif corruption=='wrong_event':doc['event_key']='0'*64
  value=json.dumps(doc)
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,row['key']))
 before=state(case);ack=turn(case,'Напомни через 30 минут','answer')
 assert state(case)==before and ack['task_id'] is None


def test_prompt_metadata_failure_rolls_back_original_queue(case,monkeypatch):
 setup(case)
 import goldway.scheduling_anchor as anchor
 def fail(*a,**kw):raise ValueError('synthetic receipt failure')
 monkeypatch.setattr(anchor,'capture',fail)
 case.e.reply('prompt','test_operator','Задача №1: напомни',source_id='telegram:1:20',channel='bot',message_at=case.clock[0])
 with pytest.raises(ValueError):case.e.process('prompt')
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe='reminder:c07-prompt:prompt'")
 assert not case.s.rows("SELECT value FROM runtime WHERE key LIKE 'scheduling-prompt:%'")
 assert not case.s.rows('SELECT * FROM c07_checks')
 assert case.s.rows("SELECT state FROM events WHERE id='prompt'")[0]['state']=='failed'


def due_prompt(c,channel='bot'):
 setup(c)
 if channel=='bot':c.s.execute("UPDATE chats SET business_chat=NULL,connection=NULL")
 turn(c,'Задача №1: напомни через 1 минуту','start')
 c.clock[0]+=60
 effects=c.e.scheduler.tick();assert effects['checks']
 c.d.flush()
 row=c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:c07:%'")[0]
 assert row['state']=='sent' and row['task_id']=='task-alpha'
 return row


@pytest.mark.parametrize('channel',['bot','business'])
def test_real_due_check_sent_result_not_done_fresh_time_prompt_loop(case,channel):
 due_prompt(case,channel);case.clock[0]+=1
 row=turn(case,'Нет','not-done',channel=channel)
 assert row['dedupe']=='reminder:c07-prompt:not-done' and 'Когда сможете закончить' in row['text']
 case.clock[0]+=1;row=turn(case,'Через 30 минут','time',channel=channel)
 assert row['task_id']=='task-alpha' and case.e.scheduler.pending('task-alpha')['at']==case.clock[0]+1800


@pytest.mark.parametrize('change',['revision','cancelled_check','wrong_task','missing_receipt','new_help','pending_outbound'])
def test_due_prompt_needs_current_host_check_and_no_new_barrier(case,change):
 row=due_prompt(case)
 if change=='revision':case.s.execute("UPDATE tasks SET revision=revision+1 WHERE id='task-alpha'")
 elif change=='cancelled_check':case.s.execute("UPDATE c07_checks SET state='cancelled'")
 elif change=='wrong_task':case.s.execute("UPDATE c07_checks SET task_id='task-beta'")
 elif change=='missing_receipt':case.s.execute("DELETE FROM runtime WHERE key LIKE 'scheduling-prompt:%'")
 elif change=='new_help':turn(case,'Что умеешь?','help')
 elif change=='pending_outbound':case.s.queue('GOLDWAY','test_operator','service','service',case.clock[0],preferred_transport='bot')
 before=state(case);case.clock[0]+=1
 ack=turn(case,'Напомни через 30 минут','answer',send=False)
 assert state(case)==before and ack['task_id'] is None


def test_due_prompt_revoked_source_not_done_no_progress_or_generic_cancel(case):
 due_prompt(case);case.clock[0]+=1
 case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-task-alpha'")
 case.s.queue('GOLDWAY','test_operator','generic','reminder:generic',case.clock[0],'task-alpha')
 class Model:
  def propose(self,context):
   return dict(intent='conversation',actions=[],summary='Уточните задачу.',reply_text='Уточните задачу.',knowledge_mode='not_needed',evidence_ids=[],dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None))
 case.e.model=Model();before=state(case)
 turn(case,'Нет','not-done',send=False)
 assert state(case)==before
 assert case.s.rows("SELECT state FROM outbox WHERE dedupe='reminder:generic'")==[dict(state='pending')]
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='reminder:c07-prompt:not-done'")


@pytest.mark.parametrize('request_text',['Отмени напоминание','Напоминай каждый день в 09:00','Сделаю через 30 минут'])
def test_other_implicit_scheduling_requests_never_use_stale_focus(case,request_text):
 old_a(case);turn(case,'Покажи задачу №2','card-B');case.clock[0]+=1
 before=state(case);series=case.s.rows('SELECT * FROM c07_series')
 row=turn(case,request_text,'ambiguous')
 assert state(case)==before and case.s.rows('SELECT * FROM c07_series')==series
 assert row['task_id'] is None and 'О какой задаче' in row['text']


def test_due_capture_failure_atomic_with_queue_check_state_and_last_reminder(case,monkeypatch):
 setup(case);turn(case,'Задача №1: напомни через 1 минуту','start');case.clock[0]+=60
 before=state(case);last=case.s.rows('SELECT id,last_reminder FROM tasks');out=case.s.rows('SELECT id FROM outbox')
 import goldway.scheduling_anchor as anchor
 def fail(*a,**kw):raise ValueError('synthetic producer failure')
 monkeypatch.setattr(anchor,'capture_due',fail)
 with pytest.raises(ValueError):case.e.scheduler.tick()
 assert state(case)==before and case.s.rows('SELECT id,last_reminder FROM tasks')==last
 assert case.s.rows('SELECT id FROM outbox')==out


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_telegram_reply_baretime_to_fresh_delivered_prompt(case,channel):
 from goldway.telegram import Updates
 row=prompt(case,channel=channel);case.clock[0]+=1
 case.c['bot_user_id']=900;case.c['bot_user_id_verified']=True
 parent=dict(message_id=int(row['message_id']),date=int(row['created']),
  chat=dict(id=1,type='private'),text=row['text'],from_=None)
 parent.pop('from_')
 parent['from']=dict(id=900,is_bot=True)
 message=dict(message_id=91,date=int(case.clock[0]),chat=dict(id=1,type='private'),
  text='Через 30 минут',reply_to_message=parent)
 message['from']=dict(id=1,is_bot=False)
 if channel=='business':
  message['business_connection_id']='conn';parent['business_connection_id']='conn'
  parent['from']=dict(id=99,is_bot=False);parent['sender_business_bot']=dict(id=900,is_bot=True)
 Updates(case.e,case.d.api,900).handle(dict(update_id=91,**{('business_message' if channel=='business' else 'message'):message}))
 payload=json.loads(case.s.rows("SELECT payload FROM events WHERE id='update:91'")[0]['payload'])
 assert payload['task_id']=='task-alpha'
 case.e.process('update:91')
 assert case.e.scheduler.pending('task-alpha')['at']==case.clock[0]+1800
 assert not case.e.scheduler.pending('task-beta')


def test_baretime_reply_without_matching_host_prompt_keeps_normal_path(case):
 prompt(case);calls=[]
 class Model:
  def propose(self,context):
   calls.append(True)
   return dict(intent='conversation',actions=[],summary='Уточните срок задачи.',reply_text='Уточните срок задачи.',knowledge_mode='not_needed',evidence_ids=[],dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None))
 case.e.model=Model();before=state(case)
 row=turn(case,'Через 30 минут','other-task',task_id='task-beta')
 assert state(case)[:2]==before[:2] and len(calls)==1 and row['text']=='Уточните срок задачи.'
 assert not case.e.scheduler.pending('task-beta')


def test_oversized_unused_route_only_omits_optional_due_proof(case):
 setup(case);case.s.execute("UPDATE chats SET business_chat=NULL,connection=?",('x'*513,))
 turn(case,'Задача №1: напомни через 1 минуту','start');case.clock[0]+=60
 effects=case.e.scheduler.tick();assert effects['checks']
 row=case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:c07:%'")[0]
 assert row['state']=='pending'
 assert not case.s.rows("SELECT value FROM runtime WHERE key LIKE 'scheduling-prompt:%'")
 case.d.flush()
 assert case.s.rows('SELECT state,transport FROM outbox WHERE id=?',(row['id'],))==[dict(state='sent',transport='bot')]
 before=state(case);case.clock[0]+=1
 ack=turn(case,'Напомни через 30 минут','no-proof',send=False)
 assert state(case)==before and ack['task_id'] is None


def test_real_due_receipt_write_failure_still_rolls_back_scheduler_transaction(case):
 import sqlite3
 setup(case);turn(case,'Задача №1: напомни через 1 минуту','start');case.clock[0]+=60
 before=state(case);last=case.s.rows('SELECT id,last_reminder FROM tasks');out=case.s.rows('SELECT id FROM outbox')
 case.s.execute("CREATE TRIGGER synthetic_receipt_write_failure BEFORE INSERT ON runtime WHEN NEW.key LIKE 'scheduling-prompt:%' BEGIN SELECT RAISE(ABORT,'synthetic write failure'); END")
 with pytest.raises(sqlite3.IntegrityError):case.e.scheduler.tick()
 assert state(case)==before and case.s.rows('SELECT id,last_reminder FROM tasks')==last
 assert case.s.rows('SELECT id FROM outbox')==out
