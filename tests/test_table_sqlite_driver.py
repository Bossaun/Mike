"""Actual durable synthetic runtime workbook + consumed nonce atomicity, no issuer in driver."""
from dataclasses import replace,FrozenInstanceError
from decimal import Decimal
from types import SimpleNamespace
import copy
import json
import sqlite3
import pytest
from goldway import table_sqlite_driver as module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.core
ISSUE=core.issue_local_approval
NOW=1000
KEY=b'OPENLY-SYNTHETIC-TEST-KEY-ONLY-0001'


def context(**kw):
 values=dict(business='A',driver_id='synthetic-driver',approval_domain='synthetic-approval',logical_workbook_id='local-book',required_approver='synthetic-approver',key_id='synthetic-key',verification_key=KEY)
 values.update(kw);return module.TableContext(**values)


def workbook(ctx=None,value=Decimal('1.00')):
 ctx=ctx or context()
 row=core.Row(4,'row-v1',(core.Cell('id',' exact-key '),core.Cell('value',value),core.Cell('untouched','original')))
 other=core.Row(9,'other-row-v1',(core.Cell('id','other'),core.Cell('value','inert =SUM(A1)'),core.Cell('untouched',None)))
 return core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('sheet','sheet-v1',(row,other)),))


# Host-only visible fixture seed. No initializer/import/reset API exists in driver.
def encoded(ctx,book,nonces=()):
 return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))


def make_driver(c,**kw):
 values=dict(store=c.s,current_context=c.callback,enabled=True,allow_fake_writes=True);values.update(kw)
 return module.SQLiteTableDriver(**values)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original_init=Store.__init__;s=Store(str(path));ctx=context();state=dict(context=ctx,calls=0,mutate=None)
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encoded(ctx,workbook(ctx))))
 s.execute('INSERT INTO runtime VALUES(?,?)',('unrelated-runtime','UNRELATED_SENTINEL'))
 def callback():
  state['calls']+=1
  if state['mutate']:state['mutate']()
  return state['context']
 c=SimpleNamespace(s=s,path=path,ctx=ctx,state=state,callback=callback,initial=encoded(ctx,workbook(ctx)))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original_init(c.s,str(path));old.db.close();c.driver=make_driver(c)
 c.reopen=reopen;c.driver=make_driver(c)
 def fail(*a,**kw):pytest.fail('unexpected issuer/constructor/network/Sheets/Engine/model/config operation')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher):monkeypatch.setattr(cls,'__init__',fail)
 monkeypatch.setattr(core,'issue_local_approval',fail)
 import socket,httpx,builtins,subprocess
 monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def plan(c,value=Decimal('2.00')):return c.driver.build_plan('sheet',core.RowIdentity('id',' exact-key '),(core.Cell('value',value),))


def token(p,nonce='synthetic-nonce',ctx=None,**kw):
 ctx=ctx or context();args=dict(decision='approve',approver=ctx.required_approver,nonce=nonce,issued_at=900,expires_at=1100,signing_key=ctx.verification_key);args.update(kw)
 return ISSUE(p,**args)


def apply(c,p,t,**kw):
 args=dict(now=NOW,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2');args.update(kw)
 return c.driver.apply(p,t,**args)


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]


def set_state(c,value):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(value,c.ctx.runtime_key))


def authorize(c,readonly=False):
 operations=[];queries=[]
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action==sqlite3.SQLITE_UPDATE:assert table=='runtime' and column=='value' and not readonly
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_TRANSACTION):pytest.fail('unexpected SQL '+str((action,table,column)))
  operations.append((action,table,column));return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(guard);c.s.db.set_trace_callback(queries.append)
 return operations,queries


def test_actual_persisted_snapshot_plan_approval_apply_atomic_restart_replay(case,monkeypatch):
 calls=[];build=core.build_plan;auth=core.validate_approval;actual_apply=core.apply_to_fake_workbook;check=core.check_plan_baseline
 for name,actual in (('build_plan',build),('validate_approval',auth),('apply_to_fake_workbook',actual_apply),('check_plan_baseline',check)):
  def wrapper(*a,_actual=actual,_name=name,**kw):calls.append(_name);return _actual(*a,**kw)
  monkeypatch.setattr(core,name,wrapper)
 before={t:case.s.rows('SELECT * FROM '+t) for t in ('tasks','sources','events','outbox','deliveries','usage')};ops,queries=authorize(case)
 observed=case.driver.snapshot();p=plan(case);approved=token(p);receipt=apply(case,p,approved)
 assert type(observed) is core.Workbook and p.workbook_id==case.ctx.workbook_id
 assert receipt.state=='local_fake_committed' and receipt.live_authorized is False and receipt.used_nonce_count==1 and receipt.plan_digest==core.plan_digest(p)
 state=json.loads(raw(case));assert state['used_nonces']==['synthetic-nonce'] and state['workbook']['revision']=='book-v2'
 assert calls.count('build_plan')==1 and calls.count('apply_to_fake_workbook')==1 and calls.count('validate_approval')==2 and calls.count('check_plan_baseline')==1
 assert calls.index('validate_approval')<calls.index('check_plan_baseline')
 assert sum(q.startswith('UPDATE runtime SET value=') for q in queries)==1
 assert all(case.ctx.runtime_key in q for q in queries if q.startswith(('SELECT ','UPDATE ')))
 case.s.db.set_authorizer(None)
 for t,value in before.items():assert case.s.rows('SELECT * FROM '+t)==value
 assert case.s.execute("SELECT value FROM runtime WHERE key='unrelated-runtime'").fetchone()[0]=='UNRELATED_SENTINEL'
 assert KEY.decode() not in raw(case) and approved.signature not in raw(case) and approved.plan_digest not in raw(case)
 case.reopen();saved=raw(case)
 with pytest.raises(core.ApprovalError):apply(case,p,approved)
 assert raw(case)==saved and case.driver.snapshot().sheets[0].rows[1]==observed.sheets[0].rows[1]
 with pytest.raises(FrozenInstanceError):receipt.state='live'


@pytest.mark.parametrize('method',['snapshot','build','apply'])
def test_defaultdisabled_zero_sql_callback(case,method):
 def fail():pytest.fail('disabled context callback')
 d=module.SQLiteTableDriver(store=case.s,current_context=fail)
 case.s.db.set_authorizer(lambda *a:pytest.fail('disabled SQL'))
 with pytest.raises(module.TableDriverUnavailable):
  if method=='snapshot':d.snapshot()
  elif method=='build':d.build_plan('sheet',None,())
  else:d.apply(None,None,now=NOW,next_workbook_revision='b',next_sheet_revision='s',next_row_revision='r')


def test_readonly_enabled_mode_never_grants_write_from_valid_token(case):
 p=plan(case);t=token(p);case.driver=make_driver(case,allow_fake_writes=False);authorize(case,readonly=True)
 assert case.driver.snapshot()==workbook(case.ctx)
 with pytest.raises(module.TableDriverUnavailable):apply(case,p,t)
 assert json.loads(raw(case))['used_nonces']==[]


@pytest.mark.parametrize('failure',['signature','expired','future','signer','altered_plan','used_nonce'])
def test_actual_approval_before_any_persisted_baseline_transfer_or_decode(case,monkeypatch,failure):
 p=plan(case);t=token(p)
 if failure=='signature':t=replace(t,signature='f'*64)
 elif failure=='expired':t=token(p,issued_at=800,expires_at=NOW)
 elif failure=='future':t=token(p,issued_at=NOW+1,expires_at=NOW+100)
 elif failure=='signer':t=token(p,approver='other')
 elif failure=='altered_plan':p=replace(p,changes=(core.Change('value',Decimal('1.00'),Decimal('3.00')),))
 state=json.loads(raw(case));state['workbook']={'BROKEN_BASELINE':'PRIVATE_SENTINEL'}
 if failure=='used_nonce':state['used_nonces']=[t.nonce]
 set_state(case,json.dumps(state))
 monkeypatch.setattr(module,'_decode',lambda *a:pytest.fail('invalid approval decoded persisted baseline'))
 monkeypatch.setattr(case.driver,'_full',lambda *a:pytest.fail('invalid approval transferred persisted workbook'))
 monkeypatch.setattr(core,'check_plan_baseline',lambda *a:pytest.fail('invalid approval reached checker'))
 with pytest.raises(core.ApprovalError):apply(case,p,t)
 assert not case.s.db.in_transaction


@pytest.mark.parametrize('dimension',['business','driver_id','approval_domain','logical_workbook_id'])
def test_namespace_canonical_book_prevents_same_local_id_plan_token_reuse(case,dimension):
 p=plan(case);t=token(p);other=replace(case.ctx,**{dimension:'B'})
 case.s.execute('INSERT INTO runtime VALUES(?,?)',(other.runtime_key,encoded(other,workbook(other))))
 assert other.runtime_key!=case.ctx.runtime_key and other.workbook_id!=case.ctx.workbook_id
 state={'ctx':other};d=make_driver(case,current_context=lambda:state['ctx']);otherplan=d.build_plan('sheet',p.identity,(core.Cell('value',Decimal('2.00')),))
 assert core.plan_digest(otherplan)!=core.plan_digest(p)
 with pytest.raises(core.ApprovalError):d.apply(otherplan,t,now=NOW,next_workbook_revision='b2',next_sheet_revision='s2',next_row_revision='r2')
 assert json.loads(case.s.execute('SELECT value FROM runtime WHERE key=?',(other.runtime_key,)).fetchone()[0])['used_nonces']==[]


@pytest.mark.parametrize('change',['position','row_revision','sheet_revision','book_revision','unplanned_value','unplanned_type','decimal_scale','cell_order','missing','ambiguous'])
def test_actual_fullrow_checker_conflict_never_consumes_nonce(case,change):
 p=plan(case);t=token(p);book=workbook(case.ctx);sheet=book.sheets[0];row=sheet.rows[0]
 if change=='position':row=replace(row,position=5)
 elif change=='row_revision':row=replace(row,revision='manual')
 elif change=='sheet_revision':sheet=replace(sheet,revision='manual')
 elif change=='book_revision':book=replace(book,revision='manual')
 elif change=='unplanned_value':row=replace(row,cells=row.cells[:2]+(core.Cell('untouched','manual'),))
 elif change=='unplanned_type':row=replace(row,cells=row.cells[:2]+(core.Cell('untouched',False),))
 elif change=='decimal_scale':row=replace(row,cells=(row.cells[0],core.Cell('value',Decimal('1.0')),row.cells[2]))
 elif change=='cell_order':row=replace(row,cells=(row.cells[1],row.cells[0],row.cells[2]))
 if change=='missing':rows=(sheet.rows[1],)
 elif change=='ambiguous':rows=(row,replace(row,position=8))
 else:rows=(row,sheet.rows[1])
 book=replace(book,sheets=(replace(sheet,rows=rows),));peer=sqlite3.connect(str(case.path),isolation_level=None)
 try:peer.execute('UPDATE runtime SET value=? WHERE key=?',(encoded(case.ctx,book),case.ctx.runtime_key))
 finally:peer.close()
 before=raw(case)
 with pytest.raises(core.ConflictError):apply(case,p,t)
 assert raw(case)==before and json.loads(raw(case))['used_nonces']==[]


@pytest.mark.parametrize('failure',['write_after','trigger','cas_drift'])
def test_failed_atomic_commit_rolls_back_workbook_and_nonce_same_token_can_retry(case,monkeypatch,failure):
 p=plan(case);t=token(p);before=raw(case);actual=case.s.execute
 if failure=='write_after':
  def execute(sql,args=()):
   result=actual(sql,args)
   if sql.startswith('UPDATE runtime SET value=? WHERE key=? AND value=?'):raise sqlite3.OperationalError('PRIVATE_KEY_SENTINEL')
   return result
  monkeypatch.setattr(case.s,'execute',execute)
 elif failure=='trigger':case.s.execute("CREATE TRIGGER synthetic_fail BEFORE UPDATE ON runtime BEGIN SELECT RAISE(ABORT,'PRIVATE_KEY_SENTINEL'); END")
 else:
  core_apply=core.apply_to_fake_workbook
  def apply_core(*a,**kw):
   result=core_apply(*a,**kw)
   def mutate():case.state['mutate']=None;actual('UPDATE runtime SET value=? WHERE key=?',(before+' ',case.ctx.runtime_key))
   case.state['mutate']=mutate;return result
  monkeypatch.setattr(core,'apply_to_fake_workbook',apply_core)
 with pytest.raises(module.TableDriverUnavailable,match='^sqlite_table_driver_unavailable$'):apply(case,p,t)
 assert raw(case)==before and not case.s.db.in_transaction
 monkeypatch.setattr(case.s,'execute',actual)
 if failure=='trigger':case.s.execute('DROP TRIGGER synthetic_fail')
 if failure=='cas_drift':monkeypatch.setattr(core,'apply_to_fake_workbook',core_apply)
 receipt=apply(case,p,t);assert receipt.used_nonce_count==1


def test_two_competing_prepared_applications_consume_only_once(case):
 p=plan(case);t=token(p);other=object.__new__(Store);other.db=sqlite3.connect(str(case.path),isolation_level=None);other.db.row_factory=sqlite3.Row
 try:
  competitor=module.SQLiteTableDriver(store=other,current_context=lambda:case.ctx,enabled=True,allow_fake_writes=True)
  apply(case,p,t)
  with pytest.raises(core.ApprovalError):competitor.apply(p,t,now=NOW,next_workbook_revision='b3',next_sheet_revision='s3',next_row_revision='r3')
  assert json.loads(raw(case))['used_nonces']==['synthetic-nonce']
 finally:other.db.close()


@pytest.mark.parametrize('value',[None,'',False,0,True,1,Decimal('0.00'),Decimal('-0.00'),Decimal('1E-18'),Decimal('1.00E+20'),'  spaces  ','=SUM(A1)','<b>instruction</b>'])
def test_tagged_codec_actual_core_types_and_exact_decimal_tuple(case,value):
 set_state(case,encoded(case.ctx,workbook(case.ctx,value)))
 cell=case.driver.snapshot().sheets[0].rows[0].cells[1]
 assert type(cell.value) is type(value)
 if type(value) is Decimal:assert cell.value.as_tuple()==value.as_tuple()
 else:assert cell.value==value
 assert module._value(cell.value)==core._value(value)


@pytest.mark.parametrize('before,after',[(None,''),(False,0),(Decimal('1.0'),Decimal('1.00')),(Decimal('0.00'),Decimal('-0.00'))])
def test_core_typed_change_not_loose_numeric_noop(case,before,after):
 set_state(case,encoded(case.ctx,workbook(case.ctx,before)));p=plan(case,after);apply(case,p,token(p))
 value=case.driver.snapshot().sheets[0].rows[0].cells[1].value
 assert core._value(value)==core._value(after)


def test_ledger_capacity_no_eviction_or_partial_consumption(case):
 set_state(case,encoded(case.ctx,workbook(case.ctx),('nonce-'+str(i) for i in range(256))))
 p=plan(case);t=token(p);before=raw(case)
 with pytest.raises(module.TableDriverUnavailable):apply(case,p,t)
 assert raw(case)==before and len(json.loads(raw(case))['used_nonces'])==256


@pytest.mark.parametrize('change',['context','key','store','db','callback','approver','namespace'])
def test_bound_current_context_key_and_host_identity_drift_denies(case,change):
 p=plan(case);t=token(p);old=raw(case)
 if change=='context':case.state['context']=copy.copy(case.ctx)
 elif change=='key':case.state['context']=replace(case.ctx,verification_key=bytes(bytearray(KEY)))
 elif change=='store':case.driver._store=copy.copy(case.s)
 elif change=='db':case.driver._host=(case.s,object(),case.callback)
 elif change=='callback':case.driver._callback=lambda:case.ctx
 elif change=='approver':case.state['context']=replace(case.ctx,required_approver='other')
 else:case.state['context']=replace(case.ctx,business='other')
 with pytest.raises(module.TableDriverUnavailable):apply(case,p,t)
 assert raw(case)==old


def test_current_key_is_explicit_no_default_and_wrong_key_auth_fails(case):
 p=plan(case);t=token(p);wrong=replace(case.ctx,verification_key=b'WRONG-SYNTHETIC-KEY-ONLY-00000000')
 case.driver=make_driver(case,current_context=lambda:wrong)
 with pytest.raises(core.ApprovalError):apply(case,p,t)
 assert json.loads(raw(case))['used_nonces']==[]


@pytest.mark.parametrize('malformed',['missing','blob','oversize','invalid_json','wrong_namespace','duplicate_keys','unknown_field','version_bool','bad_ledger','ledger257','bad_book','bad_decimal','huge_decimal','bool_int','noncanonical_int','three_sheets','rows33','cells17'])
def test_strict_bounded_state_no_silent_repair_or_seed(case,malformed):
 state=json.loads(raw(case))
 if malformed=='missing':case.s.execute('DELETE FROM runtime WHERE key=?',(case.ctx.runtime_key,));value=None
 elif malformed=='blob':value=b'blob'
 elif malformed=='oversize':value='x'*262145
 elif malformed=='invalid_json':value='{bad'
 elif malformed=='duplicate_keys':value='{"version":1,"version":1}'
 else:
  if malformed=='wrong_namespace':state['namespace']['business']='B'
  elif malformed=='unknown_field':state['key']='NEVER_PERSIST_KEYS'
  elif malformed=='version_bool':state['version']=True
  elif malformed=='bad_ledger':state['used_nonces']=['same','same']
  elif malformed=='ledger257':state['used_nonces']=['n'+str(i) for i in range(257)]
  elif malformed=='bad_book':state['workbook']['id']='wrong'
  elif malformed in ('bad_decimal','huge_decimal','bool_int','noncanonical_int'):
   state['workbook']['sheets'][0]['rows'][0]['cells'][1][1]={'bad_decimal':['decimal','NaN'],'huge_decimal':['decimal','1E+99999999999999999'],'bool_int':['int',True],'noncanonical_int':['int','01']}[malformed]
  elif malformed=='three_sheets':state['workbook']['sheets']*=3
  elif malformed=='rows33':state['workbook']['sheets'][0]['rows']*=17
  elif malformed=='cells17':state['workbook']['sheets'][0]['rows'][0]['cells']*=6
  value=json.dumps(state)
 if value is not None:set_state(case,value)
 with pytest.raises(module.TableDriverUnavailable):case.driver.snapshot()


def test_malformed_metadata_does_not_become_empty_nonce_ledger(case):
 p=plan(case);t=token(p);state=json.loads(raw(case));state['used_nonces']=None;set_state(case,json.dumps(state))
 with pytest.raises(module.TableDriverUnavailable):apply(case,p,t)
 assert json.loads(raw(case))['used_nonces'] is None


def test_active_transaction_refused_without_commit_or_rollback_owned_by_caller(case):
 p=plan(case);t=token(p);case.s.execute('BEGIN IMMEDIATE')
 with pytest.raises(module.TableDriverUnavailable):apply(case,p,t)
 assert case.s.db.in_transaction;case.s.execute('ROLLBACK')


def test_build_plan_missing_identity_and_exact_spaces_no_fallback(case):
 for value in ('exact-key','missing'):
  with pytest.raises(core.LookupError):case.driver.build_plan('sheet',core.RowIdentity('id',value),(core.Cell('value',2),))
 assert plan(case).identity.value==' exact-key '


def test_context_and_key_not_exposed_by_repr_or_persisted_state(case):
 assert KEY.decode() not in repr(case.ctx) and KEY.decode() not in raw(case)
 assert 'signature' not in raw(case) and 'verification_key' not in raw(case)
