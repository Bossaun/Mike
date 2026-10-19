"""Actual two durable synthetic workbooks -> exact lookup -> inventory recount."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json
import sqlite3
import pytest
from goldway import table_recount_reader as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables
inv=module.inventory
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
DAY=NOW.date()
KEY=b'OPENLY-SYNTHETIC-RECOUNT-TEST-KEY-0001'
ISSUE=core.issue_local_approval
APPLY=driver_module.SQLiteTableDriver.apply
BUILD=driver_module.SQLiteTableDriver.build_plan
VALIDATE=core.validate_approval
CORE_APPLY=core.apply_to_fake_workbook
CORE_BUILD=core.build_plan
CHECK=core.check_plan_baseline
INIT=driver_module.SQLiteTableDriver.__init__


def schema():return module.ColumnSchema('sku','variant','location','batch','quantity','unit','day','kind')

def row(identifier,sku='SKU',position=1,quantity=Decimal(5),kind='book',**kw):
 values=dict(id=identifier,sku=sku,variant='variant',location='location',batch='batch',quantity=quantity,unit='piece',day=DAY.isoformat(),kind=kind,unmapped=Decimal('1.0'));values.update(kw)
 return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in values.items()))

def encoded(context,book,nonces=()):
 return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=context.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))

def binding(identifier):return module.RowBinding('sheet',core.RowIdentity('id',identifier))

def reader(c,**kw):
 d=dict(book_driver=c.drivers[0],physical_driver=c.drivers[1],current_sources=c.current,expected_sources=c.sources,book_bindings=c.bindings[0],physical_bindings=c.bindings[1],columns=c.columns,enabled=True);d.update(kw);return module.TableRecountReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 original=Store.__init__;stores=[];contexts=[];books=[];drivers=[];paths=[];callbacks=[]
 for index,kind in enumerate(('book','physical')):
  path=tmp_path/(kind+'.sqlite');s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-'+kind,'synthetic-approval',kind,'synthetic-reviewer','synthetic-key',KEY)
  book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('sheet','sheet-v1',(row('main',quantity=Decimal(5 if index==0 else 3),kind=kind),row('unselected',sku='UNSELECTED',position=2,kind=kind))),))
  s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encoded(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign-runtime','UNTOUCHED'))
  stores.append(s);contexts.append(ctx);books.append(book);paths.append(path)
 state={'sources':None,'contexts':contexts.copy(),'source_calls':0,'driver_calls':[0,0]}
 for index in range(2):
  def callback(index=index):state['driver_calls'][index]+=1;return state['contexts'][index]
  callbacks.append(callback);drivers.append(driver_module.SQLiteTableDriver(store=stores[index],current_context=callback,enabled=True))
 sources=module.SourcePair(module.StockSource('SYNTHETIC',contexts[0].workbook_id,inv.StockKind.BOOK,1,'UTC',NOW-timedelta(days=2),NOW+timedelta(days=2)),module.StockSource('SYNTHETIC',contexts[1].workbook_id,inv.StockKind.PHYSICAL,1,'UTC',NOW-timedelta(days=2),NOW+timedelta(days=2)))
 state['sources']=sources
 def current():state['source_calls']+=1;return state['sources']
 c=SimpleNamespace(stores=stores,contexts=contexts,books=books,drivers=drivers,paths=paths,callbacks=callbacks,state=state,current=current,sources=sources,bindings=((binding('main'),),(binding('main'),)),columns=schema());c.reader=reader(c)
 def persist(index,book,nonces=()):c.stores[index].execute('UPDATE runtime SET value=? WHERE key=?',(encoded(contexts[index],book,nonces),contexts[index].runtime_key))
 def reopen():
  for i in range(2):
   old=c.stores[i];c.stores[i]=object.__new__(Store);original(c.stores[i],str(paths[i]));old.db.close();c.drivers[i]=object.__new__(driver_module.SQLiteTableDriver);INIT(c.drivers[i],store=c.stores[i],current_context=callbacks[i],enabled=True)
  c.reader=reader(c)
 def update_physical(value):
  with monkeypatch.context() as fixture_write:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('check_plan_baseline',CHECK),('apply_to_fake_workbook',CORE_APPLY)):fixture_write.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.stores[1],current_context=callbacks[1],enabled=True,allow_fake_writes=True)
   plan=BUILD(writer,'sheet',core.RowIdentity('id','main'),(core.Cell('quantity',value),))
   token=ISSUE(plan,decision='approve',approver=contexts[1].required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,plan,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.update_physical=update_physical
 def forbidden(*a,**kw):pytest.fail('recount reader used constructor/write/approval/network/send/model/private path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',forbidden)
 for name in ('build_plan','apply'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,forbidden)
 for name in ('issue_local_approval','validate_approval','apply_to_fake_workbook','build_plan','check_plan_baseline'):monkeypatch.setattr(core,name,forbidden)
 monkeypatch.setattr(Store,'queue',forbidden)
 import socket,httpx,builtins,subprocess
 monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden);monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
 yield c
 for s in c.stores:
  s.db.set_authorizer(None)
  if s.db.in_transaction:s.db.rollback()
  s.db.close()


def raw(c,index):return c.stores[index].execute('SELECT value FROM runtime WHERE key=?',(c.contexts[index].runtime_key,)).fetchone()[0]

def call(c,**kw):
 d=dict(observed_on=DAY,now=NOW);d.update(kw);return c.reader.read_recount(**d)

def deny(fn):
 with pytest.raises(module.RecountUnavailable,match='^table_recount_unavailable$'):fn()


def alter(c,index,**changes):
 book=c.books[index];r=book.sheets[0].rows[0];r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells));new=replace(book,sheets=(replace(book.sheets[0],rows=(r,)+book.sheets[0].rows[1:]),));c.persist(index,new);return new


def test_actual_two_durable_sources_restart_approved_physical_correction_new_delta(case,monkeypatch):
 calls=[];lookup=core.lookup_row;recount=inv.recount
 def find(*a,**kw):calls.append('lookup');return lookup(*a,**kw)
 def actual(*a,**kw):calls.append('recount');return recount(*a,**kw)
 monkeypatch.setattr(core,'lookup_row',find);monkeypatch.setattr(inv,'recount',actual)
 before=[raw(case,i) for i in range(2)];first=call(case)
 assert first.lines[0].delta==Decimal(-2) and first.lines[0].state is inv.RecountState.COMPARABLE and calls==['lookup','lookup','recount']
 assert [raw(case,i) for i in range(2)]==before and first.book_count==first.physical_count==1
 assert first.requires_review and not first.live_authorized and not first.warehouse_activation and first.scope=='declared_two_workbook_sample'
 assert not hasattr(first,'workbook') and KEY.decode() not in repr(first)
 case.reopen();same=call(case);assert same.lines==first.lines and same.book_fingerprint==first.book_fingerprint and same.physical_fingerprint==first.physical_fingerprint
 receipt=case.update_physical(Decimal(6));assert receipt.state=='local_fake_committed'
 case.reopen();before=[raw(case,i) for i in range(2)];next_report=call(case)
 assert next_report.lines[0].delta==Decimal(1) and next_report.book_fingerprint==first.book_fingerprint and next_report.physical_fingerprint!=first.physical_fingerprint
 assert [raw(case,i) for i in range(2)]==before and json.loads(before[1])['used_nonces']==['synthetic-nonce']
 with pytest.raises(FrozenInstanceError):next_report.lines=()


def test_one_union_contains_all_actual_missing_unknown_unresolved_and_signed_states(case):
 bookrows=(row('a',sku='A',quantity=Decimal(5)),row('b',sku='B',position=2,quantity=Decimal(1)),row('d',sku='D',position=3,quantity=None),row('e',sku='E',position=4,quantity=Decimal(3),variant=None))
 physicalrows=(row('a',sku='A',quantity=Decimal(3),kind='physical'),row('c',sku='C',position=2,quantity=0,kind='physical'),row('d',sku='D',position=3,quantity=Decimal(2),kind='physical'),row('e',sku='E',position=4,quantity=Decimal(4),variant=None,kind='physical'))
 for i,rows in enumerate((bookrows,physicalrows)):case.persist(i,replace(case.books[i],sheets=(replace(case.books[i].sheets[0],rows=rows),)))
 case.bindings=(tuple(binding(x) for x in ('a','b','d','e')),tuple(binding(x) for x in ('a','c','d','e')));case.reader=reader(case)
 output=call(case);lines={line.key.item.sku:line for line in output.lines}
 assert len(lines)==5 and output.book_count==output.physical_count==4 and lines['A'].delta==Decimal(-2)
 assert lines['B'].state is inv.RecountState.MISSING_PHYSICAL and lines['B'].physical_units is inv.UNKNOWN
 assert lines['C'].state is inv.RecountState.MISSING_BOOK and lines['C'].physical_units==Decimal(0)
 assert lines['D'].state is inv.RecountState.UNKNOWN_QUANTITY and lines['D'].delta is inv.UNKNOWN
 assert lines['E'].state is inv.RecountState.UNRESOLVED_KEY and lines['E'].key.item.variant is inv.UNKNOWN
 assert len(output.observation_fingerprints)==8


@pytest.mark.parametrize('side',[0,1])
def test_one_empty_selected_side_never_padded_to_zero(case,side):
 bindings=list(case.bindings);bindings[side]=();case.bindings=tuple(bindings);case.reader=reader(case)
 output=call(case);line=output.lines[0]
 assert (output.book_count,output.physical_count)==((0,1) if side==0 else (1,0))
 assert line.state is (inv.RecountState.MISSING_BOOK if side==0 else inv.RecountState.MISSING_PHYSICAL) and line.delta is inv.UNKNOWN


@pytest.mark.parametrize('change',['sku','variant','location','batch','case','literal_unknown'])
def test_exact_key_dimensions_not_merged_or_unknown_language_parsed(case,change):
 field='sku' if change=='case' else 'variant' if change=='literal_unknown' else change
 alter(case,1,**{field:'sku' if change=='case' else 'UNKNOWN' if change=='literal_unknown' else 'DIFFERENT'})
 value=call(case);assert len(value.lines)==2 and {r.state for r in value.lines}=={inv.RecountState.MISSING_BOOK,inv.RecountState.MISSING_PHYSICAL}
 assert all(r.delta is inv.UNKNOWN for r in value.lines)


@pytest.mark.parametrize('changes,state', [({'quantity':None,'variant':None},inv.RecountState.UNKNOWN_QUANTITY),({'variant':None},inv.RecountState.UNRESOLVED_KEY),({'quantity':0},inv.RecountState.COMPARABLE),({'quantity':Decimal('-0.00')},inv.RecountState.COMPARABLE)])
def test_unknown_quantity_precedes_unresolved_and_zero_known(case,changes,state):
 alter(case,0,**changes)
 if changes.get('variant','known') is None:alter(case,1,variant=None)
 output=call(case);assert output.lines[0].state is state
 if state is not inv.RecountState.COMPARABLE:assert output.lines[0].delta is inv.UNKNOWN


@pytest.mark.parametrize('index',[0,1])
@pytest.mark.parametrize('value',[True,False,1.5,'3','=SUM(A1)',Decimal('-1'),Decimal('NaN'),Decimal('Infinity'),Decimal('1e19'),Decimal('1e-19'),Decimal('1'*51),10**19])
def test_bad_source_quantity_not_coerced_or_guessed(case,index,value):
 try:alter(case,index,quantity=value)
 except ValueError:
  state=json.loads(raw(case,index));cells=state['workbook']['sheets'][0]['rows'][0]['cells'];next(c for c in cells if c[0]=='quantity')[1]=['float' if type(value) is float else 'int' if type(value) is int else 'decimal',str(value)]
  case.stores[index].execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.contexts[index].runtime_key))
 deny(lambda:call(case))


@pytest.mark.parametrize('index',[0,1])
@pytest.mark.parametrize('change',['date','unit','kind','sku_empty','dim_empty','dim_space','unit_long'])
def test_exact_schema_role_date_unit_labels_refuse_whole(case,index,change):
 values={'date':{'day':'2026-10-18'},'unit':{'unit':'OTHER'},'kind':{'kind':'free'},'sku_empty':{'sku':''},'dim_empty':{'location':''},'dim_space':{'variant':' variant '},'unit_long':{'unit':'x'*41}}
 alter(case,index,**values[change]);deny(lambda:call(case))


@pytest.mark.parametrize('change',['missing_sheet','missing_row','duplicate_row','missing_cell','duplicate_cell','duplicate_selector','same_row_two_selectors','duplicate_stock_key'])
def test_actual_lookup_and_duplicate_stock_key_whole_refusal(case,change):
 book=case.books[0];r=book.sheets[0].rows[0]
 if change=='missing_sheet':case.bindings=((module.RowBinding('absent',core.RowIdentity('id','main')),),case.bindings[1])
 elif change=='missing_row':case.bindings=((binding('absent'),),case.bindings[1])
 elif change=='duplicate_row':book=replace(book,sheets=(replace(book.sheets[0],rows=(r,replace(r,position=5))),))
 elif change=='missing_cell':book=replace(book,sheets=(replace(book.sheets[0],rows=(replace(r,cells=tuple(c for c in r.cells if c.column!='batch')),)),))
 elif change=='duplicate_cell':
  state=json.loads(raw(case,0));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['quantity',['int','0']]);case.stores[0].execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.contexts[0].runtime_key))
 elif change=='duplicate_selector':case.bindings=(case.bindings[0]*2,case.bindings[1])
 elif change=='same_row_two_selectors':
  case.bindings=((binding('main'),module.RowBinding('sheet',core.RowIdentity('sku','SKU'))),case.bindings[1]);book=replace(book,sheets=(replace(book.sheets[0],rows=(r,)),))
 elif change=='duplicate_stock_key':
  other=replace(r,position=4,cells=tuple(core.Cell(c.column,'other' if c.column=='id' else c.value) for c in r.cells));book=replace(book,sheets=(replace(book.sheets[0],rows=(r,other)),));case.bindings=((binding('main'),binding('other')),case.bindings[1])
 if change!='duplicate_cell':case.persist(0,book)
 case.reader=reader(case);deny(lambda:call(case))


@pytest.mark.parametrize('change',['empty','six_selectors','six_union','same_driver','same_workbook','roles_reversed'])
def test_caps_distinct_roles_and_sources_not_same_database_assumption(case,change):
 args={}
 if change=='empty':args.update(book_bindings=(),physical_bindings=())
 elif change=='six_selectors':args['book_bindings']=tuple(binding(str(i)) for i in range(6))
 elif change=='six_union':
  for index in range(2):
   ids=tuple(str(index)+'-'+str(i) for i in range(3));rows=tuple(row(x,sku=x,position=i+1,kind='book' if index==0 else 'physical') for i,x in enumerate(ids));case.persist(index,replace(case.books[index],sheets=(replace(case.books[index].sheets[0],rows=rows),)))
  args.update(book_bindings=tuple(binding('0-'+str(i)) for i in range(3)),physical_bindings=tuple(binding('1-'+str(i)) for i in range(3)))
 elif change=='same_driver':args['physical_driver']=case.drivers[0]
 else:
  sources=replace(case.sources)
  object.__setattr__(sources,'physical',replace(sources.physical,workbook_id=sources.book.workbook_id) if change=='same_workbook' else replace(sources.physical,role=inv.StockKind.BOOK))
  args['expected_sources']=sources
 case.reader=reader(case,**args);deny(lambda:call(case))


@pytest.mark.parametrize('change',['business','timezone','revision','workbook','role','revoked','expired','future','callback','method','driver_context'])
@pytest.mark.parametrize('index',[0,1])
def test_each_current_source_role_business_validity_method_context_drift(case,change,index):
 source=case.sources.book if index==0 else case.sources.physical
 if change=='callback':case.reader._callback=lambda:case.sources
 elif change=='method':
  original=case.drivers[index].snapshot;case.drivers[index].snapshot=MethodType(lambda self:original(),case.drivers[index])
 elif change=='driver_context':
  call(case);case.state['contexts'][index]=replace(case.contexts[index])
 else:
  key,value={'business':('business','OTHER'),'timezone':('timezone_name','Asia/Tokyo'),'revision':('revision',2),'workbook':('workbook_id','sqlite-book:'+'0'*64),'role':('role',inv.StockKind.PHYSICAL if index==0 else inv.StockKind.BOOK),'revoked':('revoked',True),'expired':('expires_at',NOW),'future':('valid_from',NOW+timedelta(hours=1))}[change]
  changed=replace(source,**{key:value});pair=replace(case.sources);object.__setattr__(pair,'book' if index==0 else 'physical',changed);case.state['sources']=pair
 before=[raw(case,i) for i in range(2)];deny(lambda:call(case));assert before==[raw(case,i) for i in range(2)]


@pytest.mark.parametrize('index',[0,1])
@pytest.mark.parametrize('change',['selected','unmapped','scale','signedzero','boolint','revision','row_order'])
def test_each_full_typed_final_snapshot_change_refuses_not_cross_database_atomic(case,monkeypatch,index,change):
 before=alter(case,index,unmapped=Decimal('0.00') if change=='signedzero' else 1 if change=='boolint' else Decimal('1.0'));case.books[index]=before
 actual=inv.recount
 def recount(*a,**kw):
  result=actual(*a,**kw)
  if change in ('selected','unmapped','scale','signedzero','boolint'):
   field='quantity' if change=='selected' else 'unmapped';value={'selected':Decimal(9),'unmapped':'CHANGED','scale':Decimal('1.00'),'signedzero':Decimal('-0.00'),'boolint':True}[change];after=alter(case,index,**{field:value})
  elif change=='revision':after=replace(before,revision='book-v2')
  else:after=replace(before,sheets=(replace(before.sheets[0],rows=tuple(reversed(before.sheets[0].rows))),))
  case.persist(index,after);return result
 monkeypatch.setattr(inv,'recount',recount);deny(lambda:call(case))


def test_defaultdisabled_zero_source_snapshot_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched source/driver')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail)
 for s in case.stores:monkeypatch.setattr(s,'execute',fail)
 case.state['sources']=None
 deny(lambda:r.read_recount(observed_on=DAY,now=NOW));assert case.state['source_calls']==0 and case.state['driver_calls']==[0,0]


@pytest.mark.parametrize('index',[0,1])
def test_disabled_actual_driver_not_enabled_or_bypassed(case,index):
 driver=object.__new__(driver_module.SQLiteTableDriver);INIT(driver,store=case.stores[index],current_context=case.callbacks[index])
 args={'book_driver' if index==0 else 'physical_driver':driver};case.reader=reader(case,**args);deny(lambda:call(case));assert case.state['driver_calls'][index]==0


@pytest.mark.parametrize('args',[{'observed_on':NOW},{'observed_on':DAY+timedelta(days=1)},{'now':NOW.replace(tzinfo=None)}])
def test_explicit_date_aware_clock_no_future_guess(case,args):deny(lambda:call(case,**args))


def test_source_timezone_day_explicit_and_no_snapshot_cache(case):
 pair=module.SourcePair(replace(case.sources.book,timezone_name='Asia/Tokyo'),replace(case.sources.physical,timezone_name='Asia/Tokyo'));case.sources=pair;case.state['sources']=pair;case.reader=reader(case)
 for i in range(2):alter(case,i,day='2026-10-20')
 value=call(case,observed_on=DAY+timedelta(days=1),now=NOW.replace(hour=18,minute=30));assert value.observed_on==date(2026,10,20)


def test_four_public_snapshots_eight_exact_readonly_rows_and_unchanged_nonces(case,monkeypatch):
 actual=driver_module.SQLiteTableDriver.snapshot;calls=[]
 def snapshot(self):calls.append(self);return actual(self)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case)
 before=[raw(case,i) for i in range(2)];traces=[[],[]]
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION):pytest.fail('consumer SQL write/transaction')
  return sqlite3.SQLITE_OK
 for i,s in enumerate(case.stores):s.db.set_authorizer(guard);s.db.set_trace_callback(traces[i].append)
 value=call(case)
 for s in case.stores:s.db.set_authorizer(None);s.db.set_trace_callback(None)
 assert calls==[case.drivers[0],case.drivers[1],case.drivers[0],case.drivers[1]] and [len(x) for x in traces]==[4,4]
 assert all('CASE WHEN typeof(value)' in q and 'LIMIT 2' in q for trace in traces for q in trace)
 assert before==[raw(case,i) for i in range(2)] and all(json.loads(x)['used_nonces']==[] for x in before)
 assert all(s.execute("SELECT value FROM runtime WHERE key='foreign-runtime'").fetchone()[0]=='UNTOUCHED' for s in case.stores)


def test_source_revoked_after_actual_core_no_partial_result(case,monkeypatch):
 actual=inv.recount
 def recount(*a,**kw):result=actual(*a,**kw);case.state['sources']=replace(case.sources,book=replace(case.sources.book,revoked=True));return result
 monkeypatch.setattr(inv,'recount',recount);deny(lambda:call(case))
