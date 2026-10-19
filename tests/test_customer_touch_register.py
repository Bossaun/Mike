"""Actual admitted touch metadata lifecycle and current synthetic permission projection."""
from dataclasses import replace,fields,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import copy
import json
import sqlite3
import pytest
from goldway import customer_touch_register as module
from goldway.customer_ingest import CustomerIngest,SourceRegistration,TouchEvaluation
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
 args=dict(store=c.s,ingest=c.ingest,source=c.source,conversation=c.conv,enabled=True);args.update(kw);return module.CustomerTouchRegister(**args)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));provider=Provider()
 source=SourceRegistration('A','fake','synthetic','synthetic-account',1,NOW-timedelta(days=2),NOW+timedelta(days=2));state={'source':source}
 def current():return state['source']
 c=SimpleNamespace(s=s,path=path,p=provider,source=source,state=state,current=current,cursor=None)
 c.ingest=CustomerIngest(store=s,current_source=current,provider=provider,enabled=True);c.conv=core.Conversation('customer','synthetic','thread',source.source_id)
 def admit(rows=None,now=NOW):
  provider.pages.append(dict(mode='incremental',cursor_before=c.cursor,cursor_after='cursor-'+str(len(provider.calls)+1),events=[event('basis'),event('permission-event',direction='incoming',at=NOW-timedelta(hours=1))] if rows is None else rows));provider.allowed=True
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



def window(start=None,end=None):return core.Window(start or NOW-timedelta(minutes=30),end or NOW+timedelta(hours=4))

def permission(c,**kw):
 d=dict(id='permission',conversation=c.conv,event_id='permission-event',granted=True,opt_out=False,window=window(NOW-timedelta(hours=2)));d.update(kw);return core.ContactPermission(**d)

def plan(c,**kw):
 d=dict(id='plan',conversation=c.conv,event_id='basis',owner_id='opaque-owner',permission_id='permission',readiness=True,window=window(),draft=module.REVIEW_DRAFT);d.update(kw);return core.TouchPlan(**d)

def receipt(c,**kw):
 d=dict(id='receipt',conversation=c.conv,plan_id='plan',event_id='receipt-event',confirmed=True);d.update(kw);return core.TouchReceipt(**d)

def raw(c):
 r=c.s.execute('SELECT value FROM runtime WHERE key=?',(c.register_key,)).fetchone();return None if r is None else r[0]

def source_raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.source.runtime_key,)).fetchone()[0]

def seed(c):
 c.r.record_permission(permission(c),now=NOW);c.r.record_plan(plan(c),now=NOW)

def assert_denied(call):
 with pytest.raises(module.TouchRegisterUnavailable,match='^customer_touch_register_unavailable$'):call()


def test_actual_admission_register_restart_new_structured_optout_blocks(case,monkeypatch):
 actual=core.touch_preview;calls=[]
 def spy(*a,**kw):calls.append(a);assert kw['dispositions']==();return actual(*a,**kw)
 monkeypatch.setattr(core,'touch_preview',spy)
 before=source_raw(case);seed(case);original=json.loads(raw(case));assert source_raw(case)==before
 case.reopen();value=case.r.preview(now=NOW)
 assert len(value.preview.candidates)==1 and value.preview.candidates[0].draft==module.REVIEW_DRAFT
 assert value.requires_review and not value.live_authorized and value.summary is None
 case.admit([event('optout',direction='incoming',at=NOW+timedelta(minutes=1))],now=NOW+timedelta(minutes=2))
 case.r.record_permission(permission(case,id='optout-record',event_id='optout',granted=False,opt_out=True,window=None),now=NOW+timedelta(minutes=2))
 updated=json.loads(raw(case));assert updated['anchors']['basis']==original['anchors']['basis'] and updated['plans']==original['plans']
 case.reopen();before=raw(case);value=case.r.preview(now=NOW+timedelta(minutes=3))
 assert not value.preview.candidates and value.preview.blocked[0].reason=='opt_out' and raw(case)==before and len(calls)==5
 assert len(case.p.calls)==2 and 'draft' not in original['plans'][0]['payload']


@pytest.mark.parametrize('flag,event_flag,expected',[(True,True,'confirmed_ids'),(False,True,'uncertain_ids'),(True,False,'uncertain_ids')])
def test_actual_later_receipt_persists_summary_without_permission_authority(case,flag,event_flag,expected,monkeypatch):
 seed(case);case.admit([event('receipt-event',at=NOW+timedelta(minutes=1),confirmed=event_flag)],now=NOW+timedelta(minutes=2))
 case.r.record_receipt(receipt(case,confirmed=flag),now=NOW+timedelta(minutes=2));case.reopen()
 calls=[];actual=core.reconcile_touches
 def spy(*a,**kw):calls.append((a,kw));return actual(*a,**kw)
 monkeypatch.setattr(core,'reconcile_touches',spy)
 value=case.r.preview(now=NOW+timedelta(minutes=3),reporting_window=window())
 assert len(calls)==1 and getattr(value.summary,expected)==('plan',) and value.summary.planned_ids==('plan',)
 assert not value.preview.candidates and value.requires_review and not value.live_authorized
 if expected=='uncertain_ids':assert value.preview.blocked[0].reason=='unknown_delivery'


@pytest.mark.parametrize('change,reason',[('missing','unknown_consent'),('unknown','unknown_or_declined_consent'),('declined','unknown_or_declined_consent'),('unconfirmed','unknown_or_declined_consent'),('readiness','unknown_or_not_ready'),('not_ready','unknown_or_not_ready'),('future','unknown_consent'),('expired','outside_consent_window'),('stale','stale_permission_reference'),('basis','unconfirmed_basis')])
def test_actual_unknown_declined_future_and_precedence(case,change,reason):
 p=permission(case);q=plan(case)
 if change=='unknown':p=replace(p,granted=None)
 if change=='declined':p=replace(p,granted=False)
 if change=='readiness':q=replace(q,readiness=None)
 if change=='not_ready':q=replace(q,readiness=False)
 if change=='expired':p=replace(p,window=window(NOW-timedelta(hours=2),NOW))
 if change=='stale':q=replace(q,permission_id='not-current')
 if change in ('unconfirmed','future','basis'):
  saved=json.loads(source_raw(case))
  # Change synthetic admitted metadata BEFORE any durable anchor registration.
  for row in saved['events']:
   if row['event_id']==('basis' if change=='basis' else 'permission-event'):
    if change=='future':row['at']=(NOW+timedelta(hours=1)).isoformat()
    else:row['confirmed']=False
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(saved),case.source.runtime_key))
 if change!='missing':case.r.record_permission(p,now=NOW)
 case.r.record_plan(q,now=NOW);value=case.r.preview(now=NOW)
 assert not value.preview.candidates and value.preview.blocked[0].reason==reason


@pytest.mark.parametrize('order',list(__import__('itertools').permutations(range(3))))
def test_canonical_all_permission_conflict_permutations_not_hidden_by_newer(case,order):
 rows=[event('a',direction='incoming',at=NOW-timedelta(minutes=20)),event('b',direction='incoming',at=NOW-timedelta(minutes=20)),event('c',direction='incoming',at=NOW-timedelta(minutes=10))];case.admit(rows)
 values=[permission(case,id=x,event_id=x,granted=v) for x,v in [('a',True),('b',False),('c',True)]]
 with pytest.raises(ValueError,match='customer_ingest_unavailable'):
  case.ingest.evaluate_touches(case.conv,now=NOW,expected_store=case.s,plans=(),permissions=tuple(values[i] for i in order),receipts=())
 assert raw(case) is None


def test_future_permission_conflict_retained_and_actual_core_ignores_until_clock(case):
 case.admit([event('a',direction='incoming',at=NOW+timedelta(hours=1)),event('b',direction='incoming',at=NOW+timedelta(hours=1))])
 for x,v in [('a',True),('b',False)]:case.r.record_permission(permission(case,id=x,event_id=x,granted=v),now=NOW)
 assert len(case.r.preview(now=NOW).anchor_seals)==2
 assert_denied(lambda:case.r.preview(now=NOW+timedelta(hours=1)))


@pytest.mark.parametrize('change',['incoming','equal_time','at_end','wrong_plan','same_event_two_plans'])
def test_receipt_actual_chronology_link_windows_and_one_event_scope(case,change):
 seed(case);when=NOW+timedelta(minutes=1);direction='outgoing'
 if change=='incoming':direction='incoming'
 if change=='equal_time':when=NOW-timedelta(hours=2)
 if change=='at_end':when=plan(case).window.end
 case.admit([event('receipt-event',at=when,direction=direction)],now=max(NOW,when))
 r=receipt(case)
 if change=='wrong_plan':r=replace(r,plan_id='unknown')
 if change=='same_event_two_plans':
  case.r.record_plan(plan(case,id='second'),now=NOW);case.r.record_receipt(r,now=NOW)
  r=replace(r,id='other-receipt',plan_id='second')
 before=raw(case);assert_denied(lambda:case.r.record_receipt(r,now=max(NOW,when)));assert raw(case)==before


def test_receipt_duplicates_are_actual_deduped_facts_and_reporting_end_clamp(case):
 seed(case);case.admit([event('receipt-event',at=NOW+timedelta(minutes=1))],now=NOW+timedelta(minutes=2))
 for x in ('one','two'):case.r.record_receipt(receipt(case,id=x),now=NOW+timedelta(minutes=2))
 assert case.r.preview(now=NOW+timedelta(minutes=2),reporting_window=window()).summary.confirmed_ids==('plan',)
 value=case.r.preview(now=NOW+timedelta(minutes=2),reporting_window=window(end=NOW+timedelta(minutes=1)))
 assert value.summary.pending_ids==('plan',) and not value.preview.candidates


def test_exact_retry_later_clock_zero_write_conflict_and_clock_rewind(case):
 seed(case);saved=raw(case);queries=[];case.s.db.set_trace_callback(queries.append)
 r=case.r.record_plan(plan(case),now=NOW+timedelta(hours=1));assert r.already_recorded and r.registered_at==NOW and raw(case)==saved
 assert not any(q.startswith(('UPDATE ','INSERT ','DELETE ')) for q in queries)
 assert case.r.preview(now=NOW).preview.candidates
 for row in (replace(plan(case),readiness=False),replace(plan(case),owner_id='other')):assert_denied(lambda:case.r.record_plan(row,now=NOW))
 assert_denied(lambda:case.r.preview(now=NOW-timedelta(seconds=1)))
 assert_denied(lambda:case.r.preview(now=NOW.replace(tzinfo=None)))


@pytest.mark.parametrize('method',['permission','plan','receipt','preview'])
def test_default_disabled_zero_sql_and_callbacks(case,monkeypatch,method):
 r=register(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched host')
 monkeypatch.setattr(case.s,'execute',fail);monkeypatch.setattr(case.ingest,'source_proof',fail)
 call={'permission':lambda:r.record_permission(permission(case),now=NOW),'plan':lambda:r.record_plan(plan(case),now=NOW),'receipt':lambda:r.record_receipt(receipt(case),now=NOW),'preview':lambda:r.preview(now=NOW)}[method]
 assert_denied(call)


@pytest.mark.parametrize('change',['source_proof','evaluate_touches','store','connection','ingest','source','conversation','revoked','expired','disabled_ingest'])
def test_captured_public_method_and_host_current_source_drift(case,change):
 seed(case);before=raw(case)
 if change in ('source_proof','evaluate_touches'):
  from types import MethodType
  original=getattr(case.ingest,change)
  def wrapper(self,*a,**kw):return original(*a,**kw)
  setattr(case.ingest,change,MethodType(wrapper,case.ingest))
 elif change=='store':case.r._store=object()
 elif change=='connection':case.r._host=(case.s,object(),case.ingest,case.source,case.conv)
 elif change=='ingest':case.r._ingest=object()
 elif change=='source':case.r._source=replace(case.source)
 elif change=='conversation':case.r._conversation=replace(case.conv)
 elif change=='revoked':case.state['source']=replace(case.source,revoked=True)
 elif change=='expired':case.state['source']=replace(case.source,expires_at=NOW)
 else:case.ingest._enabled=False
 assert_denied(lambda:case.r.preview(now=NOW));assert raw(case)==before


@pytest.mark.parametrize('dimension',['customer','thread','account','channel','business','revision'])
def test_foreign_scope_cannot_use_admitted_anchors(case,dimension):
 args={}
 if dimension in ('customer','thread'):args['conversation']=replace(case.conv,**{dimension+'_id':'foreign'})
 else:
  source=replace(case.source,**{('account_id' if dimension=='account' else dimension):'foreign' if dimension!='revision' else 2});args['source']=source
  args['conversation']=replace(case.conv,source_id=source.source_id,channel=source.channel)
 r=register(case,**args);assert_denied(lambda:r.record_plan(plan(case,conversation=args.get('conversation',case.conv)),now=NOW))


@pytest.mark.parametrize('change',['at','confirmed','direction','customer','missing'])
def test_source_advance_cannot_rebuild_changed_registered_anchor(case,change):
 seed(case);saved=json.loads(source_raw(case))
 for row in saved['events']:
  if row['event_id']=='basis':
   if change=='at':row['at']=(NOW-timedelta(hours=3)).isoformat()
   elif change=='customer':row['customer_id']='foreign'
   elif change=='direction':row['direction']='incoming'
   elif change=='confirmed':row['confirmed']=False
 if change=='missing':saved['events']=[r for r in saved['events'] if r['event_id']!='basis']
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(saved),case.source.runtime_key));before=raw(case)
 assert_denied(lambda:case.r.preview(now=NOW));assert raw(case)==before


@pytest.mark.parametrize('fault',['write','cas','postwrite_source','postwrite_host'])
def test_atomic_runtime_cas_and_postwrite_drift_roll_back_whole(case,monkeypatch,fault):
 case.r.record_permission(permission(case),now=NOW);before=raw(case);source=source_raw(case);original=case.s.execute
 def execute(sql,params=()):
  if sql.startswith('UPDATE runtime SET value=') and params[1]==case.register_key:
   if fault=='write':raise sqlite3.OperationalError('PRIVATE_PAYLOAD')
   if fault=='cas':return SimpleNamespace(rowcount=0)
   result=original(sql,params)
   if fault=='postwrite_source':case.state['source']=replace(case.source,revoked=True)
   if fault=='postwrite_host':case.r._ingest=object()
   return result
  return original(sql,params)
 monkeypatch.setattr(case.s,'execute',execute)
 assert_denied(lambda:case.r.record_plan(plan(case),now=NOW));assert raw(case)==before and source_raw(case)==source and not case.s.db.in_transaction


def test_readonly_current_source_callback_drift_and_no_foreign_writes(case,monkeypatch):
 seed(case);before=raw(case);actual=core.touch_preview
 def drift(*a,**kw):
  value=actual(*a,**kw);case.state['source']=replace(case.source,revoked=True);return value
 monkeypatch.setattr(core,'touch_preview',drift);assert_denied(lambda:case.r.preview(now=NOW));assert raw(case)==before


@pytest.mark.parametrize('bad',['blob','oversize','json','duplicate','extra','version','revision','clock','scope','proof','seal','missing_seal','extra_seal','plans9','permissions17','receipts17','unknown_plan','draft','naive_window'])
def test_malformed_closed_state_no_reset_or_partial_subset(case,bad):
 seed(case);state=json.loads(raw(case))
 if bad=='blob':value=b'not-text'
 elif bad=='oversize':value='x'*65537
 elif bad=='json':value='{'
 elif bad=='duplicate':value=raw(case)[:-1]+',"version":1}'
 else:
  if bad=='extra':state['extra']=1
  elif bad=='version':state['version']=True
  elif bad=='revision':state['revision']=True
  elif bad=='clock':state['last_write_at']='2026-10-19T12:00:00'
  elif bad=='scope':state['scope_fingerprint']='other'
  elif bad=='proof':state['plans'][0]['source_proof']['state_present']=False
  elif bad=='seal':state['anchors']['basis']='bad'
  elif bad=='missing_seal':del state['anchors']['basis']
  elif bad=='extra_seal':state['anchors']['foreign']='0'*64
  elif bad in ('plans9','permissions17','receipts17'):
   name,n={'plans9':('plans',9),'permissions17':('permissions',17),'receipts17':('receipts',17)}[bad];state[name]=state['plans' if name=='receipts' else name]*n
  elif bad=='unknown_plan':state['plans'][0]['payload']['permission_id']='x';state['plans'][0]['payload']['id']='permission'
  elif bad=='draft':state['plans'][0]['payload']['draft']='untrusted body'
  elif bad=='naive_window':state['plans'][0]['payload']['window'][0]='2026-10-19T11:30:00'
  value=json.dumps(state)
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,case.register_key));assert_denied(lambda:case.r.preview(now=NOW));assert raw(case)==value


@pytest.mark.parametrize('budget',['MAX_BYTES','MAX_ROWS','MAX_QUERIES'])
def test_cumulative_own_reader_budget_whole_failure(case,monkeypatch,budget):
 seed(case);before=raw(case);monkeypatch.setattr(module,budget,0)
 assert_denied(lambda:case.r.record_plan(plan(case,id='another'),now=NOW));assert raw(case)==before


@pytest.mark.parametrize('window_value',[core.Window(NOW,NOW+timedelta(days=367)),None])
def test_window_caps_and_explicit_types(case,window_value):
 if window_value is None:
  assert_denied(lambda:case.r.record_plan(replace(plan(case),draft='some customer message'),now=NOW))
 else:
  assert_denied(lambda:case.r.record_permission(replace(permission(case),window=window_value),now=NOW))
  assert_denied(lambda:case.r.preview(now=NOW,reporting_window=window_value))
 assert raw(case) is None


def test_actual_max_capacity_8_16_16_40_without_eviction(case):
 # Distinct permission timestamps prevent conflicts; all explicit references retained.
 rows=[event('p'+str(i),direction='incoming',at=NOW-timedelta(minutes=30)+timedelta(seconds=i)) for i in range(16)]
 rows += [event('b'+str(i),at=NOW-timedelta(hours=2)) for i in range(8)]
 rows += [event('r'+str(i),at=NOW+timedelta(minutes=1,seconds=i)) for i in range(16)]
 for chunk in (rows[:24],rows[24:]):case.admit(chunk,now=NOW+timedelta(minutes=2))
 now=NOW+timedelta(minutes=2)
 for i in range(16):case.r.record_permission(permission(case,id='perm'+str(i),event_id='p'+str(i)),now=now)
 for i in range(8):case.r.record_plan(plan(case,id='plan'+str(i),event_id='b'+str(i),permission_id='perm15'),now=now)
 for i in range(16):case.r.record_receipt(receipt(case,id='receipt'+str(i),plan_id='plan'+str(i%8),event_id='r'+str(i)),now=now)
 state=json.loads(raw(case));assert len(state['anchors'])==40 and len(raw(case).encode())<=65536
 assert len(case.r.preview(now=now,reporting_window=window()).summary.confirmed_ids)==8
 before=raw(case)
 for kind,row in [('permission',permission(case,id='excess')),('plan',plan(case,id='excess')),('receipt',receipt(case,id='excess'))]:assert_denied(lambda:getattr(case.r,'record_'+kind)(row,now=now))
 assert raw(case)==before


def test_narrow_extraction_keeps_public_promise_seal_exact_bytes(case):
 import hashlib
 p=core.Promise('promise',case.conv,'basis','opaque-owner',NOW,True)
 result=case.ingest.preview_promises(case.conv,now=NOW,expected_store=case.s,promises=(p,),results=())
 data=['customer-promise-anchor:v1',[case.conv.customer_id,case.conv.channel,case.conv.thread_id,case.conv.source_id],'basis','outgoing',(NOW-timedelta(hours=2)).isoformat(),True,None,None]
 # Evidence identity is admitted I63's deterministic metadata ID, not manufactured in the adapter.
 data[-1]='customer-evidence:'+hashlib.sha256(json.dumps([case.source.source_id,case.conv.channel,case.conv.customer_id,case.conv.thread_id,'basis'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 expected=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 assert result.anchor_seals==( ('basis',expected), )
 touch=case.ingest.evaluate_touches(case.conv,now=NOW,expected_store=case.s,plans=(plan(case),),permissions=(permission(case),),receipts=())
 assert dict(touch.anchor_seals)['basis']!=expected and len(touch.preview.candidates)==1


def test_public_one_admitted_view_actual_both_cores_and_sql_boundary(case,monkeypatch):
 seed(case);calls=[];preview=core.touch_preview;summary=core.reconcile_touches
 def first(*a,**kw):calls.append(('preview',a[0]));return preview(*a,**kw)
 def second(*a,**kw):calls.append(('summary',a[0]));return summary(*a,**kw)
 monkeypatch.setattr(core,'touch_preview',first);monkeypatch.setattr(core,'reconcile_touches',second)
 source=source_raw(case);before=raw(case);queries=[];case.s.db.set_trace_callback(queries.append)
 value=case.r.preview(now=NOW,reporting_window=window())
 assert [v[0] for v in calls]==['preview','summary'] and calls[0][1] is calls[1][1]
 assert value.summary.pending_ids==('plan',) and source_raw(case)==source and raw(case)==before
 assert not any(q.startswith(('UPDATE ','INSERT ','DELETE ')) for q in queries)
 with pytest.raises(ValueError):case.ingest.evaluate_touches(case.conv,now=NOW,expected_store=object(),plans=(),permissions=(),receipts=())


def test_active_caller_transaction_not_committed_or_seeded(case):
 case.s.db.execute('BEGIN');assert_denied(lambda:case.r.record_plan(plan(case),now=NOW));assert case.s.db.in_transaction and raw(case) is None
 case.s.db.rollback()
