"""Actual SQLite tenant bridge; host assertions are not authenticated credentials."""
from dataclasses import replace,FrozenInstanceError
from types import SimpleNamespace
import copy
import json
import sqlite3
import pytest

from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key
from goldway.knowledge import KnowledgeIndex
from goldway.intelligence import Intelligence
from goldway import tenant_adapter as module

core=module.core
NOW=1000


@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');tenants={};api=SimpleNamespace(calls=[])
 def api_call(*a,**kw):api.calls.append((a,kw));pytest.fail('adapter must never call Telegram')
 api.call=api_call
 for tenant in ('A','B'):
  c=dict(business=tenant,timezone='UTC',employees={'owner':dict(name='Owner',telegram_user_id=1),'admin':dict(name='Admin',telegram_user_id=2)},admins=['admin'],allowed_live_recipients=['owner','admin'],intelligence=dict(enabled=True,sources=[dict(id='registration',readers=['owner','admin'],type='hypothetical')]))
  e=Engine(s,c,None,lambda:NOW);d=Dispatcher(s,c,api,lambda:NOW)
  source=e.intelligence.prefix+'registration';document=tenant+'-document';chunk=tenant+'-chunk'
  s.execute('INSERT INTO sources(business,id,body,link,deleted) VALUES(?,?,?,?,0)',(tenant,'same-source','SOURCE_BODY_'+tenant,'unread'))
  s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,revision,created,public_number) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(tenant,'same-task','TITLE_'+tenant,'owner',None,'NEXT_'+tenant,'CRITERION_'+tenant,'confirmed','same-source',1,900,1))
  s.execute('INSERT INTO task_sources VALUES(?,?,?)',(tenant,'same-task','same-source'))
  s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',(tenant,'same-task',json.dumps([chunk])))
  s.execute('INSERT INTO knowledge_documents(id,source_id,document_id,title,url,observed_at,permission_expires_at,confirmed) VALUES(?,?,?,?,?,?,?,?)',(document,source,'same-external-id','DOC_TITLE_'+tenant,'https://example.invalid/',900,2000,1))
  s.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?)',(chunk,document,0,'CHUNK_BODY_'+tenant))
  s.execute('INSERT INTO knowledge_document_observations VALUES(?,?,?)',(document,source,900))
  for actor,uid in (('owner','1'),('admin','2')):
   s.execute('INSERT INTO knowledge_readers VALUES(?,?)',(document,reader_key(tenant,actor)))
   s.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?)',(tenant,actor,uid))
  scope=core.TenantScope(tenant,frozenset({'owner','admin'}),frozenset({'admin'}),frozenset({'bot:owner','bot:admin'}),frozenset({'rule'}),frozenset({'read','prepare_delivery'}),900,2000)
  state=dict(context=(scope,core.ScopedRef(tenant,'reader','owner')),mappings=((tenant,chunk,document,source,'registration'),),context_calls=0,binding_calls=0)
  def context(state=state):state['context_calls']+=1;return state['context']
  def mappings(state=state):state['binding_calls']+=1;return state['mappings']
  adapter=module.TenantAdapter(engine=e,dispatcher=d,current_context=context,source_bindings=mappings,enabled=True)
  tenants[tenant]=SimpleNamespace(c=c,e=e,d=d,state=state,adapter=adapter,source=source,document=document,chunk=chunk,task=core.ScopedRef(tenant,'task','same-task'))
 def fail(*a,**kw):pytest.fail('unexpected constructor/context/refresh/model/provider/write helper')
 for t in tenants.values():
  t.e._context=fail;t.e.model=SimpleNamespace(propose=fail);t.e.intelligence.sources.refresh=fail;t.e.intelligence.index.search=fail;t.e.intelligence.memory.get=fail
  t.d.flush=fail;t.e.s.queue=fail
 for cls in (Store,Engine,Intelligence,KnowledgeIndex):monkeypatch.setattr(cls,'__init__',fail)
 import socket,httpx,builtins
 monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(builtins,'open',fail)
 yield SimpleNamespace(s=s,A=tenants['A'],B=tenants['B'],api=api)
 s.db.set_authorizer(None);s.db.set_trace_callback(None);s.db.close()


def read(c,t=None):
 t=t or c.A
 return t.adapter.read_task(t.task,now=NOW)


def acl(c,value):c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE business=? AND task_id=?',(value,'A','same-task'))


def authorize(c,*,deny_titles=False):
 reads=[];queries=[]
 def guard(action,table,column,*args):
  if action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION):pytest.fail('non-SELECT adapter access '+str(action))
  if action==sqlite3.SQLITE_READ:
   reads.append((table,column))
   if table in ('events','runtime','deliveries','outbox','audit','task_status_history'):pytest.fail('unsupported global/history access')
   if (table,column) in (('sources','body'),('knowledge_chunks','text'),('knowledge_documents','title'),('knowledge_documents','url')):pytest.fail('private body or unrelated title transfer')
   if deny_titles and (table,column)==('tasks','title'):pytest.fail('title accessed before denial')
  return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(guard);c.s.db.set_trace_callback(queries.append)
 return reads,queries


@pytest.mark.parametrize('tenant',['A','B'])
def test_actual_db_read_core_point_grants_and_same_ids_isolation(case,monkeypatch,tenant):
 t=getattr(case,tenant);actual=core.validate_task_access;calls=[]
 def validate(scope,**kw):calls.append((scope,kw));return actual(scope,**kw)
 monkeypatch.setattr(core,'validate_task_access',validate)
 reads,queries=authorize(case)
 result=read(case,t)
 assert result.title=='TITLE_'+tenant and result.next_step=='NEXT_'+tenant and result.criterion=='CRITERION_'+tenant
 assert result.task==t.task and result.owner==core.ScopedRef(tenant,'reader','owner')
 assert result.sources==(core.ScopedRef(tenant,'source',t.chunk),) and result.live_authorized is False and len(result.proof_digest)==64
 assert len(calls)==2
 for scope,kw in calls:
  assert scope.tenant==tenant and kw['binding'].task==t.task and kw['acls'][0].readers==(t.state['context'][1],)
  assert kw['acls'][0].source.id==t.chunk and kw['acls'][0].issued_at==900 and kw['acls'][0].expires_at==2000
 assert all(q.startswith('SELECT ') for q in queries) and all(' LIMIT ' in q for q in queries)
 assert not case.api.calls
 with pytest.raises(FrozenInstanceError):result.title='forged'


@pytest.mark.parametrize('method',['read_task','preview_bot_route'])
def test_default_disabled_zero_sql_or_callback(case,method):
 t=case.A
 def fail():pytest.fail('disabled callback invoked')
 a=module.TenantAdapter(engine=t.e,dispatcher=t.d,current_context=fail,source_bindings=fail)
 case.s.db.set_authorizer(lambda *a:pytest.fail('disabled SQL'))
 with pytest.raises(module.TenantAdapterUnavailable):getattr(a,method)(t.task,now=NOW)


@pytest.mark.parametrize('actor',['owner','admin'])
def test_actual_own_bot_route_and_real_prepare_delivery_no_queue_or_api(case,monkeypatch,actor):
 t=case.A;t.state['context']=(t.state['context'][0],core.ScopedRef('A','reader',actor))
 actual=core.prepare_delivery;calls=[]
 def prepare(scope,**kw):calls.append((scope,kw));return actual(scope,**kw)
 monkeypatch.setattr(core,'prepare_delivery',prepare)
 before=case.s.db.total_changes;reads,queries=authorize(case)
 r=t.adapter.preview_bot_route(t.task,now=NOW)
 assert r.snapshot.title=='TITLE_A' and r.recipient.id==actor and r.connection.id=='bot:'+actor and r.transport=='bot' and r.chat_id==('1' if actor=='owner' else '2')
 assert r.review_required is True and r.live_authorized is False and len(calls)==1 and calls[0][1]['recipient']==t.state['context'][1]
 assert case.s.db.total_changes==before and not case.api.calls
 assert sum(q.startswith('SELECT * FROM chats') for q in queries)==1


@pytest.mark.parametrize('change',['cross_task','foreign_actor','revoked_scope','expired_scope','no_read','membership','admin_membership','ownerless','unknown_owner','bad_status','bad_revision','bad_number','missing_task','missing_acl','empty_acl','bad_acl','duplicate_acl','work_dependency','foreign_chunk','missing_chunk','missing_mapping','foreign_mapping','wrong_document','wrong_source','wrong_registration','source_policy','deleted_source','missing_source','missing_link','replace_link','missing_reader','admin_no_reader','unconfirmed','expired_doc','future_doc','missing_doc','changed_doc_source','missing_observation','changed_observation','revocation','inclusive_revocation'])
def test_current_denial_before_title_without_admin_source_bypass(case,change):
 t=case.A;scope,actor=t.state['context'];task=t.task
 if change=='cross_task':task=case.B.task
 elif change=='foreign_actor':t.state['context']=(scope,core.ScopedRef('B','reader','owner'))
 elif change=='revoked_scope':t.state['context']=(replace(scope,revoked=True),actor)
 elif change=='expired_scope':t.state['context']=(replace(scope,issued_at=0,expires_at=1000),actor)
 elif change=='no_read':t.state['context']=(replace(scope,operations=frozenset()),actor)
 elif change=='membership':t.state['context']=(replace(scope,readers=frozenset({'admin','stranger'})),core.ScopedRef('A','reader','stranger'))
 elif change=='admin_membership':t.state['context']=(replace(scope,admins=frozenset({'owner'})),actor)
 elif change=='ownerless':case.s.execute("UPDATE tasks SET owner=NULL WHERE business='A'")
 elif change=='unknown_owner':case.s.execute("UPDATE tasks SET owner='stranger' WHERE business='A'")
 elif change=='bad_status':case.s.execute("UPDATE tasks SET status='invented' WHERE business='A'")
 elif change=='bad_revision':case.s.execute("UPDATE tasks SET revision=0 WHERE business='A'")
 elif change=='bad_number':case.s.execute("UPDATE tasks SET public_number=NULL WHERE business='A'")
 elif change=='missing_task':case.s.execute("DELETE FROM tasks WHERE business='A'")
 elif change=='missing_acl':case.s.execute("DELETE FROM intelligence_task_acl WHERE business='A'")
 elif change=='empty_acl':acl(case,'[]')
 elif change=='bad_acl':acl(case,'{broken')
 elif change=='duplicate_acl':acl(case,json.dumps([t.chunk,t.chunk]))
 elif change=='work_dependency':acl(case,json.dumps(['work-task:dependency']))
 elif change=='foreign_chunk':acl(case,json.dumps([case.B.chunk]))
 elif change=='missing_chunk':case.s.execute('DELETE FROM knowledge_chunks WHERE id=?',(t.chunk,))
 elif change=='missing_mapping':t.state['mappings']=()
 elif change in ('foreign_mapping','wrong_document','wrong_source','wrong_registration'):
  row=list(t.state['mappings'][0]);row[{'foreign_mapping':0,'wrong_document':2,'wrong_source':3,'wrong_registration':4}[change]]='B' if change=='foreign_mapping' else 'wrong';t.state['mappings']=(tuple(row),)
 elif change=='source_policy':t.c['intelligence']['sources'][0]['readers']=['admin']
 elif change=='deleted_source':case.s.execute("UPDATE sources SET deleted=1 WHERE business='A'")
 elif change=='missing_source':case.s.execute("DELETE FROM sources WHERE business='A'")
 elif change=='missing_link':case.s.execute("DELETE FROM task_sources WHERE business='A'")
 elif change=='replace_link':case.s.execute("UPDATE task_sources SET source_id='foreign' WHERE business='A'")
 elif change in ('missing_reader','admin_no_reader'):
  actorname='admin' if change=='admin_no_reader' else 'owner';t.state['context']=(scope,core.ScopedRef('A','reader',actorname));case.s.execute('DELETE FROM knowledge_readers WHERE document_key=? AND reader=?',(t.document,reader_key('A',actorname)))
 elif change=='unconfirmed':case.s.execute('UPDATE knowledge_documents SET confirmed=0 WHERE id=?',(t.document,))
 elif change=='expired_doc':case.s.execute('UPDATE knowledge_documents SET permission_expires_at=1000 WHERE id=?',(t.document,))
 elif change=='future_doc':case.s.execute('UPDATE knowledge_documents SET observed_at=1100 WHERE id=?',(t.document,));case.s.execute('UPDATE knowledge_document_observations SET observed_at=1100 WHERE document_key=?',(t.document,))
 elif change=='missing_doc':case.s.execute('DELETE FROM knowledge_documents WHERE id=?',(t.document,))
 elif change=='changed_doc_source':case.s.execute("UPDATE knowledge_documents SET source_id='wrong' WHERE id=?",(t.document,))
 elif change=='missing_observation':case.s.execute('DELETE FROM knowledge_document_observations WHERE document_key=?',(t.document,))
 elif change=='changed_observation':case.s.execute('UPDATE knowledge_document_observations SET observed_at=901 WHERE document_key=?',(t.document,))
 elif change in ('revocation','inclusive_revocation'):case.s.execute('INSERT INTO knowledge_source_revocations VALUES(?,?,?)',(t.source,901 if change=='revocation' else 900,1))
 authorize(case,deny_titles=True)
 with pytest.raises(module.TenantAdapterUnavailable):t.adapter.read_task(task,now=NOW)


@pytest.mark.parametrize('observed,expiry,now,allowed',[(900.2,1001.9,1000,True),(900.2,1000.9,1000,False),(1000.1,2000,1000,False),(1000,2000,1000,True),(900,1000,1000,False)])
def test_fractional_validity_intersection_never_extends_permission(case,monkeypatch,observed,expiry,now,allowed):
 t=case.A
 case.s.execute('UPDATE knowledge_documents SET observed_at=?,permission_expires_at=? WHERE id=?',(observed,expiry,t.document));case.s.execute('UPDATE knowledge_document_observations SET observed_at=? WHERE document_key=?',(observed,t.document))
 if allowed:assert t.adapter.read_task(t.task,now=now).title=='TITLE_A'
 else:
  authorize(case,deny_titles=True)
  with pytest.raises(module.TenantAdapterUnavailable):t.adapter.read_task(t.task,now=now)


@pytest.mark.parametrize('change',['store','config','business','intelligence','index','sources','source_specs','dispatcher_store','dispatcher_config','dispatcher_business','route_instance','route_class'])
def test_equal_value_authority_replacement_denies_before_sql(case,monkeypatch,change):
 t=case.A
 if change=='store':t.e.s=copy.copy(case.s)
 elif change=='config':t.e.c=copy.deepcopy(t.c)
 elif change=='business':t.e.b='B'
 elif change=='intelligence':t.e.intelligence=copy.copy(t.e.intelligence)
 elif change=='index':t.e.intelligence.index=copy.copy(t.e.intelligence.index)
 elif change=='sources':t.e.intelligence.sources=copy.copy(t.e.intelligence.sources)
 elif change=='source_specs':t.e.intelligence.sources.specs[0]['readers']=[]
 elif change=='dispatcher_store':t.d.s=copy.copy(case.s)
 elif change=='dispatcher_config':t.d.c=copy.deepcopy(t.c)
 elif change=='dispatcher_business':t.d.b='B'
 elif change=='route_instance':t.d.route=lambda *a:({'chat_id':'1'},'bot')
 elif change=='route_class':monkeypatch.setattr(Dispatcher,'route',lambda *a:({'chat_id':'1'},'bot'))
 case.s.db.set_authorizer(lambda *a:pytest.fail('host drift must deny before SQL'))
 with pytest.raises(module.TenantAdapterUnavailable):read(case)


@pytest.mark.parametrize('change',['revision','title','acl','reader','mapping','scope','parent','link','source','context_config'])
def test_between_pass_db_or_callback_drift_fails_whole_call(case,monkeypatch,change):
 t=case.A;actual=module._one;done=[]
 def one(reader,table,spec,where,args):
  row=actual(reader,table,spec,where,args)
  if table=='tasks' and 'title' in spec and not done:
   done.append(True)
   if change=='revision':case.s.execute("UPDATE tasks SET revision=2 WHERE business='A'")
   elif change=='title':case.s.execute("UPDATE tasks SET title='CHANGED' WHERE business='A'")
   elif change=='acl':acl(case,json.dumps([case.B.chunk]))
   elif change=='reader':case.s.execute('DELETE FROM knowledge_readers WHERE document_key=?',(t.document,))
   elif change=='mapping':t.state['mappings']=(("A",t.chunk,t.document,t.source,'different'),)
   elif change=='scope':t.state['context']=(replace(t.state['context'][0],revoked=True),t.state['context'][1])
   elif change=='parent':case.s.execute('UPDATE knowledge_chunks SET document_key=? WHERE id=?',(case.B.document,t.chunk))
   elif change=='link':case.s.execute("DELETE FROM task_sources WHERE business='A'")
   elif change=='source':case.s.execute("UPDATE sources SET deleted=1 WHERE business='A'")
   elif change=='context_config':t.e.c=copy.deepcopy(t.c)
  return row
 monkeypatch.setattr(module,'_one',one)
 with pytest.raises(module.TenantAdapterUnavailable):read(case)


@pytest.mark.parametrize('change',['wrong_chat','missing_chat','missing_uid','unknown_connection','no_prepare','route_mutates_chat','route_mutates_acl','route_wrong_output'])
def test_own_bot_route_current_destination_connection_and_callback_races(case,monkeypatch,change):
 t=case.A;scope,actor=t.state['context']
 if change=='wrong_chat':case.s.execute("UPDATE chats SET bot_chat='2' WHERE business='A' AND recipient='owner'")
 elif change=='missing_chat':case.s.execute("DELETE FROM chats WHERE business='A' AND recipient='owner'")
 elif change=='missing_uid':t.c['employees']['owner'].pop('telegram_user_id')
 elif change=='unknown_connection':t.state['context']=(replace(scope,connections=frozenset()),actor)
 elif change=='no_prepare':t.state['context']=(replace(scope,operations=frozenset({'read'})),actor)
 else:
  actual=Dispatcher.route
  # Patch before constructing a fresh host factory is still unsupported implementation.
  # Instead mutate database from the existing method's Store read callback.
  original=case.s.execute;done=[]
  def execute(sql,args=()):
   if sql.startswith('SELECT * FROM chats') and not done:
    done.append(True)
    if change=='route_mutates_chat':original("UPDATE chats SET bot_chat='2' WHERE business='A' AND recipient='owner'")
    elif change=='route_mutates_acl':original("DELETE FROM knowledge_readers WHERE document_key=?",(t.document,))
    else:return SimpleNamespace(fetchone=lambda:{'connection':None,'business_chat':None,'bot_chat':'wrong','group_chat':None})
   return original(sql,args)
  monkeypatch.setattr(case.s,'execute',execute)
 with pytest.raises(module.TenantAdapterUnavailable):t.adapter.preview_bot_route(t.task,now=NOW)
 assert not case.api.calls


@pytest.mark.parametrize('field,value',[('title','x'*2049),('title',b'blob'),('next_step','x'*2049),('criterion','x'*2049),('deadline','x'*65),('owner','x'*129)])
def test_sql_cells_guarded_before_transfer_whole_refusal(case,monkeypatch,field,value):
 case.s.execute('UPDATE tasks SET '+field+'=? WHERE business=?',(value,'A'))
 with pytest.raises(module.TenantAdapterUnavailable):read(case)


@pytest.mark.parametrize('kind',['acl_bytes','refs21','mapping21','links21','reader_bytes','reader_rows','reader_queries'])
def test_count_and_cumulative_reader_budget_fail_closed(case,monkeypatch,kind):
 import goldway.monthly_task_history as history
 t=case.A
 if kind=='acl_bytes':acl(case,' '*8193)
 elif kind=='refs21':acl(case,json.dumps(['ref'+str(i) for i in range(21)]))
 elif kind=='mapping21':t.state['mappings']=tuple(('A','c'+str(i),'d',t.source,'registration') for i in range(21))
 elif kind=='links21':
  for i in range(20):case.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('A','same-task','s'+str(i)))
 elif kind=='reader_bytes':monkeypatch.setattr(history,'MAX_BYTES',8)
 elif kind=='reader_rows':monkeypatch.setattr(history,'MAX_ROWS',1)
 elif kind=='reader_queries':monkeypatch.setattr(history,'MAX_QUERIES',1)
 with pytest.raises(module.TenantAdapterUnavailable):read(case)


@pytest.mark.parametrize('task',[core.ScopedRef('A','runtime','legacy'),core.ScopedRef('A','delivery','1'),core.ScopedRef('B','task','same-task'),None,'same-task'])
def test_unsupported_global_or_ambient_refs_do_not_migrate(case,task):
 with pytest.raises(module.TenantAdapterUnavailable):case.A.adapter.read_task(task,now=NOW)


def test_snapshot_not_cached_as_future_authority(case):
 first=read(case);case.s.execute('DELETE FROM knowledge_readers WHERE document_key=?',(case.A.document,))
 with pytest.raises(module.TenantAdapterUnavailable):read(case)
 assert first.title=='TITLE_A' and first.live_authorized is False


@pytest.mark.parametrize('field,value',[('business_chat','x'*33),('connection','x'*129),('group_chat','x'*33),('last_inbound','bad'),('group_thread_id','bad')])
def test_oversized_or_invalid_other_chat_columns_deny_before_legacy_route(case,monkeypatch,field,value):
 case.s.execute('UPDATE chats SET '+field+"=? WHERE business='A' AND recipient='owner'",(value,))
 actual=case.s.execute
 def execute(sql,args=()):
  assert not sql.startswith('SELECT * FROM chats'),'legacy full chat transfer reached'
  return actual(sql,args)
 monkeypatch.setattr(case.s,'execute',execute)
 with pytest.raises(module.TenantAdapterUnavailable):case.A.adapter.preview_bot_route(case.A.task,now=NOW)
