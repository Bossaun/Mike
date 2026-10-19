"""I40 bounded own-reference metadata listing: actual Engine, synthetic only."""
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from datetime import datetime,timezone
import json,re
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher
import goldway.personal_reference as personal
import goldway.own_impediments as command

NOW=1792174600
TEXT='Итоги обсуждения.\nКакой образец отправить клиенту?\nРешение пока не принимали.'
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
 return SimpleNamespace(s=s,e=engine,c=config,clock=clock)


@pytest.fixture
def case(monkeypatch):
 value=make_case()
 import builtins,socket,httpx
 def no_io(*a,**k):pytest.fail('unexpected external I/O')
 monkeypatch.setattr(builtins,'open',no_io);monkeypatch.setattr(socket,'socket',no_io);monkeypatch.setattr(httpx,'Client',no_io)
 yield value
 value.s.db.close()





def task(c,number,owner='test_operator',status='blocked',title=None,next_step='Согласовать образец.',deadline='2026-12-01',refs=()):
 tid='task-'+str(number);sid='synthetic:task:'+str(number)
 c.s.execute('INSERT INTO sources VALUES(?,?,?,?,?)',('GOLDWAY',sid,'PRIVATE SOURCE BODY MUST NOT BE READ','synthetic',0))
 c.s.execute('INSERT INTO tasks(business,id,public_number,title,owner,deadline,next_step,criterion,status,source_id,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('GOLDWAY',tid,number,title or 'Задача '+str(number),owner,deadline,next_step,'Готово',status,sid,'PRIVATE EVIDENCE MUST NOT BE READ',NOW))
 c.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY',tid,sid))
 c.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY',tid,json.dumps(list(refs))))
 return tid,sid


class API:
 def __init__(self,callback=None):self.calls=[];self.callback=callback
 def call(self,method,**kw):
  if method=='getBusinessConnection':
   if self.callback:self.callback()
   return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
  assert method=='sendMessage';self.calls.append(kw);return dict(message_id=len(self.calls)+100)


def ready(c,channel='bot',callback=None,guard=True):
 c.channel=channel;c.api=API(callback)
 c.s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',NOW))
 c.d=Dispatcher(c.s,c.c,c.api,lambda:c.clock[0],own_impediments_guard=command.make_delivery_guard(c.e) if guard else None)
 def fail(*a,**k):pytest.fail('unexpected model/context/source refresh/search')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail;c.e.intelligence.index.search=fail;c.e.intelligence.sources.refresh=fail


def ask(c,event='request',sender='test_operator',channel=None,text='Что мешает моим задачам?'):
 c.clock[0]+=1;channel=channel or c.channel;uid=c.c['employees'][sender]['telegram_user_id']
 return c.e.reply(event,sender,text,source_id=(f'telegram:{uid}:10' if channel=='bot' else f'business:conn:{uid}:10'),channel=channel,message_at=c.clock[0])


def output(c):
 return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'own-impediments:%' ORDER BY id")


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_owned_blocked_before_cap_current_snapshot_dispatch(case,channel):
 from goldway.work_context import work_id
 for n in range(1,6):task(case,n,status='in_progress',deadline='2026-01-01')
 tid,sid=task(case,6,deadline='2026-12-01');task(case,7,owner='frost',title='HIDDEN STAFF BLOCKER',deadline='2026-01-01')
 ready(case,channel);before=case.s.rows('SELECT * FROM tasks');trace=[];case.s.db.set_trace_callback(trace.append)
 try:assert ask(case)
 finally:case.s.db.set_trace_callback(None)
 row=output(case)[0]
 assert '№6' in row['text'] and 'Записанный следующий шаг: Согласовать образец.' in row['text']
 assert 'HIDDEN STAFF' not in row['text'] and all('№'+str(n)+' ' not in row['text'] for n in range(1,6))
 assert row['task_id'] is None and len(row['text'].encode('utf-16-le'))//2<=3500
 assert not any('THEN body END' in sql or re.search(r'SELECT evidence(?:,| FROM)',sql) for sql in trace)
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids'])
 assert work_id('GOLDWAY',tid) in refs and work_id('GOLDWAY','task-7') not in refs
 # Advancing time alone keeps the saved snapshot timestamp valid.
 case.clock[0]+=60;case.d.flush()
 assert output(case)[0]['state']=='sent' and len(case.api.calls)==1
 assert before==case.s.rows('SELECT * FROM tasks')


@pytest.mark.parametrize('field,value',[('owner','frost'),('status','completed'),('next_step','Изменённый шаг'),('title','Изменённая задача'),('revision',2)])
def test_pending_display_state_changes_cancel_before_route(case,field,value):
 tid,_=task(case,1);ready(case);ask(case)
 case.s.execute('UPDATE tasks SET '+field+'=? WHERE id=?',(value,tid));case.d.flush()
 assert output(case)[0]['state']=='cancelled' and not case.api.calls


@pytest.mark.parametrize('field,value',[('owner','frost'),('status','in_progress'),('next_step','Изменённый шаг'),('title','Изменённая задача'),('revision',2)])
def test_business_rights_lookup_state_changes_cancel_transactional_preclaim(case,field,value):
 tid,_=task(case,1)
 def mutate():case.s.execute('UPDATE tasks SET '+field+'=? WHERE id=?',(value,tid))
 ready(case,'business',mutate);ask(case);case.d.flush()
 assert output(case)[0]['state']=='cancelled' and not case.api.calls
 assert output(case)[0]['attempts']==0


@pytest.mark.parametrize('change',['source','acl','config','identity','route','fallback'])
def test_preclaim_permission_host_and_exact_route_changes_fail_closed(case,change):
 tid,sid=task(case,1)
 def mutate():
  if change=='source':case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',(sid,))
  elif change=='acl':case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE task_id=?',(json.dumps(['knowledge:revoked']),tid))
  elif change=='config':case.c['employees']['frost']['name']='drift'
  elif change=='identity':case.e.intelligence=None
  elif change=='route':case.s.execute("UPDATE chats SET business_chat='999' WHERE recipient='test_operator'")
 ready(case,'business',mutate);ask(case)
 if change=='route':
  # Current route snapshot itself is already wrong, even if the host remains valid.
  case.s.execute("UPDATE chats SET business_chat='999' WHERE recipient='test_operator'")
 if change=='fallback':
  case.c['business_bot_fallback']=True
  case.api.call=lambda method,**kw:dict(is_enabled=False,user=dict(id=99),rights={}) if method=='getBusinessConnection' else pytest.fail('fallback must not send')
 case.d.flush()
 assert output(case)[0]['state']=='cancelled' and not case.api.calls


def test_all_permitted_consulted_roots_protected_and_no_hidden_count(case):
 from goldway.work_context import work_id
 for n in range(1,8):task(case,n)
 ready(case);ask(case);row=output(case)[0]
 assert '№5' in row['text'] and '№6' not in row['text'] and 'ещё' not in row['text']
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids'])
 assert work_id('GOLDWAY','task-7') in refs
 case.s.execute("UPDATE tasks SET owner='frost' WHERE id='task-7'");case.d.flush()
 assert output(case)[0]['state']=='cancelled'


def test_acl_dependency_not_causal_and_empty_is_not_no_business_problem(case):
 from goldway.work_context import work_id
 dependency,_=task(case,2,owner='frost',status='in_progress',title='DEPENDENCY PRIVATE DISPLAY')
 task(case,1,status='in_progress',refs=[work_id('GOLDWAY',dependency)])
 ready(case);ask(case)
 text=output(case)[0]['text']
 assert 'нет собственных задач с отметкой препятствия' in text and 'DEPENDENCY PRIVATE' not in text
 assert 'проблем нет' not in text
 case.d.flush();assert output(case)[0]['state']=='sent'


@pytest.mark.parametrize('status',['confirmed','in_progress','proposed','completed'])
def test_status_only_nonblocked_does_not_invent_obstacle(case,status):
 task(case,1,status=status,deadline='2026-01-01');ready(case);ask(case)
 assert '№1' not in output(case)[0]['text']


def test_missing_next_step_and_html_are_literal_not_reason(case):
 task(case,1,title='<b>Запись & условие</b>',next_step='');ready(case);ask(case)
 text=output(case)[0]['text'];assert '&lt;b&gt;' in text and 'следующий шаг: не указан' in text and 'Фактические причины этим срезом не установлены' in text


@pytest.mark.parametrize('change',['staff','group','missing_intelligence','nonclosed'])
def test_unavailable_private_soleadmin_gate_no_record_transfer(case,change):
 task(case,1);ready(case)
 if change=='missing_intelligence':case.e.intelligence=None
 elif change=='nonclosed':case.c.pop('team_intake')
 sender='frost' if change=='staff' else 'test_operator'
 ask(case,sender=sender,channel='group' if change=='group' else None)
 assert not output(case) and not case.s.rows("SELECT * FROM outbox WHERE text LIKE '%Задача 1%'")


def test_default_guard_denies_only_new_namespace_and_unknown_recover_never_replays(case):
 task(case,1);ready(case,guard=False);ask(case)
 case.s.queue('GOLDWAY','test_operator','Ordinary','ordinary',NOW,preferred_transport='bot')
 case.d.flush();assert output(case)[0]['state']=='cancelled' and len(case.api.calls)==1
 ready_guard=command.make_delivery_guard(case.e)
 case.d.own_impediments_guard=ready_guard;ask(case,event='second');row=output(case)[-1]
 case.s.execute("UPDATE outbox SET state='sending' WHERE id=?",(row['id'],));case.d.recover();case.d.flush()
 assert output(case)[-1]['state']=='unknown' and len(case.api.calls)==1


def test_replay_restart_callback_body_tamper_and_provenance_tamper(case):
 task(case,1);ready(case);ask(case)
 assert not ask(case)
 assert len(output(case))==1
 # Restart callback uses only the provided initialized Engine, no Intel factory.
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0]);case.d.own_impediments_guard=command.make_delivery_guard(case.e)
 row=output(case)[0];case.s.execute("UPDATE outbox SET text='tampered' WHERE id=?",(row['id'],));case.d.flush()
 assert output(case)[0]['state']=='cancelled'


@pytest.mark.parametrize('kind',['title','register','receipt','budget'])
def test_malformed_or_overflow_fixed_notice_no_partial(case,monkeypatch,kind):
 task(case,1);ready(case)
 if kind=='title':case.s.execute("UPDATE tasks SET title=? WHERE id='task-1'",('x'*4001,))
 elif kind=='register':
  for n in range(2,130):task(case,n,owner='frost')
 elif kind=='budget':monkeypatch.setattr(__import__('goldway.monthly_task_history',fromlist=['x']),'MAX_QUERIES',2)
 elif kind=='receipt':
  real=case.s.execute
  def mutate(sql,*args):
   ret=real(sql,*args)
   if sql.startswith('INSERT INTO runtime') and 'own-impediments:' in str(args):real("UPDATE runtime SET value='bad' WHERE key LIKE 'own-impediments:%'")
   return ret
  monkeypatch.setattr(case.s,'execute',mutate)
 ask(case);assert not output(case)
 rows=case.s.rows('SELECT * FROM outbox');assert len(rows)==1 and rows[0]['text']==command.NOTICE


@pytest.mark.parametrize('phase',['queue','protect','identity'])
def test_build_savepoint_rolls_back_partial_body_receipt_and_acl(case,monkeypatch,phase):
 task(case,1);ready(case);real=case.s.queue
 if phase=='queue':
  def fail(*a,**kw):
   if str(a[3]).startswith(command.PREFIX):real(*a,**kw);raise RuntimeError('synthetic')
   return real(*a,**kw)
  monkeypatch.setattr(case.s,'queue',fail)
 else:
  original=case.e.intelligence.protect_outbox
  def drift(*a,**kw):
   original(*a,**kw)
   if phase=='identity':case.e.intelligence=None
   else:raise RuntimeError('synthetic')
  monkeypatch.setattr(case.e.intelligence,'protect_outbox',drift)
 ask(case);assert not output(case) and not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'own-impediments:%'")
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')


def test_multibyte_command_whitespace_over_accepted_byte_cap_keeps_ordinary_route(case):
 task(case,1);ready(case)
 text='Что'+'\u2003'*50+'мешает моим задачам?'
 assert len(text)<=128 and len(text.encode())>160 and not command.is_command(text)
 seen=[]
 case.e._context=lambda *args:seen.append(args) or {'tasks':[]}
 ask(case,text=text)
 assert seen
 assert not output(case) and case.s.rows("SELECT state FROM events WHERE id='request'")[0]['state']=='pending'


@pytest.mark.parametrize('point',['before_route','preclaim'])
def test_generic_acl_independent_of_optional_snapshot_callback(case,point):
 tid,_=task(case,1)
 def revoke():case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE task_id=?',(json.dumps(['knowledge:revoked']),tid))
 ready(case,'business',revoke if point=='preclaim' else None);ask(case)
 case.d.own_impediments_guard=lambda *a:True
 if point=='before_route':revoke()
 case.d.flush()
 assert output(case)[0]['state']=='cancelled' and not case.api.calls


def test_one_cumulative_reader_per_build_guard_and_no_index_factory(case,monkeypatch):
 import goldway.own_task_overview as readers_module
 from goldway.knowledge import KnowledgeIndex
 readers=[];base=readers_module._Reader
 class Counting(base):
  def __init__(self,*a,**k):super().__init__(*a,**k);readers.append(self)
 monkeypatch.setattr(readers_module,'_Reader',Counting)
 task(case,1);ready(case)
 with monkeypatch.context() as m:
  def no_init(*a,**kw):pytest.fail('own impediments initialized index')
  m.setattr(KnowledgeIndex,'__init__',no_init)
  ask(case);assert len(readers)==1
  assert command.make_delivery_guard(case.e)(output(case)[0]) is True
  assert len(readers)==2
 readers.clear();case.d.flush()
 assert len(readers)==2 and all(r.queries<=2048 and r.rows<=8192 and r.bytes<=4*1024*1024 for r in readers)
 assert output(case)[0]['state']=='sent'
