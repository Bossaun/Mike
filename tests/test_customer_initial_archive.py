"""Synthetic normalized archive seed → unchanged incremental path; no real export."""
from datetime import datetime,timedelta,timezone
from dataclasses import replace,FrozenInstanceError
from types import SimpleNamespace
import json
import sqlite3
import pytest
from goldway import customer_ingest as m
from goldway.store import Store

NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
EXPORT=NOW-timedelta(hours=1)


def row(identifier='e1',**kw):
 d=dict(event_id=identifier,customer_id='synthetic-customer',thread_id='synthetic-thread',at=NOW-timedelta(hours=2),direction='incoming',confirmed=True,reply_to=None)
 d.update(kw);return d


class Provider:
 def __init__(self):self.calls=[];self.pages=[]
 def fetch(self,cursor,limit=32):
  self.calls.append((cursor,limit));return self.pages.pop(0)


@pytest.fixture
def c():
 s=Store(':memory:');p=Provider()
 registration=m.SourceRegistration('A','fake','synthetic','local-account',1,NOW-timedelta(days=2),NOW+timedelta(days=2))
 holder=dict(source=registration,calls=0,callback=None)
 def current():
  holder['calls']+=1
  if holder['callback']:holder['callback'](holder['calls'])
  return holder['source']
 d=m.CustomerIngest(store=s,current_source=current,provider=p,enabled=True)
 value=SimpleNamespace(s=s,p=p,d=d,h=holder,current=current,source=registration)
 yield value
 s.db.set_authorizer(None);s.db.close()


def archive(c,**kw):
 args=dict(source=c.source,exported_at=EXPORT,handoff_cursor='handoff',events=(row(),),now=NOW);args.update(kw)
 return c.d.import_initial_archive(**args)


def saved(c):return tuple(tuple(r) for r in c.s.execute('SELECT key,value FROM runtime ORDER BY key'))


def fresh(c):return m.CustomerIngest(store=c.s,current_source=c.current,provider=c.p,enabled=True)


def state(c):return json.loads(c.s.execute('SELECT value FROM runtime WHERE key=?',(c.source.runtime_key,)).fetchone()[0])


def advance(c,rows=()):
 c.p.pages.append(dict(mode='incremental',cursor_before='handoff',cursor_after='next',events=rows))
 return c.d.ingest_once(now=NOW)


def preview(c):
 conv=m.core.Conversation('synthetic-customer','synthetic','synthetic-thread',c.source.source_id)
 return c.d.preview_unanswered(conv,now=NOW,window=m.core.Window(NOW-timedelta(days=1),NOW+timedelta(days=1)),stale_after=timedelta(minutes=30),dispositions=())


def test_actual_archive_restart_incremental_handoff_and_public_evaluator(c,monkeypatch):
 calls=[];actual=m.core.normalize_batch
 def normalize(events,**kw):
  value=actual(events,**kw);calls.append((kw,value));return value
 monkeypatch.setattr(m.core,'normalize_batch',normalize)
 first=archive(c)
 assert not c.p.calls and len(saved(c))==2
 assert [kw['mode'] for kw,_ in calls]==['archive']
 assert first.retained_events==1 and first.live_authorized is False
 assert 'local-account' not in repr(first) and 'handoff' not in repr(first)
 with pytest.raises(FrozenInstanceError):first.replayed=True
 assert len(preview(c).candidates)==1
 proof=c.d.source_proof(now=NOW,expected_store=c.s)
 c.d=fresh(c)
 response=row('reply',at=NOW-timedelta(minutes=50),direction='outgoing',reply_to='e1')
 advance(c,(response,))
 assert c.p.calls==[('handoff',32)]
 assert [kw['mode'] for kw,_ in calls]==['archive','incremental']
 assert len(state(c)['events'])==2 and not preview(c).candidates
 assert c.d.source_proof(now=NOW,expected_store=c.s)!=proof
 assert state(c)['committed_cursors']==['handoff','next']


@pytest.mark.parametrize('advanced',[False,True])
def test_exact_replay_is_zero_write_and_preserves_latest_receipt(c,advanced):
 archive(c)
 if advanced:advance(c,(row('new',at=NOW-timedelta(minutes=40)),))
 before=saved(c);c.d=fresh(c);changes=c.s.db.total_changes
 receipt=archive(c,now=NOW+timedelta(hours=1))
 assert receipt.replayed and saved(c)==before and c.s.db.total_changes==changes
 # Read-only replay time is not a persisted watermark.
 assert archive(c,now=NOW+timedelta(minutes=1)).replayed


@pytest.mark.parametrize('count',[0,1,128])
def test_count_boundaries_and_four_conversations(c,count):
 rows=tuple(row(str(i),customer_id='c'+str(i%4),thread_id='t'+str(i%4)) for i in range(count))
 assert archive(c,events=rows).retained_events==count
 assert len(state(c)['events'])==count


def test_identical_duplicates_actual_core_dedup(c):
 assert archive(c,events=(row(),row())).retained_events==1


@pytest.mark.parametrize('kw',[
 {'events':[]},{'events':tuple(row(str(i)) for i in range(129))},
 {'events':tuple(row(str(i),thread_id=str(i)) for i in range(5))},
 {'events':(row(confirmed=1),)},{'events':(row(at=NOW),)},
 {'events':(row(at=NOW.replace(tzinfo=None)),)},
 {'events':(row(body='private text not supported'),)},
 {'events':(row(direction='unknown'),)}, {'events':(row(event_id=''),)},
 {'events':(row(),row(confirmed=False))},
 {'events':(row('parent'),row('child',reply_to='parent'))},
 {'exported_at':NOW+timedelta(seconds=1)}, {'exported_at':EXPORT.replace(tzinfo=None)},
 {'handoff_cursor':None},{'handoff_cursor':''},{'handoff_cursor':False},
 {'handoff_cursor':'x'*129},{'now':NOW.replace(tzinfo=None)},
])
def test_closed_input_refuses_whole_without_fetch_or_write(c,kw):
 with pytest.raises(m.CustomerIngestUnavailable,match='^customer_ingest_unavailable$'):archive(c,**kw)
 assert not saved(c) and not c.p.calls


@pytest.mark.parametrize('mode',['disabled','revoked','expired','revision','business','source_validity'])
def test_current_source_and_enabled_gate(c,mode):
 if mode=='disabled':c.d._enabled=False
 elif mode=='revoked':c.h['source']=replace(c.source,revoked=True)
 elif mode=='expired':c.h['source']=replace(c.source,expires_at=NOW)
 elif mode=='revision':c.h['source']=replace(c.source,revision=2)
 elif mode=='business':c.h['source']=replace(c.source,business='B')
 else:c.h['source']=replace(c.source,expires_at=NOW+timedelta(hours=1))
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert not saved(c)


@pytest.mark.parametrize('phase',[2,3])
@pytest.mark.parametrize('drift',['revoke','expiry','provider','callback','connection','input'])
def test_late_current_callback_or_host_drift_rolls_back(c,phase,drift):
 rows=(row(),);other=Store(':memory:') if drift=='connection' else None
 def callback(n):
  if n!=phase:return
  if drift=='revoke':c.h['source']=replace(c.source,revoked=True)
  elif drift=='expiry':c.h['source']=replace(c.source,expires_at=NOW)
  elif drift=='provider':c.p.fetch=lambda *a,**k:None
  elif drift=='callback':c.d._current_source=lambda:c.source
  elif drift=='connection':c.s.db=other.db
  else:rows[0]['confirmed']=False
 original=c.s.db;c.h['callback']=callback
 try:
  with pytest.raises(m.CustomerIngestUnavailable):archive(c,events=rows)
 finally:
  c.s.db=original
  if other:other.db.close()
 assert not saved(c)


@pytest.mark.parametrize('when',['initial','advanced'])
@pytest.mark.parametrize('change',['export','cursor','event','now_before'])
def test_replay_different_immutable_envelope_refuses_no_reset(c,when,change):
 archive(c)
 if when=='advanced':advance(c)
 before=saved(c)
 kw={'export':{'exported_at':EXPORT-timedelta(minutes=1)},'cursor':{'handoff_cursor':'other'},'event':{'events':(row(confirmed=False),)},'now_before':{'now':NOW-timedelta(seconds=1)}}[change]
 with pytest.raises(m.CustomerIngestUnavailable):archive(c,**kw)
 assert saved(c)==before


@pytest.mark.parametrize('corruption',['missing_state','missing_receipt','bad_json','bool_count','oversize','blob','anchor','cursor','seed','receipt_time'])
def test_corrupt_or_orphan_state_receipt_fail_closed(c,corruption):
 archive(c);key='customer-initial-archive:v1:'+c.source.stream_hash
 if corruption=='missing_state':c.s.execute('DELETE FROM runtime WHERE key=?',(c.source.runtime_key,))
 elif corruption=='missing_receipt':c.s.execute('DELETE FROM runtime WHERE key=?',(key,))
 elif corruption in ('anchor','cursor','seed'):
  data=state(c)
  if corruption=='anchor':data['events'][0]['confirmed']=False
  elif corruption=='cursor':data['committed_cursors'][0]='other';data['cursor']='other';data['last_receipt']['cursor_after']='other'
  else:data['last_receipt']['fingerprint']='0'*64
  c.s.execute('UPDATE runtime SET value=? WHERE key=?',(m._json(data),c.source.runtime_key))
 else:
  data=json.loads(c.s.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()[0])
  if corruption=='bool_count':data['count']=True
  elif corruption=='receipt_time':data['imported_at']=NOW.replace(tzinfo=None).isoformat()
  value={'bad_json':'{','oversize':'x'*8193,'blob':b'bad'}.get(corruption,m._json(data))
  c.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,key))
 before=saved(c)
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert saved(c)==before


def test_incremental_only_existing_stream_cannot_be_adopted(c):
 c.p.pages.append(dict(mode='incremental',cursor_before=None,cursor_after='old',events=(row(),)))
 c.d.ingest_once(now=NOW);before=saved(c)
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert saved(c)==before


@pytest.mark.parametrize('abort',['ABORT','ROLLBACK'])
def test_second_write_failure_never_partial_import(c,abort):
 c.s.execute("CREATE TRIGGER reject_archive BEFORE INSERT ON runtime WHEN NEW.key LIKE 'customer-initial-archive:%' BEGIN SELECT RAISE("+abort+",'synthetic write failure'); END")
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert not saved(c)


def test_active_external_transaction_refused_before_core(c,monkeypatch):
 c.s.execute('BEGIN IMMEDIATE')
 monkeypatch.setattr(m.core,'normalize_batch',lambda *a,**k:pytest.fail('core inside external transaction'))
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert c.s.db.in_transaction
 c.s.execute('ROLLBACK')


def test_actual_retention_failure_no_eviction(c):
 archive(c,events=tuple(row(str(i)) for i in range(128)));before=saved(c)
 with pytest.raises(m.CustomerIngestUnavailable):advance(c,(row('129'),))
 assert saved(c)==before


def test_one_reader_budget_includes_state_and_sql_gated_receipt(c,monkeypatch):
 readers=[];real=m._Reader
 class Reader(real):
  def __init__(self,*a):super().__init__(*a);readers.append(self)
 monkeypatch.setattr(m,'_Reader',Reader)
 archive(c);stored=saved(c);assert len(readers)==1
 state_bytes=len(next(v for k,v in stored if k==c.source.runtime_key).encode())
 receipt_bytes=len(next(v for k,v in stored if k!=c.source.runtime_key).encode())
 assert readers[0].bytes==state_bytes+receipt_bytes and readers[0].queries==6
 archive(c)
 assert len(readers)==2 and readers[1].bytes==3*(state_bytes+receipt_bytes) and readers[1].queries==6
 monkeypatch.setattr(m,'MAX_BYTES',3*(state_bytes+receipt_bytes)-1)
 before=saved(c)
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert saved(c)==before


def test_replacement_connection_unrelated_transaction_untouched(c):
 original=c.s.db;replacement=Store(':memory:')
 replacement.execute('BEGIN IMMEDIATE')
 replacement.execute("INSERT INTO runtime(key,value) VALUES('unrelated','keep')")
 def callback(n):
  if n==3:c.s.db=replacement.db
 c.h['callback']=callback
 try:
  with pytest.raises(m.CustomerIngestUnavailable):archive(c)
  assert replacement.db.in_transaction
  assert replacement.execute("SELECT value FROM runtime WHERE key='unrelated'").fetchone()[0]=='keep'
  assert not original.in_transaction
 finally:
  c.s.db=original;replacement.db.rollback();replacement.db.close()
 assert not saved(c)


def test_current_callback_state_cas_race_refuses_whole(c):
 def callback(n):
  if n==2:c.s.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(c.source.runtime_key,'race'))
 c.h['callback']=callback
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert not saved(c)


def test_replay_still_requires_current_not_revoked_source(c):
 archive(c);before=saved(c);c.h['source']=replace(c.source,revoked=True)
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert saved(c)==before


@pytest.mark.parametrize('kind',['detached','duplicate','wrong_mode'])
def test_unexpected_core_manifest_refused(c,monkeypatch,kind):
 actual=m.core.normalize_batch
 def normalize(events,**kw):
  result=actual(events,**kw)
  if kind=='duplicate':return replace(result,events=result.events*2)
  if kind=='wrong_mode':return replace(result,mode='incremental')
  return replace(result,events=(replace(result.events[0],id='detached'),))
 monkeypatch.setattr(m.core,'normalize_batch',normalize)
 with pytest.raises(m.CustomerIngestUnavailable):archive(c)
 assert not saved(c)


def test_only_own_two_runtime_keys_and_no_provider_activity(c):
 c.s.execute("INSERT INTO runtime(key,value) VALUES('other-business','opaque')")
 writes=[]
 def authorizer(action,table,column,*unused):
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
   assert table=='runtime';writes.append(action)
  return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(authorizer);archive(c)
 assert len(writes)==1  # SQLite prepares identical INSERT SQL once, executes twice.
 assert len(saved(c))==3 and ('other-business','opaque') in saved(c) and not c.p.calls


def test_seed_consumes_cursor_slot_no_reuse_after_handoff(c):
 archive(c);advance(c);before=saved(c)
 c.p.pages.append(dict(mode='incremental',cursor_before='next',cursor_after='handoff',events=()))
 with pytest.raises(m.CustomerIngestUnavailable):c.d.ingest_once(now=NOW)
 assert saved(c)==before


def test_unconfirmed_archive_is_not_inferred_confirmed(c):
 archive(c,events=(row(confirmed=False),))
 assert state(c)['events'][0]['confirmed'] is False and not preview(c).candidates


@pytest.mark.parametrize('interruption',[KeyboardInterrupt,SystemExit])
@pytest.mark.parametrize('replace_connection',[False,True])
def test_callback_interruption_after_both_writes_rolls_back_original(c,interruption,replace_connection):
 original=c.s.db;replacement=Store(':memory:') if replace_connection else None
 if replacement:
  replacement.execute('BEGIN IMMEDIATE')
  replacement.execute("INSERT INTO runtime(key,value) VALUES('unrelated','preserve')")
 def callback(n):
  if n==3:
   assert original.execute('SELECT count(*) FROM runtime').fetchone()[0]==2
   if replacement:c.s.db=replacement.db
   raise interruption('synthetic interruption')
 c.h['callback']=callback
 try:
  with pytest.raises(interruption,match='synthetic interruption'):archive(c)
  assert not original.in_transaction
  assert original.execute('SELECT count(*) FROM runtime').fetchone()[0]==0
  if replacement:
   assert replacement.db.in_transaction
   assert replacement.execute("SELECT value FROM runtime WHERE key='unrelated'").fetchone()[0]=='preserve'
 finally:
  c.s.db=original
  if replacement:replacement.db.rollback();replacement.db.close()


def test_synthetic_file_store_close_reopen_handoff_public_proof_and_preview(tmp_path):
 # Only this test-created empty fixture database; never real/live SQLite.
 path=tmp_path/'synthetic-archive.sqlite'
 source=m.SourceRegistration('A','fake','synthetic','local-account',1,NOW-timedelta(days=2),NOW+timedelta(days=2))
 provider=Provider();holder=dict(source=source)
 def current():return holder['source']
 first=Store(str(path))
 c=SimpleNamespace(s=first,p=provider,source=source,current=current)
 c.d=m.CustomerIngest(store=first,current_source=current,provider=provider,enabled=True)
 try:
  archive(c)
  assert len(preview(c).candidates)==1
  proof=c.d.source_proof(now=NOW,expected_store=first)
 finally:first.db.close()
 reopened=Store(str(path));c.s=reopened
 c.d=m.CustomerIngest(store=reopened,current_source=current,provider=provider,enabled=True)
 try:
  assert archive(c).replayed
  assert c.d.source_proof(now=NOW,expected_store=reopened)==proof
  response=row('reply',at=NOW-timedelta(minutes=50),direction='outgoing',reply_to='e1')
  advance(c,(response,))
  assert provider.calls==[('handoff',32)] and not preview(c).candidates
  assert c.d.source_proof(now=NOW,expected_store=reopened)!=proof
  before=saved(c);assert archive(c).replayed and saved(c)==before
 finally:reopened.db.close()
