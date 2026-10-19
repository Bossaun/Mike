"""Actual persisted/reopened SQLite table -> real lookup and daily KPI core."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json
import sqlite3
import pytest
from goldway import table_kpi_reader as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables
reports=module.reports
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
PERIOD=reports.Period(date(2026,10,18),date(2026,10,19))
KEY=b'EXPLICIT-SYNTHETIC-TEST-KEY-NOT-HUMAN-0001'
ISSUE=core.issue_local_approval
APPLY=driver_module.SQLiteTableDriver.apply
BUILD=driver_module.SQLiteTableDriver.build_plan
VALIDATE=core.validate_approval
CORE_APPLY=core.apply_to_fake_workbook
CORE_BUILD=core.build_plan
CHECK=core.check_plan_baseline
DRIVER_INIT=driver_module.SQLiteTableDriver.__init__


def columns():return module.ColumnSchema('owner','start','end','value','unit','currency','observed')

def definition(identifier='metric',**kw):
 d=dict(id=identifier,label='Synthetic metric',unit='count',currency=None,target=Decimal('2.00'),direction='at_least');d.update(kw);return reports.MetricDefinition(**d)

def row(identifier='row',position=1,value=Decimal('2.00'),**kw):
 cells=dict(id=identifier,owner='opaque-owner',start=PERIOD.start.isoformat(),end=PERIOD.end.isoformat(),value=value,unit='count',currency=None,observed=PERIOD.end.isoformat(),unmapped='inert =SUM(A1)');cells.update(kw)
 return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in cells.items()))

def encode(context,book,nonces=()):
 return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=context.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))

def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,bindings=c.bindings,columns=c.columns,enabled=True);d.update(kw);return module.TableKpiReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path))
 ctx=driver_module.TableContext('SYNTHETIC','fake-driver','synthetic-approval','book','synthetic-reviewer','synthetic-key',KEY)
 source=module.KpiSource('SYNTHETIC',ctx.workbook_id,'opaque-owner','UTC',1,NOW-timedelta(days=2),NOW+timedelta(days=2))
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('sheet','sheet-v1',(row(),row('unselected',position=2,value=None,unmapped=Decimal('-0.00')))),))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('unrelated-runtime','untouched'))
 state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 d=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,source=source,book=book,columns=columns(),bindings=(module.MetricBinding('metric','sheet',core.RowIdentity('id','row')),),state=state,current=current,context=context,driver=d)
 c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);DRIVER_INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 c.persist=persist;c.reopen=reopen
 def write_value(value):
  # Fixture-only synthetic approval/application; reader never receives key/token.
  with monkeypatch.context() as host:
   host.setattr(core,'validate_approval',VALIDATE);host.setattr(core,'apply_to_fake_workbook',CORE_APPLY);host.setattr(core,'build_plan',CORE_BUILD);host.setattr(core,'check_plan_baseline',CHECK)
   writer=object.__new__(driver_module.SQLiteTableDriver);DRIVER_INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   plan=BUILD(writer,'sheet',core.RowIdentity('id','row'),(core.Cell('value',value),))
   token=ISSUE(plan,decision='approve',approver=ctx.required_approver,nonce='explicit-synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,plan,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.write_value=write_value
 def forbidden(*a,**kw):pytest.fail('reader used constructor/write/approval/model/network/body/outbox path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',forbidden)
 for name in ('build_plan','apply'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,forbidden)
 for name in ('issue_local_approval','validate_approval','apply_to_fake_workbook','build_plan','check_plan_baseline'):monkeypatch.setattr(core,name,forbidden)
 monkeypatch.setattr(Store,'queue',forbidden)
 import socket,httpx,builtins,subprocess
 monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden);monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def call(c,**kw):
 d=dict(period=PERIOD,definitions=(definition(),),coverage_complete=True,coverage_observed_on=PERIOD.end,now=NOW);d.update(kw);return c.reader.read_daily(**d)


def deny(fn):
 with pytest.raises(module.TableKpiUnavailable,match='^table_kpi_unavailable$'):fn()


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]


def alter(c,**changes):
 r=c.book.sheets[0].rows[0];cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells);new=replace(c.book,sheets=(replace(c.book.sheets[0],rows=(replace(r,cells=cells),)+c.book.sheets[0].rows[1:]),));c.persist(new);return new


def test_real_persisted_reopen_approved_fixture_update_reopen_changes_actual_core(case,monkeypatch):
 calls=[];actual_lookup=core.lookup_row;actual_kpi=reports.kpi_snapshot
 def lookup(*a,**kw):calls.append('lookup');return actual_lookup(*a,**kw)
 def kpi(**kw):calls.append('kpi');assert kw['owners']==('opaque-owner',) and kw['require_daily'] is True;return actual_kpi(**kw)
 monkeypatch.setattr(core,'lookup_row',lookup);monkeypatch.setattr(reports,'kpi_snapshot',kpi)
 before=raw(case);first=call(case);assert first.lines[0].value==Decimal('2.00') and first.lines[0].target_state=='met' and calls==['lookup','kpi'] and raw(case)==before
 assert first.requires_review and not first.live_authorized and not first.staff_assessment_authorized and first.scope=='configured_synthetic_workbook_subset'
 assert not hasattr(first,'workbook') and not hasattr(first,'key')
 case.reopen();same=call(case);assert same.workbook_fingerprint==first.workbook_fingerprint and same.lines==first.lines
 receipt=case.write_value(Decimal('1.00'));assert receipt.state=='local_fake_committed'
 case.reopen();after=raw(case);updated=call(case)
 assert updated.lines[0].value==Decimal('1.00') and updated.lines[0].target_state=='missed' and updated.workbook_fingerprint!=first.workbook_fingerprint and raw(case)==after
 assert json.loads(after)['used_nonces']==['explicit-synthetic-nonce'] and KEY.decode() not in repr(updated)
 with pytest.raises(FrozenInstanceError):updated.lines=()


@pytest.mark.parametrize('value,target,direction,complete,observed,status,state',[(0,Decimal(0),'at_least',True,PERIOD.end,'known','met'),(Decimal('2.00'),Decimal('2'),'at_most',True,PERIOD.end,'known','met'),(Decimal('3'),Decimal('2'),'at_most',True,PERIOD.end,'known','missed'),(Decimal(0),None,'at_least',True,PERIOD.end,'known','unknown'),(Decimal(1),None,'informational',True,PERIOD.end,'known','informational'),(Decimal(1),Decimal(2),'at_least',False,PERIOD.end,'provisional','unknown'),(Decimal(1),Decimal(2),'at_least',True,PERIOD.start,'provisional','unknown'),(None,Decimal(2),'at_least',True,PERIOD.end,'unknown','unknown'),(None,Decimal(2),'at_least',True,None,'unknown','unknown'),(Decimal('-0.00'),Decimal('-0'),'at_least',True,PERIOD.end,'known','met')])
def test_actual_kpi_zero_unknown_provisional_target_directions(case,value,target,direction,complete,observed,status,state):
 alter(case,value=value,observed=observed.isoformat() if observed else None)
 output=call(case,definitions=(definition(target=target,direction=direction),),coverage_complete=complete)
 assert output.lines[0].status==status and output.lines[0].target_state==state and output.lines[0].value==(Decimal(value) if type(value) is int else value)
 assert output.unobserved_metric_ids==(('metric',) if observed is None else ())
 if value is None and observed is not None:assert len(output.lines[0].sources)==2


def test_three_exact_distinct_rows_money_currency_null_scale_provenance(case,monkeypatch):
 rows=(row('a',position=1,value=Decimal('2.00')),row('b',position=2,value=Decimal('-0.00'),unit='money',currency='RUB'),row('c',position=3,value=None,observed=None))
 case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=rows),)))
 case.bindings=tuple(module.MetricBinding(x,'sheet',core.RowIdentity('id',x)) for x in ('a','b','c'));case.reader=reader(case)
 definitions=(definition('a'),definition('b',unit='money',currency='RUB',target=Decimal(0)),definition('c',target=None))
 output=call(case,definitions=definitions)
 assert len(output.lines)==3 and output.lines[1].value.as_tuple()==Decimal('-0.00').as_tuple() and output.unobserved_metric_ids==('c',)
 assert len(output.observation_fingerprints)==3 and len(set(p for i,p in output.observation_fingerprints))==3


@pytest.mark.parametrize('value',[True,False,1.5,'2','=SUM(A1)',Decimal('-1'),Decimal('NaN'),Decimal('Infinity'),Decimal('1e19'),Decimal('1e-19'),Decimal('1'*51),10**19])
def test_invalid_numeric_row_not_coerced_or_zero_filled(case,value):
 # Unsupported cells fail at actual driver decode when fixture raw tagging is malformed.
 try:alter(case,value=value)
 except ValueError:
  state=json.loads(raw(case));state['workbook']['sheets'][0]['rows'][0]['cells'][4][1]=['float' if type(value) is float else 'int' if type(value) is int else 'decimal',str(value)];case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 deny(lambda:call(case))


@pytest.mark.parametrize('target',[1,True,1.5,Decimal('-1'),Decimal('NaN'),Decimal('1e19')])
def test_malformed_existing_metric_definition_target_never_normalized(case,target,monkeypatch):
 d=definition();object.__setattr__(d,'target',target)
 def fail(*a,**kw):pytest.fail('invalid definition reached snapshot/core')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail);case.reader=reader(case);monkeypatch.setattr(reports,'kpi_snapshot',fail)
 deny(lambda:call(case,definitions=(d,)))


@pytest.mark.parametrize('changes',[{'owner':'foreign'},{'owner':None},{'start':'2026-10-17'},{'end':'2026-10-20'},{'start':'18.10.2026'},{'unit':'money'},{'currency':''},{'currency':'RUB'},{'observed':''},{'observed':'2026-10-20'},{'observed':'2026-10-17'},{'value':Decimal(1),'observed':None}])
def test_exact_row_scope_date_unit_currency_known_date_required(case,changes):
 alter(case,**changes);deny(lambda:call(case))


@pytest.mark.parametrize('change',['sheet','missing_row','duplicate_row','missing_cell','duplicate_cell','duplicate_binding','same_row_two_metrics'])
def test_actual_lookup_exact_ambiguity_and_schema_whole_refusal(case,change):
 book=case.book;r=book.sheets[0].rows[0]
 if change=='sheet':case.bindings=(replace(case.bindings[0],sheet_id='absent'),)
 elif change=='missing_row':case.bindings=(replace(case.bindings[0],identity=core.RowIdentity('id','absent')),)
 elif change=='duplicate_row':book=replace(book,sheets=(replace(book.sheets[0],rows=(r,replace(r,position=3))),))
 elif change=='missing_cell':book=replace(book,sheets=(replace(book.sheets[0],rows=(replace(r,cells=tuple(c for c in r.cells if c.column!='observed')),)),))
 elif change=='duplicate_cell':
  state=json.loads(raw(case));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['value',['int','0']]);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 elif change=='duplicate_binding':case.bindings=(case.bindings[0],case.bindings[0])
 elif change=='same_row_two_metrics':
  case.bindings=(case.bindings[0],module.MetricBinding('other','sheet',core.RowIdentity('owner','opaque-owner')));book=replace(book,sheets=(replace(book.sheets[0],rows=(r,)),))
 if change not in ('duplicate_cell',):case.persist(book)
 case.reader=reader(case)
 defs=(definition(),definition('other')) if change=='same_row_two_metrics' else (definition(),)
 deny(lambda:call(case,definitions=defs))


@pytest.mark.parametrize('bad',['period_two_days','future_period','coverage_future','coverage_before_end','coverage_bool','datetime_date','definitions_list','missing_definition','duplicate_definition','now_naive'])
def test_explicit_daily_coverage_definition_and_clock_types(case,bad):
 args={}
 if bad=='period_two_days':args['period']=reports.Period(PERIOD.start,PERIOD.end+timedelta(days=1))
 elif bad=='future_period':args['period']=reports.Period(PERIOD.end+timedelta(days=1),PERIOD.end+timedelta(days=2))
 elif bad=='coverage_future':args['coverage_observed_on']=PERIOD.end+timedelta(days=1)
 elif bad=='coverage_before_end':args['coverage_observed_on']=PERIOD.start
 elif bad=='coverage_bool':args['coverage_complete']=1
 elif bad=='datetime_date':args['coverage_observed_on']=NOW
 elif bad=='definitions_list':args['definitions']=[definition()]
 elif bad=='missing_definition':args['definitions']=(definition('other'),)
 elif bad=='duplicate_definition':args['definitions']=(definition(),definition())
 else:args['now']=NOW.replace(tzinfo=None)
 deny(lambda:call(case,**args))


def test_defaultdisabled_zero_callbacks_snapshot_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled reader touched provider/SQL')
 monkeypatch.setattr(case.s,'execute',fail);case.state['source']=None
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail)
 deny(lambda:r.read_daily(period=PERIOD,definitions=(definition(),),coverage_complete=True,coverage_observed_on=PERIOD.end,now=NOW))


def test_disabled_actual_driver_not_bypassed(case):
 driver=object.__new__(driver_module.SQLiteTableDriver);DRIVER_INIT(driver,store=case.s,current_context=case.context)
 case.reader=reader(case,driver=driver);deny(lambda:call(case));assert case.state['driver_calls']==0


@pytest.mark.parametrize('change',['business','workbook','owner','timezone','revision','expired','future','revoked','driver','callback','method'])
def test_current_source_driver_public_method_scope_drift(case,change):
 source=case.source
 if change=='business':case.state['source']=replace(source,business='OTHER')
 elif change=='workbook':case.state['source']=replace(source,workbook_id='sqlite-book:'+'0'*64)
 elif change=='owner':case.state['source']=replace(source,owner_id='other-owner')
 elif change=='timezone':case.state['source']=replace(source,timezone_name='Europe/Moscow')
 elif change=='revision':case.state['source']=replace(source,revision=2)
 elif change=='expired':case.state['source']=replace(source,expires_at=NOW)
 elif change=='future':case.state['source']=replace(source,valid_from=NOW+timedelta(hours=1))
 elif change=='revoked':case.state['source']=replace(source,revoked=True)
 elif change=='driver':case.reader._driver=object()
 elif change=='callback':case.reader._callback=lambda:source
 else:
  original=case.driver.snapshot
  case.driver.snapshot=MethodType(lambda self:original(),case.driver)
 before=raw(case);deny(lambda:call(case));assert raw(case)==before


@pytest.mark.parametrize('change',['selected','unmapped','decimal_scale','signed_zero','bool_int','position','revision','sheet_order','row_order'])
def test_full_typed_final_snapshot_fingerprint_race_no_equal_numeric_fallback(case,monkeypatch,change):
 # Start with types whose numerical equality cannot be trusted as a snapshot proof.
 book=alter(case,unmapped=Decimal('1.0'));case.book=book
 real=reports.kpi_snapshot
 def kpi(**kw):
  result=real(**kw);sheet=book.sheets[0];r=sheet.rows[0];other=sheet.rows[1]
  if change in ('selected','unmapped','decimal_scale','signed_zero','bool_int'):
   column='value' if change=='selected' else 'unmapped'
   value={'selected':Decimal(3),'unmapped':'CHANGED','decimal_scale':Decimal('1.00'),'signed_zero':Decimal('-0.00'),'bool_int':True}[change]
   if change=='signed_zero':
    # Distinct signed zero must remain distinct even though Decimal compares equal.
    value=Decimal('-0.00')
   r=replace(r,cells=tuple(core.Cell(c.column,value if c.column==column else c.value) for c in r.cells))
  if change=='position':r=replace(r,position=5)
  if change=='revision':r=replace(r,revision='row-v2')
  rows=(other,r) if change=='row_order' else (r,other)
  sheets=(replace(sheet,rows=rows),)
  if change=='sheet_order':sheets=(core.Sheet('extra','extra-v1',()),)+sheets
  case.persist(replace(book,sheets=sheets));return result
 monkeypatch.setattr(reports,'kpi_snapshot',kpi);deny(lambda:call(case))


def test_exact_decimal_scale_and_boolint_identity_change_even_dataclass_equality(case,monkeypatch):
 for old,new in [(Decimal('1.0'),Decimal('1.00')),(Decimal('0.00'),Decimal('-0.00')),(1,True)]:
  first=alter(case,unmapped=old);second=alter(case,unmapped=new)
  assert first==second and module._workbook_fingerprint(first)!=module._workbook_fingerprint(second)


def test_callback_revocation_after_actual_core_and_context_identity_replacement(case,monkeypatch):
 real=reports.kpi_snapshot
 def kpi(**kw):value=real(**kw);case.state['source']=replace(case.source,revoked=True);return value
 monkeypatch.setattr(reports,'kpi_snapshot',kpi);deny(lambda:call(case))
 case.state['source']=case.source;case.state['context']=replace(case.ctx)
 deny(lambda:call(case))


def test_two_actual_snapshots_four_readonly_runtime_transfers_no_nonce_change(case,monkeypatch):
 calls=[];actual=driver_module.SQLiteTableDriver.snapshot
 def snapshot(self):calls.append(self);return actual(self)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case)
 before=raw(case);trace=[]
 def authorizer(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION):pytest.fail('reader side effect '+str((action,table,column)))
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorizer);case.s.db.set_trace_callback(trace.append)
 call(case);case.s.db.set_authorizer(None);case.s.db.set_trace_callback(None)
 assert len(calls)==2 and len(trace)==4 and all('CASE WHEN typeof(value)' in q and 'LIMIT 2' in q and case.ctx.runtime_key in q for q in trace)
 assert raw(case)==before and json.loads(before)['used_nonces']==[]


def test_local_timezone_controls_future_observation_day_not_machine_clock(case):
 # UTC18:30 is next local calendar date in Tokyo; explicit source assertion only.
 source=replace(case.source,timezone_name='Asia/Tokyo');case.source=source;case.state['source']=source;case.reader=reader(case)
 now=NOW.replace(hour=18,minute=30);observed=date(2026,10,20);alter(case,observed=observed.isoformat())
 value=call(case,now=now,coverage_observed_on=observed)
 assert value.lines[0].status=='known' and value.as_of==now


def test_exact_metadata_columns_and_binding_caps(case):
 with pytest.raises(ValueError):module.ColumnSchema('same','same','end','value','unit','currency','observed')
 for bindings in ([],(),case.bindings*4):
  case.reader=reader(case,bindings=bindings);deny(lambda:call(case))
