"""Actual I36 own-meeting runtime and guards; synthetic data, no old acceptance suites."""
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
from goldway.weekly_review_command import make_delivery_guard
import goldway.weekly_review_command as weekly

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
 c.api=API();c.d=Dispatcher(c.s,c.c,c.api,lambda:c.clock[0],weekly_guard=make_delivery_guard(c.e))
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



def weekly_rows(c):
 return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'weekly-review:%' ORDER BY id")


def request(c,*,text=None,event='weekly',sender='test_operator',channel=None,source=None):
 channel=channel or c.channel;c.clock[0]+=1
 c.e.reply(event,sender,text or ('Сверь планёрку '+c.code+' за 2026-10-12 — 2026-10-18'),channel=channel,
  source_id=source or ('telegram:1:90' if channel=='bot' else 'business:conn:1:90'),message_at=c.clock[0])
 return weekly_rows(c)


def ready(c,channel='bot'):
 setup(c,channel=channel);confirm(c,send=True)
 c.api.calls.clear()


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_runtime_i35_i04_i07_no_model_or_task_schedule_write(case,monkeypatch,channel):
 ready(case,channel)
 # A completed record deliberately has NO registered dated outcome.
 case.s.execute("UPDATE tasks SET status='completed',revision=revision+1")
 before=case.s.rows('SELECT * FROM tasks');schedules=case.s.rows("SELECT name,sql FROM sqlite_master WHERE type='table' AND name LIKE '%schedule%'")
 actual=weekly.adapter.project_week;seen=[]
 def spy(*a,**kw):
  seen.append(kw);return actual(*a,**kw)
 monkeypatch.setattr(weekly.adapter,'project_week',spy)
 import sqlite3
 writes=[]
 def observe(action,table,*rest):
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(observe)
 rows=request(case)
 case.s.db.set_authorizer(None)
 assert set(writes)<={'events','answer_citation_turns','outbox','intelligence_outbox_acl','runtime'}
 assert len(rows)==1,case.s.rows('SELECT text,error FROM outbox ORDER BY id')
 assert seen[0]['start']==date(2026,10,12) and seen[0]['end']==date(2026,10,19)
 assert seen[0]['sources']==() and seen[0]['result_bindings']==()
 body=rows[0]['text']
 assert 'Статус переданной записи: выполнено' in body
 assert 'датированный результат за период не передан' in body
 assert 'Источник 1 · строки 2–2' in body
 assert case.task['id'] not in body and case.task['source_id'] not in body and 'GOLDWAY' in body
 assert before==case.s.rows('SELECT * FROM tasks') and schedules==case.s.rows("SELECT name,sql FROM sqlite_master WHERE type='table' AND name LIKE '%schedule%'")
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(rows[0]['id'],))[0]['evidence_ids'])
 assert refs and not any(r.startswith('meeting-access:') for r in refs)
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['text']==body


@pytest.mark.parametrize('change',['revoke','expiry','source','deleted','revision','acl','policy','body','refs','missing_receipt','oversized_receipt','bad_receipt','wrong_recipient','wrong_transport','wrong_scope','missing_event'])
def test_pending_current_drift_cancels_without_send_or_rebuild(case,change):
 ready(case);row=request(case)[0]
 if change=='revoke':
  doc=receipt(case);doc['state']='revoked';case.s.execute("UPDATE runtime SET value=? WHERE key LIKE 'meeting-binding:v1:%'",(json.dumps(doc),))
 elif change=='expiry':case.clock[0]+=31*86400
 elif change=='source':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(case.task['source_id'],))
 elif change=='deleted':case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',(case.task['source_id'],))
 elif change=='revision':case.s.execute('UPDATE tasks SET revision=revision+1')
 elif change=='acl':case.s.execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',(case.e.b,case.task['id'],json.dumps(['unknown-document'])))
 elif change=='policy':case.c['timezone']='UTC'
 elif change=='body':case.s.execute("UPDATE outbox SET text='forged' WHERE id=?",(row['id'],))
 elif change=='refs':case.s.execute("UPDATE intelligence_outbox_acl SET evidence_ids='[]' WHERE outbox_id=?",(row['id'],))
 elif change=='missing_receipt':case.s.execute("DELETE FROM runtime WHERE key LIKE 'weekly-review:%'")
 elif change=='oversized_receipt':case.s.execute("UPDATE runtime SET value=? WHERE key LIKE 'weekly-review:%'",('x'*16385,))
 elif change=='bad_receipt':case.s.execute("UPDATE runtime SET value='{}' WHERE key LIKE 'weekly-review:%'")
 elif change=='wrong_recipient':case.s.execute("UPDATE outbox SET recipient='frost' WHERE id=?",(row['id'],))
 elif change=='wrong_transport':case.s.execute("UPDATE outbox SET preferred_transport='business' WHERE id=?",(row['id'],))
 elif change=='wrong_scope':case.s.execute("UPDATE chats SET bot_chat='2'")
 elif change=='missing_event':case.s.execute('DELETE FROM answer_citation_turns WHERE event_key=?',(weekly._hash('weekly'),))
 case.d.flush()
 assert case.api.calls==[]
 current=case.s.rows('SELECT * FROM outbox WHERE id=?',(row['id'],))[0]
 assert current['state']=='cancelled' and current['error']==weekly.CANCEL
 assert len(weekly_rows(case))==1
 case.d.flush();assert case.api.calls==[]


def test_real_restart_receipt_survives_new_guard_and_dispatcher(case):
 ready(case);row=request(case)[0]
 # Existing initialized host is reused, not constructed by the guard.
 fresh=Engine(case.s,case.c,None,lambda:case.clock[0])
 d=Dispatcher(case.s,case.c,case.api,lambda:case.clock[0],weekly_guard=make_delivery_guard(fresh));d.recover();d.flush()
 assert len(case.api.calls)==1
 assert case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='sent'


def test_namespace_default_deny_only_and_unknown_never_replays(case):
 ready(case);row=request(case)[0]
 d=Dispatcher(case.s,case.c,case.api,lambda:case.clock[0]);d.flush()
 assert case.api.calls==[] and weekly_rows(case)[0]['state']=='cancelled'
 case.s.queue(case.e.b,'test_operator','ordinary','ordinary',case.clock[0],preferred_transport='bot');d.flush()
 assert [v['text'] for v in case.api.calls]==['ordinary']
 request(case,event='second');second=weekly_rows(case)[-1]
 case.s.execute("UPDATE outbox SET state='sending' WHERE id=?",(second['id'],))
 case.d.recover();case.d.flush()
 assert len(case.api.calls)==1 and weekly_rows(case)[-1]['state']=='unknown'


@pytest.mark.parametrize('change',['revoke','expiry','route_destination','fallback','generic_denied','source','revision','body'])
def test_post_route_preclaim_guard_and_independent_generic_acl(case,monkeypatch,change):
 ready(case,'business');row=request(case)[0]
 original=case.api.call
 generic=[True]
 if change=='generic_denied':monkeypatch.setattr('goldway.intelligence.delivery_permitted',lambda *a,**k:generic[0])
 def callback(method,**kw):
  if method=='getBusinessConnection':
   if change=='revoke':
    doc=receipt(case);doc['state']='revoked';case.s.execute("UPDATE runtime SET value=? WHERE key LIKE 'meeting-binding:v1:%'",(json.dumps(doc),))
   elif change=='expiry':case.clock[0]+=31*86400
   elif change=='source':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(case.task['source_id'],))
   elif change=='revision':case.s.execute('UPDATE tasks SET revision=revision+1')
   elif change=='body':case.s.execute("UPDATE outbox SET text='forged' WHERE id=?",(row['id'],))
   elif change=='generic_denied':generic[0]=False
   elif change=='fallback':return dict(is_enabled=False)
  return original(method,**kw)
 case.api.call=callback
 if change=='fallback':case.c['business_bot_fallback']=True
 if change=='route_destination':case.s.execute("UPDATE chats SET business_chat='2'")
 case.d.flush()
 assert case.api.calls==[] and weekly_rows(case)[0]['state']=='cancelled'


@pytest.mark.parametrize('text',[
 'Сверь планёрку MB-0000000000000000 за 2026-10-12 — 2026-10-18',
 'Сверь планёрку {code}',
 'Сверь планёрку {code} за 2026-10-12 — 2026-10-19',
 'Сверь планёрку {code} за 2026-02-30 — 2026-03-06',
 'Сверь планёрку {code} за 20261012 — 20261018',
 'Сверь планёрку {code} за 2026-10-12 - 2026-10-18',
 'Сверь планёрку {code} за 2026-10-12 — 2026-10-18\nещё'])
def test_exact_command_no_latest_or_date_guess(case,text):
 ready(case);assert request(case,text=text.format(code=case.code))==[]
 assert case.s.rows("SELECT text FROM outbox WHERE dedupe LIKE 'weekly-review-notice:%'")[0]['text']==weekly.NOTICE


@pytest.mark.parametrize('kwargs',[dict(sender='frost',source='telegram:2:90'),dict(channel='group',source='telegram:-5:90'),dict(channel='business'),dict(source='telegram:2:90')])
def test_wrong_private_identity_or_transport_never_builds(case,kwargs):
 ready(case);assert request(case,**kwargs)==[]
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'weekly-review:%'")


@pytest.mark.parametrize('change',['task_bytes','acl_bytes','task_type','register','unexpected_sql'])
def test_i04_readview_bounds_before_cell_transfer(case,change):
 ready(case)
 view=weekly._ReadView(case.s)
 if change=='task_bytes':case.s.execute('UPDATE tasks SET title=?',('x'*4001,))
 elif change=='task_type':case.s.execute('UPDATE tasks SET title=?',(b'blob',))
 elif change=='acl_bytes':case.s.execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',(case.e.b,case.task['id'],'x'*16385))
 elif change=='register':
  for i in range(128):case.s.execute("INSERT INTO tasks(business,id,title,next_step,criterion,status,source_id,created,public_number) VALUES(?,?,?,'','','proposed',?,0,?)",(case.e.b,'extra-'+str(i),'extra',case.task['source_id'],100+i))
 elif change=='unexpected_sql':
  with pytest.raises(ValueError):view.execute('SELECT * FROM sources')
  with pytest.raises(ValueError):view.execute('UPDATE tasks SET title=title')
  return
 if change=='acl_bytes':query='SELECT task_id,evidence_ids FROM intelligence_task_acl WHERE business=?'
 else:query='SELECT id,public_number,title,owner,deadline,next_step,criterion,status,revision FROM tasks WHERE business=?'
 with pytest.raises(ValueError):view.rows(query,(case.e.b,))
 assert view.reader.bytes<4*1024*1024
 assert request(case)==[]


@pytest.mark.parametrize('change',['protect','queue','source','policy','page_limit','total_limit','body','receipt'])
def test_atomic_pages_protection_receipts_and_final_rechecks(case,monkeypatch,change):
 ready(case)
 original=case.e.intelligence.protect_outbox;calls=[]
 def protect(identifier,refs):
  calls.append(identifier);original(identifier,refs)
  if len(calls)==2:
   if change=='protect':raise ValueError('synthetic protect race')
   if change=='source':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(case.task['source_id'],))
   if change=='policy':case.c['timezone']='UTC'
   if change=='body':case.s.execute("UPDATE outbox SET text='changed' WHERE id=?",(calls[0],))
   if change=='receipt':case.s.execute("UPDATE runtime SET value='{}' WHERE key LIKE 'weekly-review:%'")
 monkeypatch.setattr(case.e.intelligence,'protect_outbox',protect)
 if change=='page_limit':pages=['x']*9
 elif change=='total_limit':pages=['x'*3400]*8
 else:pages=['first page','second page']
 monkeypatch.setattr(weekly.render,'render_weekly_draft',lambda *a:pages)
 if change=='queue':
  actual=case.s.queue
  def queue(*a,**kw):
   if len(calls)==1:raise ValueError('synthetic queue race')
   return actual(*a,**kw)
  monkeypatch.setattr(case.s,'queue',queue)
 # Queue-race fake permits the fixed notice after rollback.
 if change=='queue':
  actual=case.s.queue
  def fail_once(*a,**kw):
   if len(calls)==1 and str(a[3]).startswith(weekly.PREFIX):raise ValueError('synthetic queue race')
   return Store.queue(case.s,*a,**kw)
  monkeypatch.setattr(case.s,'queue',fail_once)
 assert request(case)==[]
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'weekly-review:%'")
 assert not case.s.rows("SELECT a.* FROM intelligence_outbox_acl a LEFT JOIN outbox o ON o.id=a.outbox_id WHERE o.id IS NULL")


def test_request_dedupe_and_receipt_page_ordinal(case,monkeypatch):
 ready(case)
 monkeypatch.setattr(weekly.render,'render_weekly_draft',lambda *a:['page one','page two'])
 rows=request(case);assert len(rows)==2
 assert len(request(case))==2
 for row in rows:
  saved=json.loads(case.s.rows('SELECT value FROM runtime WHERE key=?',(weekly._receipt_key(case.e.b,row['id']),))[0]['value'])
  assert saved['pages']==2 and saved['ordinal'] in (1,2)
 case.d.flush();assert len(case.api.calls)==2


@pytest.mark.parametrize('change',['doc_expiry','doc_reader','doc_version','dependency_revision','dependency_acl'])
def test_real_document_and_dependency_current_proof(case,change):
 ready(case)
 from goldway.intelligence import reader_key
 from goldway.work_context import work_id
 index=case.e.intelligence.index
 index.upsert(dict(source_id='synthetic-source',document_id='synthetic-document',title='Synthetic document',
  url='https://example.test/document',text='Synthetic document evidence',readers=[reader_key(case.e.b,'test_operator')],
  observed_at=case.clock[0]-1,permission_expires_at=case.clock[0]+600,confirmed=True))
 chunk=case.s.rows('SELECT id FROM knowledge_chunks')[0]['id']
 case.s.execute("INSERT INTO tasks(business,id,title,next_step,criterion,status,source_id,created,public_number) VALUES(?,?,'Synthetic dependency','','','confirmed',?,0,2)",(case.e.b,'synthetic-dependency',case.task['source_id']))
 case.s.execute('INSERT INTO task_sources VALUES(?,?,?)',(case.e.b,'synthetic-dependency',case.task['source_id']))
 depref=work_id(case.e.b,'synthetic-dependency')
 case.s.execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',(case.e.b,case.task['id'],json.dumps([depref])))
 case.s.execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',(case.e.b,'synthetic-dependency',json.dumps([chunk])))
 rows=request(case);assert len(rows)==1
 saved=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(rows[0]['id'],))[0]['evidence_ids'])
 assert chunk in saved and depref in saved and not any(r.startswith('meeting-access:') for r in saved)
 if change=='doc_expiry':case.clock[0]+=601
 elif change=='doc_reader':case.s.execute('DELETE FROM knowledge_readers')
 elif change=='doc_version':case.s.execute('UPDATE knowledge_documents SET observed_at=observed_at+0.5')
 elif change=='dependency_revision':case.s.execute("UPDATE tasks SET revision=revision+1 WHERE id='synthetic-dependency'")
 elif change=='dependency_acl':case.s.execute("UPDATE intelligence_task_acl SET evidence_ids='[\"denied-document\"]' WHERE task_id='synthetic-dependency'")
 case.d.flush();assert case.api.calls==[] and weekly_rows(case)[0]['state']=='cancelled'


@pytest.mark.parametrize('field,value',[('start','2026-10-11'),('end','2026-10-17'),('ordinal',2),('pages',False),('event_id','other'),('request',{}),('refs',['meeting-access:forged'])])
def test_malformed_or_mutated_receipt_metadata(case,field,value):
 ready(case);row=request(case)[0];key=weekly._receipt_key(case.e.b,row['id'])
 doc=json.loads(case.s.rows('SELECT value FROM runtime WHERE key=?',(key,))[0]['value']);doc[field]=value
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(doc),key))
 case.d.flush();assert case.api.calls==[] and weekly_rows(case)[0]['state']=='cancelled'


def test_canonical_window_mutation_fails_exact_accepted_request_proof(case):
 ready(case);row=request(case)[0];key=weekly._receipt_key(case.e.b,row['id'])
 doc=json.loads(case.s.rows('SELECT value FROM runtime WHERE key=?',(key,))[0]['value'])
 doc['start']='2026-10-11';doc['end']='2026-10-17'
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(doc),key))
 case.d.flush();assert case.api.calls==[] and weekly_rows(case)[0]['state']=='cancelled'


@pytest.mark.parametrize('phase',['build_queue','build_protect','send_route'])
@pytest.mark.parametrize('change',['config','intelligence','index'])
def test_captured_host_identity_replacements_fail_closed(case,monkeypatch,phase,change):
 ready(case,'business')
 import copy
 original_c=case.e.c;original_i=case.e.intelligence
 # Prepare replacement objects in the test; runtime never constructs them.
 replacement=copy.copy(original_i);replacement_index=copy.copy(original_i.index)
 def swap():
  if change=='config':case.e.c=copy.deepcopy(original_c)
  elif change=='intelligence':case.e.intelligence=replacement
  else:original_i.index=replacement_index
 if phase=='send_route':
  row=request(case)[0];actual=case.api.call
  def api(method,**kw):
   if method=='getBusinessConnection':swap()
   return actual(method,**kw)
  monkeypatch.setattr(case.api,'call',api)
  case.d.flush();assert case.api.calls==[] and weekly_rows(case)[0]['state']=='cancelled'
 else:
  if phase=='build_queue':
   actual=case.s.queue
   def queue(*a,**kw):
    result=actual(*a,**kw)
    if str(a[3]).startswith(weekly.PREFIX):swap()
    return result
   monkeypatch.setattr(case.s,'queue',queue)
  else:
   actual=original_i.protect_outbox
   def protect(*a,**kw):actual(*a,**kw);swap()
   monkeypatch.setattr(original_i,'protect_outbox',protect)
  assert request(case)==[]
  assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'weekly-review:%'")


def test_fixed_actual_reader_passes_and_honest_composite_caps(case,monkeypatch):
 ready(case)
 actual=weekly._Reader;readers=[]
 class Tracked(actual):
  def __init__(self,*a,**k):super().__init__(*a,**k);readers.append(self)
 monkeypatch.setattr(weekly,'_Reader',Tracked)
 monkeypatch.setattr('goldway.own_task_overview._Reader',Tracked)
 rows=request(case);assert len(rows)==1
 assert len(readers)==4
 assert sum(r.bytes for r in readers)<=16*1024*1024
 assert all(r.bytes<=4*1024*1024 and r.queries<=2048 and r.rows<=8192 for r in readers)
 readers.clear();case.d.flush();assert len(case.api.calls)==1
 assert len(readers)==4 # two fixed guards, two readers each
 assert sum(r.bytes for r in readers)<=16*1024*1024
 assert all(r.bytes<=4*1024*1024 and r.queries<=2048 and r.rows<=8192 for r in readers)


def test_absent_initialized_intelligence_denies_new_command_without_model(case):
 ready(case);case.e.intelligence=None
 assert request(case)==[]
 assert case.s.rows("SELECT state FROM events WHERE id='weekly'")[0]['state']=='done'
 assert callable(make_delivery_guard(case.e))
