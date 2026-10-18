"""Actual current-input meeting question boundary; no network or old core suite."""
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher
import json,re
import goldway.meeting_binding as command
from datetime import date,timedelta
from goldway.meeting_week_adapter import project_week

NOW=1792174600
TEXT='Итоги планёрки.\nПодготовить список товаров.\nКакой образец выбрать?'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
_MISSING=object()


def result(actions=(),questions=_MISSING,intent='meeting',text='Нужно уточнить, какой образец отправить клиенту.'):
 value=dict(actions=list(actions),summary='Synthetic meeting draft',reply_text=text,intent=intent,
  dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 if questions is not _MISSING:value['meeting_question_lines']=questions
 return value


def make_case():
 s=Store(':memory:');clock=[NOW]
 config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,
  allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 engine=Engine(s,config,None,lambda:clock[0]);engine.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute("INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES('GOLDWAY','test_operator','1','1','conn',?)",(NOW,))
 return SimpleNamespace(s=s,e=engine,c=config,clock=clock)


@pytest.fixture
def case(monkeypatch):
 value=make_case()
 import builtins,socket,httpx
 def no_io(*a,**k):pytest.fail('unexpected external I/O')
 monkeypatch.setattr(builtins,'open',no_io);monkeypatch.setattr(socket,'socket',no_io);monkeypatch.setattr(httpx,'Client',no_io)
 yield value
 value.s.db.close()


def submit(case,value,text=TEXT,sender='test_operator',channel='bot',event='meeting',callback=None):
 case.clock[0]+=1;seen=[]
 class Model:
  def propose(self,context):
   seen.append(dict(context))
   if callback:callback(context)
   return value
 case.e.model=Model()
 uid=case.c['employees'][sender]['telegram_user_id']
 source=f'telegram:{uid}:10' if channel=='bot' else f'business:conn:{uid}:10'
 case.e.reply(event,sender,text,source_id=source,channel=channel,message_at=case.clock[0]);case.e.process(event)
 return seen


def outputs(case):return case.s.rows('SELECT * FROM outbox ORDER BY id')


def new_action():
 return dict(op='create',task_id=None,title='Проверить образцы',owner=None,deadline=None,next_step='Сверить образцы',criterion='Готов отчёт проверки',status='proposed',set_fields=[],evidence_lines=[1],owner_lines=[],deadline_lines=[])



class API:
 def __init__(self):self.calls=[];self.n=100
 def call(self,method,**kw):
  if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
  assert method=='sendMessage';self.calls.append(kw);self.n+=1;return dict(message_id=self.n)


def setup(c,*,channel='bot',send=True,questions=True):
 action=dict(op='create',task_id=None,title='Подготовить список товаров',owner=None,deadline=None,next_step='Подготовить список',criterion='Список готов',status='proposed',set_fields=[],evidence_lines=[2],owner_lines=[],deadline_lines=[])
 submit(c,result([action],questions=[[3]] if questions else []),channel=channel)
 rows=c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'meeting-binding-template:%'")
 assert len(rows)==1
 c.code=re.search(r'MB-[0-9a-f]{16}',rows[0]['text'])[0]
 c.api=API();c.d=Dispatcher(c.s,c.c,c.api,lambda:c.clock[0])
 if send:c.d.flush()
 c.task=c.s.rows('SELECT * FROM tasks')[0]
 c.channel=channel
 def fail(*a,**kw):pytest.fail('confirmation called model/refresh/context/search')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail;c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail
 return rows[0]


def block(c,*,day='2026-10-16',until='2026-10-30',selection=None,access='только для меня',consent=None):
 return ('Регистрация планёрки '+c.code+'\nДата планёрки: '+day+'\nДоступ: '+access+'\nДоступ до конца дня: '+until+'\n'+
  (selection or 'Решение: строки 2; задача №'+str(c.task['public_number']))+'\n'+(consent or command._CONSENT))


def confirm(c,text=None,event='confirm',*,send=False,channel=None,at=None):
 channel=channel or c.channel;c.clock[0]+=1
 c.e.reply(event,'test_operator',text or block(c),channel=channel,source_id=('telegram:1:20' if channel=='bot' else 'business:conn:1:20'),message_at=c.clock[0] if at is None else at)
 rows=c.s.rows('SELECT * FROM outbox WHERE dedupe=?',('meeting-binding:'+event,))
 if send:c.d.flush()
 return rows[0] if rows else None


def receipt(c):return json.loads(c.s.rows("SELECT value FROM runtime WHERE key LIKE 'meeting-binding:v1:%'")[0]['value'])


def consumer_payload(c):return dict(sender='test_operator',channel=c.channel,source_id=('telegram:1:99' if c.channel=='bot' else 'business:conn:1:99'))


@pytest.mark.parametrize('channel',['bot','business'])
def test_real_producer_delivered_review_explicit_confirmation_and_actual_i04(case,channel):
 setup(case,channel=channel);before=case.s.rows('SELECT * FROM tasks')
 row=confirm(case);assert row and 'Зарегистрированы дата 2026-10-16' in row['text']
 assert 'остаются черновиками' in row['text'] and 'результаты не подтверждены' in row['text']
 assert before==case.s.rows('SELECT * FROM tasks') and receipt(case)['state']=='registered'
 inputs=command.registered_inputs(case.e,case.code,consumer_payload(case))
 draft=project_week(case.s,case.c,'test_operator',meeting=inputs.meeting,start=date(2026,10,16),end=date(2026,10,23),task_bindings=inputs.task_bindings,result_bindings=(),sources=(),document_ids=inputs.document_ids,meeting_evidence_ids=inputs.meeting_evidence_ids,meeting_anchor_confirmed=True,now=case.clock[0])
 assert draft.progress[0].state=='missing_results'
 assert draft.progress[0].tasks[0].recorded_status=='proposed'
 assert draft.progress[0].tasks[0].result_state=='missing'
 assert inputs.meeting.agreements[0].value=='Подготовить список товаров.'
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")
 assert not any('Подготовить список товаров.' in value for value in [json.dumps(receipt(case),ensure_ascii=False)])
 assert not case.s.rows("SELECT * FROM knowledge_documents")


@pytest.mark.parametrize('kwargs',[{'day':'[уточнить]'}, {'day':'2026-02-30'}, {'day':'2026-10-17'}, {'day':'2026-01-01'},
 {'until':'[уточнить]'}, {'until':'2026-10-01'}, {'until':'2026-12-01'}, {'access':'всей команде'},
 {'consent':'Да'}, {'selection':'Решение: строки 0; задача №1'}, {'selection':'Решение: строки 999; задача №1'},
 {'selection':'Решение: строки 2; задача №999'}, {'selection':'Решение: строки 3; задача №1'},
 {'selection':'Решение: строки 2-3; задача №1'}, {'selection':'Решение: строки 2; задача №1\nРешение: строки 2; задача №1'}])
def test_missing_or_invalid_explicit_assertions_stay_template_no_approval(case,kwargs):
 setup(case);before=case.s.rows('SELECT * FROM tasks')
 row=confirm(case,block(case,**kwargs))
 assert row['text'].startswith('Регистрация планёрки '+case.code)
 assert receipt(case)['state']=='candidate' and before==case.s.rows('SELECT * FROM tasks')
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,consumer_payload(case))


def test_returned_literal_template_roundtrip_and_delivered_correction(case):
 template=setup(case);created=receipt(case)['created']
 row=confirm(case,template['text'],send=True,event='missing')
 assert row['text']==template['text'] and receipt(case)['created']==created
 ack=confirm(case,event='corrected')
 assert 'Зарегистрированы дата' in ack['text'] and receipt(case)['state']=='registered'


@pytest.mark.parametrize('change',['unsent','unknown','missing_delivery','duplicate_delivery','body','wrong_scope','late_old','new_turn','new_outbox','source_edited','source_deleted','revision','status','link_deleted','expired','oversized_kv'])
def test_current_prospective_delivery_source_and_task_prerequisites(case,change):
 setup(case,send=change!='unsent');before=case.s.rows('SELECT * FROM tasks')
 doc=receipt(case)
 if change=='unknown':case.s.execute("UPDATE outbox SET state='unknown' WHERE id=?",(doc['review'][0],))
 elif change=='missing_delivery':case.s.execute('DELETE FROM deliveries WHERE outbox_id=?',(doc['review'][0],))
 elif change=='duplicate_delivery':case.s.execute("INSERT INTO deliveries(outbox_id,at,state) VALUES(?,?,'sent')",(doc['review'][0],case.clock[0]))
 elif change=='body':case.s.execute("UPDATE outbox SET text='forged review' WHERE id=?",(doc['review'][0],))
 elif change=='new_turn':case.e.reply('new','test_operator','Что умеешь?',source_id='telegram:1:19',channel='bot',message_at=case.clock[0])
 elif change=='new_outbox':case.s.queue('GOLDWAY','test_operator','service','service',case.clock[0],preferred_transport='bot')
 elif change=='source_edited':case.s.execute('UPDATE sources SET body=? WHERE id=?',('Changed source',doc['source']))
 elif change=='source_deleted':case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',(doc['source'],))
 elif change=='revision':case.s.execute('UPDATE tasks SET revision=revision+1 WHERE id=?',(case.task['id'],))
 elif change=='status':case.s.execute("UPDATE tasks SET status='confirmed' WHERE id=?",(case.task['id'],))
 elif change=='link_deleted':case.s.execute('DELETE FROM task_sources WHERE task_id=?',(case.task['id'],))
 elif change=='expired':case.clock[0]+=86401
 elif change=='oversized_kv':case.s.execute("UPDATE runtime SET value=? WHERE key LIKE 'meeting-binding:v1:%'",('x'*16385,))
 before=case.s.rows('SELECT * FROM tasks')
 row=confirm(case,channel='business' if change=='wrong_scope' else None,at=NOW-1 if change=='late_old' else None)
 assert row['text']==command.NOTICE and before==case.s.rows('SELECT * FROM tasks')
 if change!='oversized_kv':assert receipt(case)['state']=='candidate'


def test_actual_delivery_time_not_original_queue_proves_chronology(case):
 setup(case,send=False);original=case.clock[0];case.clock[0]+=10;case.d.flush()
 assert confirm(case,at=original)['text']==command.NOTICE


def test_revocation_expiry_and_reader_business_isolation(case):
 setup(case);confirm(case)
 original=consumer_payload(case)
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,dict(original,sender='frost',source_id='telegram:2:20'))
 case.e.b='OTHER'
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,original)
 case.e.b='GOLDWAY'
 revoke=confirm(case,'Отмени регистрацию планёрки '+case.code,event='revoke')
 assert 'отозвана' in revoke['text'] and receipt(case)['state']=='revoked'
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,original)
 assert confirm(case,event='no-reapprove')['text']==command.NOTICE


def test_registration_expiry_no_rebase_or_cached_fallback(case):
 setup(case);confirm(case);expires=receipt(case)['expiry'];case.clock[0]=expires
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,consumer_payload(case))
 assert receipt(case)['expiry']==expires


def test_event_replay_one_ack_and_new_attempt_does_not_extend_registered_receipt(case):
 setup(case);confirm(case);before=receipt(case);out=case.s.rows('SELECT id FROM outbox')
 assert case.e.reply('confirm','test_operator',block(case),source_id='telegram:1:20',channel='bot',message_at=case.clock[0]) is False
 case.e.process('confirm')
 assert receipt(case)==before and case.s.rows('SELECT id FROM outbox')==out
 assert confirm(case,event='again')['text']==command.NOTICE and receipt(case)==before


def test_question_only_meeting_produces_no_binding_candidate(case):
 submit(case,result([],questions=[[3]]))
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'meeting-binding:v1:%'")
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'meeting-binding-template:%'")


def test_ack_uses_existing_work_provenance_not_new_anchor_as_generic_grant(case):
 setup(case);row=confirm(case)
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids'])
 assert refs and all(not value.startswith('meeting-access:') for value in refs)
 assert not case.s.rows('SELECT * FROM knowledge_documents')


@pytest.mark.parametrize('phase',['queue','save','protect'])
def test_registration_atomic_write_ack_guard_failure_rolls_back(case,monkeypatch,phase):
 setup(case);before=receipt(case);out=case.s.rows('SELECT id FROM outbox');tasks=case.s.rows('SELECT * FROM tasks')
 if phase=='queue':
  def fail(*a,**kw):raise RuntimeError('synthetic queue failure')
  monkeypatch.setattr(case.s,'queue',fail)
 elif phase=='save':
  def fail(*a,**kw):raise RuntimeError('synthetic metadata failure')
  monkeypatch.setattr(command,'_save',fail)
 else:
  def fail(*a,**kw):raise RuntimeError('synthetic protection failure')
  monkeypatch.setattr(case.e.intelligence,'protect_outbox',fail)
 with pytest.raises(RuntimeError):confirm(case)
 assert receipt(case)==before and case.s.rows('SELECT id FROM outbox')==out and case.s.rows('SELECT * FROM tasks')==tasks
 assert not case.s.rows("SELECT id FROM events WHERE id='confirm'")


@pytest.mark.parametrize('phase',['queue','protect'])
def test_registration_captured_intelligence_drift_rollback(case,monkeypatch,phase):
 setup(case);before=receipt(case);out=case.s.rows('SELECT id FROM outbox')
 obj=case.s if phase=='queue' else case.e.intelligence;name='queue' if phase=='queue' else 'protect_outbox';real=getattr(obj,name)
 def drift(*a,**kw):
  value=real(*a,**kw);case.e.intelligence=None;return value
 monkeypatch.setattr(obj,name,drift)
 with pytest.raises(ValueError):confirm(case)
 assert receipt(case)==before and case.s.rows('SELECT id FROM outbox')==out


def test_registered_source_edit_withdraws_consumer_inputs(case):
 setup(case);confirm(case);case.s.execute('UPDATE sources SET body=? WHERE id=?',('Changed',receipt(case)['source']))
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,consumer_payload(case))


def test_after_registration_current_completed_status_is_not_dated_result(case):
 setup(case);confirm(case)
 case.s.execute("UPDATE tasks SET status='completed',revision=revision+1 WHERE id=?",(case.task['id'],))
 inputs=command.registered_inputs(case.e,case.code,consumer_payload(case))
 draft=project_week(case.s,case.c,'test_operator',meeting=inputs.meeting,start=date(2026,10,16),end=date(2026,10,23),task_bindings=inputs.task_bindings,result_bindings=(),sources=(),document_ids=inputs.document_ids,meeting_evidence_ids=inputs.meeting_evidence_ids,meeting_anchor_confirmed=True,now=case.clock[0])
 assert draft.progress[0].tasks[0].recorded_status=='completed' and draft.progress[0].tasks[0].result_state=='missing'
 assert draft.progress[0].state=='missing_results'


def test_registered_metadata_survives_restart_without_approval_replay(case):
 setup(case);confirm(case);before=receipt(case)
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0])
 inputs=command.registered_inputs(case.e,case.code,consumer_payload(case))
 assert len(inputs.task_bindings)==1 and receipt(case)==before
 assert case.s.rows('SELECT status FROM tasks')==[dict(status='proposed')]


def test_two_literal_decisions_bind_only_two_exact_current_proposals(case):
 text='Итоги планёрки.\nПодготовить список товаров.\nПроверить образцы.\nКто примет образец?'
 actions=[]
 for title,line in [('Подготовить список товаров',2),('Проверить образцы',3)]:
  actions.append(dict(op='create',task_id=None,title=title,owner=None,deadline=None,next_step='Проверить',criterion='Готов результат',status='proposed',set_fields=[],evidence_lines=[line],owner_lines=[],deadline_lines=[]))
 submit(case,result(actions,questions=[[4]]),text=text)
 template=case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'meeting-binding-template:%'")[0]
 case.code=re.search(r'MB-[0-9a-f]{16}',template['text'])[0];case.task=case.s.rows('SELECT * FROM tasks ORDER BY public_number')[0];case.channel='bot'
 case.api=API();case.d=Dispatcher(case.s,case.c,case.api,lambda:case.clock[0]);case.d.flush()
 before=case.s.rows('SELECT * FROM tasks')
 confirm(case,block(case,selection='Решение: строки 2; задача №1\nРешение: строки 3; задача №2'))
 inputs=command.registered_inputs(case.e,case.code,consumer_payload(case))
 assert len(inputs.meeting.agreements)==2 and len(inputs.task_bindings)==2
 assert [a.value for a in inputs.meeting.agreements]==['Подготовить список товаров.','Проверить образцы.']
 assert before==case.s.rows('SELECT * FROM tasks')


def test_consumer_current_task_document_revocation_denies(case):
 setup(case);confirm(case)
 case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?) ON CONFLICT(business,task_id) DO UPDATE SET evidence_ids=excluded.evidence_ids',('GOLDWAY',case.task['id'],json.dumps(['knowledge:unavailable'])))
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,consumer_payload(case))


def test_capture_metadata_write_failure_rolls_back_meeting_tasks_and_template(case,monkeypatch):
 def fail(*a,**kw):raise RuntimeError('synthetic candidate metadata write failure')
 monkeypatch.setattr(command,'_save',fail)
 with pytest.raises(RuntimeError):setup(case)
 assert not case.s.rows('SELECT * FROM tasks')
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'meeting-binding:v1:%'")
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'review:%' OR dedupe LIKE 'meeting-binding-template:%'")


def test_capture_identity_drift_after_template_does_not_leave_partial_candidate(case,monkeypatch):
 real=case.s.queue
 def drift(*a,**kw):
  value=real(*a,**kw)
  if str(a[3]).startswith('meeting-binding-template:'):case.e.intelligence=None
  return value
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises(Exception):setup(case)
 assert not case.s.rows('SELECT * FROM tasks')
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'meeting-binding:v1:%'")


def test_whole_canonical_source_preflight_rejects_unselected_long_line(case):
 action=new_action();action['evidence_lines']=[2]
 submit(case,result([action],questions=[]),text='Итоги планёрки.\nПодготовить список товаров.\n'+'x'*2001)
 template=case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'meeting-binding-template:%'")[0]
 case.code=re.search(r'MB-[0-9a-f]{16}',template['text'])[0]
 case.task=case.s.rows('SELECT * FROM tasks')[0];case.channel='bot'
 case.api=API();case.d=Dispatcher(case.s,case.c,case.api,lambda:case.clock[0]);case.d.flush()
 before=case.s.rows('SELECT * FROM tasks')
 response=confirm(case)
 assert 'Зарегистрированы' not in response['text']
 assert receipt(case)['state']=='candidate' and case.s.rows('SELECT * FROM tasks')==before
 with pytest.raises(ValueError):command.registered_inputs(case.e,case.code,consumer_payload(case))


def test_unrelated_large_revoked_outbox_body_is_not_read_or_false_block(case):
 case.s.queue('GOLDWAY','test_operator','Synthetic unrelated private body '*1000,'older-private',case.clock[0],preferred_transport='bot')
 unrelated=case.s.rows("SELECT id FROM outbox WHERE dedupe='older-private'")[0]['id']
 case.e.intelligence.protect_outbox(unrelated,['knowledge:revoked'])
 setup(case)
 assert case.s.rows('SELECT state FROM outbox WHERE id=?',(unrelated,))[0]['state']!='sent'
 statements=[];case.s.db.set_trace_callback(statements.append)
 try:response=confirm(case)
 finally:case.s.db.set_trace_callback(None)
 assert 'Зарегистрированы' in response['text'] and receipt(case)['state']=='registered'
 windows=[sql for sql in statements if 'FROM outbox NOT INDEXED' in sql]
 assert windows and all('THEN text END' not in sql for sql in windows)
 points=[sql for sql in statements if 'THEN text END' in sql and 'FROM outbox' in sql]
 assert points and all('WHERE id=' in sql for sql in points)
 assert all('WHERE id='+str(unrelated)+' ' not in sql for sql in points)


def test_revoked_candidate_doc_denies_before_source_or_review_body_read(case):
 setup(case)
 case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?) ON CONFLICT(business,task_id) DO UPDATE SET evidence_ids=excluded.evidence_ids',('GOLDWAY',case.task['id'],json.dumps(['knowledge:unavailable'])))
 statements=[];case.s.db.set_trace_callback(statements.append)
 try:response=confirm(case)
 finally:case.s.db.set_trace_callback(None)
 assert response['text']==command.NOTICE and receipt(case)['state']=='candidate'
 assert not any('THEN body END' in sql and 'FROM sources' in sql for sql in statements)
 assert not any('THEN text END' in sql and 'FROM outbox' in sql for sql in statements)
