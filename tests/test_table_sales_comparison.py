"""Actual durable selected sales sample -> SAME Sale tuple -> two actual cores."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json,sqlite3
import pytest
from goldway import table_sales_comparison as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;finance=module.finance
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc);MONDAY=date(2026,10,19)
KEY=b'OPENLY-SYNTHETIC-SALES-TEST-KEY-0001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


def columns():return module.SaleColumns('operation','date','currency','revenue','cogs','barter','kind')
def row(identifier='current',position=1,**kw):
 d=dict(operation=identifier,date=MONDAY.isoformat(),currency='RUB',revenue=Decimal(200),cogs=None,barter=False,kind='sale',unmapped=Decimal('1.0'));d.update(kw);return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in d.items()))
def rows():return (row('prior',position=1,date='2026-10-12',revenue=Decimal(100),cogs=Decimal(60)),row('current',position=2),row('under',position=3,date='2026-10-20',revenue=Decimal(0),cogs=Decimal(5)),row('barter',position=4,date='2026-10-20',revenue=Decimal(999),cogs=None,barter=True),row('known',position=5,date='2026-10-21',revenue=Decimal(50),cogs=Decimal(20)))
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='sales',columns=c.columns,enabled=True);d.update(kw);return module.TableSalesComparisonReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-sales','synthetic-approval','sales-book','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('sales','sheet-v1',rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.SalesSource('SYNTHETIC','opaque-account',ctx.workbook_id,module.SourceRole.SALES_SAMPLE,1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,columns=columns());c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_cogs(value):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('apply_to_fake_workbook',CORE_APPLY),('check_plan_baseline',CHECK)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   p=BUILD(writer,'sales',core.RowIdentity('operation','current'),(core.Cell('cogs',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_cogs=approved_cogs
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
 d=dict(week_start=MONDAY,currency='RUB',now=NOW);d.update(kw);return c.reader.read_weeks(**d)
def deny(fn):
 with pytest.raises(module.SalesComparisonUnavailable,match='^table_sales_comparison_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,**changes):
 rows=tuple(replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells)) if r.position==2 else r for r in c.book.sheets[0].rows);return selected(c,rows)


def test_actual_durable_same_sale_tuple_both_cores_restart_approved_cogs_changes_flags_scope(case,monkeypatch):
 calls=[];compare=finance.compare_weeks;flags=finance.cost_flags;lookup=core.lookup_row
 def first(records,**kw):calls.append(('compare',records));return compare(records,**kw)
 def second(records,**kw):calls.append(('flags',records));return flags(records,**kw)
 finds=[]
 def find(*a,**kw):finds.append(kw['identity']);return lookup(*a,**kw)
 monkeypatch.setattr(finance,'compare_weeks',first);monkeypatch.setattr(finance,'cost_flags',second);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);report=call(case);metrics=report.comparison.current
 assert len(calls)==2 and calls[0][0]=='compare' and calls[1][0]=='flags' and calls[0][1] is calls[1][1] and type(calls[0][1]) is tuple and all(type(s) is finance.Sale for s in calls[0][1])
 assert len(finds)==5 and report.sample_count==5 and metrics.sale_count==3 and metrics.revenue==Decimal(250)
 assert metrics.known_cost_count==2 and metrics.unknown_cost_count==1 and metrics.known_cost_revenue==Decimal(50) and metrics.known_cogs==Decimal(25) and metrics.gross_profit==Decimal(25)
 assert metrics.barter_count==1 and metrics.known_barter_cost==Decimal(0) and metrics.unknown_barter_cost_count==1
 assert {(f.sale_id,f.kind) for f in report.flags}=={('current','missing_cost'),('under','under_cost'),('barter','missing_cost')}
 assert report.comparison.revenue_delta==Decimal(150) and report.comparison.revenue_change_pct==Decimal(150) and report.comparison.gross_margin_change_pp==Decimal(10)
 assert report.company_completeness is None and report.requires_review and not report.live_authorized and report.scope=='declared_sales_sample' and raw(case)==before
 case.reopen();same=call(case);assert same.comparison==report.comparison and same.flags==report.flags and same.workbook_fingerprint==report.workbook_fingerprint
 receipt=case.approved_cogs(Decimal(100));assert receipt.state=='local_fake_committed';case.reopen();before=raw(case);updated=call(case)
 assert updated.comparison.current.known_cost_count==3 and updated.comparison.current.unknown_cost_count==0 and updated.comparison.current.known_cost_revenue==Decimal(250) and updated.comparison.current.gross_profit==Decimal(125)
 assert ('current','missing_cost') not in {(f.sale_id,f.kind) for f in updated.flags} and updated.workbook_fingerprint!=report.workbook_fingerprint and raw(case)==before
 assert json.loads(before)['used_nonces']==['synthetic-nonce'] and KEY.decode() not in repr(updated) and not hasattr(updated,'workbook')
 with pytest.raises(FrozenInstanceError):updated.company_completeness=True


def test_empty_sample_not_missing_sheet_and_no_company_completeness(case):
 selected(case,());value=call(case);assert value.sample_count==0 and not value.flags and value.company_completeness is None
 current=value.comparison.current;assert current.revenue==Decimal(0) and current.gross_profit is None and current.gross_margin_pct is None and current.cost_coverage is None
 assert value.comparison.revenue_change_pct is None and value.comparison.known_scope_gross_profit_delta is None
 case.reader=reader(case,sheet_id='absent');deny(lambda:call(case))


@pytest.mark.parametrize('records,expected',[( (row(revenue=Decimal(0),cogs=Decimal(5)),), 'under_cost'),((row(revenue=Decimal(0),cogs=Decimal(0)),),None),((row(revenue=Decimal(0),cogs=None),),'missing_cost'),((row(revenue=Decimal(100),cogs=Decimal(500),barter=True),),None),((row(revenue=Decimal(0),cogs=None,barter=True),),'missing_cost')])
def test_zero_not_barter_and_actual_unknown_cost_under_cost_exclusions(case,records,expected):
 selected(case,records);result=call(case)
 assert tuple(f.kind for f in result.flags)==((expected,) if expected else ())
 if records[0].cells[5].value is None:pass
 assert result.company_completeness is None


def test_no_known_cost_scope_and_zero_previous_revenue_preserve_none(case):
 selected(case,(row('prior',date='2026-10-12',revenue=Decimal(0),cogs=None),row('current',position=2,revenue=Decimal(7),cogs=None)))
 result=call(case);assert result.comparison.current.gross_profit is None and result.comparison.prior.gross_profit is None
 assert result.comparison.revenue_change_pct is None and result.comparison.gross_margin_change_pp is None and result.comparison.known_scope_gross_profit_delta is None


def test_known_cost_zero_denominator_margin_none_and_barter_known_subtotal_scope(case):
 selected(case,(row('zero',revenue=Decimal(0),cogs=Decimal(5)),row('barter',position=2,barter=True,cogs=Decimal(7)),row('barter-missing',position=3,barter=True,cogs=None)))
 r=call(case).comparison.current
 assert r.sale_count==1 and r.revenue==Decimal(0) and r.gross_profit==Decimal(-5) and r.gross_margin_pct is None
 assert r.barter_count==2 and r.known_barter_cost==Decimal(7) and r.unknown_barter_cost_count==1


def test_distinct_identical_value_operations_kept_duplicate_id_refused(case):
 selected(case,(row('a',revenue=Decimal(2),cogs=Decimal(1)),row('b',position=2,revenue=Decimal(2),cogs=Decimal(1))))
 assert call(case).comparison.current.revenue==Decimal(4)
 selected(case,(row('same'),row('same',position=2)));deny(lambda:call(case))


@pytest.mark.parametrize('field',['revenue','cogs'])
@pytest.mark.parametrize('value',[True,False,1.5,'2','=SUM(A1)',Decimal('-1'),Decimal('NaN'),Decimal('Infinity'),Decimal('1e19'),Decimal('1e-19'),Decimal('1'*51),10**19])
def test_strict_numeric_source_no_core_string_coercion(case,field,value):
 try:alter(case,**{field:value})
 except ValueError:
  state=json.loads(raw(case));cells=state['workbook']['sheets'][0]['rows'][1]['cells'];next(c for c in cells if c[0]==field)[1]=['float' if type(value) is float else 'int' if type(value) is int else 'decimal',str(value)];case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 deny(lambda:call(case))


def test_revenue_none_refused_but_zero_and_cogs_none_distinct(case):alter(case,revenue=None);deny(lambda:call(case))


@pytest.mark.parametrize('changes',[{'currency':'USD'},{'currency':'rub'},{'date':'2026-10-11'},{'date':'2026-10-26'},{'date':'2026-10-22'},{'date':'19.10.2026'},{'date':'2026-10-19T00:00:00+00:00'},{'kind':'return'},{'kind':'credit_note'},{'barter':None},{'barter':0},{'operation':''},{'operation':' spaced '}])
def test_whole_sample_exact_dates_currency_kind_and_economic_identity(case,changes):alter(case,**changes);deny(lambda:call(case))


@pytest.mark.parametrize('args',[{'week_start':date(2026,10,20)},{'week_start':NOW},{'week_start':date.min},{'week_start':date(9999,12,27)},{'currency':'rub'},{'now':NOW.replace(tzinfo=None)}])
def test_monday_date_overflow_currency_and_aware_clock(case,args):deny(lambda:call(case,**args))


def test_all_twelve_rows_consumed_thirteen_refused_no_filter_or_truncation(case,monkeypatch):
 records=tuple(row(str(i),position=i+1,revenue=Decimal(1),cogs=0) for i in range(12));selected(case,records);actual=core.lookup_row;calls=[]
 def find(*a,**kw):calls.append(kw['identity']);return actual(*a,**kw)
 monkeypatch.setattr(core,'lookup_row',find);report=call(case)
 assert report.sample_count==12 and report.comparison.current.sale_count==12 and len(calls)==12 and len(report.observation_fingerprints)==12
 selected(case,records+(row('extra',position=13),));deny(lambda:call(case))


@pytest.mark.parametrize('change',['missing_cell','duplicate_cell','ambiguous_id','wrong_sheet'])
def test_required_cells_and_actual_lookup_unique_identity(case,change):
 if change=='wrong_sheet':case.reader=reader(case,sheet_id='other')
 elif change=='missing_cell':
  r=case.book.sheets[0].rows[0];r=replace(r,cells=tuple(c for c in r.cells if c.column!='barter'));selected(case,(r,))
 elif change=='duplicate_cell':
  state=json.loads(raw(case));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['cogs',['null',None]]);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 else:selected(case,(row('a'),row('a',position=2)))
 deny(lambda:call(case))


@pytest.mark.parametrize('change',['business','account','workbook','role','revision','timezone','revoked','expired','future','method','callback','context'])
def test_current_source_method_scope_and_driver_context_drift(case,change):
 if change=='method':
  original=case.driver.snapshot;case.driver.snapshot=MethodType(lambda self:original(),case.driver)
 elif change=='callback':case.reader._callback=lambda:case.source
 elif change=='context':call(case);case.state['context']=replace(case.ctx)
 else:
  key,value={'business':('business','OTHER'),'account':('account_scope','other-account'),'workbook':('workbook_id','sqlite-book:'+'0'*64),'role':('role','sales_sample'),'revision':('revision',2),'timezone':('timezone_name','Asia/Tokyo'),'revoked':('revoked',True),'expired':('expires_at',NOW),'future':('valid_from',NOW+timedelta(hours=1))}[change]
  source=replace(case.source)
  if change=='role':object.__setattr__(source,key,value)
  else:source=replace(source,**{key:value})
  case.state['source']=source
 before=raw(case);deny(lambda:call(case));assert raw(case)==before


@pytest.mark.parametrize('change',['selected','unselected','scale','signedzero','boolint','revision','sheet_order','row_order','source','columns'])
def test_full_typed_final_snapshot_or_policy_change_refuses_whole_report(case,monkeypatch,change):
 book=case.book
 if change in ('signedzero','boolint'):
  other=book.sheets[1];r=other.rows[0];r=replace(r,cells=(r.cells[0],core.Cell('value',Decimal('0.00') if change=='signedzero' else 1)));book=replace(book,sheets=(book.sheets[0],replace(other,rows=(r,))));case.book=book;case.persist(book)
 actual=finance.cost_flags
 def flags(*a,**kw):
  result=actual(*a,**kw)
  if change=='selected':alter(case,revenue=Decimal(201))
  elif change in ('unselected','scale','signedzero','boolint'):
   other=book.sheets[1];r=other.rows[0];v={'unselected':'CHANGED','scale':Decimal('1.00'),'signedzero':Decimal('-0.00'),'boolint':True}[change];r=replace(r,cells=(r.cells[0],core.Cell('value',v)));case.persist(replace(book,sheets=(book.sheets[0],replace(other,rows=(r,)))))
  elif change=='revision':case.persist(replace(book,revision='book-v2'))
  elif change=='sheet_order':case.persist(replace(book,sheets=tuple(reversed(book.sheets))))
  elif change=='row_order':case.persist(replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1])))
  elif change=='source':case.state['source']=replace(case.source,revoked=True)
  else:case.reader._columns=replace(case.columns)
  return result
 monkeypatch.setattr(finance,'cost_flags',flags);deny(lambda:call(case))


@pytest.mark.parametrize('bad',['comparison_type','week_type','currency','period','count_bool','nan','flags_list','unknown_id','unknown_flag','duplicate_flag'])
def test_malformed_actual_result_closed_scope_refuses(case,monkeypatch,bad):
 compare=finance.compare_weeks;flags=finance.cost_flags
 def comparison(*a,**kw):
  result=compare(*a,**kw)
  if bad=='comparison_type':return {}
  if bad=='week_type':return replace(result,current={})
  if bad=='currency':return replace(result,current=replace(result.current,currency='USD'))
  if bad=='period':return replace(result,current=replace(result.current,start=MONDAY+timedelta(days=1)))
  if bad=='count_bool':return replace(result,current=replace(result.current,sale_count=True))
  if bad=='nan':return replace(result,revenue_delta=Decimal('NaN'))
  return result
 def flagged(*a,**kw):
  result=flags(*a,**kw)
  if bad=='flags_list':return list(result)
  if bad=='unknown_id':return (finance.CostFlag('foreign','missing_cost'),)
  if bad=='unknown_flag':return (finance.CostFlag('current','paid'),)
  if bad=='duplicate_flag':return result+(result[0],)
  return result
 monkeypatch.setattr(finance,'compare_weeks',comparison);monkeypatch.setattr(finance,'cost_flags',flagged);deny(lambda:call(case))


def test_defaultdisabled_zero_callback_snapshot_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched source')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail);monkeypatch.setattr(case.s,'execute',fail)
 deny(lambda:r.read_weeks(week_start=MONDAY,currency='RUB',now=NOW));assert case.state['source_calls']==case.state['driver_calls']==0


def test_disabled_actual_driver_not_enabled_or_bypassed(case):
 d=object.__new__(driver_module.SQLiteTableDriver);INIT(d,store=case.s,current_context=case.context);case.reader=reader(case,driver=d);deny(lambda:call(case));assert case.state['driver_calls']==0


def test_two_public_snapshots_four_bounded_readonly_transfers_no_nonce_change(case,monkeypatch):
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


def test_source_timezone_controls_future_row_date_not_machine_clock(case):
 source=replace(case.source,timezone_name='Asia/Tokyo');case.source=source;case.state['source']=source;case.reader=reader(case);selected(case,(row(date='2026-10-22'),))
 value=call(case,now=NOW.replace(hour=18,minute=30));assert value.sample_count==1 and value.as_of.hour==18
