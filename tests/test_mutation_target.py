"""New ordinary update-target boundaries; synthetic actual Engine/Dispatcher."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.model import ScriptedModel
from goldway.task_transition_history import append_transition

NOW=1792174600
A='task_a_0123456789';B='task_b_9876543210'
TEXT='С этим всё сделано: итоговый отчёт https://example.invalid/report'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']


def result(actions=(),text='Сообщение принято.',task_id=A,intent=None):
 return dict(actions=list(actions),summary='Synthetic result',reply_text=text,intent=intent or ('task_action' if actions else 'conversation'),
  dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=task_id),evidence_ids=[],knowledge_mode='not_needed')


def update(task_id=A,**fields):
 mask=list(fields)
 return dict(op='update',task_id=task_id,title=None,owner=None,deadline=None,next_step=None,criterion=None,status=None,
             evidence_lines=[1],owner_lines=[1] if fields.get('owner') else [],deadline_lines=[1] if fields.get('deadline') else [],set_fields=mask,**{})|fields


def make_case():
 s=Store(':memory:');clock=[NOW]
 c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:clock[0]);e.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',NOW))
 class API:
  def call(self,method,**kw):
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';return dict(message_id=11)
 d=Dispatcher(s,c,API(),lambda:clock[0])
 return SimpleNamespace(s=s,e=e,c=c,d=d,clock=clock)


@pytest.fixture
def case(monkeypatch):
 c=make_case()
 import builtins,socket,httpx
 def fail(*a,**kw):pytest.fail('external I/O')
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield c
 c.s.db.close()


def tasks(c,status_a='completed',status_b='completed',owner='test_operator'):
 with c.s.transaction():
  for task_id,title,status in [(A,'ALPHA',status_a),(B,'BETA',status_b)]:
   c.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',('GOLDWAY','source-'+task_id,'Synthetic original result','synthetic'))
   c.s.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
       VALUES(?,?,?,?,?,?,?,?,?,?,?)''',('GOLDWAY',task_id,title,owner,'2026-10-19','next','criterion',status,'source-'+task_id,'Original registered evidence',NOW))
   append_transition(c.s,'GOLDWAY',task_id,None,at=NOW,source='reply_bot',creation='new')


def prior_focus(c,channel='bot'):
 c.clock[0]+=1;c.e.model=ScriptedModel(result(text='Предыдущий ответ по ALPHA.',task_id=A))
 source='telegram:1:1' if channel=='bot' else 'business:conn:1:1'
 c.e.reply('old-focus','test_operator','Расскажи о задаче №1',source_id=source,channel=channel,message_at=c.clock[0]);c.e.process('old-focus');c.d.flush()


def card_b(c,channel='bot',send=True,event='card-b'):
 c.clock[0]+=1
 c.e.reply(event,'test_operator','Покажи задачу №2',source_id='telegram:1:2' if channel=='bot' else 'business:conn:1:2',channel=channel,message_at=c.clock[0])
 if send:c.d.flush()


def mutate(c,text=TEXT,action=None,event='mutation',channel='bot',task_id=None,callback=None):
 c.clock[0]+=1;seen=[]
 class Model:
  def propose(self,context):
   seen.append(dict(context))
   if callback:callback(context)
   return result([action or update(status='completed')])
 c.e.model=Model()
 c.e.reply(event,'test_operator',text,task_id=task_id,source_id='telegram:1:3' if channel=='bot' else 'business:conn:1:3',channel=channel,message_at=c.clock[0]);c.e.process(event)
 return seen


def row(c,tid=A):
 return dict(c.s.execute('SELECT * FROM tasks WHERE business=? AND id=?',('GOLDWAY',tid)).fetchone())


def counts(c):
 return {table:c.s.execute('SELECT COUNT(*) FROM '+table+' WHERE business=?',('GOLDWAY',)).fetchone()[0]
         for table in ('task_sources','task_status_history')}


def clarified(c,event='mutation'):
 from goldway.mutation_target import NOTICE
 assert dict(c.s.execute('SELECT state,error FROM events WHERE business=? AND id=?',('GOLDWAY',event)).fetchone())==dict(state='done',error=None)
 out=c.s.execute('SELECT text,task_id FROM outbox WHERE business=? AND dedupe=?',('GOLDWAY','mutation-target:'+event)).fetchone()
 assert out and out['text']==NOTICE and out['task_id'] is None
 assert 'https://' not in out['text']


@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('active',[False,True])
def test_actual_old_focus_cannot_overwrite_after_new_delivered_card(case,channel,active):
 tasks(case,status_a='confirmed' if active else 'completed');prior_focus(case,channel);card_b(case,channel)
 before=(row(case),row(case,B),counts(case));seen=mutate(case,channel=channel)
 assert len(seen)==1 and [t['id'] for t in seen[0]['tasks']]==[A]
 assert (row(case),row(case,B),counts(case))==before
 assert not case.s.execute('SELECT 1 FROM sources WHERE business=? AND id=?',('GOLDWAY','telegram:1:3' if channel=='bot' else 'business:conn:1:3')).fetchone()
 clarified(case)


def test_derivative_card_also_blocks_old_focus(case):
 tasks(case);prior_focus(case);card_b(case)
 case.clock[0]+=1;case.e.reply('derivative','test_operator','Кто отвечает по этой задаче?',source_id='telegram:1:7',channel='bot',message_at=case.clock[0]);case.d.flush()
 assert case.s.execute('SELECT 1 FROM outbox WHERE dedupe=?',('task-reference:derivative',)).fetchone()
 before=row(case);mutate(case);assert row(case)==before;clarified(case)


@pytest.mark.parametrize('reference',['number','title','trusted_reply'])
def test_explicit_completed_A_preserved_after_B(case,reference):
 tasks(case,status_a='confirmed' if reference=='title' else 'completed');prior_focus(case);card_b(case)
 text={'number':'По задаче №1 всё сделано: готов итоговый отчёт https://example.invalid/report',
       'title':'ALPHA: всё сделано, готов итоговый отчёт https://example.invalid/report','trusted_reply':TEXT}[reference]
 before=row(case);mutate(case,text=text,task_id=A if reference=='trusted_reply' else None)
 assert row(case)['revision']==before['revision']+1 and row(case)['evidence']==text
 assert not case.s.execute("SELECT 1 FROM outbox WHERE dedupe='mutation-target:mutation'").fetchone()


@pytest.mark.parametrize('previous',['none','same_card'])
def test_genuine_single_active_task_preserved(case,previous):
 tasks(case,status_a='confirmed')
 if previous=='same_card':
  case.clock[0]+=1;case.e.reply('card-a','test_operator','Покажи задачу №1',source_id='telegram:1:1',channel='bot',message_at=case.clock[0]);case.d.flush()
 before=row(case);mutate(case)
 assert row(case)['revision']==before['revision']+1 and row(case)['status']=='completed'


@pytest.mark.parametrize('change',[dict(status='in_progress'),dict(owner='frost'),dict(deadline='2026-10-25')])
def test_other_implicit_mutations_cannot_use_incompatible_card(case,change):
 tasks(case,status_a='confirmed');prior_focus(case);card_b(case)
 text='По этой задаче делаю итоговый отчёт; ответственный frost, новый срок 2026-10-25'
 before=(row(case),counts(case));mutate(case,text=text,action=update(**change));assert (row(case),counts(case))==before;clarified(case)


@pytest.mark.parametrize('condition',['pending','legacy','late','missing_time','changed_card','clarification'])
def test_unsupported_latest_card_never_authorizes_old_singleton(case,condition):
 tasks(case,status_a='confirmed');prior_focus(case);card_b(case,send=condition!='pending')
 if condition=='legacy':case.s.execute('DELETE FROM task_card_references')
 if condition=='changed_card':case.s.execute("UPDATE outbox SET text='different' WHERE dedupe='task-card:card-b'")
 if condition=='clarification':
  mutate(case);case.d.flush()
 before=row(case)
 if condition in ('late','missing_time'):
  case.clock[0]+=1;case.e.model=ScriptedModel(result([update(status='completed')]))
  case.e.reply('mutation','test_operator',TEXT,source_id='telegram:1:9',channel='bot',message_at=NOW if condition=='late' else None);case.e.process('mutation')
 else:mutate(case,event='mutation2' if condition=='clarification' else 'mutation')
 assert row(case)==before;clarified(case,'mutation2' if condition=='clarification' else 'mutation')


@pytest.mark.parametrize('drift',['remove_team','business','replace_intel','kind','revision','owner','source_deleted'])
def test_actual_model_callback_current_identity_revision_scope_fail_closed(case,drift):
 tasks(case,status_a='confirmed');before=row(case);before_counts=counts(case)
 def callback(context):
  if drift=='remove_team':case.c.pop('team_intake')
  elif drift=='business':case.c['business']='OTHER'
  elif drift=='replace_intel':case.e.intelligence=SimpleNamespace()
  elif drift=='kind':context['kind']='conversation'
  elif drift=='revision':case.s.execute('UPDATE tasks SET revision=revision+1 WHERE id=?',(A,))
  elif drift=='owner':case.s.execute("UPDATE tasks SET owner='frost' WHERE id=?",(A,))
  else:case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',('source-'+A,))
 mutate(case,callback=callback)
 after=row(case)
 assert after['evidence']==before['evidence'] and counts(case)==before_counts
 assert after['revision']==before['revision']+(drift=='revision')
 assert case.s.execute("SELECT state FROM events WHERE id='mutation'").fetchone()[0]=='done'
 assert not case.s.execute("SELECT 1 FROM outbox WHERE dedupe LIKE 'action:%'").fetchone()
 if drift not in ('remove_team','business','replace_intel'):clarified(case)


def test_no_retry_or_duplicate_notice(case):
 tasks(case);prior_focus(case);card_b(case);mutate(case)
 before=case.s.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
 class NoModel:
  def propose(self,*a):pytest.fail('done event replay called model')
 case.e.model=NoModel();case.e.process('mutation')
 assert case.s.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==before
 clarified(case)


def test_completed_read_only_query_not_intercepted(case):
 tasks(case);prior_focus(case);card_b(case)
 case.clock[0]+=1;case.e.model=ScriptedModel(result(text='ALPHA завершена; независимой проверки результата нет.',task_id=A))
 case.e.reply('readonly','test_operator','Какой результат у задачи №1?',source_id='telegram:1:4',channel='bot',message_at=case.clock[0]);case.e.process('readonly')
 assert case.s.execute("SELECT state FROM events WHERE id='readonly'").fetchone()[0]=='done'
 assert case.s.execute("SELECT 1 FROM outbox WHERE dedupe='clarify:readonly'").fetchone()


def test_large_unrelated_register_does_not_cap_plain_singleton_update(case):
 tasks(case,status_a='confirmed')
 for i in range(140):
  case.s.execute('INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
   ('GOLDWAY','unrelated-'+str(i),'Unrelated','test_operator','next','criterion','completed','source-'+B,'registered',NOW))
 before=row(case);mutate(case)
 assert row(case)['revision']==before['revision']+1


def test_guard_is_not_provider_visible(case):
 tasks(case,status_a='confirmed');seen=mutate(case)
 # Model's object is later reused by Engine; capture visibility at proposal time.
 assert '_mutation_target_guard' not in seen[0]


def test_staff_ambiguous_pre_model_progress_does_not_touch_last_reply(case):
 tasks(case,status_a='confirmed',owner='frost')
 case.s.execute('INSERT INTO chats(business,recipient,bot_chat,last_inbound) VALUES(?,?,?,?)',('GOLDWAY','frost','2',NOW))
 case.clock[0]+=1;case.e.reply('staff-card','frost','Покажи задачу №2',source_id='telegram:2:1',channel='bot',message_at=case.clock[0]);case.d.flush()
 before=(row(case),row(case,B),counts(case));case.clock[0]+=1
 case.e.model=ScriptedModel(result([update(status='completed')]))
 case.e.reply('mutation','frost','Готово: итоговый отчёт https://example.invalid/report',source_id='telegram:2:2',channel='bot',message_at=case.clock[0])
 assert (row(case),row(case,B),counts(case))==before
 case.e.process('mutation');assert (row(case),row(case,B),counts(case))==before;clarified(case)


def test_final_acl_race_after_actual_intelligence_validation_rolls_back(case):
 tasks(case,status_a='confirmed');before=row(case);before_counts=counts(case)
 validate=case.e.intelligence.validate
 def revoke_after_validation(*args):
  value=validate(*args)
  case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY',A,'["revoked-unavailable-document"]'))
  return value
 case.e.intelligence.validate=revoke_after_validation
 mutate(case)
 assert row(case)==before and counts(case)==before_counts
 clarified(case)


def test_whole_action_batch_rolls_back_before_links_history_or_reply(case):
 tasks(case,status_a='confirmed',status_b='confirmed')
 case.clock[0]+=1;case.e.reply('card-a','test_operator','Покажи задачу №1',source_id='telegram:1:8',channel='bot',message_at=case.clock[0]);case.d.flush()
 before=(row(case),row(case,B),counts(case));case.clock[0]+=1
 case.e.model=ScriptedModel(result([update(status='completed'),update(B,status='completed')]))
 case.e.reply('mutation','test_operator','ALPHA: всё сделано; итоговый отчёт https://example.invalid/report',
  source_id='telegram:1:5',channel='bot',message_at=case.clock[0]);case.e.process('mutation')
 assert (row(case),row(case,B),counts(case))==before
 assert not case.s.execute("SELECT 1 FROM sources WHERE id='telegram:1:5'").fetchone()
 assert not case.s.execute("SELECT 1 FROM outbox WHERE dedupe LIKE 'action:%'").fetchone()
 clarified(case)


def test_no_prior_delivery_is_not_a_new_permission_for_multiple_tasks(case):
 tasks(case,status_a='confirmed',status_b='confirmed');before=(row(case),row(case,B))
 # The original multi-candidate guard still refuses an unreferenced target.
 from goldway.engine import ClarificationRequired
 with pytest.raises(ClarificationRequired):mutate(case)
 assert (row(case),row(case,B))==before
 assert case.s.execute("SELECT state FROM events WHERE id='mutation'").fetchone()[0]=='failed'


def test_other_transport_card_is_not_an_implicit_target(case):
 tasks(case,status_a='confirmed');card_b(case,channel='business')
 before=row(case);mutate(case,channel='bot')
 assert row(case)['revision']==before['revision']+1


def test_changed_revision_inside_model_cannot_edit_even_explicit_task(case):
 tasks(case,status_a='confirmed');before=row(case)
 def bump(context):case.s.execute('UPDATE tasks SET revision=revision+1 WHERE id=?',(A,))
 mutate(case,text='Задача №1: всё сделано, итоговый отчёт https://example.invalid/report',callback=bump)
 assert row(case)['revision']==before['revision']+1 and row(case)['evidence']==before['evidence'];clarified(case)


def test_host_guard_is_absent_from_short_consent_route(case):
 from goldway.mutation_target import capture
 tasks(case,status_a='proposed')
 p=dict(text='Да',sender='test_operator',channel='bot')
 assert capture(case.e,'synthetic',p,dict(kind='operator_chat',tasks=[]),()) is None


@pytest.mark.parametrize('metadata',['source_id','deleted','acl'])
def test_bounded_invalid_current_metadata_returns_fixed_clarification(case,metadata):
 tasks(case,status_a='confirmed');before=row(case)
 validate=case.e.intelligence.validate
 def change(*args):
  value=validate(*args)
  if metadata=='source_id':case.s.execute('UPDATE tasks SET source_id=? WHERE id=?',('X'*20000,A))
  elif metadata=='deleted':case.s.execute('UPDATE sources SET deleted=? WHERE id=?',('X'*20000,'source-'+A))
  else:case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY',A,'X'*20000))
  return value
 case.e.intelligence.validate=change;mutate(case)
 assert row(case)['revision']==before['revision'] and row(case)['evidence']==before['evidence'];clarified(case)
