"""I33 real Scheduler generation and actual Engine/Dispatcher readonly view."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher,Updates
from goldway.intelligence import delivery_permitted
from goldway.work_context import work_id
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


import goldway.recurring_history_command as command
from goldway.reminder_times import resolve_recurrence
from datetime import datetime
from zoneinfo import ZoneInfo


def setup(c,*,generate=True):
 c.clock=[NOW];c.e.now=lambda:c.clock[0];c.e.scheduler.now=lambda:c.clock[0];c.d.now=lambda:c.clock[0]
 c.s.execute("INSERT INTO sources(business,id,body,link) VALUES('GOLDWAY','origin','SYNTHETIC SOURCE NEVER READ','local')")
 c.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)',('GOLDWAY','template','Регулярный список','frost','2026-10-20','next','criterion','confirmed','origin',NOW-10))
 c.s.execute("INSERT INTO chats(business,recipient,bot_chat) VALUES('GOLDWAY','frost','2')")
 rule=resolve_recurrence('Каждый день в 09:00',datetime.fromtimestamp(NOW,ZoneInfo('Europe/Moscow')))
 series=c.e.scheduler.register_series('template','test_operator',rule,'registered',NOW)
 c.series=series['id'];c.instance=None
 if generate:
  c.clock[0]=series['next_at'];effects=c.e.scheduler.tick()
  c.instance=effects['occurrences'][0]['task_id'];c.d.flush()
 def fail(*a,**kw):pytest.fail('readonly history called model/context/refresh/search/scheduler')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail;c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail


def history(c,*,event='history',number=1,task_id=None,channel='bot',sender='test_operator',text=None):
 uid=c.c['employees'][sender]['telegram_user_id']
 if text is None:text='Покажи историю регулярной задачи'+(' №'+str(number) if number is not None else '')
 c.e.reply(event,sender,text,task_id=task_id,channel=channel,source_id=(f'telegram:{uid}:20' if channel=='bot' else f'business:conn:{uid}:20'),message_at=c.clock[0])
 rows=c.s.rows('SELECT * FROM outbox WHERE dedupe=?',('recurring-history:'+event,))
 return rows[0] if rows else None


def state(c):
 return (c.s.rows('SELECT * FROM c07_series'),c.s.rows('SELECT * FROM c07_occurrences'),c.s.rows('SELECT * FROM c07_checks'),c.s.rows('SELECT * FROM c07_events'),c.s.rows('SELECT * FROM tasks'))


@pytest.mark.parametrize('channel',['bot','business'])
def test_real_generation_actual_history_dispatch_and_restart_is_readonly(case,channel):
 setup(case);before=state(case)
 case.e=Engine(case.s,case.c,case.e.model,lambda:case.clock[0]);case.e._context=lambda *a,**kw:pytest.fail('history reached context')
 row=history(case,channel=channel)
 assert row and 'регулярного поручения №1' in row['text'] and 'Задача №2' in row['text']
 assert 'Плановый период:' in row['text'] and 'запись экземпляра создана:' in row['text']
 assert 'не является временем выполнения' in row['text'] and 'не обещание запуска или доставки' in row['text']
 assert row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode'] is None
 assert state(case)==before and not case.s.rows('SELECT * FROM usage')
 case.d.flush();assert case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))==[dict(state='sent')]
 assert case.s.rows('SELECT phase FROM answer_citation_turns')==[dict(phase='pending')]


def test_explicit_occurrence_number_resolves_saved_parent(case):
 setup(case);row=history(case,number=2)
 assert 'регулярного поручения №1' in row['text'] and 'Задача №2' in row['text']


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_updates_trusted_occurrence_reply(case,channel):
 setup(case)
 # Send actual host I15 instance card in requesting scope first.
 case.c['bot_user_id']=900;case.c['bot_user_id_verified']=True
 case.e.reply('card','test_operator','Покажи задачу №2',channel=channel,source_id=('telegram:1:21' if channel=='bot' else 'business:conn:1:21'),message_at=case.clock[0])
 case.d.flush();card=case.s.rows("SELECT * FROM outbox WHERE dedupe='task-card:card'")[0]
 parent=dict(message_id=int(card['message_id']),date=int(card['created']),chat=dict(id=1,type='private'),text=card['text'])
 parent['from']=dict(id=900,is_bot=True)
 message=dict(message_id=92,date=int(case.clock[0]),chat=dict(id=1,type='private'),text='Покажи историю регулярной задачи',reply_to_message=parent)
 message['from']=dict(id=1,is_bot=False)
 if channel=='business':
  message['business_connection_id']='conn';parent['business_connection_id']='conn';parent['from']=dict(id=99,is_bot=False);parent['sender_business_bot']=dict(id=900,is_bot=True)
 Updates(case.e,case.d.api,900).handle(dict(update_id=92,**{('business_message' if channel=='business' else 'message'):message}))
 row=case.s.rows("SELECT * FROM outbox WHERE dedupe='recurring-history:update:92'")[0]
 assert 'регулярного поручения №1' in row['text'] and 'Задача №2' in row['text']


@pytest.mark.parametrize('text',['Покажи историю задачи №1','Покажи историю регулярной задачи №1 завтра','История регулярной задачи №1',
 'Не покажи историю регулярной задачи №1','Покажи историю регулярной задачи №1 и №2','Какая история регулярной задачи №1?'])
def test_no_substring_or_other_routes(text):assert not command.is_recurring_history_command(text)


def test_no_implicit_latest_or_memory_selection(case):
 setup(case);assert history(case,number=None)['text']==command.NOTICE


def test_no_saved_series_distinct_from_unavailable(case):
 setup(case);case.s.execute('DELETE FROM c07_occurrences');case.s.execute('DELETE FROM c07_series')
 row=history(case);assert 'нет сохранённой связи' in row['text'] and row['text']!=command.NOTICE


def test_registered_series_no_instances_is_honest_empty(case):
 setup(case,generate=False);row=history(case)
 assert 'Нет доступных зарегистрированных экземпляров' in row['text'] and 'Сохранённая серия: активна' in row['text']


def test_paused_saved_next_time_not_restart_or_delivery_promise(case):
 setup(case);case.e.scheduler.pause_series(case.series,'test_operator','pause')
 before=state(case);row=history(case)
 assert 'Сохранённая серия: приостановлена' in row['text'] and state(case)==before


def test_replacement_template_ambiguous_but_explicit_old_instance_is_exact(case):
 setup(case);rule=resolve_recurrence('Каждую пятницу в 10:00',datetime.fromtimestamp(case.clock[0],ZoneInfo('Europe/Moscow')))
 case.e.scheduler.register_series('template','test_operator',rule,'replacement',case.clock[0])
 before=state(case)
 assert history(case)['text']==command.AMBIGUOUS
 row=history(case,event='old-instance',number=2)
 assert 'приостановлена' in row['text'] and 'Задача №2' in row['text'] and state(case)==before


def append_instance(c,i,*,status='confirmed',source='origin'):
 tid='manual-instance-'+str(i);at=NOW-i
 c.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)',('GOLDWAY',tid,'Synthetic instance','frost','2026-10-20','next','criterion',status,source,at))
 c.s.execute('INSERT INTO c07_occurrences VALUES(?,?,?,?,?,?)',('GOLDWAY',c.series,'manual-slot-'+str(i),at,tid,at))
 return tid


def test_completed_old_instance_never_completes_later_or_template(case):
 setup(case);old=append_instance(case,1,status='completed')
 row=history(case)
 assert row['text'].count('Выполнено')==1 and row['text'].count('Поручение')==1
 assert case.s.rows("SELECT status FROM tasks WHERE id='template'")==[dict(status='confirmed')]
 assert 'периода и создания' in row['text']


def test_limit5_all_permitted_omitted_refs_protected(case):
 setup(case)
 tids=[append_instance(case,i) for i in range(7)]
 row=history(case)
 assert row['text'].count('• Задача №')==5 and 'не показаны: 3' in row['text']
 refs=set(json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids']))
 assert {work_id('GOLDWAY',t) for t in ['template',case.instance,*tids]}<=refs


@pytest.mark.parametrize('change',['source','dependency_source','document'])
def test_denied_instance_no_number_or_count_influence(case,change):
 setup(case);tid=append_instance(case,1)
 if change=='source':case.s.execute("INSERT INTO sources(business,id,body,link,deleted) VALUES('GOLDWAY','dead','PRIVATE','local',1)");case.s.execute('UPDATE tasks SET source_id=? WHERE id=?',('dead',tid))
 elif change=='dependency_source':case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY',tid,json.dumps([work_id('GOLDWAY','missing-dependency')])))
 else:case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY',tid,json.dumps(['knowledge:missing'])))
 number=case.s.rows('SELECT public_number FROM tasks WHERE id=?',(tid,))[0]['public_number']
 row=history(case)
 assert 'Задача №'+str(number) not in row['text'] and row['text'].count('• Задача №')==1


@pytest.mark.parametrize('sender',['frost','alen'])
def test_no_staff_history_route_or_new_rights(case,sender):
 setup(case);before=state(case);assert history(case,sender=sender) is None and state(case)==before


@pytest.mark.parametrize('change',['selected_source','missing_number','another_business','missing_tables'])
def test_fixed_unavailable_no_target_content(case,change):
 setup(case);number=1
 if change=='selected_source':case.s.execute("UPDATE sources SET deleted=1 WHERE id='origin'")
 elif change=='missing_number':number=999
 elif change=='another_business':case.s.execute("UPDATE c07_series SET business='OTHER'")
 elif change=='missing_tables':case.s.execute('DROP TABLE c07_occurrences')
 row=history(case,number=number)
 assert row['text']==command.NOTICE or (change=='another_business' and 'нет сохранённой связи' in row['text'])


@pytest.mark.parametrize('field,value',[('at',1e100),('created',NOW+999999),('task_id',None),('occurrence_key','x'*257)])
def test_bad_occurrence_metadata_fixed_unavailable(case,field,value):
 setup(case);case.s.execute('UPDATE c07_occurrences SET '+field+'=?',(value,))
 assert history(case)['text']==command.NOTICE


@pytest.mark.parametrize('field,value',[('state','invented'),('next_at',1e100),('owner','unknown'),('id','x'*161)])
def test_bad_series_metadata_fixed_unavailable(case,field,value):
 setup(case);case.s.execute('UPDATE c07_series SET '+field+'=?',(value,))
 assert history(case)['text']==command.NOTICE


def test_occurrence_register_cap_before_partial_output(case):
 setup(case)
 for i in range(64):append_instance(case,i)
 assert history(case)['text']==command.NOTICE


def test_duplicate_request_and_process_no_extra_output_or_mutation(case):
 setup(case);row=history(case);before=state(case)
 assert case.e.reply('history','test_operator','Покажи историю регулярной задачи №1',channel='bot',source_id='telegram:1:20',message_at=case.clock[0]) is False
 case.e.process('history')
 assert len(case.s.rows("SELECT id FROM outbox WHERE dedupe='recurring-history:history'"))==1 and state(case)==before


@pytest.mark.parametrize('phase',['queue','protect'])
def test_captured_intelligence_drift_rolls_back(case,monkeypatch,phase):
 setup(case)
 obj=case.s if phase=='queue' else case.e.intelligence;name='queue' if phase=='queue' else 'protect_outbox';real=getattr(obj,name)
 before=case.s.rows('SELECT id FROM outbox')
 def drift(*a,**kw):
  value=real(*a,**kw);case.e.intelligence=None;return value
 monkeypatch.setattr(obj,name,drift)
 with pytest.raises(ValueError):history(case)
 assert case.s.rows('SELECT id FROM outbox')==before and not case.s.rows("SELECT id FROM events WHERE id='history'")


def test_fresh_final_instance_change_fails_fixed_notice(case,monkeypatch):
 setup(case);real=command._render
 def mutate(*a,**kw):
  text=real(*a,**kw);case.s.execute('UPDATE tasks SET status=? WHERE id=?',('completed',case.instance));return text
 monkeypatch.setattr(command,'_render',mutate)
 assert history(case)['text']==command.NOTICE


def test_no_new_index_or_unbounded_read_helpers(case,monkeypatch):
 setup(case)
 from goldway.knowledge import KnowledgeIndex
 def fail(*a,**kw):pytest.fail('history constructed index or used unbounded reader')
 monkeypatch.setattr(KnowledgeIndex,'__init__',fail);monkeypatch.setattr(case.e,'tasks',fail)
 import goldway.work_context as work
 monkeypatch.setattr(work,'readable_work_ids',fail)
 row=history(case);assert row['text']!=command.NOTICE


def test_before_send_work_role_revoke_denies_pending_history(case):
 setup(case);row=history(case)
 case.c['admins']=[]
 assert not delivery_permitted(case.s,'GOLDWAY','test_operator',row['id'],case.clock[0],case.c)


def test_malformed_instance_status_is_fatal_not_permission_skip(case):
 setup(case);case.s.execute('UPDATE tasks SET status=? WHERE id=?',('invented',case.instance))
 assert history(case)['text']==command.NOTICE


def test_missing_bound_instance_is_unavailable_not_empty(case):
 setup(case);case.s.execute('DELETE FROM tasks WHERE id=?',(case.instance,))
 assert history(case)['text']==command.NOTICE


def test_series_register_cap_no_unbounded_scan_or_partial_view(case):
 setup(case)
 original=case.s.rows('SELECT * FROM c07_series')[0]
 columns=','.join(original)
 for i in range(64):
  row=dict(original,id='extra-series-'+str(i),task_id='other-task-'+str(i))
  case.s.execute('INSERT INTO c07_series('+columns+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
 assert history(case)['text']==command.NOTICE


def test_no_group_history_reply(case):
 setup(case)
 case.e.reply('group','test_operator','Покажи историю регулярной задачи №1',channel='group',source_id='telegram:-100:20',message_at=case.clock[0])
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe='recurring-history:group'")


def test_protection_failure_rolls_back_without_task_or_schedule_change(case,monkeypatch):
 setup(case);before=state(case);out=case.s.rows('SELECT id FROM outbox')
 def fail(*a,**kw):raise RuntimeError('synthetic protection error')
 monkeypatch.setattr(case.e.intelligence,'protect_outbox',fail)
 with pytest.raises(RuntimeError):history(case)
 assert state(case)==before and case.s.rows('SELECT id FROM outbox')==out
 assert not case.s.rows('SELECT * FROM answer_citation_turns')


def test_actual_generated_instance_that_becomes_template_still_selects_exact_parent(case):
 setup(case)
 rule=resolve_recurrence('Каждый день в 10:00',datetime.fromtimestamp(case.clock[0],ZoneInfo('Europe/Moscow')))
 case.e.scheduler.register_series(case.instance,'test_operator',rule,'instance-template',case.clock[0])
 before=state(case);row=history(case,number=2)
 assert row['text']!=command.AMBIGUOUS and 'регулярного поручения №1' in row['text']
 assert 'Задача №2' in row['text'] and state(case)==before


def test_fatal_reader_budget_not_swallowed_as_denied_instance(case,monkeypatch):
 setup(case)
 import goldway.monthly_task_history as shared
 monkeypatch.setattr(shared,'MAX_QUERIES',1)
 assert history(case)['text']==command.NOTICE
