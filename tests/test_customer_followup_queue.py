"""Actual I63 public proof/evaluation + durable explicit local Dispositions."""
from dataclasses import replace,fields,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import copy
import json
import sqlite3
import pytest
from goldway import customer_followup_queue as module
from goldway.customer_ingest import CustomerIngest,SourceRegistration,SourceProof,registration_fingerprint
from goldway.store import Store
from goldway.engine import Engine
from goldway.knowledge import KnowledgeIndex
from goldway.intelligence import Intelligence
from goldway.telegram import Dispatcher

core=module.core
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
WINDOW=core.Window(NOW-timedelta(days=1),NOW+timedelta(days=1))
STALE=timedelta(hours=1)


class Provider:
 def __init__(self):self.pages=[];self.calls=[];self.allowed=False
 def fetch(self,cursor,limit=32):
  assert self.allowed,'queue fetched provider'
  self.calls.append((cursor,limit));return self.pages.pop(0)


def event(identifier='incoming',**kw):
 row=dict(event_id=identifier,customer_id='customer',thread_id='thread',at=NOW-timedelta(hours=2),direction='incoming',confirmed=True,reply_to=None);row.update(kw);return row


def queue(c,**kw):
 values=dict(store=c.s,ingest=c.ingest,source=c.source,conversation=c.conv,enabled=True);values.update(kw)
 return module.CustomerFollowupQueue(**values)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));provider=Provider()
 source=SourceRegistration('A','fake','synthetic','synthetic-account',1,NOW-timedelta(days=1),NOW+timedelta(days=1))
 state={'source':source,'proof_calls':0};captured=[]
 def current():return state['source']
 ingest=CustomerIngest(store=s,current_source=current,provider=provider,enabled=True)
 conv=core.Conversation('customer','synthetic','thread',source.source_id)
 c=SimpleNamespace(s=s,path=path,p=provider,source=source,state=state,current=current,ingest=ingest,conv=conv,cursor=None)
 def admit(rows=None,now=NOW):
  after='cursor-'+str(len(provider.calls)+1);provider.pages.append(dict(mode='incremental',cursor_before=c.cursor,cursor_after=after,events=[event()] if rows is None else rows));provider.allowed=True
  try:result=c.ingest.ingest_once(now=now)
  finally:provider.allowed=False
  c.cursor=result.cursor;return result
 c.admit=admit;admit();c.q=queue(c)
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close()
  c.ingest=CustomerIngest(store=c.s,current_source=current,provider=provider,enabled=True);c.q=queue(c)
 c.reopen=reopen
 def fail(*a,**kw):pytest.fail('unexpected constructor/network/model/send/config operation')
 for cls in (Store,Engine,KnowledgeIndex,Intelligence,Dispatcher):monkeypatch.setattr(cls,'__init__',fail)
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def refresh(c,**kw):
 values=dict(now=NOW,window=WINDOW,stale_after=STALE);values.update(kw);return c.q.refresh_unanswered(**values)


def record(c,receipt,**kw):
 values=dict(state='acknowledged',observation_id='observation',evidence_id='host-observation',at=NOW,now=NOW);values.update(kw);return c.q.record_disposition(receipt,**values)


def ownkey(c):
 rows=c.s.rows("SELECT key FROM runtime WHERE key LIKE 'customer-followup:v1:%'")
 return rows[0]['key'] if rows else c.q._scope()[1]


def raw(c):
 row=c.s.execute('SELECT value FROM runtime WHERE key=?',(ownkey(c),)).fetchone();return row[0] if row else None


def source_raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.source.runtime_key,)).fetchone()[0]


def spies(c,monkeypatch):
 calls=[];proof=CustomerIngest.source_proof;preview=CustomerIngest.preview_unanswered;post=core.Disposition.__post_init__
 def source(self,**kw):calls.append(('proof',kw));return proof(self,**kw)
 def evaluate(self,*a,**kw):calls.append(('preview',kw));return preview(self,*a,**kw)
 dispositions=[]
 def disposition(self):dispositions.append(self);return post(self)
 monkeypatch.setattr(CustomerIngest,'source_proof',source);monkeypatch.setattr(CustomerIngest,'preview_unanswered',evaluate);monkeypatch.setattr(core.Disposition,'__post_init__',disposition)
 return calls,dispositions


def authorizer(c):
 queries=[]
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE):assert table=='runtime'
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION):pytest.fail('unexpected SQL '+str((action,table,column)))
  return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(guard);c.s.db.set_trace_callback(queries.append);return queries


@pytest.mark.parametrize('state',['acknowledged','resolved'])
def test_actual_public_ingest_candidate_explicit_disposition_restart_suppresses_new_incoming_visible(case,monkeypatch,state):
 calls,constructed=spies(case,monkeypatch);original=source_raw(case);domain={t:case.s.rows('SELECT * FROM '+t) for t in ('tasks','sources','events','outbox','deliveries','usage')};queries=authorizer(case)
 view=refresh(case);assert type(view.preview) is core.Preview and len(view.preview.candidates)==1 and view.receipt and not view.live_authorized
 assert len(calls)==4 and [name for name,kw in calls].count('preview')==1
 calls.clear();result=record(case,view.receipt,state=state)
 assert result.state==state and not result.already_recorded and result.local_processing_only and not result.live_authorized
 assert len(calls)==6 and len([n for n,kw in calls if n=='preview'])==2
 assert all(type(row) is core.Disposition and row.conversation==case.conv for row in constructed)
 assert source_raw(case)==original and len(case.p.calls)==1
 assert all('customer-followup:v1:' in q for q in queries if q.startswith(('INSERT ','UPDATE ')))
 assert all(('customer-ingest:v1:' in q or 'customer-followup:v1:' in q) for q in queries if q.startswith('SELECT '))
 stored=json.loads(raw(case));assert stored['current'] is None and stored['records'][0]['observations'][0]['state']==state
 assert 'Проверьте диалог' not in raw(case) and 'draft' not in raw(case)
 case.s.db.set_authorizer(None)
 for t,before in domain.items():assert case.s.rows('SELECT * FROM '+t)==before
 case.reopen();assert refresh(case).receipt is None
 case.admit([event('later-incoming',at=NOW-timedelta(minutes=90))]);new=refresh(case)
 assert new.receipt and new.receipt.candidate_id!=view.receipt.candidate_id and new.receipt.target_id=='later-incoming'
 with pytest.raises(module.FollowupUnavailable):record(case,view.receipt,observation_id='old-receipt')
 assert new.receipt.target_id=='later-incoming'


def test_explicit_ack_to_resolved_immutable_prior_receipt_and_no_regression(case):
 receipt=refresh(case).receipt;record(case,receipt);assert refresh(case).receipt is None
 resolved=record(case,receipt,state='resolved',observation_id='resolution',at=NOW+timedelta(minutes=1),now=NOW+timedelta(minutes=1))
 assert resolved.state=='resolved' and not resolved.already_recorded
 before=raw(case)
 with pytest.raises(module.FollowupUnavailable):record(case,receipt,observation_id='reack',now=NOW+timedelta(minutes=1))
 assert raw(case)==before and [r['state'] for r in json.loads(before)['records'][0]['observations']]==['acknowledged','resolved']


def test_exact_retry_later_now_zero_writes_and_committed_clock_boundary(case):
 receipt=refresh(case).receipt;record(case,receipt);before=raw(case);changes=case.s.db.total_changes
 retry=record(case,receipt,now=NOW+timedelta(hours=1));assert retry.already_recorded and raw(case)==before and case.s.db.total_changes==changes
 assert json.loads(raw(case))['last_now']==NOW.isoformat() # readonly retry doesn't advance committed clock
 assert refresh(case,now=NOW+timedelta(minutes=1)).receipt is None
 with pytest.raises(module.FollowupUnavailable):refresh(case,now=NOW)


@pytest.mark.parametrize('change',['state','evidence','at','receipt'])
def test_conflicting_observation_id_reuse_whole_refusal(case,change):
 receipt=refresh(case).receipt;record(case,receipt);before=raw(case);kw={};chosen=receipt
 if change=='state':kw['state']='resolved'
 elif change=='evidence':kw['evidence_id']='other'
 elif change=='at':kw.update(at=NOW+timedelta(seconds=1),now=NOW+timedelta(seconds=1))
 else:chosen=replace(receipt,target_id='other')
 with pytest.raises(module.FollowupUnavailable):record(case,chosen,**kw)
 assert raw(case)==before


def test_identical_refresh_reuses_receipt_zero_write_no_autoack(case):
 view=refresh(case);before=raw(case);changes=case.s.db.total_changes;again=refresh(case)
 assert again.receipt==view.receipt and raw(case)==before and case.s.db.total_changes==changes
 assert json.loads(before)['records']==[]


def test_metadata_only_source_proof_exact_store_and_absent_state(case):
 proof=case.ingest.source_proof(now=NOW,expected_store=case.s)
 assert type(proof) is SourceProof and {f.name for f in fields(proof)}=={'source_id','business','channel','registration_fingerprint','state_digest','state_present'}
 assert proof.registration_fingerprint==registration_fingerprint(case.source) and proof.state_present is True
 for forbidden in ('registration','account_id','cursor','events','provider','store'):assert not hasattr(proof,forbidden)
 with pytest.raises(FrozenInstanceError):proof.state_present=False
 copied=object.__new__(Store);copied.db=case.s.db
 with pytest.raises(Exception):case.ingest.source_proof(now=NOW,expected_store=copied)
 case.s.execute('DELETE FROM runtime WHERE key=?',(case.source.runtime_key,));empty=case.ingest.source_proof(now=NOW,expected_store=case.s)
 assert empty.state_present is False and empty.state_digest!=proof.state_digest


@pytest.mark.parametrize('method',['refresh','record'])
def test_disabled_queue_zero_sql_or_i63_calls(case,monkeypatch,method):
 receipt=refresh(case).receipt
 case.q=queue(case,enabled=False)
 monkeypatch.setattr(CustomerIngest,'source_proof',lambda *a,**kw:pytest.fail('disabled public proof'))
 case.s.db.set_authorizer(lambda *a:pytest.fail('disabled SQL'))
 with pytest.raises(module.FollowupUnavailable):refresh(case) if method=='refresh' else record(case,receipt)


def test_disabled_ingest_not_bypassed_through_private_helpers(case):
 case.ingest=CustomerIngest(store=case.s,current_source=case.current,provider=case.p);case.q=queue(case)
 with pytest.raises(module.FollowupUnavailable):refresh(case)
 assert raw(case) is None and len(case.p.calls)==1


@pytest.mark.parametrize('dimension',['customer','thread','account','channel','business','revision'])
def test_exact_source_and_conversation_isolation_no_foreign_rows(case,dimension):
 source=case.source;conv=case.conv
 if dimension=='customer':conv=replace(conv,customer_id='foreign')
 elif dimension=='thread':conv=replace(conv,thread_id='foreign')
 else:
  field={'account':'account_id','channel':'channel','business':'business','revision':'revision'}[dimension]
  source=replace(source,**{field:2 if dimension=='revision' else 'foreign'})
 case.q=queue(case,source=source,conversation=conv)
 if dimension in ('customer','thread'):assert refresh(case).receipt is None
 else:
  with pytest.raises(module.FollowupUnavailable):refresh(case)
 assert len(case.p.calls)==1


@pytest.mark.parametrize('change',['revoked','expired','revision','store','ingest','source','conversation'])
def test_current_source_or_captured_identity_drift_denies(case,change):
 receipt=refresh(case).receipt;before=raw(case)
 if change=='revoked':case.state['source']=replace(case.source,revoked=True)
 elif change=='expired':case.state['source']=replace(case.source,expires_at=NOW)
 elif change=='revision':case.state['source']=replace(case.source,revision=2)
 elif change=='store':case.q._store=copy.copy(case.s)
 elif change=='ingest':case.q._ingest=copy.copy(case.ingest)
 elif change=='source':case.q._source=copy.copy(case.source)
 else:case.q._conversation=copy.copy(case.conv)
 with pytest.raises(module.FollowupUnavailable):record(case,receipt)
 assert raw(case)==before


@pytest.mark.parametrize('event_row',[event('reply',direction='outgoing',at=NOW-timedelta(hours=1)),event('newer',at=NOW-timedelta(minutes=90)),event('uncertain',at=NOW-timedelta(minutes=30),confirmed=False)])
def test_changed_ingest_provenance_never_closes_old_receipt_or_falls_back(case,event_row):
 receipt=refresh(case).receipt;before=raw(case);case.admit([event_row])
 with pytest.raises(module.FollowupUnavailable):record(case,receipt)
 assert raw(case)==before


@pytest.mark.parametrize('field,value',[('target_id','forged'),('candidate_id','forged'),('kind','promise'),('local_revision',True),('prepared_at',NOW-timedelta(seconds=1)),('scope_fingerprint','f'*64),('evidence_ids',('forged',)),('stale_after',timedelta(minutes=1))])
def test_altered_receipt_fields_are_not_authority(case,field,value):
 receipt=refresh(case).receipt;before=raw(case)
 with pytest.raises(module.FollowupUnavailable):record(case,replace(receipt,**{field:value}))
 assert raw(case)==before


@pytest.mark.parametrize('kwargs',[dict(at=NOW-timedelta(seconds=1)),dict(at=NOW+timedelta(seconds=1)),dict(now=NOW-timedelta(seconds=1)),dict(at=NOW.replace(tzinfo=None)),dict(state='done'),dict(observation_id=''),dict(evidence_id='x'*129)])
def test_explicit_observation_bounds_and_no_guessed_outcome(case,kwargs):
 receipt=refresh(case).receipt;before=raw(case)
 with pytest.raises(module.FollowupUnavailable):record(case,receipt,**kwargs)
 assert raw(case)==before


@pytest.mark.parametrize('kwargs',[dict(now=NOW.replace(tzinfo=None)),dict(window=core.Window(NOW-timedelta(days=367),NOW)),dict(stale_after=timedelta(0)),dict(stale_after=timedelta(days=367))])
def test_refresh_closed_time_window_threshold_bounds(case,kwargs):
 with pytest.raises(module.FollowupUnavailable):refresh(case,**kwargs)
 assert raw(case) is None


@pytest.mark.parametrize('kind',['write_failure','cas','postwrite_source','postwrite_host'])
def test_queue_atomic_rollback_does_not_write_ingest_or_partial_observation(case,monkeypatch,kind):
 receipt=refresh(case).receipt;before=raw(case);source=source_raw(case);actual=case.s.execute;done=[]
 def execute(sql,args=()):
  if sql.startswith('UPDATE runtime SET value=? WHERE key=? AND value=?') and not done:
   done.append(True)
   if kind=='cas':actual('UPDATE runtime SET value=? WHERE key=?',(before+' ',ownkey(case)))
   result=actual(sql,args)
   if kind=='write_failure':raise sqlite3.OperationalError('PRIVATE_SENTINEL')
   if kind=='postwrite_source':case.state['source']=replace(case.source,revoked=True)
   if kind=='postwrite_host':case.q._ingest=copy.copy(case.ingest)
   return result
  return actual(sql,args)
 monkeypatch.setattr(case.s,'execute',execute)
 with pytest.raises(module.FollowupUnavailable,match='^customer_followup_unavailable$'):record(case,receipt)
 assert raw(case)==before and source_raw(case)==source and not case.s.db.in_transaction


def test_source_changed_by_actual_evaluator_between_proofs_is_fatal(case,monkeypatch):
 receipt=refresh(case).receipt;before=raw(case);actual=core.unanswered
 def evaluate(*a,**kw):
  result=actual(*a,**kw);case.state['source']=replace(case.source,revoked=True);return result
 monkeypatch.setattr(core,'unanswered',evaluate)
 with pytest.raises(module.FollowupUnavailable):record(case,receipt)
 assert raw(case)==before


@pytest.mark.parametrize('malformed',['blob','oversize','bad_json','extra','duplicate_keys','clock','scope','bool_revision','bad_receipt','records65','observations129','wrong_observation_state'])
def test_malformed_queue_whole_refusal_no_reset(case,malformed):
 receipt=refresh(case).receipt;record(case,receipt);state=json.loads(raw(case))
 if malformed=='blob':value=b'blob'
 elif malformed=='oversize':value='x'*65537
 elif malformed=='bad_json':value='{broken'
 elif malformed=='duplicate_keys':value='{"version":1,"version":1}'
 else:
  if malformed=='extra':state['draft']='not allowed'
  elif malformed=='clock':state['last_now']='2026-10-19T12:00:00'
  elif malformed=='scope':state['scope']['business']='B'
  elif malformed=='bool_revision':state['revision']=True
  elif malformed=='bad_receipt':state['records'][0]['receipt']['source_proof']['state_present']=1
  elif malformed=='records65':state['records']*=65
  elif malformed=='observations129':state['records'][0]['observations']*=129
  elif malformed=='wrong_observation_state':state['records'][0]['observations'][0]['state']='sent'
  value=json.dumps(state)
 key=ownkey(case);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,key));before=case.s.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()[0]
 with pytest.raises(module.FollowupUnavailable):refresh(case)
 assert case.s.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()[0]==before


@pytest.mark.parametrize('budget',['MAX_BYTES','MAX_ROWS','MAX_QUERIES'])
def test_cumulative_queue_reader_budget_fails_without_mutation(case,monkeypatch,budget):
 refresh(case);before=raw(case);monkeypatch.setattr(module,budget,0)
 with pytest.raises(module.FollowupUnavailable):refresh(case)
 assert raw(case)==before


def test_active_caller_transaction_not_committed_or_rolled_back(case):
 case.s.execute('BEGIN')
 with pytest.raises(module.FollowupUnavailable):refresh(case)
 assert case.s.db.in_transaction;case.s.execute('ROLLBACK')


def test_explicit_resolution_cannot_predate_saved_ack_even_with_later_now(case):
 receipt=refresh(case).receipt
 record(case,receipt,at=NOW+timedelta(minutes=2),now=NOW+timedelta(minutes=2));before=raw(case)
 with pytest.raises(module.FollowupUnavailable):record(case,receipt,state='resolved',observation_id='resolution',at=NOW+timedelta(minutes=1),now=NOW+timedelta(minutes=3))
 assert raw(case)==before


def test_persisted_reversed_ack_resolution_chronology_whole_refusal(case):
 receipt=refresh(case).receipt;record(case,receipt,at=NOW+timedelta(minutes=2),now=NOW+timedelta(minutes=2))
 record(case,receipt,state='resolved',observation_id='resolution',at=NOW+timedelta(minutes=3),now=NOW+timedelta(minutes=3))
 state=json.loads(raw(case));state['records'][0]['observations'][1]['at']=(NOW+timedelta(minutes=1)).isoformat()
 value=json.dumps(state);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,ownkey(case)))
 with pytest.raises(module.FollowupUnavailable):refresh(case,now=NOW+timedelta(minutes=4))
 assert raw(case)==value


def test_valid_full_record_capacity_preserves_unsuppressed_anchor_without_eviction(case):
 view=refresh(case);state=json.loads(raw(case));records=[]
 for i in range(64):
  receipt=replace(view.receipt,candidate_id='closed-'+str(i),target_id='t'+str(i),evidence_ids=('e'+str(i),))
  records.append(dict(receipt=module._receipt_data(receipt),observations=[dict(id='a'+str(i),state='acknowledged',evidence_id='e'+str(i),at=NOW.isoformat())]))
 state['records']=records;state['current']=None
 value=module._json(state)
 assert len(value.encode())<=65536
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,ownkey(case)))
 with pytest.raises(module.FollowupUnavailable):refresh(case)
 assert raw(case)==value and len(json.loads(raw(case))['records'])==64 and len(case.p.calls)==1


@pytest.mark.parametrize('change',['window','threshold'])
def test_empty_refresh_query_change_is_persisted_and_old_ack_cannot_resolve(case,change):
 receipt=refresh(case).receipt;record(case,receipt);before=json.loads(raw(case));kwargs={}
 if change=='window':kwargs['window']=core.Window(NOW-timedelta(minutes=30),NOW+timedelta(hours=1))
 else:kwargs['stale_after']=timedelta(minutes=30)
 empty=refresh(case,**kwargs);saved=raw(case)
 assert empty.receipt is None and not empty.preview.candidates
 assert json.loads(saved)['query']!=before['query'] and json.loads(saved)['records']==before['records']
 with pytest.raises(module.FollowupUnavailable):record(case,receipt,state='resolved',observation_id='resolution')
 assert raw(case)==saved
 # Exact immutable prior observation retry is still an idempotent read, not a new transition.
 retry=record(case,receipt)
 assert retry.already_recorded and raw(case)==saved
