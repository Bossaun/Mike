"""Actual persisted FREE/SALES/PLAN triplets -> existing inventory cores only."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json,sqlite3
import pytest
from goldway import table_stock_plan as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;inv=module.inventory
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc);DAY=NOW.date()
KEY=b'OPENLY-SYNTHETIC-STOCK-PLAN-TEST-KEY-0001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


def columns():return module.StockPlanColumns('id','role','sku','variant','location','batch','unit','date','units','days')
def row(identifier,role,position,sku='A',units=Decimal(5),**kw):
 d=dict(id=identifier,role=role,sku=sku,variant='variant',location='location',batch='batch',unit='piece',date=DAY.isoformat(),units=units,days=30 if role=='sales' else None,unmapped=Decimal('1.0'));d.update(kw);return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in d.items()))
def rows():
 result=[]
 for index,(sku,stock,sold,plan,days) in enumerate((('A',5,10,5,30),('B',2,0,0,90),('C',3,10,None,30))):
  for offset,(role,amount) in enumerate((('free',stock),('sales',sold),('plan',plan))):result.append(row(sku+'-'+role,role,index*3+offset+1,sku=sku,units=None if amount is None else Decimal(amount),days=days if role=='sales' else None))
 return tuple(result)
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='planning',columns=c.columns,enabled=True);d.update(kw);return module.TableStockPlanReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-stock-plan','synthetic-approval','planning','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('planning','sheet-v1',rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.StockPlanSource('SYNTHETIC','opaque-warehouse-source',ctx.workbook_id,module.SourceRole.STOCK_PLAN_SAMPLE,1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,columns=columns());c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_plan(value):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('apply_to_fake_workbook',CORE_APPLY),('check_plan_baseline',CHECK)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   p=BUILD(writer,'planning',core.RowIdentity('id','A-plan'),(core.Cell('units',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_plan=approved_plan
 def fail(*a,**kw):pytest.fail('reader used constructor/approval/write/network/model/outbox path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',fail)
 for name in ('apply','build_plan'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,fail)
 for name in ('build_plan','validate_approval','check_plan_baseline','apply_to_fake_workbook','issue_local_approval'):monkeypatch.setattr(core,name,fail)
 monkeypatch.setattr(Store,'queue',fail)
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]
def call(c,**kw):
 d=dict(observed_on=DAY,month_days=Decimal(30),thresholds=None,now=NOW);d.update(kw);return c.reader.read_plan(**d)
def deny(fn):
 with pytest.raises(module.StockPlanUnavailable,match='^table_stock_plan_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,identifier='A-free',**changes):
 records=[]
 for r in c.book.sheets[0].rows:
  if next(cell.value for cell in r.cells if cell.column=='id')==identifier:r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells))
  records.append(r)
 return selected(c,records)


def test_actual_three_durable_triplets_core_counts_hypothetical_restart_approved_plan_change(case,monkeypatch):
 previews=[];coverages=[];lookups=[];preview=inv.procurement_preview;coverage=inv.coverage;lookup=core.lookup_row
 def first(*a,**kw):previews.append(a);return preview(*a,**kw)
 def second(*a,**kw):coverages.append(a);return coverage(*a,**kw)
 def find(*a,**kw):lookups.append(kw['identity']);return lookup(*a,**kw)
 monkeypatch.setattr(inv,'procurement_preview',first);monkeypatch.setattr(inv,'coverage',second);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);r=call(case);a,b,c=r.lines
 assert len(previews)==3 and len(coverages)==6 and len(lookups)==9 and len(r.observation_fingerprints)==9
 assert all(coverages[i][1] is coverages[i+1][1] for i in (0,2,4))
 assert a.preview.expected_free_units==Decimal(10) and a.before.months==Decimal('0.5') and a.after.months==Decimal(1)
 assert b.before.state is inv.CoverageState.INFINITE and b.after.state is inv.CoverageState.INFINITE
 assert c.preview.expected_free_units is inv.UNKNOWN and c.before.state is inv.CoverageState.FINITE and c.after.state is inv.CoverageState.UNKNOWN
 assert all(line.before_flags is None and line.after_flags is None and line.hypothetical and line.hypothetical_after.source_id.startswith('hypothetical-stock-plan:') for line in r.lines)
 assert r.requires_review and not r.live_authorized and not r.warehouse_activation and not r.purchase_authorized and raw(case)==before
 case.reopen();same=call(case);assert same.lines==r.lines and same.workbook_fingerprint==r.workbook_fingerprint
 receipt=case.approved_plan(Decimal(15));assert receipt.state=='local_fake_committed';case.reopen();before=raw(case);updated=call(case)
 assert updated.lines[0].preview.expected_free_units==Decimal(20) and updated.lines[0].after.months==Decimal(2) and updated.workbook_fingerprint!=r.workbook_fingerprint and raw(case)==before
 assert json.loads(before)['used_nonces']==['synthetic-nonce'] and KEY.decode() not in repr(updated) and not hasattr(updated,'workbook')
 with pytest.raises(FrozenInstanceError):updated.warehouse_activation=True


def test_explicit_threshold_flags_equality_zero_demand_no_duplicate_coverage_calls(case,monkeypatch):
 import inspect
 thresholds=inv.Thresholds(Decimal('0.5'),Decimal(1),90);flags=[];coverage_callers=[];actual_flags=inv.stock_flags;actual_coverage=inv.coverage
 def flagged(*a,**kw):flags.append(a);return actual_flags(*a,**kw)
 def coverage(*a,**kw):coverage_callers.append(inspect.currentframe().f_back.f_code.co_name);return actual_coverage(*a,**kw)
 monkeypatch.setattr(inv,'stock_flags',flagged);monkeypatch.setattr(inv,'coverage',coverage)
 value=call(case,thresholds=thresholds);a,b,c=value.lines
 assert len(flags)==6 and coverage_callers==['stock_flags']*6 and a.before is a.before_flags.coverage and a.after is a.after_flags.coverage
 assert a.before_flags.low_stock is False and a.after_flags.overstock is False
 assert b.before_flags.no_sales is True and b.before_flags.overstock is True and b.before_flags.low_stock is False
 assert c.after_flags.low_stock is inv.UNKNOWN and c.after_flags.overstock is inv.UNKNOWN


@pytest.mark.parametrize('role',['free','sales','plan'])
def test_unknown_quantity_stays_unknown_not_zero(case,role):
 alter(case,'A-'+role,units=None);line=call(case).lines[0]
 if role!='plan':assert line.before.state is inv.CoverageState.UNKNOWN
 else:assert line.before.state is inv.CoverageState.FINITE
 assert line.after.state is inv.CoverageState.UNKNOWN


@pytest.mark.parametrize('dimension',['variant','location','batch'])
def test_unknown_dimensions_no_finite_addition_or_coverage(case,dimension):
 records=[]
 for r in case.book.sheets[0].rows:
  if next(c.value for c in r.cells if c.column=='sku')=='A':r=replace(r,cells=tuple(core.Cell(c.column,None if c.column==dimension else c.value) for c in r.cells))
  records.append(r)
 selected(case,records);line=call(case).lines[0]
 assert line.preview.expected_free_units is inv.UNKNOWN and line.before.state is inv.CoverageState.UNKNOWN and 'scope' in line.before.reasons and line.after.state is inv.CoverageState.UNKNOWN


def test_zero_stock_zero_sales_and_positive_stock_zero_sales_not_finite(case):
 altered=alter(case,'B-free',units=Decimal(0));case.book=altered;alter(case,'B-plan',units=Decimal(0));value=call(case).lines[1]
 assert value.before.state is inv.CoverageState.UNKNOWN and value.before.reasons==('zero_stock_and_zero_sales',) and value.after.state is inv.CoverageState.UNKNOWN


@pytest.mark.parametrize('days,no_sales',[(89,inv.UNKNOWN),(90,True),(91,True)])
def test_no_sales_depends_explicit_duration_and_threshold(case,days,no_sales):
 alter(case,'B-sales',days=days);value=call(case,thresholds=inv.Thresholds(Decimal(1),Decimal(2),90));assert value.lines[1].before_flags.no_sales is no_sales


@pytest.mark.parametrize('change',['missing_role','duplicate_role','duplicate_id','unrelated_role','wrong_sku','wrong_location','wrong_batch','wrong_unit','wrong_date','unknown_id','missing_cell','duplicate_cell','too_few','too_many','missing_sheet'])
def test_complete_exact_triplets_or_whole_refusal_no_padding(case,change):
 records=list(case.book.sheets[0].rows)
 if change=='missing_role':records.pop(2)
 elif change=='duplicate_role':records[2]=replace(records[2],cells=tuple(core.Cell(c.column,'free' if c.column=='role' else c.value) for c in records[2].cells))
 elif change=='duplicate_id':records[2]=replace(records[2],cells=tuple(core.Cell(c.column,'A-free' if c.column=='id' else c.value) for c in records[2].cells))
 elif change in ('unrelated_role','wrong_sku','wrong_location','wrong_batch','wrong_unit','wrong_date','unknown_id'):
  key,value={'unrelated_role':('role','book'),'wrong_sku':('sku','OTHER'),'wrong_location':('location','OTHER'),'wrong_batch':('batch','OTHER'),'wrong_unit':('unit','OTHER'),'wrong_date':('date','2026-10-20'),'unknown_id':('id','')}[change];records[2]=replace(records[2],cells=tuple(core.Cell(c.column,value if c.column==key else c.value) for c in records[2].cells))
 elif change=='missing_cell':records[0]=replace(records[0],cells=tuple(c for c in records[0].cells if c.column!='days'))
 elif change=='duplicate_cell':
  state=json.loads(raw(case));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['days',['null',None]]);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 elif change=='too_few':records=records[:2]
 elif change=='too_many':records.append(replace(records[0],position=10))
 elif change=='missing_sheet':case.reader=reader(case,sheet_id='absent')
 if change!='duplicate_cell':selected(case,records)
 deny(lambda:call(case))


@pytest.mark.parametrize('role',['free','sales','plan'])
@pytest.mark.parametrize('value',[True,1.5,'5','=SUM(A1)',Decimal('-1'),Decimal('NaN'),Decimal('Infinity'),Decimal('1e19'),Decimal('1e-19'),Decimal('1'*51),10**19])
def test_strict_source_quantities_not_string_or_formula_coercion(case,role,value):
 try:alter(case,'A-'+role,units=value)
 except ValueError:
  state=json.loads(raw(case));r=next(r for r in state['workbook']['sheets'][0]['rows'] if next(c[1][1] for c in r['cells'] if c[0]=='id')=='A-'+role);next(c for c in r['cells'] if c[0]=='units')[1]=['float' if type(value) is float else 'int' if type(value) is int else 'decimal',str(value)];case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 deny(lambda:call(case))


@pytest.mark.parametrize('days',[None,True,0,36601,Decimal(30),'30'])
def test_sales_days_explicit_exact_bound_no_default(case,days):alter(case,'A-sales',days=days);deny(lambda:call(case))
@pytest.mark.parametrize('role',['free','plan'])
def test_non_sales_days_must_be_typed_null(case,role):alter(case,'A-'+role,days=30);deny(lambda:call(case))


@pytest.mark.parametrize('month',[None,True,30,'30',Decimal(0),Decimal(-1),Decimal(36601),Decimal('NaN'),Decimal('1e-19')])
def test_month_days_explicit_positive_decimal_no_chosen_convention(case,month):deny(lambda:call(case,month_days=month))


def test_thresholds_strict_actual_type_unknown_partial_never_defaulted(case):
 for thresholds in ({'low':1},True,Decimal(1)):
  deny(lambda:call(case,thresholds=thresholds))
 thresholds=inv.Thresholds(Decimal(1),Decimal(2),90);object.__setattr__(thresholds,'low_months',inv.UNKNOWN);deny(lambda:call(case,thresholds=thresholds))


@pytest.mark.parametrize('change',['business','warehouse','workbook','role','revision','timezone','revoked','expired','future','callback','method','context'])
def test_current_source_config_method_and_driver_context_drift(case,change):
 if change=='callback':case.reader._callback=lambda:case.source
 elif change=='method':
  original=case.driver.snapshot;case.driver.snapshot=MethodType(lambda self:original(),case.driver)
 elif change=='context':call(case);case.state['context']=replace(case.ctx)
 else:
  key,value={'business':('business','OTHER'),'warehouse':('warehouse_source_scope','OTHER'),'workbook':('workbook_id','sqlite-book:'+'0'*64),'role':('role','stock_plan_sample'),'revision':('revision',2),'timezone':('timezone_name','Asia/Tokyo'),'revoked':('revoked',True),'expired':('expires_at',NOW),'future':('valid_from',NOW+timedelta(hours=1))}[change];source=replace(case.source)
  if change=='role':object.__setattr__(source,key,value)
  else:source=replace(source,**{key:value})
  case.state['source']=source
 before=raw(case);deny(lambda:call(case));assert raw(case)==before


@pytest.mark.parametrize('change',['selected','unselected','scale','signedzero','boolint','revision','row_order','sheet_order','source','columns','threshold'])
def test_final_typed_snapshot_source_or_explicit_input_drift_refuses_all(case,monkeypatch,change):
 book=case.book
 if change in ('signedzero','boolint'):
  other=book.sheets[1];r=other.rows[0];r=replace(r,cells=(r.cells[0],core.Cell('value',Decimal('0.00') if change=='signedzero' else 1)));book=replace(book,sheets=(book.sheets[0],replace(other,rows=(r,))));case.book=book;case.persist(book)
 thresholds=inv.Thresholds(Decimal(1),Decimal(2),90);actual=inv.procurement_preview;fired=[]
 def preview(*a,**kw):
  result=actual(*a,**kw)
  if fired:return result
  fired.append(True)
  if change=='selected':alter(case,'A-plan',units=Decimal(8))
  elif change in ('unselected','scale','signedzero','boolint'):
   other=book.sheets[1];r=other.rows[0];v={'unselected':'CHANGED','scale':Decimal('1.00'),'signedzero':Decimal('-0.00'),'boolint':True}[change];r=replace(r,cells=(r.cells[0],core.Cell('value',v)));case.persist(replace(book,sheets=(book.sheets[0],replace(other,rows=(r,)))))
  elif change=='revision':case.persist(replace(book,revision='book-v2'))
  elif change=='row_order':case.persist(replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1])))
  elif change=='sheet_order':case.persist(replace(book,sheets=tuple(reversed(book.sheets))))
  elif change=='source':case.state['source']=replace(case.source,revoked=True)
  elif change=='columns':case.reader._columns=replace(case.columns)
  else:object.__setattr__(thresholds,'low_months',Decimal('1.5'))
  return result
 monkeypatch.setattr(inv,'procurement_preview',preview);deny(lambda:call(case,thresholds=thresholds))


@pytest.mark.parametrize('bad',['preview_type','preview_stock','expected_bool','coverage_type','coverage_state','coverage_nan','coverage_reasons','flags_type','flags_bool'])
def test_malformed_actual_result_fixed_whole_refusal(case,monkeypatch,bad):
 actual_preview=inv.procurement_preview;actual_coverage=inv.coverage;actual_flags=inv.stock_flags
 def preview(*a,**kw):
  result=actual_preview(*a,**kw)
  if bad=='preview_type':return {}
  if bad=='preview_stock':return replace(result,stock=replace(result.stock))
  if bad=='expected_bool':return replace(result,expected_free_units=True)
  return result
 def coverage(*a,**kw):
  result=actual_coverage(*a,**kw)
  if bad=='coverage_type':return {}
  if bad=='coverage_state':return replace(result,state='finite')
  if bad=='coverage_nan':return replace(result,months=Decimal('NaN'))
  if bad=='coverage_reasons':return replace(result,reasons=('PRIVATE',))
  return result
 def flags(*a,**kw):
  result=actual_flags(*a,**kw)
  if bad=='flags_type':return {}
  if bad=='flags_bool':return replace(result,low_stock=1)
  return result
 monkeypatch.setattr(inv,'procurement_preview',preview);monkeypatch.setattr(inv,'coverage',coverage);monkeypatch.setattr(inv,'stock_flags',flags)
 thresholds=inv.Thresholds(Decimal(1),Decimal(2),90) if bad.startswith('flags') else None
 deny(lambda:call(case,thresholds=thresholds))


def test_defaultdisabled_zero_source_snapshot_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched host')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail);monkeypatch.setattr(case.s,'execute',fail)
 deny(lambda:r.read_plan(observed_on=DAY,month_days=Decimal(30),thresholds=None,now=NOW));assert case.state['source_calls']==case.state['driver_calls']==0


def test_disabled_driver_not_enabled_or_bypassed(case):
 d=object.__new__(driver_module.SQLiteTableDriver);INIT(d,store=case.s,current_context=case.context);case.reader=reader(case,driver=d);deny(lambda:call(case));assert case.state['driver_calls']==0


@pytest.mark.parametrize('args',[{'observed_on':NOW},{'observed_on':DAY+timedelta(days=1)},{'now':NOW.replace(tzinfo=None)}])
def test_explicit_date_aware_clock_and_no_future_guess(case,args):deny(lambda:call(case,**args))


def test_two_public_snapshots_four_bounded_readonly_transfers_unchanged_nonces(case,monkeypatch):
 actual=driver_module.SQLiteTableDriver.snapshot;calls=[]
 def snapshot(self):calls.append(self);return actual(self)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case);before=raw(case);trace=[]
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION):pytest.fail('consumer SQL/write/transaction')
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(guard);case.s.db.set_trace_callback(trace.append);call(case);case.s.db.set_authorizer(None);case.s.db.set_trace_callback(None)
 assert len(calls)==2 and len(trace)==4 and all('CASE WHEN typeof(value)' in q and 'LIMIT 2' in q for q in trace)
 assert raw(case)==before and json.loads(before)['used_nonces']==[] and case.s.execute("SELECT value FROM runtime WHERE key='foreign'").fetchone()[0]=='UNTOUCHED'
