"""Actual accepted Engine command → I92 → guarded synthetic dispatch only."""
from types import SimpleNamespace
import ast,copy,json,sqlite3
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key
from goldway.work_context import work_id
from goldway import design_brief_command as command
from goldway import task_card_command as cards
from goldway import task_design_brief as adapter

NOW=1792174600
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
FORM='ТЗ дизайнеру по задаче №1\nЦель: Explicit design goal\nАудитория: Explicit audience\nФормат: Banner\nМатериалы: Unverified photo description'
class NoModel:
 def propose(self,*a,**k):pytest.fail('unexpected model call')
class API:
 def __init__(self):self.sent=[];self.lookups=[];self.callback=None;self.error=None
 def call(self,method,**kw):
  if method=='getBusinessConnection':
   self.lookups.append(kw)
   if self.callback:self.callback()
   return dict(user=dict(id=99),is_enabled=True,rights=dict(can_reply=True))
  assert method=='sendMessage'
  self.sent.append(kw)
  if self.error:raise self.error
  return dict(message_id=100+len(self.sent))


def make(path=':memory:'):
 s=Store(path);clock=[NOW]
 config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,config,NoModel(),lambda:clock[0]);api=API()
 return SimpleNamespace(s=s,e=e,c=config,api=api,clock=clock)


@pytest.fixture
def c(monkeypatch):
 value=make()
 def fail(*a,**k):pytest.fail('unexpected context/refresh/network')
 value.e._context=fail;value.e.intelligence.sources.refresh=fail
 import socket,httpx
 monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield value
 value.s.db.set_authorizer(None);value.s.db.close()


def task(c,**kw):
 d=dict(business='GOLDWAY',id='opaque-task',public_number=1,title='Canonical task',owner='alen',deadline='2026-10-19',next_step='Next',criterion='Criterion',status='confirmed',source_id='opaque-source',evidence='NO EVIDENCE READ',created=NOW)
 d.update(kw)
 c.s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',(d['business'],d['source_id'],'NO BODY READ','NO LINK READ'))
 c.s.execute('INSERT INTO tasks('+','.join(d)+') VALUES('+','.join('?' for _ in d)+')',tuple(d.values()))
 c.s.execute('INSERT INTO task_sources VALUES(?,?,?)',(d['business'],d['id'],d['source_id']))
 c.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',(d['business'],d['id'],'[]'))


def doc(c):
 c.e.intelligence.index.upsert(dict(source_id='doc-source',document_id='doc',title='NO TITLE',url='https://example.invalid/no',text='NO BODY',readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW-10,permission_expires_at=NOW+100,confirmed=True))
 ref=c.s.execute('SELECT id FROM knowledge_chunks').fetchone()[0]
 c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps([ref]),));return ref


def ready(c,guard=True):
 c.s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',NOW))
 return Dispatcher(c.s,c.c,c.api,lambda:c.clock[0],task_brief_guard=command.make_task_brief_guard(c.e) if guard else None)


def ask(c,text=FORM,event='request',sender='test_operator',channel='bot',source=None):
 uid=c.c['employees'][sender]['telegram_user_id']
 return c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:'+str(uid)+':10' if channel=='bot' else 'business:conn:'+str(uid)+':10'),message_at=c.clock[0])


def output(c):return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'task-design-brief:%'")
def receipts(c):return c.s.rows("SELECT * FROM runtime WHERE key LIKE 'task-design-brief:%'")
def state(c):return output(c)[0]['state']


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_accepted_engine_i92_protection_guarded_own_sender_delivery(c,monkeypatch,channel):
 task(c);ref=doc(c);d=ready(c);before=c.s.rows('SELECT * FROM tasks');calls=[];actual=adapter.TaskDesignBrief.preview
 def preview(self,*a,**k):calls.append(1);return actual(self,*a,**k)
 monkeypatch.setattr(adapter.TaskDesignBrief,'preview',preview)
 assert ask(c,channel=channel) and calls==[1]
 row=output(c)[0];assert row['task_id']=='opaque-task' and row['recipient']=='test_operator' and row['preferred_transport']==channel
 assert row['parse_mode'] is None and 'Explicit design goal' in row['text'] and 'Canonical task' in row['text']
 assert 'opaque-task' not in row['text'] and ref not in row['text']
 protection=json.loads(c.s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],)).fetchone()[0])
 assert protection==sorted((work_id('GOLDWAY','opaque-task'),ref)) and len(receipts(c))==1
 d.flush();assert state(c)=='sent' and len(c.api.sent)==1 and c.api.sent[0]['chat_id']=='1'
 assert bool(c.api.sent[0].get('business_connection_id'))==(channel=='business')
 assert not c.s.rows('SELECT * FROM usage') and c.s.rows('SELECT * FROM tasks')==before


def test_header_template_is_exact_fillable_and_no_task_read(c):
 d=ready(c);trace=[];c.s.db.set_trace_callback(trace.append)
 ask(c,'ТЗ дизайнеру по задаче №1')
 template=c.s.execute('SELECT text FROM outbox').fetchone()[0]
 assert len(template.splitlines())==5 and template.startswith('ТЗ дизайнеру по задаче №1\n')
 assert not output(c) and not any('FROM tasks' in q for q in trace)
 c.s.db.set_trace_callback(None);task(c)
 ask(c,template,event='filled')
 assert len(output(c))==1 and 'Цель: [уточнить]' in output(c)[0]['text']


@pytest.mark.parametrize('text',[
 'Подготовь ТЗ дизайнеру','Подготовь ТЗ для дизайнера',
 'ТЗ дизайнеру\nЦель: Old goal\nАудитория: Audience\nФормат: Banner\nСрок: 2026-10-20T12:00+03:00\nМатериалы: Manual description'])
def test_legacy_manual_request_form_unchanged_and_guard_optional(c,text):
 d=ready(c,False);ask(c,text)
 row=c.s.rows('SELECT * FROM outbox')[0]
 assert row['dedupe']=='design-brief:request' and row['task_id'] is None
 assert not output(c) and not receipts(c)
 d.flush();assert len(c.api.sent)==1


@pytest.mark.parametrize('text',[
 FORM.replace('№1','№01'),FORM.replace('№1','№0'),FORM.replace('№1','№1000000000'),
 FORM+'\nСрок: 2026-10-19',FORM.replace('Цель: Explicit design goal','Цель: x\nЦель: y'),
 FORM.replace('Материалы:', 'Срок:'),FORM.replace('Explicit audience','x'*1001),FORM.replace('Banner','x'*101),
 FORM.replace('Explicit design goal','a\tcontrol'),FORM.replace('Explicit design goal','a\u2028b')])
def test_malformed_exact_new_form_never_partial_private_output(c,text):
 task(c);ready(c);ask(c,text)
 assert not output(c) and not receipts(c)
 assert all('Canonical task' not in r['text'] for r in c.s.rows('SELECT * FROM outbox'))


@pytest.mark.parametrize('kind',['employee','other_uid','group','multiple_admin','missing_task','completed','proposed','source_deleted','doc_revoked'])
def test_invalid_scope_current_acl_or_state_no_task_output(c,kind):
 if kind!='missing_task':task(c,status=kind if kind in ('completed','proposed') else 'confirmed')
 if kind=='source_deleted':c.s.execute('UPDATE sources SET deleted=1')
 if kind=='doc_revoked':doc(c);c.s.execute('DELETE FROM knowledge_readers')
 ready(c)
 if kind=='multiple_admin':c.c['admins'].append('alen')
 if kind=='employee':ask(c,sender='alen')
 else:ask(c,source={'other_uid':'telegram:333:10','group':'telegram:-100:10'}.get(kind))
 assert not output(c) and not receipts(c)


@pytest.mark.parametrize('phase',['queue','protect'])
@pytest.mark.parametrize('kind',['revision','criterion','acl','source','config','intel'])
def test_queue_or_protect_callback_drift_rolls_back_all_private_output(c,monkeypatch,phase,kind):
 task(c);ready(c)
 def drift():
  if kind=='revision':c.s.execute('UPDATE tasks SET revision=revision+1')
  elif kind=='criterion':c.s.execute("UPDATE tasks SET criterion='Changed'")
  elif kind=='acl':c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps(['missing']),))
  elif kind=='source':c.s.execute('UPDATE sources SET deleted=1')
  elif kind=='config':c.e.c=copy.deepcopy(c.e.c)
  else:c.e.intelligence=copy.copy(c.e.intelligence)
 if phase=='queue':
  actual=c.s.queue
  def queue(*a,**k):
   result=actual(*a,**k)
   if a[3].startswith(command.TASK_PREFIX):drift()
   return result
  monkeypatch.setattr(c.s,'queue',queue)
 else:
  actual=c.e.intelligence.protect_outbox
  def protect(*a,**k):result=actual(*a,**k);drift();return result
  monkeypatch.setattr(c.e.intelligence,'protect_outbox',protect)
 ask(c)
 assert not output(c) and not receipts(c)
 assert not c.s.rows('SELECT * FROM intelligence_outbox_acl')


@pytest.mark.parametrize('phase',['queue','protect','receipt'])
def test_storage_fault_propagates_and_rolls_back_no_partial_queue(c,monkeypatch,phase):
 task(c);ready(c)
 if phase=='queue':
  actual=c.s.queue
  def failure(*a,**k):actual(*a,**k);raise sqlite3.IntegrityError('fixture queue failure')
  monkeypatch.setattr(c.s,'queue',failure)
 elif phase=='protect':
  actual=c.e.intelligence.protect_outbox
  def failure(*a,**k):actual(*a,**k);raise sqlite3.IntegrityError('fixture protection failure')
  monkeypatch.setattr(c.e.intelligence,'protect_outbox',failure)
 else:c.s.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON runtime WHEN NEW.key LIKE 'task-design-brief:%' BEGIN SELECT RAISE(ABORT,'fixture receipt failure'); END")
 with pytest.raises(sqlite3.IntegrityError):ask(c)
 assert not output(c) and not receipts(c) and not c.s.rows('SELECT * FROM intelligence_outbox_acl')


@pytest.mark.parametrize('kind',['no_guard','title','criterion','next_step','revision','acl','source','body','receipt_missing','receipt_bad','receipt_oversize','receipt_blob','protection','policy','event','accepted_barrier'])
def test_current_drift_cancels_pending_without_any_api_send(c,kind):
 task(c);d=ready(c,kind!='no_guard');ask(c)
 if kind in ('title','criterion','next_step'):c.s.execute('UPDATE tasks SET '+kind+"='Changed'")
 elif kind=='revision':c.s.execute('UPDATE tasks SET revision=revision+1')
 elif kind=='acl':c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps(['missing']),))
 elif kind=='source':c.s.execute('UPDATE sources SET deleted=1')
 elif kind=='body':c.s.execute("UPDATE outbox SET text='Altered'")
 elif kind=='receipt_missing':c.s.execute("DELETE FROM runtime WHERE key LIKE 'task-design-brief:%'")
 elif kind in ('receipt_bad','receipt_oversize','receipt_blob'):
  value={'receipt_bad':'{','receipt_oversize':'x'*16385,'receipt_blob':b'bad'}[kind]
  c.s.execute("UPDATE runtime SET value=? WHERE key LIKE 'task-design-brief:%'",(value,))
 elif kind=='protection':c.s.execute('DELETE FROM intelligence_outbox_acl')
 elif kind=='policy':c.c['allowed_live_recipients'].remove('alen')
 elif kind=='event':c.s.execute("UPDATE events SET payload=json_set(payload,'$.text','ТЗ дизайнеру по задаче №2')")
 elif kind=='accepted_barrier':
  from goldway.answer_citations import begin_answer
  begin_answer(c.s,c.c,'later',dict(sender='test_operator',channel='bot',source_id='telegram:1:99'),NOW)
 d.flush();assert state(c)=='cancelled' and not c.api.sent


@pytest.mark.parametrize('kind',['revision','acl','source','body','connection','chat','fallback','thread'])
def test_route_callback_drift_fails_second_guard_no_send(c,kind):
 task(c);d=ready(c);ask(c,channel='business')
 if kind=='fallback':
  c.c['business_bot_fallback']=True
  d.route=lambda *a,**k:({'chat_id':'1'},'bot')
 elif kind=='thread':d.route=lambda *a,**k:({'chat_id':'1','business_connection_id':'conn','message_thread_id':5},'business')
 elif kind=='connection':d.route=lambda *a,**k:({'chat_id':'1','business_connection_id':'other'},'business')
 elif kind=='chat':d.route=lambda *a,**k:({'chat_id':'333','business_connection_id':'conn'},'business')
 else:
  def callback():
   if kind=='revision':c.s.execute('UPDATE tasks SET revision=revision+1')
   elif kind=='acl':c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps(['missing']),))
   elif kind=='source':c.s.execute('UPDATE sources SET deleted=1')
   else:c.s.execute("UPDATE outbox SET text='Changed during route'")
  c.api.callback=callback
 d.flush();assert state(c)=='cancelled' and not c.api.sent


def test_generic_acl_is_independent_after_each_guard(c,monkeypatch):
 import goldway.intelligence as intel
 task(c);d=ready(c);ask(c);calls=[];actual=intel.delivery_permitted
 def permit(*a,**k):calls.append(1);return actual(*a,**k) if len(calls)==1 else False
 monkeypatch.setattr(intel,'delivery_permitted',permit)
 d.flush();assert len(calls)==2 and state(c)=='cancelled' and not c.api.sent


@pytest.mark.parametrize('existing',['unknown','sending','sent'])
def test_no_replay_unknown_sending_sent(c,existing):
 task(c);d=ready(c);ask(c);c.s.execute('UPDATE outbox SET state=?',(existing,))
 d.flush();assert state(c)==existing and not c.api.sent
 if existing=='sending':d.recover();assert state(c)=='unknown'


def test_retry_duplicate_event_and_one_send(c):
 task(c);d=ready(c);assert ask(c);assert not ask(c)
 c.s.execute("UPDATE outbox SET state='retry'")
 d.flush();d.flush();assert len(c.api.sent)==1 and state(c)=='sent'


def test_actual_file_restart_fresh_engine_guard_dispatch(tmp_path):
 path=str(tmp_path/'synthetic-taskbrief.sqlite');c=make(path);task(c);ready(c);ask(c);body=output(c)[0]['text'];c.s.db.close()
 c=make(path)
 try:
  d=Dispatcher(c.s,c.c,c.api,lambda:c.clock[0],task_brief_guard=command.make_task_brief_guard(c.e));d.recover();d.flush()
  assert state(c)=='sent' and c.api.sent[0]['text']==body
 finally:c.s.db.close()


def test_factory_without_intelligence_preserves_ordinary_route(c):
 c.e.intelligence=None;guard=command.make_task_brief_guard(c.e);d=ready(c,False);d.task_brief_guard=guard
 c.s.queue('GOLDWAY','test_operator','ordinary','ordinary',NOW,preferred_transport='bot');d.flush()
 assert len(c.api.sent)==1
 assert guard(dict(dedupe='ordinary')) is False


def test_receipt_sql_gate_and_actual_reader_counts(c,monkeypatch):
 import goldway.monthly_task_history as history
 task(c);readers=[];actual=history._Reader
 class Reader(actual):
  def __init__(self,*a):super().__init__(*a);readers.append(self)
 monkeypatch.setattr(history,'_Reader',Reader);monkeypatch.setattr(cards,'_Reader',Reader)
 d=ready(c);ask(c)
 assert len(readers)==4 and sum(r.bytes for r in readers)<=16*1024*1024
 readers.clear();d.flush()
 assert len(readers)==4 and sum(r.bytes for r in readers)<=16*1024*1024


def test_cli_injects_factory_into_single_existing_dispatcher_ast():
 from pathlib import Path
 tree=ast.parse(Path(__file__).parents[1].joinpath('goldway/cli.py').read_text())
 calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='Dispatcher']
 assert len(calls)==1
 keyword=next(k.value for k in calls[0].keywords if k.arg=='task_brief_guard')
 assert isinstance(keyword,ast.Call) and isinstance(keyword.func,ast.Name) and keyword.func.id=='make_task_brief_guard'
 assert len(keyword.args)==1 and isinstance(keyword.args[0],ast.Name) and keyword.args[0].id=='e'


@pytest.mark.parametrize('field',['bot_chat','business_chat','connection','last_inbound'])
def test_actual_current_chat_drift_during_route_denies(c,field):
 task(c);d=ready(c);ask(c,channel='business')
 def callback():
  value={'bot_chat':'333','business_chat':'333','connection':'revoked-conn','last_inbound':NOW-86400}[field]
  c.s.execute('UPDATE chats SET '+field+'=?',(value,))
 c.api.callback=callback
 d.flush()
 if field=='bot_chat':assert state(c)=='sent'  # Business route does not derive authority from bot fallback.
 else:assert state(c)=='cancelled' and not c.api.sent


def test_guard_factory_bound_identity_change_then_new_factory_after_restart(c):
 task(c);d=ready(c);ask(c)
 c.e.c=copy.deepcopy(c.e.c)
 d.flush();assert state(c)=='cancelled' and not c.api.sent


def test_missing_intelligence_factory_new_namespace_denies(c):
 task(c);d=ready(c);ask(c);c.e.intelligence=None
 d.task_brief_guard=command.make_task_brief_guard(c.e)
 d.flush();assert state(c)=='cancelled' and not c.api.sent


def test_header_trailing_blank_does_not_return_unfillable_template(c):
 ready(c);ask(c,'ТЗ дизайнеру по задаче №1\n')
 assert not output(c)
 rows=c.s.rows('SELECT * FROM outbox')
 assert len(rows)==1 and rows[0]['text']==command._NOTICE


def test_task_doc_expiry_between_guards_no_send(c):
 task(c);doc(c);d=ready(c);ask(c,channel='business')
 c.api.callback=lambda:c.clock.__setitem__(0,NOW+100)
 d.flush();assert state(c)=='cancelled' and not c.api.sent


def test_new_guard_failures_never_apply_to_ordinary_namespace(c):
 d=ready(c)
 def deny(*a,**k):pytest.fail('new guard called for ordinary output')
 d.task_brief_guard=deny
 c.s.queue('GOLDWAY','test_operator','ordinary','ordinary',NOW,preferred_transport='bot')
 d.flush();assert len(c.api.sent)==1


def test_no_source_body_or_task_evidence_reads_in_handler(c):
 task(c);doc(c);ready(c);reads=[]
 def authorize(action,table,column,*unused):
  if action==sqlite3.SQLITE_READ:
   reads.append((table,column))
   if (table=='sources' and column in ('body','link')) or (table=='tasks' and column=='evidence') or (table in ('knowledge_chunks','knowledge_documents') and column in ('text','title','url')):return sqlite3.SQLITE_DENY
  return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(authorize)
 ask(c)
 assert len(output(c))==1 and ('tasks','title') in reads


def test_send_unknown_never_rebuilds_or_resends(c):
 task(c);d=ready(c);ask(c);c.api.error=RuntimeError('synthetic unknown provider outcome')
 d.flush();assert state(c)=='unknown' and len(c.api.sent)==1
 c.api.error=None;d.flush();assert state(c)=='unknown' and len(c.api.sent)==1


def test_failed_event_is_not_current_accepted_command(c):
 task(c);d=ready(c);ask(c)
 c.s.execute("UPDATE events SET state='failed' WHERE id='request'")
 d.flush();assert state(c)=='cancelled' and not c.api.sent
