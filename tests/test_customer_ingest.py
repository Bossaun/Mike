"""Actual metadata persistence/cursor bridge and actual readonly customer evaluator."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import copy
import json
import sqlite3
import pytest
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway import customer_ingest as module

core=module.core
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)


class FakeProvider:
 def __init__(self,store):self.store=store;self.pages=[];self.calls=[];self.after_fetch=None
 def fetch(self,cursor,limit=32):
  assert not self.store.db.in_transaction
  self.calls.append((cursor,limit))
  page=self.pages.pop(0)
  if self.after_fetch:self.after_fetch()
  if isinstance(page,Exception):raise page
  return page


def registration(**kw):
 values=dict(business='A',provider_id='fake-provider',channel='synthetic',account_id='local-account',revision=1,valid_from=NOW-timedelta(days=1),expires_at=NOW+timedelta(days=1))
 values.update(kw);return module.SourceRegistration(**values)


def event(identifier='e1',**kw):
 values=dict(event_id=identifier,customer_id='customer',thread_id='thread',at=NOW-timedelta(hours=2),direction='incoming',confirmed=True,reply_to=None)
 values.update(kw);return values


def page(rows=None,before=None,after='c1',mode='incremental'):
 return dict(mode=mode,cursor_before=before,cursor_after=after,events=[event()] if rows is None else rows)


@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');p=FakeProvider(s);state={'registration':registration(),'calls':0}
 def current():state['calls']+=1;return state['registration']
 driver=module.CustomerIngest(store=s,current_source=current,provider=p,enabled=True)
 def fail(*a,**kw):pytest.fail('unexpected constructor/network/provider discovery')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex):monkeypatch.setattr(cls,'__init__',fail)
 import socket,httpx,builtins
 monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(builtins,'open',fail)
 yield SimpleNamespace(s=s,p=p,state=state,current=current,driver=driver)
 s.db.set_authorizer(None);s.db.set_trace_callback(None);s.db.close()


def fresh(c,*,provider=None,current=None):return module.CustomerIngest(store=c.s,current_source=current or c.current,provider=provider or c.p,enabled=True)


def ingest(c,value=None):
 c.p.pages.append(page() if value is None else value)
 return c.driver.ingest_once(now=NOW)


def raw(c):
 row=c.s.execute('SELECT value FROM runtime WHERE key=?',(c.state['registration'].runtime_key,)).fetchone()
 return row[0] if row else None


def conversation(c,**kw):
 values=dict(customer_id='customer',channel=c.state['registration'].channel,thread_id='thread',source_id=c.state['registration'].source_id);values.update(kw)
 return core.Conversation(**values)


def preview(c,*,conv=None,dispositions=()):
 return c.driver.preview_unanswered(conv or conversation(c),now=NOW,window=core.Window(NOW-timedelta(days=1),NOW+timedelta(days=1)),stale_after=timedelta(hours=1),dispositions=dispositions)


def spy_core(monkeypatch):
 normalize=core.normalize_batch;unanswered=core.unanswered;calls=[];previews=[]
 def normalized(events,**kw):
  result=normalize(events,**kw);calls.append((events,kw,result));return result
 def evaluated(events,**kw):
  result=unanswered(events,**kw);previews.append((events,kw,result));return result
 monkeypatch.setattr(core,'normalize_batch',normalized);monkeypatch.setattr(core,'unanswered',evaluated)
 return calls,previews


def authorize(c,*,readonly=False):
 operations=[];queries=[]
 def guard(action,table,column,*args):
  if action==sqlite3.SQLITE_READ:
   assert table=='runtime' and column in ('key','value')
  elif action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE):
   assert not readonly and table=='runtime'
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION):pytest.fail('unexpected SQL operation '+str((action,table,column)))
  operations.append((action,table,column));return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(guard);c.s.db.set_trace_callback(queries.append)
 return operations,queries


def test_two_pages_actual_normalization_persisted_cursor_fresh_driver_actual_answer_closure(case,monkeypatch):
 calls,previews=spy_core(monkeypatch)
 existing={row[0] for row in case.s.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 domain=tuple(table for table in ('tasks','sources','task_sources','knowledge_documents','events','outbox','deliveries','usage','task_status_history') if table in existing)
 before={table:case.s.rows('SELECT * FROM '+table) for table in domain}
 operations,queries=authorize(case)
 first=ingest(case);assert first.cursor=='c1' and first.retained_events==1 and first.live_authorized is False
 result=preview(case);assert len(result.candidates)==1 and not result.blocked
 assert result.candidates[0].draft_only is True
 case.driver=fresh(case)
 second=ingest(case,page([event('e2',at=NOW-timedelta(hours=1),direction='outgoing',reply_to='e1')],before='c1',after='c2'))
 assert second.cursor=='c2' and second.retained_events==2 and not preview(case).candidates
 assert case.p.calls==[(None,32),('c1',32)] and len(calls)==len(previews)==2
 assert all(type(row) is core.Event for events,kw,result in calls for row in events)
 assert len(calls[1][0])==2 and calls[1][1]['cursor_before']=='c1' and calls[1][1]['cursor_after']=='c2'
 stored=json.loads(raw(case));assert stored['cursor']=='c2' and len(stored['events'])==2 and stored['last_receipt']['cursor_after']=='c2'
 assert len([q for q in queries if q.startswith('INSERT INTO runtime')])==2
 assert all('customer-ingest:v1:' in q for q in queries if q.startswith('SELECT '))
 case.s.db.set_authorizer(None)
 for table in domain:assert case.s.rows('SELECT * FROM '+table)==before[table]
 with pytest.raises(FrozenInstanceError):second.cursor='forged'


def test_exact_last_page_replay_zero_write_and_older_stale_reject(case,monkeypatch):
 calls,_=spy_core(monkeypatch);p1=page();p2=page([event('e2',direction='outgoing',at=NOW-timedelta(hours=1))],before='c1',after='c2')
 ingest(case,p1);ingest(case,p2);original=raw(case);changes=case.s.db.total_changes
 result=ingest(case,copy.deepcopy(p2));assert result.replayed and result.cursor=='c2' and result.retained_events==2 and case.s.db.total_changes==changes and raw(case)==original
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,p1)
 assert raw(case)==original and len(calls)==3


@pytest.mark.parametrize('initial',[False,True])
def test_same_cursor_empty_noop_no_runtime_write(case,initial):
 if initial:ingest(case)
 original=raw(case);changes=case.s.db.total_changes;cursor='c1' if initial else None
 result=ingest(case,page([],before=cursor,after=cursor))
 assert result.no_op and not result.replayed and result.cursor==cursor and raw(case)==original and case.s.db.total_changes==changes


def test_actual_duplicate_dedup_and_utc_equivalent_timestamps(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=event();equiv=event(at=row['at'].astimezone(timezone(timedelta(hours=8))))
 receipt=ingest(case,page([row,equiv]));assert receipt.retained_events==1 and len(calls)==1 and len(calls[0][2].events)==1
 assert json.loads(raw(case))['events'][0]['at']==row['at'].isoformat()


@pytest.mark.parametrize('field,value',[('customer_id','different'),('at',NOW-timedelta(hours=3)),('direction','outgoing'),('confirmed',False),('reply_to','parent')])
def test_actual_conflicting_identity_across_retained_and_new_rejects_atomic(case,field,value):
 ingest(case);original=raw(case)
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([event(**{field:value})],before='c1',after='c2'))
 assert raw(case)==original


@pytest.mark.parametrize('at',[NOW-timedelta(hours=2),NOW-timedelta(hours=3)])
def test_actual_retained_parent_chronology_rejected(case,at):
 ingest(case);original=raw(case)
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([event('e2',direction='outgoing',reply_to='e1',at=at)],before='c1',after='c2'))
 assert raw(case)==original


def test_absent_or_wrong_reply_parent_never_closes_newer_incoming(case):
 ingest(case,page([event('older',at=NOW-timedelta(hours=4)),event('newer',at=NOW-timedelta(hours=3)),event('out',direction='outgoing',at=NOW-timedelta(hours=2),reply_to='older')]))
 result=preview(case);assert len(result.candidates)==1 and result.candidates[0].target_id=='newer'


@pytest.mark.parametrize('confirmed',[False,True])
def test_unknown_observation_never_becomes_send_receipt(case,confirmed):
 ingest(case,page([event(),event('out',direction='outgoing',at=NOW-timedelta(hours=1),confirmed=confirmed)]))
 result=preview(case)
 if confirmed:assert not result.candidates and not result.blocked
 else:assert not result.candidates and len(result.blocked)==1 and result.blocked[0].reason=='unconfirmed_message_state'
 assert json.loads(raw(case))['events'][1]['confirmed'] is confirmed


@pytest.mark.parametrize('dimension',['business','provider_id','channel','account_id'])
def test_same_local_customer_thread_events_are_isolated_by_registered_stream(case,dimension):
 ingest(case);first_key=case.state['registration'].runtime_key;first_source=case.state['registration'].source_id
 case.state['registration']=replace(case.state['registration'],**{dimension:'B'})
 # Existing driver cannot silently switch identity, even to a valid assertion.
 with pytest.raises(module.CustomerIngestUnavailable):preview(case)
 case.driver=fresh(case);ingest(case,page([event(direction='outgoing')]))
 assert first_key!=case.state['registration'].runtime_key and first_source!=case.state['registration'].source_id
 assert not preview(case).candidates
 old=json.loads(case.s.execute('SELECT value FROM runtime WHERE key=?',(first_key,)).fetchone()[0]);assert old['events'][0]['direction']=='incoming'
 with pytest.raises(module.CustomerIngestUnavailable):preview(case,conv=conversation(case,source_id=first_source))


def test_readonly_actual_unanswered_dispositions_same_conversation_not_persisted(case,monkeypatch):
 ingest(case);candidate=preview(case).candidates[0];before=raw(case);changes=case.s.db.total_changes
 calls,previews=spy_core(monkeypatch);authorize(case,readonly=True)
 disposition=core.Disposition('ack',conversation(case),candidate.id,NOW,'acknowledged','host-observation')
 result=preview(case,dispositions=(disposition,));assert result.candidates==() and len(previews)==1 and not calls
 assert raw(case)==before and case.s.db.total_changes==changes and len(case.p.calls)==1
 with pytest.raises(module.CustomerIngestUnavailable):preview(case,dispositions=(replace(disposition,conversation=conversation(case,customer_id='foreign')),))


def test_missing_exact_conversation_empty_is_not_history_completeness_claim(case):
 assert preview(case).candidates==()
 ingest(case)
 result=preview(case,conv=conversation(case,thread_id='unseen'));assert result==core.Preview((),()) and len(case.p.calls)==1


@pytest.mark.parametrize('method',['ingest_once','preview_unanswered'])
def test_default_disabled_zero_sql_callback_provider(case,method):
 def fail():pytest.fail('disabled policy callback')
 driver=module.CustomerIngest(store=case.s,current_source=fail,provider=case.p)
 case.s.db.set_authorizer(lambda *a:pytest.fail('disabled SQL'))
 with pytest.raises(module.CustomerIngestUnavailable):
  if method=='ingest_once':driver.ingest_once(now=NOW)
  else:driver.preview_unanswered(conversation(case),now=NOW,window=core.Window(NOW-timedelta(days=1),NOW),stale_after=timedelta(hours=1),dispositions=())
 assert not case.p.calls


@pytest.mark.parametrize('change',['revoked','expired','future','revision','business','provider_id','channel','account_id','store','db','callback','provider','fetch'])
def test_current_registration_and_captured_host_drift_deny(case,monkeypatch,change):
 ingest(case);before=raw(case)
 if change=='revoked':case.state['registration']=replace(case.state['registration'],revoked=True)
 elif change=='expired':case.state['registration']=replace(case.state['registration'],expires_at=NOW)
 elif change=='future':case.state['registration']=replace(case.state['registration'],valid_from=NOW+timedelta(seconds=1))
 elif change in ('revision','business','provider_id','channel','account_id'):case.state['registration']=replace(case.state['registration'],**{change:2 if change=='revision' else 'changed'})
 elif change=='store':case.driver._store=copy.copy(case.s)
 elif change=='db':case.driver._host=(case.s,object(),*case.driver._host[2:])
 elif change=='callback':case.driver._current_source=lambda:case.state['registration']
 elif change=='provider':case.driver._provider=copy.copy(case.p)
 elif change=='fetch':monkeypatch.setattr(case.p,'fetch',lambda *a,**kw:page())
 with pytest.raises(module.CustomerIngestUnavailable):preview(case)
 assert len(case.p.calls)==1


@pytest.mark.parametrize('point',['after_fetch','before_commit'])
@pytest.mark.parametrize('change',['revoke','expiry','revision','identity'])
def test_source_policy_recheck_after_fetch_and_before_commit_rolls_back(case,point,change):
 original=case.current
 def mutate():
  if change=='revoke':case.state['registration']=replace(case.state['registration'],revoked=True)
  elif change=='expiry':case.state['registration']=replace(case.state['registration'],expires_at=NOW)
  elif change=='revision':case.state['registration']=replace(case.state['registration'],revision=2)
  else:case.state['registration']=replace(case.state['registration'],account_id='other')
 if point=='after_fetch':case.p.after_fetch=mutate
 else:
  def current():
   if case.state['calls']==2:mutate()
   return original()
  case.driver=module.CustomerIngest(store=case.s,current_source=current,provider=case.p,enabled=True)
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case)
 assert not case.s.rows('SELECT * FROM runtime') and not case.s.db.in_transaction


def test_concurrent_state_change_after_fetch_not_overwritten(case):
 ingest(case);old=json.loads(raw(case));old['cursor']='other';old['last_receipt']['cursor_after']='other';concurrent=module._json(old)
 case.p.after_fetch=lambda:case.s.execute('UPDATE runtime SET value=? WHERE key=?',(concurrent,case.state['registration'].runtime_key))
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([event('e2')],before='c1',after='c2'))
 assert raw(case)==concurrent


def test_actual_runtime_write_fault_rolls_back_events_cursor_receipt(case,monkeypatch):
 ingest(case);before=raw(case);original=case.s.execute
 def execute(sql,args=()):
  result=original(sql,args)
  if sql.startswith('INSERT INTO runtime'):raise sqlite3.OperationalError('synthetic payload secret')
  return result
 monkeypatch.setattr(case.s,'execute',execute)
 with pytest.raises(module.CustomerIngestUnavailable,match='^customer_ingest_unavailable$'):ingest(case,page([event('e2')],before='c1',after='c2'))
 assert raw(case)==before and not case.s.db.in_transaction


@pytest.mark.parametrize('value',[RuntimeError('payload credential secret'),None,[],{'mode':'incremental'},page(mode='archive'),page(mode='unknown'),dict(page(),body='secret'),page(before='wrong'),page(after=None),page(after=''),page(after=' '),page(after=1),page(after='x'*129),page(rows=[event()]*33)])
def test_provider_page_failures_fixed_safe_no_state_write(case,value,caplog):
 with pytest.raises(module.CustomerIngestUnavailable,match='^customer_ingest_unavailable$'):ingest(case,value if value is not None else [])
 assert raw(case) is None and len(case.p.calls)==1 and 'credential' not in caplog.text


@pytest.mark.parametrize('key,value',[('body','hidden body'),('text','hidden text'),('caption','hidden caption'),('account_id','same'),('channel','synthetic'),('event_id',''),('customer_id','x'*129),('thread_id','bad\nvalue'),('event_id',' x'),('event_id','x\ud800'),('at',NOW.replace(tzinfo=None)),('at',NOW.isoformat()),('direction','unknown'),('confirmed',None),('confirmed',1),('reply_to','')])
def test_closed_event_metadata_no_body_or_guessed_types(case,key,value):
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([event(**{key:value})]))
 assert raw(case) is None


@pytest.mark.parametrize('key',['event_id','customer_id','thread_id','at','direction','confirmed'])
def test_every_required_metadata_field_explicit(case,key):
 row=event();row.pop(key)
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([row]))
 assert raw(case) is None


def test_four_conversation_cap_and_128_event_retention_no_pruning(case):
 for n in range(4):ingest(case,page([event('e'+str(n*32+i),customer_id='c'+str(n),thread_id='t'+str(n)) for i in range(32)],before=None if n==0 else 'c'+str(n),after='c'+str(n+1)))
 before=raw(case);assert len(json.loads(before)['events'])==128
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([event('new')],before='c4',after='c5'))
 assert raw(case)==before


def test_fifth_conversation_rejects_whole_page(case):
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([event(str(i),customer_id='c'+str(i)) for i in range(5)]))
 assert raw(case) is None


@pytest.mark.parametrize('kind',['oversize','blob','malformed','duplicate_keys','extra_body','version_bool','bad_receipt','state_event_extra','state_event_duplicate','state_event_time','registration_revision_bool','count129'])
def test_malformed_stored_state_never_fetches_or_silently_repairs(case,kind):
 ingest(case);state=json.loads(raw(case))
 if kind=='oversize':value='x'*262145
 elif kind=='blob':value=b'blob'
 elif kind=='malformed':value='{bad'
 elif kind=='duplicate_keys':value='{"version":1,"version":1}'
 else:
  if kind=='extra_body':state['body']='secret'
  elif kind=='version_bool':state['version']=True
  elif kind=='bad_receipt':state['last_receipt']['cursor_after']='wrong'
  elif kind=='state_event_extra':state['events'][0]['body']='secret'
  elif kind=='state_event_duplicate':state['events'].append(state['events'][0])
  elif kind=='state_event_time':state['events'][0]['at']='2026-10-19T18:00:00+08:00'
  elif kind=='registration_revision_bool':state['registration']['revision']=True
  elif kind=='count129':state['events']*=129
  value=module._json(state)
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,case.state['registration'].runtime_key))
 with pytest.raises(module.CustomerIngestUnavailable):case.driver.ingest_once(now=NOW)
 assert len(case.p.calls)==1


@pytest.mark.parametrize('budget',['MAX_BYTES','MAX_ROWS','MAX_QUERIES'])
def test_cumulative_read_budget_and_state_size_refuse_whole(case,monkeypatch,budget):
 ingest(case);before=raw(case);monkeypatch.setattr(module,budget,1)
 with pytest.raises(module.CustomerIngestUnavailable):preview(case)
 assert raw(case)==before


def test_archive_cannot_advance_existing_incremental_cursor(case):
 ingest(case);before=raw(case)
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([event('archive')],before='c1',after='archive-cursor',mode='archive'))
 assert raw(case)==before


def test_readonly_evaluation_rechecks_current_state_after_actual_core(case,monkeypatch):
 ingest(case);actual=core.unanswered
 def evaluated(*a,**kw):
  result=actual(*a,**kw);case.s.execute('DELETE FROM runtime WHERE key=?',(case.state['registration'].runtime_key,));return result
 monkeypatch.setattr(core,'unanswered',evaluated)
 with pytest.raises(module.CustomerIngestUnavailable):preview(case)


def test_actual_old_cursor_cycle_rejected_and_old_page_cannot_become_new(case):
 p1=page();p2=page([event('e2',direction='outgoing',at=NOW-timedelta(hours=1))],before='c1',after='c2')
 ingest(case,p1);ingest(case,p2);before=raw(case);changes=case.s.db.total_changes
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([],before='c2',after='c1'))
 assert raw(case)==before and case.s.db.total_changes==changes
 replay=ingest(case,p2)
 assert replay.replayed is True and replay.cursor=='c2' and raw(case)==before and case.s.db.total_changes==changes
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,p1)
 assert json.loads(raw(case))['committed_cursors']==['c1','c2']


def test_empty_advancing_pages_consume_cursor_capacity_without_eviction(case):
 for i in range(128):
  receipt=ingest(case,page([],before=None if i==0 else 'cursor-'+str(i),after='cursor-'+str(i+1)))
  assert receipt.retained_events==0
 before=raw(case);changes=case.s.db.total_changes
 with pytest.raises(module.CustomerIngestUnavailable):ingest(case,page([],before='cursor-128',after='cursor-129'))
 assert raw(case)==before and case.s.db.total_changes==changes
 state=json.loads(before);assert len(state['committed_cursors'])==128 and state['committed_cursors'][0]=='cursor-1' and state['committed_cursors'][-1]=='cursor-128'
 replay=ingest(case,page([],before='cursor-127',after='cursor-128'))
 assert replay.replayed and case.s.db.total_changes==changes


@pytest.mark.parametrize('kind',['missing','empty','duplicate','none','nonstring','oversize','current_not_last','transition_mismatch'])
def test_closed_committed_cursor_inventory_invalidity_never_fetches(case,kind):
 ingest(case);state=json.loads(raw(case))
 if kind=='missing':state.pop('committed_cursors')
 elif kind=='empty':state['committed_cursors']=[]
 elif kind=='duplicate':state['committed_cursors']=['c1','c1']
 elif kind=='none':state['committed_cursors']=[None,'c1']
 elif kind=='nonstring':state['committed_cursors']=[1,'c1']
 elif kind=='oversize':state['committed_cursors']=['cursor-'+str(i) for i in range(129)]
 elif kind=='current_not_last':state['committed_cursors']=['c1','other']
 else:state['committed_cursors']=['prior','c1']
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(module._json(state),case.state['registration'].runtime_key))
 with pytest.raises(module.CustomerIngestUnavailable):case.driver.ingest_once(now=NOW)
 assert len(case.p.calls)==1
