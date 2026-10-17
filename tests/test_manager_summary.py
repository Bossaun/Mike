"""I31 existing Updates→manager summary→actual synthetic Dispatcher boundary."""
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
 s=Store(':memory:');c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:NOW);e.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',NOW))
 class API:
  def __init__(self):self.calls=[]
  def call(self,method,**kw):
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';self.calls.append(kw);return dict(message_id=11)
 d=Dispatcher(s,c,API(),lambda:NOW)
 import builtins,socket,httpx
 def fail(*a,**kw):pytest.fail('external I/O')
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield SimpleNamespace(e=e,s=s,c=c,d=d)
 s.db.close()


def setup(c):
 c.clock=[NOW];c.e.now=lambda:c.clock[0];c.d.now=lambda:c.clock[0]
 for tid,title,owner,status in [('task-own','OWN','test_operator','completed'),('task-other','OTHER PRIVATE TITLE','frost','confirmed')]:
  c.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',('GOLDWAY','source-'+tid,'synthetic source body never needed','synthetic'))
  c.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)',('GOLDWAY',tid,title,owner,'2026-10-15','next','criterion',status,'source-'+tid,NOW-10))
 def fail(*a,**kw):pytest.fail('summary reached model/refresh/search')
 c.e.model=SimpleNamespace(propose=fail);c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail


def request(c,number=20,text='Сводка',channel='bot'):
 message=dict(message_id=number,date=int(c.clock[0]),chat=dict(id=1,type='private'),text=text)
 message['from']=dict(id=1,is_bot=False)
 if channel=='business':message['business_connection_id']='conn'
 Updates(c.e,c.d.api,900).handle(dict(update_id=number,**{('business_message' if channel=='business' else 'message'):message}))
 rows=c.s.rows('SELECT * FROM outbox WHERE dedupe=?',('summary:request:'+str(number)+':test_operator',))
 return rows[0] if rows else None


def test_actual_empty_document_refs_summary_pending_role_loss_denies_delivery(case):
 setup(case);row=request(case)
 assert row and 'OTHER PRIVATE TITLE' in row['text'] and row['task_id'] is None
 # The only role change is synthetic; known recipient/route allowlist remains.
 case.c['admins']=[]
 case.d.flush()
 assert not case.d.api.calls
 assert case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))==[dict(state='blocked')]


import goldway.manager_summary as command
from goldway.intelligence import reader_key
from goldway.knowledge import KnowledgeIndex


@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('phrase',['Сводка','Дай сводку','Покажи задачи','Список задач','Что по задачам','Итоги планёрки','/summary'])
def test_existing_update_phrases_current_summary_actual_send_no_task_mutation(case,channel,phrase):
 setup(case);before=case.s.rows('SELECT * FROM tasks');queries=[]
 case.s.db.set_trace_callback(queries.append)
 row=request(case,text=phrase,channel=channel)
 case.s.db.set_trace_callback(None)
 assert row and 'Выполнено: №1 OWN' in row['text'] and 'Задерживается: №2 OTHER PRIVATE TITLE' in row['text']
 assert 'Есть препятствие:' in row['text'] and 'Нужно решение:' in row['text'] and 'Срез на' in row['text']
 assert 'не отчёт за период' in row['text']
 assert row['task_id'] is None and row['parse_mode'] is None and row['recipient']=='test_operator'
 # Existing summary chooses its existing route; no transport/recipient change.
 assert row['preferred_transport'] is None
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids'])
 assert set(refs)=={work_id('GOLDWAY','task-own'),work_id('GOLDWAY','task-other')}
 assert all('source body' not in q for q in queries if q.lstrip().upper().startswith('SELECT'))
 assert not any('SELECT * FROM tasks' in q for q in queries)
 assert before==case.s.rows('SELECT * FROM tasks')
 assert not case.s.rows('SELECT * FROM usage')
 assert case.s.rows('SELECT phase FROM answer_citation_turns')==[dict(phase='pending')]
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'dialogue:%'")
 case.d.flush();assert len(case.d.api.calls)==1 and case.d.api.calls[0]['text']==row['text']


def add(c,tid,status='confirmed',owner='frost',deadline='2026-10-20',title=None):
 c.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',('GOLDWAY','source-'+tid,'PRIVATE SOURCE NEVER READ','synthetic'))
 c.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,created) VALUES(?,?,?,?,?,?,?,?,?,?)',('GOLDWAY',tid,title or 'TITLE-'+tid,owner,deadline,'next','criterion',status,'source-'+tid,NOW))


def test_real_status_groups_unknown_fields_and_proposal_not_overdue(case):
 setup(case)
 add(case,'blocked',status='blocked',deadline='2026-10-15')
 add(case,'proposal',status='proposed',deadline='2026-10-15')
 add(case,'unknown',owner=None,deadline=None)
 row=request(case);lines=row['text'].splitlines()
 delayed=next(x for x in lines if x.startswith('Задерживается:'))
 decision=next(x for x in lines if x.startswith('Нужно решение:'))
 blocked=next(x for x in lines if x.startswith('Есть препятствие:'))
 assert 'TITLE-proposal' not in delayed and 'TITLE-blocked' in delayed
 assert 'TITLE-proposal' in decision and 'TITLE-unknown' in decision
 assert 'TITLE-blocked' in blocked


@pytest.mark.parametrize('deadline,overdue',[('2026-10-16',False),('2026-10-15',True),
 ('2026-10-16T17:00:00+00:00',True),('2026-10-16T20:00:00+00:00',False)])
def test_date_whole_local_day_and_aware_instant_semantics(case,deadline,overdue):
 setup(case);case.s.execute("UPDATE tasks SET deadline=? WHERE id='task-other'",(deadline,))
 row=request(case);delayed=next(x for x in row['text'].splitlines() if x.startswith('Задерживается:'))
 assert ('OTHER PRIVATE TITLE' in delayed)==overdue


def protect_doc(c,tid,*,reader='test_operator'):
 c.e.intelligence.index.upsert(dict(source_id='synthetic-doc-'+tid,document_id='doc-'+tid,
  title='Synthetic instruction',url='https://example.invalid/synthetic',text='synthetic text',
  readers=[reader_key('GOLDWAY',reader)],observed_at=NOW-1,permission_expires_at=NOW+600))
 # Locate actual registered chunk provenance instead of inventing evidence IDs.
 ref=c.s.rows('SELECT c.id FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_key WHERE d.source_id=?',('synthetic-doc-'+tid,))[0]['id']
 c.s.execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY',tid,json.dumps([ref])))
 return ref


def test_current_document_revocation_excludes_all_title_and_count_influence(case):
 setup(case);ref=protect_doc(case,'task-other')
 case.s.execute('DELETE FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY','test_operator'),))
 row=request(case)
 assert 'OTHER PRIVATE TITLE' not in row['text']
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids'])
 assert work_id('GOLDWAY','task-other') not in refs and ref not in refs


def test_retained_doc_grants_do_not_substitute_for_current_task_role(case):
 setup(case);ref=protect_doc(case,'task-other');row=request(case)
 case.c['admins']=[]
 assert case.s.rows('SELECT 1 FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY','test_operator'),))
 assert not delivery_permitted(case.s,'GOLDWAY','test_operator',row['id'],NOW,case.c)
 case.d.flush();assert not case.d.api.calls


def test_dependency_work_refs_and_docrefs_all_protected(case):
 setup(case);ref=protect_doc(case,'task-other')
 case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','task-own',json.dumps([work_id('GOLDWAY','task-other')])))
 row=request(case)
 refs=set(json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids']))
 assert refs=={ref,work_id('GOLDWAY','task-own'),work_id('GOLDWAY','task-other')}
 case.s.execute('DELETE FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY','test_operator'),))
 assert not delivery_permitted(case.s,'GOLDWAY','test_operator',row['id'],NOW,case.c)


@pytest.mark.parametrize('source_change',['deleted','missing','linked_deleted','dependency_deleted'])
def test_current_source_liveness_before_content_projection(case,source_change):
 setup(case)
 if source_change=='deleted':case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-task-other'")
 elif source_change=='missing':case.s.execute("DELETE FROM sources WHERE id='source-task-other'")
 elif source_change=='linked_deleted':
  case.s.execute("INSERT INTO sources(business,id,body,link,deleted) VALUES('GOLDWAY','linked','PRIVATE','local',1)")
  case.s.execute("INSERT INTO task_sources VALUES('GOLDWAY','task-other','linked')")
 else:
  case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','task-other',json.dumps([work_id('GOLDWAY','task-own')])))
  case.s.execute("UPDATE sources SET deleted=1 WHERE id='source-task-own'")
 row=request(case);assert 'OTHER PRIVATE TITLE' not in row['text']


def test_all_permitted_unshown_and_future_roots_remain_protected(case):
 setup(case)
 for i in range(7):add(case,'more-'+str(i),status='completed')
 add(case,'future',deadline='2027-01-01')
 row=request(case);refs=set(json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids']))
 assert len(refs)==10 and work_id('GOLDWAY','future') in refs
 assert 'TITLE-future' not in row['text'] and 'TITLE-more-6' not in row['text'] and '(ещё 5)' in row['text']
 assert work_id('GOLDWAY','more-6') in refs


@pytest.mark.parametrize('field,value',[('id','x'*161),('title','x'*4001),('title',''),
 ('status','invented'),('deadline','tomorrow'),('deadline','2026-10-16T12:00'),('deadline','x'*65),('owner','unknown-reader'),
 ('criterion','x'*4001),('next_step','x'*4001),('public_number',None),('revision',0)])
def test_malformed_or_oversized_register_fixed_unavailable_no_partial(case,field,value):
 setup(case)
 case.s.execute('UPDATE tasks SET '+field+'=? WHERE id=?',(value,'task-other'))
 row=request(case)
 assert row['text']==command.NOTICE and 'OWN' not in row['text'] and 'PRIVATE' not in row['text']
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')


def test_register_overflow_fixed_unavailable_before_display(case):
 setup(case)
 for i in range(63):add(case,'overflow-'+str(i))
 row=request(case);assert row['text']==command.NOTICE


def test_plain_text_escaping_utf16_bound_and_no_internal_ids(case):
 setup(case);case.s.execute("UPDATE tasks SET title=? WHERE id='task-other'",('<b>😀 & synthetic</b>',))
 row=request(case)
 assert '<b>😀 & synthetic</b>' in row['text'] and row['parse_mode'] is None
 assert len(row['text'].encode('utf-16-le'))//2<=3500
 assert 'work-task:' not in row['text'] and 'task-other' not in row['text'] and 'revision' not in row['text']


@pytest.mark.parametrize('phase',['queue','protect'])
@pytest.mark.parametrize('drift',['intelligence','config','admin','business'])
def test_captured_authority_drift_rolls_back_summary_and_inbound_barrier(case,monkeypatch,phase,drift):
 setup(case)
 target=case.s if phase=='queue' else case.e.intelligence
 name='queue' if phase=='queue' else 'protect_outbox';original=getattr(target,name)
 def changed(*a,**kw):
  value=original(*a,**kw)
  if drift=='intelligence':case.e.intelligence=None
  elif drift=='config':case.e.c=dict(case.c)
  elif drift=='admin':case.c['admins']=[]
  elif drift=='business':case.c['business']='OTHER'
  return value
 monkeypatch.setattr(target,name,changed)
 with pytest.raises(ValueError):request(case)
 assert not case.s.rows('SELECT * FROM outbox') and not case.s.rows('SELECT * FROM answer_citation_turns')


def test_protection_write_failure_rolls_back_queue(case,monkeypatch):
 setup(case)
 def fail(*a,**kw):raise RuntimeError('synthetic failure')
 monkeypatch.setattr(case.e.intelligence,'protect_outbox',fail)
 with pytest.raises(RuntimeError):request(case)
 assert not case.s.rows('SELECT * FROM outbox') and not case.s.rows('SELECT * FROM answer_citation_turns')


def test_duplicate_update_and_queue_request_never_reprotects_existing_body(case,monkeypatch):
 setup(case);row=request(case)
 def fail(*a,**kw):pytest.fail('duplicate re-evaluated or protected existing body')
 monkeypatch.setattr(command,'build_manager_summary',fail)
 request(case);case.e.queue_summary('request:20')
 assert len(case.s.rows('SELECT * FROM outbox'))==1
 assert len(case.s.rows('SELECT * FROM answer_citation_turns'))==1


@pytest.mark.parametrize('change',['none','business','store','config'])
def test_unavailable_initial_intelligence_fixed_notice_no_plaintext_fallback(case,change):
 setup(case)
 if change=='none':case.e.intelligence=None
 elif change=='business':case.e.intelligence.b='OTHER'
 elif change=='store':case.e.intelligence.s=object()
 elif change=='config':case.e.intelligence.c=dict(case.c)
 row=request(case)
 assert row['text']==command.NOTICE and 'PRIVATE' not in row['text']


def test_fresh_final_snapshot_not_cached_when_permitted_title_changes(case):
 setup(case);calls=[0]
 def clock():
  calls[0]+=1
  if calls[0]==2:case.s.execute("UPDATE tasks SET title='CHANGED PRIVATE' WHERE id='task-other'")
  return NOW
 with pytest.raises(command.SummaryUnavailable):command.build_manager_summary(case.s,case.c,'test_operator',case.e.intelligence.index,clock=clock)


def test_staff_request_not_new_summary_route(case):
 setup(case)
 # Existing update intercept is admin-only. No new staff/group request hook added.
 message=dict(message_id=30,date=NOW,chat=dict(id=2,type='private'),text='Сводка')
 message['from']=dict(id=2,is_bot=False)
 Updates(case.e,case.d.api,900).handle(dict(update_id=30,message=message))
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'summary:%'")
 assert case.s.rows("SELECT state FROM events WHERE id='update:30'")==[dict(state='pending')]


def test_reader_budget_failure_is_fatal_not_semantic_skip(case,monkeypatch):
 setup(case)
 import goldway.monthly_task_history as shared
 monkeypatch.setattr(shared,'MAX_QUERIES',1)
 row=request(case)
 assert row['text']==command.NOTICE and 'OWN' not in row['text']


def test_output_budget_failure_fixed_notice_no_partial(case,monkeypatch):
 setup(case);monkeypatch.setattr(command,'MAX_OUTPUT_UNITS',1)
 row=request(case);assert row['text']==command.NOTICE


def test_no_new_index_construction_or_unbounded_task_read_helpers(case,monkeypatch):
 setup(case)
 def fail(*a,**kw):pytest.fail('summary constructed Index or used unbounded work/task reader')
 monkeypatch.setattr(KnowledgeIndex,'__init__',fail)
 monkeypatch.setattr(case.e,'tasks',fail)
 import goldway.work_context as work
 monkeypatch.setattr(work,'readable_work_ids',fail)
 row=request(case);assert row['text']!=command.NOTICE


def test_missing_dependency_does_not_grant_or_disclose_root(case):
 setup(case)
 case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','task-other',json.dumps([work_id('GOLDWAY','missing-task')])))
 row=request(case);assert 'OTHER PRIVATE TITLE' not in row['text']


def test_final_permission_revocation_no_partial_title(case,monkeypatch):
 setup(case);protect_doc(case,'task-other')
 real=command._render
 def revoke(*a,**kw):
  result=real(*a,**kw)
  case.s.execute('DELETE FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY','test_operator'),))
  return result
 monkeypatch.setattr(command,'_render',revoke)
 row=request(case);assert row['text']==command.NOTICE and 'OTHER PRIVATE TITLE' not in row['text']


def test_direct_builder_requires_current_admin_even_for_own_only_records(case):
 setup(case)
 with pytest.raises(command.SummaryUnavailable):
  command.build_manager_summary(case.s,case.c,'frost',case.e.intelligence.index,clock=lambda:NOW)
