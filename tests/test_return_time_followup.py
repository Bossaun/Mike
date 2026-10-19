"""Actual own progress→delivered host C07 prompt→explicit next time scheduling."""
from types import SimpleNamespace
from dataclasses import replace
import copy,json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.model import ScriptedModel,response_schema
from goldway.intelligence import reader_key
from goldway import scheduling_dialogue as dialogue
MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600
class Model(ScriptedModel):
 def __init__(self):super().__init__({});self.mode='update';self.contexts=[];self.hook=None;self.schema=False;self.envelopes=[]
 def propose(self,context):
  self.contexts.append(copy.deepcopy(context));task=next((t for t in context['tasks'] if t['id']=='task-alpha'),None);actions=[]
  if self.mode!='conversation' and task:
   actions=[dict(op='update',task_id=task['id'],status='in_progress',evidence_lines=[1])]
   if self.mode=='nextstep':actions[0]['next_step']='Synthetic new next step'
   if self.mode=='mixed':actions.append(dict(op='update',task_id='task-beta',status='in_progress',evidence_lines=[1]))
   if self.mode=='fieldmix':actions[0]['title']='Synthetic changed title'
   if self.mode=='completed':actions[0]['status']='completed'
   if self.mode=='wrongtask':actions[0]['task_id']='task-beta'
   if self.mode=='invalid':actions[0]['status']='invalid'
   if self.mode=='nochange':actions[0].pop('status')
  if self.schema:
   full=[]
   for action in actions:
    mask=['status']+(['next_step'] if 'next_step' in action else [])+(['title'] if 'title' in action else [])
    envelope=dict(op=action['op'],task_id=action['task_id'],title=action.get('title'),owner=None,deadline=None,next_step=action.get('next_step'),criterion=None,status=action.get('status'),evidence_lines=[1],owner_lines=[],deadline_lines=[],set_fields=mask)
    assert set(envelope)==set(response_schema(context)['properties']['actions']['items']['required'])
    full.append(envelope);self.envelopes.append(copy.deepcopy(envelope))
   actions=full
  self.result=dict(actions=actions,intent='task_action' if actions else 'conversation',summary='Synthetic progress',reply_text='MODEL-QUESTION-MUST-NOT-AUTHORIZE: когда вернуться?',dialogue_state=dict(topic='Synthetic',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
  if self.hook:self.hook()
  return super().propose(context)
@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';s=Store(str(path));clock=[NOW];c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]),scheduling=dict(enabled=True));model=Model();now=lambda:clock[0];e=Engine(s,c,model,now)
 for name in MEMBERS:
  uid=c['employees'][name]['telegram_user_id'];s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY',name,str(uid),str(uid),'conn',NOW))
 for n in ('alpha','beta'):
  s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',('GOLDWAY','source-'+n,'Synthetic source','local'))
  s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('GOLDWAY','task-'+n,n.upper(),'frost','2026-10-20','Synthetic next','criterion','confirmed','source-'+n,'Synthetic initial',NOW-10))
 class API:
  def __init__(self):self.calls=[]
  def call(self,method,**kw):
   self.calls.append((method,kw))
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';return dict(message_id=len(self.calls)+10)
 api=API();d=Dispatcher(s,c,api,now);value=SimpleNamespace(s=s,c=c,e=e,clock=clock,now=now,model=model,d=d,api=api,path=path)
 def reopen():
  value.s.db.close();value.s=Store(str(path));value.e=Engine(value.s,c,model,now);value.d=Dispatcher(value.s,c,api,now)
 value.reopen=reopen
 import builtins,socket,httpx,subprocess
 def fail(*a,**kw):pytest.fail('external network/model/provider/real file read')
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield value
 value.s.db.close()
def task(c,identifier='task-alpha'):return dict(c.s.execute('SELECT * FROM tasks WHERE id=?',(identifier,)).fetchone())
def prompts(c):return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:c07-prompt:%' ORDER BY id")
def checks(c):return c.s.rows('SELECT * FROM c07_checks ORDER BY id')
def turn(c,text,event='progress',*,sender='frost',channel='bot',task_id=None,send=False,at=None,process=True):
 uid=c.c['employees'][sender]['telegram_user_id'];source=f'telegram:{uid}:20' if channel=='bot' else f'business:conn:{uid}:20' if channel=='business' else 'telegram:group:20'
 c.e.reply(event,sender,text,task_id=task_id,channel=channel,source_id=source,message_at=c.clock[0] if at is None else at)
 if process:c.e.process(event)
 if send:c.d.flush()
 return c.s.rows('SELECT * FROM outbox ORDER BY id DESC LIMIT 1')[0] if c.s.rows('SELECT id FROM outbox') else None

def test_actual_employee_progress_fixed_host_prompt_delivered_then_existing_schedule_check_deadline_unchanged(case):
 before=task(case);row=turn(case,'Задача №1: работаю над проверкой','progress',send=True)
 assert row['text']==dialogue._RETURN_QUESTION and row['dedupe']=='reminder:c07-prompt:progress' and row['task_id']=='task-alpha' and row['recipient']=='frost'
 assert row['state']=='sent' and row['transport']=='bot' and row['destination_chat']=='2' and not checks(case)
 assert task(case)['status']=='in_progress' and task(case)['deadline']==before['deadline'] and 'MODEL-QUESTION' not in row['text']
 assert case.s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],)).fetchone() and case.s.execute("SELECT value FROM runtime WHERE key LIKE 'scheduling-prompt:v1:%'").fetchone()
 case.clock[0]+=1;case.model.mode='conversation';turn(case,'Через 30 минут','time',send=True)
 assert len(checks(case))==1 and checks(case)[0]['task_id']=='task-alpha' and checks(case)[0]['owner']=='frost' and checks(case)[0]['at']==case.clock[0]+1800 and task(case)['deadline']==before['deadline']

@pytest.mark.parametrize('channel',['bot','business'])
def test_delivered_prompt_reopen_then_explicit_next_time_existing_resolver(case,channel):
 turn(case,'Работаю над проверкой','progress',task_id='task-alpha',channel=channel,send=True);case.clock[0]+=1;case.reopen();case.model.mode='conversation';turn(case,'Через 30 минут','time',channel=channel)
 assert len(checks(case))==1 and checks(case)[0]['at']==case.clock[0]+1800 and checks(case)[0]['task_id']=='task-alpha'

@pytest.mark.parametrize('mode',['update','invalid','fieldmix','mixed'])
def test_current_future_check_preserved_even_rejected_or_mixed_model_actions(case,mode):
 saved=case.e.scheduler.schedule_check('task-alpha','frost',NOW+3600,'agreed',NOW-1);before=checks(case);case.model.mode=mode
 if mode=='invalid':
  with pytest.raises(ValueError,match='^Invalid status$'):turn(case,'Задача №1: работаю над проверкой')
  assert case.s.execute('SELECT state FROM events WHERE id=?',('progress',)).fetchone()[0]=='failed'
 else:turn(case,'Задача №1: работаю над проверкой')
 assert checks(case)==before and saved['at']==NOW+3600 and not prompts(case)


def test_real_progress_unchanged_status_supported_new_evidence_and_nextstep(case):
 case.s.execute("UPDATE tasks SET status='in_progress' WHERE id='task-alpha'");case.model.mode='nextstep';turn(case,'Задача №1: продолжаю проверку')
 assert len(prompts(case))==1 and task(case)['next_step']=='Synthetic new next step'

@pytest.mark.parametrize('change',['completed','proposed','blocked','wrongowner','admin','group','scheduler','noIntel','question','unsupported','unfocused','multireference','wrongtask','mixed','fieldmix','nostatus','noactions'])
def test_no_prompt_outside_narrow_validated_own_single_progress(case,change):
 text='Задача №1: работаю над проверкой';kw={}
 if change in ('completed','proposed','blocked'):case.s.execute('UPDATE tasks SET status=? WHERE id=?',(change,'task-alpha'))
 elif change=='wrongowner':case.s.execute("UPDATE tasks SET owner='alen' WHERE id='task-alpha'")
 elif change=='admin':kw['sender']='test_operator'
 elif change=='group':kw['channel']='group'
 elif change=='scheduler':case.e.scheduler=None
 elif change=='noIntel':case.e.intelligence=None
 elif change=='question':text='Задача №1: работаю над проверкой?'
 elif change=='unsupported':text='Задача №1: ещё проверка не закончена'
 elif change=='unfocused':text='Работаю над проверкой'
 elif change=='multireference':text='Задача №1 и задача №2: работаю над проверкой'
 elif change in ('wrongtask','mixed','fieldmix'):case.model.mode=change
 elif change=='nostatus':case.model.mode='nochange'
 else:case.model.mode='conversation'
 if change=='unfocused':
  with pytest.raises(ValueError,match='^Уточните номер задачи, которую нужно изменить: сейчас подходят несколько задач.$'):turn(case,text,**kw)
  assert case.s.execute('SELECT state FROM events WHERE id=?',('progress',)).fetchone()[0]=='failed'
 else:turn(case,text,**kw)
 assert not prompts(case)


def test_event_replay_process_and_reply_dedupe_no_duplicate_prompt(case):
 turn(case,'Задача №1: работаю над проверкой');before=prompts(case);case.e.process('progress');turn(case,'Задача №1: работаю над проверкой');assert prompts(case)==before

@pytest.mark.parametrize('change',['pending','failed','unknown','sending','missing_delivery','revision','source_deleted','acl_loss','wrongroute','stale','intervening'])
def test_actual_time_reply_requires_current_delivered_c07_anchor(case,change):
 row=turn(case,'Задача №1: работаю над проверкой',send=True);case.clock[0]+=1;case.model.mode='conversation'
 if change in ('pending','failed','unknown','sending'):case.s.execute('UPDATE outbox SET state=? WHERE id=?',(change,row['id']))
 elif change=='missing_delivery':case.s.execute('DELETE FROM deliveries WHERE outbox_id=?',(row['id'],))
 elif change=='revision':case.s.execute("UPDATE tasks SET revision=revision+1 WHERE id='task-alpha'")
 elif change=='source_deleted':case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-alpha'")
 elif change=='acl_loss':case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE business=? AND task_id=?',(json.dumps(['unreadable']),'GOLDWAY','task-alpha'))
 elif change=='wrongroute':case.s.execute("UPDATE outbox SET destination_chat='999' WHERE id=?",(row['id'],))
 elif change=='stale':case.clock[0]+=86401
 else:turn(case,'Спасибо','intervening',send=True);case.clock[0]+=1
 turn(case,'Через 30 минут','time');assert not checks(case)


def test_model_question_alone_has_no_host_anchor_or_schedule_authority(case):
 case.model.mode='conversation';turn(case,'Задача №1: ещё проверка не закончена',send=True);case.clock[0]+=1
 turn(case,'Через 30 минут','time');assert not checks(case) and not prompts(case)

@pytest.mark.parametrize('change',['revision','acl','source','sourcebinding','body','policy','configidentity','Intelidentity','queueerror'])
def test_queue_or_protection_race_rolls_back_task_and_return_prompt(case,monkeypatch,change):
 original=case.e.intelligence.protect_outbox;queue=case.s.queue;prior=task(case);oldintel=case.e.intelligence;oldcfg=case.e.c
 def protect(identifier,refs):
  original(identifier,refs);row=case.s.execute('SELECT dedupe FROM outbox WHERE id=?',(identifier,)).fetchone()
  if not row or not row[0].startswith('reminder:c07-prompt:'):return
  if change=='revision':case.s.execute("UPDATE tasks SET revision=revision+1 WHERE id='task-alpha'")
  elif change=='acl':case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE task_id=?',(json.dumps(['unreadable']),'task-alpha'))
  elif change=='source':case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-alpha'")
  elif change=='sourcebinding':case.s.execute("DELETE FROM task_sources WHERE task_id='task-alpha'")
  elif change=='body':case.s.execute('UPDATE outbox SET text=? WHERE id=?',('forged question',identifier))
  elif change=='policy':case.c['live_send']=False
  elif change=='configidentity':case.e.c=copy.deepcopy(case.c)
  elif change=='Intelidentity':case.e.intelligence=object()
 def enqueue(*a,**kw):
  if change=='queueerror' and a[3].startswith('reminder:c07-prompt:'):raise ValueError('synthetic queuefailure')
  return queue(*a,**kw)
 monkeypatch.setattr(oldintel,'protect_outbox',protect);monkeypatch.setattr(case.s,'queue',enqueue)
 try:
  with pytest.raises(ValueError,match='return_question_changed|synthetic queuefailure'):turn(case,'Задача №1: работаю над проверкой')
 finally:case.e.intelligence=oldintel;case.e.c=oldcfg
 assert not prompts(case) and task(case)['status']==prior['status'] and task(case)['revision']==prior['revision'] and not checks(case)


def test_pending_question_no_second_active_prompt_and_revoked_permission_no_prompt(case):
 turn(case,'Задача №1: работаю над проверкой');first=prompts(case);case.clock[0]+=1;turn(case,'Задача №1: продолжаю новую проверку','progress2')
 assert len([r for r in prompts(case) if r['state']=='pending'])<=1
 case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE task_id=?',(json.dumps(['unreadable']),'task-alpha'));case.clock[0]+=1
 turn(case,'Задача №1: работаю над проверкой','revoked')
 assert not any(r['dedupe']=='reminder:c07-prompt:revoked' for r in prompts(case))


@pytest.mark.parametrize('mode',['update','nextstep'])
def test_actual_response_schema_full_null_envelope_creates_delivered_return_anchor(case,mode):
 case.model.schema=True;case.model.mode=mode
 row=turn(case,'Задача №1: работаю над проверкой',send=True)
 assert row['dedupe']=='reminder:c07-prompt:progress' and row['text']==dialogue._RETURN_QUESTION and row['state']=='sent'
 envelope=case.model.envelopes[0]
 assert envelope['owner_lines']==envelope['deadline_lines']==[] and all(envelope[k] is None for k in ('title','owner','deadline','criterion'))
 case.clock[0]+=1;case.model.mode='conversation';turn(case,'Через 30 минут','time')
 assert len(checks(case))==1 and checks(case)[0]['at']==case.clock[0]+1800

@pytest.mark.parametrize('mode',['update','invalid','mixed','fieldmix'])
def test_schema_shaped_future_agreement_preserved_and_mixed_never_prompts(case,mode):
 case.model.schema=True;case.model.mode=mode;case.e.scheduler.schedule_check('task-alpha','frost',NOW+3600,'agreed',NOW-1);before=checks(case)
 if mode=='invalid':
  with pytest.raises(ValueError,match='^Invalid status$'):turn(case,'Задача №1: работаю над проверкой')
  assert case.s.execute('SELECT state FROM events WHERE id=?',('progress',)).fetchone()[0]=='failed'
 else:turn(case,'Задача №1: работаю над проверкой')
 assert checks(case)==before and not prompts(case) and case.model.envelopes


def test_full_schema_genuine_mixed_title_edit_has_no_return_prompt(case):
 case.model.schema=True;case.model.mode='fieldmix';turn(case,'Задача №1: работаю над проверкой')
 assert 'title' in case.model.envelopes[0]['set_fields'] and not prompts(case)
