"""Actual agenda/digest consumer: durable local capture only, no transport."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from dataclasses import replace,FrozenInstanceError
from zoneinfo import ZoneInfo
import json
import sqlite3
import pytest
from goldway import morning_digest_capture as module
from goldway.engine import Engine
from goldway.store import Store
from goldway.intelligence import Intelligence,reader_key
from goldway.knowledge import KnowledgeIndex
from goldway.model import ScriptedModel
from goldway.intake import validate_team

ZONE=ZoneInfo('Europe/Moscow')
WHEN=datetime(2026,10,19,9,tzinfo=ZONE)


def config():
 names=['test_operator','frost','alen','nastya','dasha_it']
 return dict(business='SYNTHETIC',employees={name:dict(name='Synthetic '+name,telegram_user_id=77+i) for i,name in enumerate(names)},admins=['test_operator'],timezone=ZONE.key,team_intake={'recipients':names,'started_at':int(WHEN.timestamp())-86400},team_roster_verified=True,business_account_user_id=99,allowed_live_recipients=names,live_send=False,recipients_verified=True,review_mode=True,allow_onboarding_replies=False,allow_registered_replies=False,sheets_write=False,daily_summary=False,intelligence={'enabled':True,'sources':[]},rules=[])


@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');c=config();time=[WHEN.timestamp()];clock=lambda:time[0];model=ScriptedModel({});engine_init=Engine.__init__;intel_init=Intelligence.__init__;index_init=KnowledgeIndex.__init__;e=Engine(s,c,model,clock)
 value=SimpleNamespace(s=s,c=c,time=time,clock=clock,e=e)
 value.capture=module.MorningDigestCapture(e,enabled=True);value.key=value.capture._policy()[2]
 def seed(identifier='task',**kw):
  row=dict(business=c['business'],id=identifier,title='Synthetic task',owner='alen',deadline='2026-10-18',next_step='Synthetic next',criterion='Synthetic criterion',status='blocked',source_id='not-read-source',evidence='BODY-MUST-NOT-BE-READ',created=WHEN.timestamp()-3600);row.update(kw)
  s.execute('INSERT INTO tasks('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()));return identifier
 def reopen():
  with monkeypatch.context() as host_setup:
   host_setup.setattr(Intelligence,'__init__',intel_init);host_setup.setattr(KnowledgeIndex,'__init__',index_init)
   value.e=object.__new__(Engine);engine_init(value.e,s,c,model,clock)
  value.capture=module.MorningDigestCapture(value.e,enabled=True)
 def document():
  value.e.intelligence.index.upsert(dict(source_id='synthetic',document_id='doc',title='Synthetic',url='https://example.invalid/synthetic',updated_at=None,text='PRIVATE-KB-BODY-NOT-READ',readers=[reader_key(c['business'],'test_operator')],observed_at=time[0]-1,permission_expires_at=time[0]+3600,confirmed=True))
  ref=next(iter(value.e.intelligence.index.allowed_ids(reader_key(c['business'],'test_operator'),now=time[0])))
  return ref
 value.seed=seed;value.reopen=reopen;value.document=document
 def forbidden(*a,**kw):pytest.fail('capture constructed host/refreshed/model/sent/read file or network')
 import builtins,socket,httpx
 for cls in (Store,Engine,Intelligence,KnowledgeIndex):monkeypatch.setattr(cls,'__init__',forbidden)
 monkeypatch.setattr(Store,'queue',forbidden);monkeypatch.setattr(model,'propose',forbidden);monkeypatch.setattr(e.intelligence.sources,'refresh',forbidden)
 monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden)
 yield value
 s.db.set_authorizer(None);s.db.set_trace_callback(None)
 if s.db.in_transaction:s.db.rollback()
 s.db.close()


def raw(c,key=None):
 row=c.s.execute('SELECT value FROM runtime WHERE key=?',(key or c.key,)).fetchone();return row[0] if row else None


def payload_key(c):return c.key+':'+datetime.fromtimestamp(c.time[0],timezone.utc).astimezone(ZONE).date().isoformat()

def deny(call):
 with pytest.raises(module.CaptureUnavailable,match='^morning_digest_capture_unavailable$'):call()


def actual_spy(monkeypatch):
 real=module.adapter.build_agenda_preview;calls=[]
 def spy(*a,**kw):calls.append(kw['as_of']);return real(*a,**kw)
 monkeypatch.setattr(module.adapter,'build_agenda_preview',spy);return calls


def test_real_085959_0900_capture_restart_replay_then_guarded_original_draft(case,monkeypatch):
 case.seed();calls=actual_spy(monkeypatch);case.time[0]-=1
 before=case.s.db.serialize();notdue=case.capture.capture_due();assert not notdue.due and notdue.payload_verified is False and raw(case) is None and case.s.db.serialize()==before
 case.time[0]+=1;receipt=case.capture.capture_due();assert receipt.due and not receipt.already_captured and receipt.capture_only and receipt.requires_review and not receipt.live_authorized and not receipt.payload_verified
 assert len(calls)==3 and len(json.loads(raw(case))['days'])==1
 original=raw(case,payload_key(case));head=raw(case);case.reopen();case.time[0]+=20
 calls.clear();queries=[];case.s.db.set_trace_callback(queries.append)
 replay=case.capture.capture_due();assert replay.already_captured and replay.payload_hash==receipt.payload_hash and replay.captured_at==WHEN.timestamp() and calls==[]
 assert raw(case)==head and raw(case,payload_key(case))==original and not any(q.startswith(('INSERT ','UPDATE ','DELETE ')) for q in queries)
 captured=case.capture.read_current();assert len(calls)==2 and captured.draft.digest.generated_at==WHEN and captured.captured_at==WHEN.timestamp()
 assert len(captured.draft.digest.lines)<=6 and captured.draft.digest.scheduled_for==WHEN and captured.capture_only and not captured.live_authorized
 assert captured.draft.snapshot_digest and captured.payload_hash==receipt.payload_hash
 with pytest.raises(FrozenInstanceError):captured.day='other'
 validate_team(case.c);assert case.c['daily_summary'] is False and not case.s.rows('SELECT * FROM outbox')
 assert not case.s.execute("SELECT 1 FROM runtime WHERE key LIKE 'summary:daily:%'").fetchone()


def test_next_day_new_current_task_data_no_prior_payload_transfer(case,monkeypatch):
 case.seed();first=case.capture.capture_due();oldkey=payload_key(case);old=raw(case,oldkey)
 case.s.execute("UPDATE tasks SET title='New day synthetic title',revision=revision+1 WHERE business=?",(case.c['business'],));case.time[0]+=86400
 reads=[];original=module._Reader.read
 def read(self,key,*a,**kw):reads.append((key,kw));assert key!=oldkey;return original(self,key,*a,**kw)
 monkeypatch.setattr(module._Reader,'read',read)
 second=case.capture.capture_due();current=case.capture.read_current()
 assert first.day!=second.day and first.payload_hash!=second.payload_hash and 'New day synthetic title' in '\n'.join(current.draft.digest.lines)
 assert old==raw(case,oldkey) and len(json.loads(raw(case))['days'])==2


@pytest.mark.parametrize('field',['next_step','criterion','revision','title_tail','acl','deleted','status'])
def test_same_day_metadata_drift_even_identical_rendered_prefix_denies_before_body(case,monkeypatch,field):
 case.seed(title='S'*100);case.capture.capture_due();before=raw(case,payload_key(case))
 if field=='next_step':case.s.execute("UPDATE tasks SET next_step='Changed next' WHERE business=?",(case.c['business'],))
 elif field=='criterion':case.s.execute("UPDATE tasks SET criterion='Changed criterion' WHERE business=?",(case.c['business'],))
 elif field=='revision':case.s.execute('UPDATE tasks SET revision=revision+1 WHERE business=?',(case.c['business'],))
 elif field=='title_tail':case.s.execute('UPDATE tasks SET title=? WHERE business=?',('S'*80+'CHANGED',case.c['business']))
 elif field=='acl':case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',(case.c['business'],'task','[]'))
 elif field=='deleted':case.s.execute('DELETE FROM tasks WHERE business=?',(case.c['business'],))
 else:case.s.execute("UPDATE tasks SET status='completed' WHERE business=?",(case.c['business'],))
 reads=[];actual=module._Reader.read
 def read(self,key,cap,**kw):
  reads.append(key)
  if key==payload_key(case):pytest.fail('old body transferred before current proof')
  return actual(self,key,cap,**kw)
 monkeypatch.setattr(module._Reader,'read',read);deny(case.capture.read_current);assert reads==[case.key]
 monkeypatch.setattr(module._Reader,'read',actual);replay=case.capture.capture_due();assert replay.already_captured and not replay.payload_verified and raw(case,payload_key(case))==before


@pytest.mark.parametrize('change',['expiry','revoke','acl_foreign'])
def test_current_protected_source_withdrawal_denies_before_payload(case,monkeypatch,change):
 ref=case.document();case.seed();case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',(case.c['business'],'task',json.dumps([ref])));case.capture.capture_due()
 if change=='expiry':case.time[0]+=3600
 elif change=='revoke':case.e.intelligence.index.revoke_source('synthetic')
 else:case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps(['unreadable']),))
 actual=module._Reader.read
 def read(self,key,*a,**kw):assert key!=payload_key(case);return actual(self,key,*a,**kw)
 monkeypatch.setattr(module._Reader,'read',read);deny(case.capture.read_current)


def test_all_consulted_omitted_refs_are_preserved_and_changed_omitted_row_denies(case):
 for i in range(5):case.seed('task'+str(i),title='Synthetic '+str(i))
 case.capture.capture_due();current=case.capture.read_current();head=json.loads(raw(case))
 assert len(current.draft.agenda.facts)==5 and len(current.draft.digest.sources)==5 and len(head['days'][0]['refs'])==5
 assert len(current.draft.digest.lines)==6 and 'Ещё пунктов' in current.draft.digest.lines[-2]
 omitted={f.id for f in current.draft.agenda.facts[3:]};assert omitted<=set(head['days'][0]['refs'])
 case.s.execute("UPDATE tasks SET criterion='Changed omitted proof' WHERE id='task4'")
 deny(case.capture.read_current)


@pytest.mark.parametrize('method',['capture_due','read_current'])
def test_default_disabled_zero_sql_clock_adapter(case,monkeypatch,method):
 capture=module.MorningDigestCapture(case.e)
 def fail(*a,**kw):pytest.fail('disabled used host')
 monkeypatch.setattr(case.s,'execute',fail);monkeypatch.setattr(module.adapter,'build_agenda_preview',fail);case.e.now=fail
 deny(getattr(capture,method))


@pytest.mark.parametrize('change',['engine','store','connection','config','business','intelligence','index','index_store','clock','timezone','admin','roster','daily_summary','intelligence_disabled'])
def test_captured_host_current_profile_and_policy_drift(case,change):
 case.seed();case.capture.capture_due()
 if change=='engine':case.capture._engine=object()
 elif change=='store':case.e.s=object()
 elif change=='connection':case.s.db=object()
 elif change=='config':case.e.c=dict(case.c)
 elif change=='business':case.e.b='FOREIGN'
 elif change=='intelligence':case.e.intelligence=object()
 elif change=='index':case.e.intelligence.index=object()
 elif change=='index_store':case.e.intelligence.index.store=object()
 elif change=='clock':case.e.now=lambda:case.time[0]
 elif change=='timezone':case.c['timezone']='UTC'
 elif change=='admin':case.c['admins']=['frost']
 elif change=='roster':case.c['employees'].pop('frost')
 elif change=='daily_summary':case.c['daily_summary']=True
 else:case.c['intelligence']['enabled']=False
 deny(case.capture.read_current)
 if change=='connection':case.s.db=case.capture._host[2]


@pytest.mark.parametrize('change',['source_policy','review_role','day_rollover','backward','clock_identity'])
def test_between_actual_passes_drift_rolls_back_no_capture(case,monkeypatch,change):
 case.seed();real=module.adapter.build_agenda_preview;calls=[]
 def spy(*a,**kw):
  value=real(*a,**kw);calls.append(value)
  if len(calls)==1:
   if change=='source_policy':case.c['intelligence']['sources']=[{'id':'changed'}]
   elif change=='review_role':case.c['review_mode']=False
   elif change=='day_rollover':case.time[0]+=86400
   elif change=='backward':case.time[0]-=1
   else:case.e.now=lambda:case.time[0]
  return value
 monkeypatch.setattr(module.adapter,'build_agenda_preview',spy);deny(case.capture.capture_due);assert raw(case) is None and raw(case,payload_key(case)) is None


def test_normal_advancing_same_day_clock_allowed_with_original_capture_time(case,monkeypatch):
 case.seed();counter=[WHEN.timestamp()]
 def clock():counter[0]+=.1;return counter[0]
 case.e.now=clock;case.capture=module.MorningDigestCapture(case.e,enabled=True)
 receipt=case.capture.capture_due();current=case.capture.read_current()
 assert current.captured_at==receipt.captured_at and current.draft.digest.generated_at.timestamp()==receipt.captured_at and counter[0]>receipt.captured_at


@pytest.mark.parametrize('fault',['payload_insert','head_insert','head_cas','postwrite_task','postwrite_host'])
def test_exact_transaction_partialwrite_cas_current_proof_failure_rolls_back(case,monkeypatch,fault):
 case.seed()
 if fault=='head_cas':case.capture.capture_due();case.time[0]+=86400
 before=case.s.rows('SELECT * FROM runtime');original=case.s.execute
 def execute(sql,args=()):
  ownwrite=sql.startswith(('INSERT INTO runtime','UPDATE runtime'))
  if ownwrite:
   key=args[0] if sql.startswith('INSERT') else args[1]
   if fault=='payload_insert' and key!=case.key:raise sqlite3.OperationalError('PRIVATE')
   if fault=='head_insert' and key==case.key:raise sqlite3.OperationalError('PRIVATE')
   if fault=='head_cas' and key==case.key:return SimpleNamespace(rowcount=0)
   cursor=original(sql,args)
   if fault=='postwrite_task':original("UPDATE tasks SET criterion='changed after write'")
   if fault=='postwrite_host':case.e.intelligence=object()
   return cursor
  return original(sql,args)
 monkeypatch.setattr(case.s,'execute',execute);deny(case.capture.capture_due)
 assert case.s.rows('SELECT * FROM runtime')==before and not case.s.db.in_transaction
 assert case.s.execute('SELECT criterion FROM tasks').fetchone()[0]=='Synthetic criterion'


@pytest.mark.parametrize('bad',['blob','oversize','json','duplicate','extra','revision','scope','future','date','policy','refs','captured_at','days33'])
def test_closed_head_malformed_whole_refusal_no_reset(case,bad):
 case.seed();case.capture.capture_due();head=json.loads(raw(case))
 if bad=='blob':value=b'blob'
 elif bad=='oversize':value='x'*65537
 elif bad=='json':value='{'
 elif bad=='duplicate':value=raw(case)[:-1]+',"version":1}'
 else:
  if bad=='extra':head['extra']=1
  elif bad=='revision':head['revision']=True
  elif bad=='scope':head['scope']='wrong'
  elif bad=='future':head['last_write_at']=WHEN.timestamp()+1
  elif bad=='date':head['days'][0]['day']='2026-99-99'
  elif bad=='policy':head['days'][0]['policy']='bad'
  elif bad=='refs':head['days'][0]['refs']=['duplicate','duplicate']
  elif bad=='captured_at':head['days'][0]['captured_at']+=86400
  elif bad=='days33':head['days']*=33
  value=json.dumps(head)
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,case.key));deny(case.capture.capture_due);deny(case.capture.read_current);assert raw(case)==value


@pytest.mark.parametrize('bad',['missing','dangling','blob','oversize','json','day','policy','time','snapshot','extra','duplicate','body_hash'])
def test_payload_envelope_replay_and_guarded_full_integrity_boundary(case,bad):
 case.seed();case.capture.capture_due();key=payload_key(case);payload=json.loads(raw(case,key))
 if bad=='missing':case.s.execute('DELETE FROM runtime WHERE key=?',(key,))
 elif bad=='dangling':case.s.execute('DELETE FROM runtime WHERE key=?',(case.key,))
 else:
  if bad=='blob':value=b'blob'
  elif bad=='oversize':value='x'*32769
  elif bad=='json':value='{'
  else:
   if bad=='day':payload['day']='2026-10-20'
   elif bad=='policy':payload['policy']='0'*64
   elif bad=='time':payload['captured_at']+=1
   elif bad=='snapshot':payload['snapshot']='0'*64
   elif bad=='extra':payload['extra']=1
   else:payload['draft']['digest']['lines'][0]='TAMPERED synthetic text'
   value=json.dumps(payload)
  if bad=='duplicate':value=value[:-1]+',"version":1}'
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,key))
 if bad=='body_hash':
  receipt=case.capture.capture_due();assert receipt.already_captured and not receipt.payload_verified
 else:deny(case.capture.capture_due)
 deny(case.capture.read_current)


@pytest.mark.parametrize('limit',['MAX_BYTES','MAX_QUERIES','MAX_ROWS','MAX_HEAD_BYTES','MAX_PAYLOAD_BYTES'])
def test_cumulative_own_bounds_no_partial_capture(case,monkeypatch,limit):
 case.seed();monkeypatch.setattr(module,limit,0);deny(case.capture.capture_due);assert raw(case) is None


def test_32_days_capacity_refuses_no_eviction_and_other_business_preserved(case):
 case.s.execute('INSERT INTO runtime VALUES(?,?)',('morning-digest-capture:v1:foreign','untouched'))
 for i in range(32):case.time[0]=WHEN.timestamp()+86400*i;case.capture.capture_due()
 head=raw(case);assert len(json.loads(head)['days'])==32
 case.time[0]+=86400;deny(case.capture.capture_due);assert raw(case)==head and raw(case,payload_key(case)) is None
 assert raw(case,'morning-digest-capture:v1:foreign')=='untouched'


def test_empty_draft_is_not_no_problems_and_current_day_only(case):
 case.capture.capture_due();value=case.capture.read_current()
 assert 'не подтверждение отсутствия проблем' in '\n'.join(value.draft.digest.lines)
 case.time[0]+=86400;deny(case.capture.read_current)


def test_capture_caller_transaction_refused_no_own_commit(case):
 case.s.db.execute('BEGIN');deny(case.capture.capture_due);assert case.s.db.in_transaction and raw(case) is None;case.s.db.rollback()


def test_actual_adapter_metadata_hash_row_order_independent_and_opaque(case,monkeypatch):
 for i in range(3):case.seed('task'+str(i))
 a=module.adapter.build_agenda_preview(case.s,case.c,'test_operator',case.e.intelligence.index,as_of=WHEN,max_items=5)
 real=case.s.rows
 def rows(sql,args=()):return list(reversed(real(sql,args)))
 monkeypatch.setattr(case.s,'rows',rows)
 b=module.adapter.build_agenda_preview(case.s,case.c,'test_operator',case.e.intelligence.index,as_of=WHEN,max_items=5)
 assert a.snapshot_digest==b.snapshot_digest and a.agenda==b.agenda and len(a.snapshot_digest)==64
 assert 'BODY-MUST-NOT-BE-READ' not in repr(a)


def test_sql_preflight_no_body_history_outbox_or_domain_write(case):
 case.seed();actions=[]
 def authorizer(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:
   assert table not in ('sources','events','outbox','deliveries','usage','chats','connections','task_transition_history')
   assert not (table=='tasks' and column in ('evidence','source_id')) and not (table=='knowledge_chunks' and column=='text')
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):assert table=='runtime'
  actions.append((action,table,column));return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorizer);trace=[];case.s.db.set_trace_callback(trace.append)
 case.capture.capture_due();case.capture.read_current()
 own=[q for q in trace if q.startswith('SELECT ') and 'FROM runtime WHERE key=' in q]
 assert own and all('CASE WHEN typeof(value)' in q and 'LIMIT 2' in q for q in own)
 assert len(actions)>0
