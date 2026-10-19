"""Actual admitted-event promise registration/restart/linked result suppression."""
from dataclasses import replace,fields,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import copy
import json
import sqlite3
import pytest
from goldway import customer_promise_register as module
from goldway.customer_ingest import CustomerIngest,SourceRegistration,PromiseEvaluation
from goldway.store import Store
from goldway.engine import Engine
from goldway.knowledge import KnowledgeIndex
from goldway.intelligence import Intelligence
from goldway.telegram import Dispatcher

core=module.core
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)


class Provider:
 def __init__(self):self.pages=[];self.calls=[];self.allowed=False
 def fetch(self,cursor,limit=32):
  assert self.allowed,'register fetched provider'
  self.calls.append((cursor,limit));return self.pages.pop(0)


def event(identifier='declaration',**kw):
 value=dict(event_id=identifier,customer_id='customer',thread_id='thread',at=NOW-timedelta(hours=2),direction='outgoing',confirmed=True,reply_to=None);value.update(kw);return value


def register(c,**kw):
 args=dict(store=c.s,ingest=c.ingest,source=c.source,conversation=c.conv,enabled=True);args.update(kw);return module.CustomerPromiseRegister(**args)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));provider=Provider()
 source=SourceRegistration('A','fake','synthetic','synthetic-account',1,NOW-timedelta(days=2),NOW+timedelta(days=2));state={'source':source}
 def current():return state['source']
 c=SimpleNamespace(s=s,path=path,p=provider,source=source,state=state,current=current,cursor=None)
 c.ingest=CustomerIngest(store=s,current_source=current,provider=provider,enabled=True);c.conv=core.Conversation('customer','synthetic','thread',source.source_id)
 def admit(rows=None,now=NOW):
  provider.pages.append(dict(mode='incremental',cursor_before=c.cursor,cursor_after='cursor-'+str(len(provider.calls)+1),events=[event()] if rows is None else rows));provider.allowed=True
  try:receipt=c.ingest.ingest_once(now=now)
  finally:provider.allowed=False
  c.cursor=receipt.cursor;return receipt
 c.admit=admit;admit();c.r=register(c);c.register_key=c.r._scope()[1]
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close()
  c.ingest=CustomerIngest(store=c.s,current_source=current,provider=provider,enabled=True);c.r=register(c)
 c.reopen=reopen
 def fail(*a,**kw):pytest.fail('unexpected constructor/provider/network/model/send/config operation')
 for cls in (Store,Engine,KnowledgeIndex,Intelligence,Dispatcher):monkeypatch.setattr(cls,'__init__',fail)
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def promise(c,**kw):
 args=dict(id='promise',conversation=c.conv,event_id='declaration',owner_id='opaque-local-owner',due_at=NOW+timedelta(hours=1),confirmed=True);args.update(kw);return core.Promise(**args)


def result(c,**kw):
 args=dict(id='result',conversation=c.conv,promise_id='promise',event_id='result-event',confirmed=True);args.update(kw);return core.PromiseResult(**args)


def add(c,p=None,now=NOW):return c.r.record_promise(p or promise(c),now=now)


def preview(c,now=NOW):return c.r.preview_due(now=now)


def ownkey(c):return c.register_key


def raw(c):
 row=c.s.execute('SELECT value FROM runtime WHERE key=?',(ownkey(c),)).fetchone();return row[0] if row else None


def source_raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.source.runtime_key,)).fetchone()[0]


def spies(monkeypatch):
 calls=[];evaluate=CustomerIngest.preview_promises;proof=CustomerIngest.source_proof;actual=core.promise_preview
 def public(self,*a,**kw):calls.append(('public',kw));return evaluate(self,*a,**kw)
 def source(self,*a,**kw):calls.append(('proof',kw));return proof(self,*a,**kw)
 def rules(*a,**kw):calls.append(('core',kw));assert kw['dispositions']==();return actual(*a,**kw)
 monkeypatch.setattr(CustomerIngest,'preview_promises',public);monkeypatch.setattr(CustomerIngest,'source_proof',source);monkeypatch.setattr(core,'promise_preview',rules)
 return calls


def authorizer(c):
 queries=[]
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE):assert table=='runtime'
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION):pytest.fail('unexpected SQL '+str((action,table,column)))
  return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(guard);c.s.db.set_trace_callback(queries.append);return queries


def test_real_admit_register_restart_due_later_linked_result_suppresses_only_its_promise(case,monkeypatch):
 case.admit([event('second-declaration',at=NOW-timedelta(minutes=90))]);calls=spies(monkeypatch);source=source_raw(case)
 domain={t:case.s.rows('SELECT * FROM '+t) for t in ('tasks','sources','events','outbox','deliveries','usage')};queries=authorizer(case)
 first=add(case);second=add(case,promise(case,id='second',event_id='second-declaration',owner_id='another-local-owner',due_at=NOW+timedelta(minutes=30)))
 assert first.requires_review and not first.live_authorized and not first.already_recorded
 assert source_raw(case)==source and all('customer-promises:v1:' in q for q in queries if q.startswith(('INSERT ','UPDATE ')))
 assert len([n for n,kw in calls if n=='public'])==2 and len([n for n,kw in calls if n=='core'])==2 and len([n for n,kw in calls if n=='proof'])==6
 saved=json.loads(raw(case));original_seal=saved['anchors']['declaration'];original_proof=saved['promises'][0]['source_proof']
 case.s.db.set_authorizer(None);case.reopen()
 due=preview(case,NOW+timedelta(hours=1));assert {c.target_id for c in due.preview.candidates}=={'promise','second'}
 assert type(due) is PromiseEvaluation and due.requires_review and not due.live_authorized
 assert due.preview.candidates[0].draft_only is True and not hasattr(due,'events') and not hasattr(due,'registration')
 case.admit([event('result-event',at=NOW+timedelta(hours=2))],now=NOW+timedelta(hours=2));source=source_raw(case)
 recorded=case.r.record_result(result(case),now=NOW+timedelta(hours=2));assert not recorded.already_recorded
 current=json.loads(raw(case));assert current['anchors']['declaration']==original_seal and current['promises'][0]['source_proof']==original_proof
 assert current['results'][0]['source_proof']['state_digest']!=original_proof['state_digest'] and source_raw(case)==source
 case.reopen();after=preview(case,NOW+timedelta(hours=2));assert [c.target_id for c in after.preview.candidates]==['second']
 assert len(case.p.calls)==3
 for table,before in domain.items():assert case.s.rows('SELECT * FROM '+table)==before
 assert 'Проверьте результат' not in raw(case) and 'draft' not in raw(case)
 with pytest.raises(FrozenInstanceError):recorded.record_id='forged'


@pytest.mark.parametrize('change',['none_due','unconfirmed_promise','unconfirmed_event','future_due','future_event'])
def test_actual_core_unknown_future_and_blocked_states_not_inferred(case,change):
 p=promise(case)
 if change=='none_due':p=replace(p,due_at=None)
 elif change=='unconfirmed_promise':p=replace(p,confirmed=False)
 elif change=='unconfirmed_event':
  case.admit([event('unconfirmed',confirmed=False)]);p=replace(p,event_id='unconfirmed')
 elif change=='future_due':p=replace(p,due_at=NOW+timedelta(days=1))
 else:case.admit([event('future-declaration',at=NOW+timedelta(hours=1))]);p=replace(p,event_id='future-declaration',due_at=NOW+timedelta(hours=2))
 add(case,p);view=preview(case)
 assert not view.preview.candidates
 assert bool(view.preview.blocked)==(change in ('none_due','unconfirmed_promise','unconfirmed_event'))


@pytest.mark.parametrize('confirmed,event_confirmed,at',[(False,True,NOW-timedelta(hours=1)),(True,False,NOW-timedelta(hours=1)),(True,True,NOW+timedelta(hours=1))])
def test_actual_result_confirmation_and_future_cutoff_preserved(case,confirmed,event_confirmed,at):
 add(case,promise(case,due_at=NOW));case.admit([event('result-event',confirmed=event_confirmed,at=at)])
 case.r.record_result(result(case,confirmed=confirmed),now=NOW);view=preview(case)
 assert [c.target_id for c in view.preview.candidates]==['promise']
 if at>NOW:assert preview(case,at).preview.candidates==()


@pytest.mark.parametrize('kind',['wrong_promise','incoming_result','equal_time','older_result','cross_conversation'])
def test_actual_wrong_or_chronologically_invalid_result_never_guessed(case,kind):
 add(case);row=event('result-event',at=NOW-timedelta(hours=1));record=result(case)
 if kind=='wrong_promise':record=replace(record,promise_id='missing')
 elif kind=='incoming_result':row['direction']='incoming'
 elif kind=='equal_time':row['at']=NOW-timedelta(hours=2)
 elif kind=='older_result':row['at']=NOW-timedelta(hours=3)
 else:row['thread_id']='foreign'
 case.admit([row]);before=raw(case)
 with pytest.raises(module.PromiseRegisterUnavailable):case.r.record_result(record,now=NOW)
 assert raw(case)==before


def test_exact_retry_after_source_advance_zero_write_conflicting_payload_refuses(case):
 p=promise(case);add(case,p);case.admit([event('unrelated',thread_id='foreign')]);before=raw(case);changes=case.s.db.total_changes
 retry=add(case,p,now=NOW+timedelta(minutes=1));assert retry.already_recorded and raw(case)==before and case.s.db.total_changes==changes
 for changed in (replace(p,owner_id='other'),replace(p,due_at=NOW+timedelta(hours=2)),replace(p,confirmed=False)):
  with pytest.raises(module.PromiseRegisterUnavailable):add(case,changed,now=NOW+timedelta(minutes=1))
 assert raw(case)==before


def test_record_and_anchor_ids_immutable_shared_result_event_refuses(case):
 case.admit([event('second-declaration',at=NOW-timedelta(minutes=90)),event('result-event',at=NOW-timedelta(minutes=30))]);add(case);add(case,promise(case,id='second',event_id='second-declaration'))
 case.r.record_result(result(case),now=NOW);before=raw(case)
 with pytest.raises(module.PromiseRegisterUnavailable):case.r.record_result(result(case,id='other-result',promise_id='second'),now=NOW)
 with pytest.raises(module.PromiseRegisterUnavailable):add(case,promise(case,id='other-promise'))
 with pytest.raises(module.PromiseRegisterUnavailable):case.r.record_result(result(case,id='promise'),now=NOW)
 assert raw(case)==before


@pytest.mark.parametrize('change',['at','confirmed','direction','customer','missing'])
def test_changed_or_missing_old_anchor_under_newer_state_is_not_rebuilt(case,change):
 add(case);before=raw(case);case.admit([event('unrelated',thread_id='foreign')])
 state=json.loads(source_raw(case));old=state['events'][0]
 if change=='at':old['at']=(NOW-timedelta(hours=2,minutes=1)).isoformat()
 elif change=='confirmed':old['confirmed']=False
 elif change=='direction':old['direction']='incoming'
 elif change=='customer':old['customer_id']='foreign'
 else:state['events']=state['events'][1:]
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.source.runtime_key))
 with pytest.raises(module.PromiseRegisterUnavailable):preview(case,NOW+timedelta(hours=2))
 assert raw(case)==before


@pytest.mark.parametrize('method',['promise','result','preview'])
def test_disabled_register_zero_sql_public_calls(case,monkeypatch,method):
 case.r=register(case,enabled=False);monkeypatch.setattr(CustomerIngest,'source_proof',lambda *a,**kw:pytest.fail('disabled proof'));case.s.db.set_authorizer(lambda *a:pytest.fail('disabled SQL'))
 with pytest.raises(module.PromiseRegisterUnavailable):
  if method=='promise':add(case)
  elif method=='result':case.r.record_result(result(case),now=NOW)
  else:preview(case)


def test_disabled_i63_cannot_be_bypassed_and_wrong_store_copy_refuses(case):
 case.ingest=CustomerIngest(store=case.s,current_source=case.current,provider=case.p);case.r=register(case)
 with pytest.raises(module.PromiseRegisterUnavailable):add(case)
 assert raw(case) is None
 case.ingest=CustomerIngest(store=case.s,current_source=case.current,provider=case.p,enabled=True)
 other=object.__new__(Store);other.db=case.s.db;case.r=register(case,store=other)
 with pytest.raises(module.PromiseRegisterUnavailable):add(case)


@pytest.mark.parametrize('dimension',['customer','thread','account','channel','business','revision'])
def test_exact_scope_no_foreign_anchor_or_payload(case,dimension):
 source=case.source;conv=case.conv
 if dimension=='customer':conv=replace(conv,customer_id='foreign')
 elif dimension=='thread':conv=replace(conv,thread_id='foreign')
 else:
  name={'account':'account_id','channel':'channel','business':'business','revision':'revision'}[dimension];source=replace(source,**{name:2 if dimension=='revision' else 'foreign'})
 case.r=register(case,source=source,conversation=conv)
 with pytest.raises(module.PromiseRegisterUnavailable):add(case,replace(promise(case),conversation=conv))
 assert len(case.p.calls)==1


@pytest.mark.parametrize('change',['revoked','expired','store','connection','ingest','source','conversation'])
def test_current_source_and_captured_identity_drift(case,change):
 add(case);before=raw(case)
 if change=='revoked':case.state['source']=replace(case.source,revoked=True)
 elif change=='expired':case.state['source']=replace(case.source,expires_at=NOW)
 elif change=='store':case.r._store=copy.copy(case.s)
 elif change=='connection':case.r._host=(case.s,object(),*case.r._host[2:])
 elif change=='ingest':case.r._ingest=copy.copy(case.ingest)
 elif change=='source':case.r._source=copy.copy(case.source)
 else:case.r._conversation=copy.copy(case.conv)
 with pytest.raises(module.PromiseRegisterUnavailable):preview(case)
 assert raw(case)==before


@pytest.mark.parametrize('fault',['write','cas','postwrite_source','postwrite_host'])
def test_full_atomic_cas_failure_rolls_back_register_only(case,monkeypatch,fault):
 add(case);case.admit([event('result-event',at=NOW-timedelta(hours=1))]);before=raw(case);source=source_raw(case);actual=case.s.execute;done=[]
 def execute(sql,args=()):
  if sql.startswith('UPDATE runtime SET value=? WHERE key=? AND value=?') and not done:
   done.append(True)
   if fault=='cas':actual('UPDATE runtime SET value=? WHERE key=?',(before+' ',ownkey(case)))
   result=actual(sql,args)
   if fault=='write':raise sqlite3.OperationalError('PRIVATE_SENTINEL')
   if fault=='postwrite_source':case.state['source']=replace(case.source,revoked=True)
   if fault=='postwrite_host':case.r._ingest=copy.copy(case.ingest)
   return result
  return actual(sql,args)
 monkeypatch.setattr(case.s,'execute',execute)
 with pytest.raises(module.PromiseRegisterUnavailable,match='^customer_promise_register_unavailable$'):case.r.record_result(result(case),now=NOW)
 assert raw(case)==before and source_raw(case)==source and not case.s.db.in_transaction


def test_readonly_due_proof_rechecks_after_actual_core(case,monkeypatch):
 add(case);actual=core.promise_preview
 def evaluate(*a,**kw):
  result=actual(*a,**kw);case.state['source']=replace(case.source,revoked=True);return result
 monkeypatch.setattr(core,'promise_preview',evaluate)
 with pytest.raises(module.PromiseRegisterUnavailable):preview(case)


@pytest.mark.parametrize('malformed',['blob','oversize','bad_json','extra','duplicate_keys','bad_clock','bad_scope','bool_revision','bad_owner','bad_proof','bad_seal','missing_seal','extra_seal','promises9','results17'])
def test_malformed_persistent_records_whole_refusal_no_reset(case,malformed):
 add(case);state=json.loads(raw(case))
 if malformed=='blob':value=b'blob'
 elif malformed=='oversize':value='x'*65537
 elif malformed=='bad_json':value='{bad'
 elif malformed=='duplicate_keys':value='{"version":1,"version":1}'
 else:
  if malformed=='extra':state['draft']='forbidden'
  elif malformed=='bad_clock':state['last_write_at']='2026-10-19T12:00:00'
  elif malformed=='bad_scope':state['scope_fingerprint']='wrong'
  elif malformed=='bool_revision':state['revision']=True
  elif malformed=='bad_owner':state['promises'][0]['payload']['owner_ref']=None
  elif malformed=='bad_proof':state['promises'][0]['source_proof']['state_present']=1
  elif malformed=='bad_seal':state['anchors']['declaration']='x'
  elif malformed=='missing_seal':state['anchors']={}
  elif malformed=='extra_seal':state['anchors']['unused']='f'*64
  elif malformed=='promises9':state['promises']*=9
  else:state['results']=[{}]*17
  value=json.dumps(state)
 key=ownkey(case);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,key));before=case.s.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()[0]
 with pytest.raises(module.PromiseRegisterUnavailable):preview(case)
 assert case.s.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()[0]==before


def test_actual_capacity_eight_promises_sixteen_results_24_anchors_no_eviction(case):
 declarations=[event('p'+str(i)) for i in range(9)];results=[event('r'+str(i),at=NOW-timedelta(hours=1)) for i in range(17)]
 case.admit(declarations+results)
 for i in range(8):add(case,promise(case,id='promise-'+str(i),event_id='p'+str(i)))
 for i in range(16):case.r.record_result(result(case,id='result-'+str(i),promise_id='promise-'+str(i%8),event_id='r'+str(i)),now=NOW)
 value=raw(case);state=json.loads(value);assert len(state['promises'])==8 and len(state['results'])==16 and len(state['anchors'])==24
 with pytest.raises(module.PromiseRegisterUnavailable):add(case,promise(case,id='ninth',event_id='p8'))
 with pytest.raises(module.PromiseRegisterUnavailable):case.r.record_result(result(case,id='seventeenth',event_id='r16',promise_id='promise-0'),now=NOW)
 assert raw(case)==value


@pytest.mark.parametrize('budget',['MAX_BYTES','MAX_ROWS','MAX_QUERIES'])
def test_register_cumulative_reader_caps_preserve_row(case,monkeypatch,budget):
 add(case);before=raw(case);monkeypatch.setattr(module,budget,0)
 with pytest.raises(module.PromiseRegisterUnavailable):preview(case)
 assert raw(case)==before


def test_clock_and_explicit_aware_owner_bounds_no_defaults(case):
 add(case);before=raw(case)
 with pytest.raises(module.PromiseRegisterUnavailable):preview(case,NOW-timedelta(seconds=1))
 with pytest.raises(module.PromiseRegisterUnavailable):add(case,now=NOW.replace(tzinfo=None))
 assert raw(case)==before


def test_source_evaluation_is_metadata_only_readonly_and_one_core_call(case,monkeypatch):
 calls=spies(monkeypatch);before=source_raw(case);changes=case.s.db.total_changes
 evaluation=case.ingest.preview_promises(case.conv,now=NOW,expected_store=case.s,promises=(promise(case),),results=())
 assert len([n for n,kw in calls if n=='core'])==1 and [name for name,kw in calls].count('proof')==0
 assert {f.name for f in fields(evaluation)}=={'preview','source_proof','anchor_seals','requires_review','live_authorized'}
 assert evaluation.anchor_seals[0][0]=='declaration' and len(evaluation.anchor_seals[0][1])==64
 assert source_raw(case)==before and case.s.db.total_changes==changes and len(case.p.calls)==1
 with pytest.raises(Exception):case.ingest.preview_promises(case.conv,now=NOW,expected_store=object(),promises=(),results=())


def test_active_caller_transaction_write_refused_without_own_commit(case):
 case.s.execute('BEGIN')
 with pytest.raises(module.PromiseRegisterUnavailable):add(case)
 assert case.s.db.in_transaction;case.s.execute('ROLLBACK')
