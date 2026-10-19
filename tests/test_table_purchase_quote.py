"""Actual durable selected rows -> freight_from_weight -> quote_purchase, synthetic only."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import copy,json,sqlite3
import pytest
from goldway import table_purchase_quote as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables
proc=module.procurement
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc);DAY=NOW.date()
KEY=b'OPENLY-SYNTHETIC-PURCHASE-TEST-KEY-0001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


def columns():
 return module.QuoteColumns(module.BatchColumns(*('batch_id sku variant quantity quantity_unit observed_on weight_amount weight_unit weight_basis weight_kind tariff_id freight_cost_id fees_complete transaction_kind'.split())),module.TariffColumns(*('tariff_id amount currency weight_unit billable_kind valid_from valid_through'.split())),module.ChargeColumns(*('cost_id kind amount currency basis batch_id sku variant observed_on included_1 included_2 included_3 included_4'.split())),module.FXColumns(*('from_currency to_currency target_per_source_unit observed_on'.split())))
def binding(column,value):return module.RowBinding('sheet',core.RowIdentity(column,value))
def row(position,**values):return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in values.items()))
def initial_rows():
 batch=dict(batch_id='BATCH',sku='SKU',variant='variant',quantity=10,quantity_unit='count_of_identical_units_in_one_batch',observed_on=DAY.isoformat(),weight_amount=Decimal(500),weight_unit='g',weight_basis='per_unit',weight_kind='gross',tariff_id='TARIFF',freight_cost_id='FREIGHT',fees_complete=True,transaction_kind='purchase')
 tariff=dict(tariff_id='TARIFF',amount=Decimal(2),currency='USD',weight_unit='kg',billable_kind='gross',valid_from='2026-10-01',valid_through='2026-10-31')
 def charge(id,kind,amount,currency='USD',basis='per_unit'):return dict(cost_id=id,kind=kind,amount=amount,currency=currency,basis=basis,batch_id='BATCH',sku='SKU',variant='variant',observed_on=DAY.isoformat(),included_1=None,included_2=None,included_3=None,included_4=None)
 return [row(1,**batch),row(2,**tariff),row(3,**charge('PURCHASE','purchase',Decimal(3))),row(4,**charge('COMMISSION','commission',Decimal(0),'RUB','batch')),row(5,**charge('FEE','fee',Decimal(5),'RUB','batch')),row(6,from_currency='USD',to_currency='RUB',target_per_source_unit=Decimal(90),observed_on=DAY.isoformat()),row(7,id='UNSELECTED',value=Decimal('1.0'),instruction='inert =SUM(A1)')]
def encode(ctx,book,nonces=()):
 return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 args=dict(driver=c.driver,current_source=c.current,expected_source=c.source,batch_binding=c.batch_binding,tariff_binding=c.tariff_binding,charge_bindings=c.charge_bindings,fx_bindings=c.fx_bindings,columns=c.columns,enabled=True);args.update(kw);return module.TablePurchaseQuoteReader(**args)


@pytest.fixture
def case(tmp_path,monkeypatch):
 original=Store.__init__;path=tmp_path/'synthetic.sqlite';s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-quote','synthetic-approval','quote-book','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('sheet','sheet-v1',tuple(initial_rows())),));s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.QuoteSource('SYNTHETIC',ctx.workbook_id,module.SourceRole.PURCHASE_QUOTE,1,'UTC',NOW-timedelta(days=3),NOW+timedelta(days=3));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,batch_binding=binding('quantity_unit','count_of_identical_units_in_one_batch'),tariff_binding=binding('valid_from','2026-10-01'),charge_bindings=tuple(binding('cost_id',x) for x in ('PURCHASE','COMMISSION','FEE')),fx_bindings=(binding('from_currency','USD'),),columns=columns());c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_tariff(value):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('check_plan_baseline',CHECK),('apply_to_fake_workbook',CORE_APPLY)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   p=BUILD(writer,'sheet',core.RowIdentity('valid_from','2026-10-01'),(core.Cell('amount',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_tariff=approved_tariff
 def fail(*a,**kw):pytest.fail('reader constructor/approval/write/network/model/outbox path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',fail)
 for name in ('build_plan','apply'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,fail)
 for name in ('issue_local_approval','validate_approval','apply_to_fake_workbook','build_plan','check_plan_baseline'):monkeypatch.setattr(core,name,fail)
 monkeypatch.setattr(Store,'queue',fail)
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]
def call(c,**kw):
 args=dict(batch_id='BATCH',sku='SKU',variant='variant',quote_on=DAY,currency='RUB',now=NOW);args.update(kw);return c.reader.read_quote(**args)
def deny(fn):
 with pytest.raises(module.PurchaseQuoteUnavailable,match='^table_purchase_quote_unavailable$'):fn()
def alter(c,position,**changes):
 rows=tuple(replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells)) if r.position==position else r for r in c.book.sheets[0].rows);book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=rows),));c.persist(book);return book


def test_actual_durable_weight_rate_fx_quote_core_once_restart_approved_tariff_new_quote(case,monkeypatch):
 calls=[];lookup=core.lookup_row;freight=proc.freight_from_weight;quote=proc.quote_purchase
 def find(*a,**kw):calls.append('lookup');return lookup(*a,**kw)
 def first(*a,**kw):calls.append('freight');return freight(*a,**kw)
 def second(**kw):calls.append('quote');return quote(**kw)
 monkeypatch.setattr(core,'lookup_row',find);monkeypatch.setattr(proc,'freight_from_weight',first);monkeypatch.setattr(proc,'quote_purchase',second)
 before=raw(case);first_report=call(case)
 assert calls==['lookup']*6+['freight','quote'] and first_report.freight.money.amount==Decimal(1) and first_report.freight.basis=='per_unit'
 assert first_report.quote.status=='known' and first_report.quote.total_batch==Decimal(3605) and first_report.quote.per_unit==Decimal('360.5')
 assert first_report.requires_review and not first_report.live_authorized and not first_report.purchase_authorized and raw(case)==before
 assert len(first_report.quote.components)==4 and first_report.quote.components[1].source_amount==Decimal(0)
 case.reopen();same=call(case);assert same.quote==first_report.quote and same.workbook_fingerprint==first_report.workbook_fingerprint
 receipt=case.approved_tariff(Decimal(4));assert receipt.state=='local_fake_committed';case.reopen();before=raw(case);updated=call(case)
 assert updated.freight.money.amount==Decimal(2) and updated.quote.total_batch==Decimal(4505) and updated.workbook_fingerprint!=first_report.workbook_fingerprint and raw(case)==before
 assert json.loads(before)['used_nonces']==['synthetic-nonce'] and KEY.decode() not in repr(updated) and not hasattr(updated,'workbook')
 with pytest.raises(FrozenInstanceError):updated.quote.status='approved'


@pytest.mark.parametrize('basis,weight_unit,amount,expected',[('batch','g',Decimal(500),Decimal(1)),('per_unit','kg',Decimal('0.5'),Decimal(1)),('batch','kg',Decimal('0.5'),Decimal(1))])
def test_actual_weight_unit_conversion_and_batch_basis_retained(case,basis,weight_unit,amount,expected):
 alter(case,1,weight_basis=basis,weight_unit=weight_unit,weight_amount=amount)
 value=call(case);assert value.freight.basis==basis and value.freight.money.amount==expected
 assert value.quote.components[-1].source_basis==basis


@pytest.mark.parametrize('position,field',[ (1,'weight_amount'),(2,'amount'),(3,'amount'),(4,'amount') ])
def test_unknown_amount_not_zero_total_and_known_subtotal_literal(case,position,field):
 alter(case,position,**{field:None});value=call(case)
 assert value.quote.status=='unknown' and value.quote.total_batch is None and value.quote.per_unit is None
 assert any(reason.startswith('amount:') for reason in value.quote.missing)
 if position in (1,2):assert value.freight.money.amount is None


@pytest.mark.parametrize('change',['missing_purchase','missing_commission','incomplete_fees','no_charges','all_amounts_unknown'])
def test_actual_missing_roles_and_enumeration_never_known_quote(case,change):
 if change=='missing_purchase':case.charge_bindings=case.charge_bindings[1:]
 elif change=='missing_commission':case.charge_bindings=(case.charge_bindings[0],case.charge_bindings[2])
 elif change=='incomplete_fees':alter(case,1,fees_complete=False)
 elif change=='no_charges':case.charge_bindings=()
 else:
  rows=[]
  for r in case.book.sheets[0].rows:
   rows.append(replace(r,cells=tuple(core.Cell(c.column,None if c.column in ('amount','weight_amount') else c.value) for c in r.cells)))
  case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=tuple(rows)),)))
 case.reader=reader(case);value=call(case);assert value.quote.status=='unknown' and value.quote.total_batch is None
 if change=='all_amounts_unknown':assert value.quote.known_components_subtotal==Decimal(0)


@pytest.mark.parametrize('change',['netgross','quantity_bool','quantity_zero','quantity_huge','quantity_unit','transaction','fees_unknown','batch','sku','variant','date','tariff','expired_tariff','future_tariff','weight_unit','weight_basis'])
def test_explicit_batch_tariff_units_and_validity_refuse(case,change):
 positions={'netgross':(2,{'billable_kind':'net'}),'quantity_bool':(1,{'quantity':True}),'quantity_zero':(1,{'quantity':0}),'quantity_huge':(1,{'quantity':10**9+1}),'quantity_unit':(1,{'quantity_unit':'kg'}),'transaction':(1,{'transaction_kind':'return'}),'fees_unknown':(1,{'fees_complete':None}),'batch':(1,{'batch_id':'OTHER'}),'sku':(1,{'sku':'OTHER'}),'variant':(1,{'variant':None}),'date':(1,{'observed_on':'2026-10-18'}),'tariff':(2,{'tariff_id':'OTHER'}),'expired_tariff':(2,{'valid_through':'2026-10-18'}),'future_tariff':(2,{'valid_from':'2026-10-20'}),'weight_unit':(1,{'weight_unit':'lb'}),'weight_basis':(1,{'weight_basis':'count'})}
 position,changes=positions[change];alter(case,position,**changes);deny(lambda:call(case))


def test_tariff_inclusive_dates_and_unknown_zero_weight_distinct(case):
 b=alter(case,2,valid_from=DAY.isoformat(),valid_through=DAY.isoformat());case.book=b;case.tariff_binding=binding('valid_from',DAY.isoformat());case.reader=reader(case);alter(case,1,weight_amount=Decimal(0))
 value=call(case);assert value.freight.money.amount==Decimal(0) and value.quote.status=='known'


@pytest.mark.parametrize('position,field',[ (1,'weight_amount'),(2,'amount'),(3,'amount'),(6,'target_per_source_unit') ])
@pytest.mark.parametrize('value',[True,1.5,'2','=SUM(A1)',Decimal('-1'),Decimal('NaN'),Decimal('1e19'),Decimal('1e-19'),Decimal('1'*51),10**19])
def test_numeric_fields_strict_typed_bounded_not_coerced(case,position,field,value):
 try:alter(case,position,**{field:value})
 except ValueError:
  state=json.loads(raw(case));cells=state['workbook']['sheets'][0]['rows'][position-1]['cells'];next(c for c in cells if c[0]==field)[1]=['float' if type(value) is float else 'int' if type(value) is int else 'decimal',str(value)];case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 deny(lambda:call(case))


@pytest.mark.parametrize('change',['missing','inverse','nonoutput','date','zero','unknown','duplicate'])
def test_direct_fx_exact_date_no_inverse_triangulation_or_zero(case,change):
 if change=='missing':case.fx_bindings=()
 elif change=='inverse':alter(case,6,from_currency='RUB',to_currency='USD');case.fx_bindings=(binding('from_currency','RUB'),)
 elif change=='nonoutput':alter(case,6,to_currency='EUR')
 elif change=='date':alter(case,6,observed_on='2026-10-18')
 elif change=='zero':alter(case,6,target_per_source_unit=Decimal(0))
 elif change=='unknown':alter(case,6,target_per_source_unit=None)
 else:
  r=replace(case.book.sheets[0].rows[5],position=8,cells=case.book.sheets[0].rows[5].cells+(core.Cell('alternate','fx2'),));case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=case.book.sheets[0].rows+(r,)),)));case.fx_bindings=(case.fx_bindings[0],binding('alternate','fx2'))
 case.reader=reader(case);deny(lambda:call(case))


@pytest.mark.parametrize('change',['freight_role','wrong_scope','bad_basis','self_include','duplicate_slots','included_overlap','economic_id_overlap','singleton_purchase'])
def test_actual_economic_identity_overlap_and_nonfreight_roles_not_reimplemented(case,change):
 changes={'freight_role':(3,{'kind':'freight'}),'wrong_scope':(3,{'sku':'OTHER'}),'bad_basis':(3,{'basis':'units'}),'self_include':(3,{'included_1':'PURCHASE'}),'duplicate_slots':(3,{'included_1':'bundled','included_2':'bundled'}),'included_overlap':(3,{'included_1':'COMMISSION'}),'economic_id_overlap':(1,{'freight_cost_id':'PURCHASE'}),'singleton_purchase':(5,{'kind':'purchase'})}
 p,values=changes[change];alter(case,p,**values);deny(lambda:call(case))


def test_nonoverlapping_bundled_cost_id_preserved_literal_not_independence_proof(case):
 alter(case,3,included_1='declared-packaging');value=call(case)
 assert value.quote.components[0].included_cost_ids==('declared-packaging',) and value.quote.status=='known'


@pytest.mark.parametrize('change',['missing_row','missing_sheet','duplicate_row','missing_cell','duplicate_cell','duplicate_selector','same_row_alternate_selector'])
def test_selected_rows_all_or_whole_refusal_no_fallback(case,change):
 if change=='missing_row':case.batch_binding=binding('batch_id','missing')
 elif change=='missing_sheet':case.batch_binding=replace(case.batch_binding,sheet_id='absent')
 elif change=='duplicate_row':case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=case.book.sheets[0].rows+(replace(case.book.sheets[0].rows[0],position=8),)),)))
 elif change=='missing_cell':
  r=case.book.sheets[0].rows[0];r=replace(r,cells=tuple(c for c in r.cells if c.column!='fees_complete'));case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=(r,)+case.book.sheets[0].rows[1:]),)))
 elif change=='duplicate_cell':
  state=json.loads(raw(case));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['quantity',['int','10']]);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 elif change=='duplicate_selector':case.charge_bindings=(case.charge_bindings[0],case.charge_bindings[0])
 else:case.tariff_binding=binding('weight_kind','gross')
 case.reader=reader(case);deny(lambda:call(case))


@pytest.mark.parametrize('change',['business','workbook','revision','timezone','role','revoked','expired','future','callback','method','context'])
def test_current_source_and_bound_driver_method_context_drift(case,change):
 if change=='callback':case.reader._callback=lambda:case.source
 elif change=='method':
  original=case.driver.snapshot;case.driver.snapshot=MethodType(lambda self:original(),case.driver)
 elif change=='context':call(case);case.state['context']=replace(case.ctx)
 else:
  source=case.source;values={'business':('business','OTHER'),'workbook':('workbook_id','sqlite-book:'+'0'*64),'revision':('revision',2),'timezone':('timezone_name','Asia/Tokyo'),'role':('role','purchase_quote'),'revoked':('revoked',True),'expired':('expires_at',NOW),'future':('valid_from',NOW+timedelta(hours=1))};key,value=values[change]
  if change=='role':source=replace(source);object.__setattr__(source,key,value)
  else:source=replace(source,**{key:value})
  case.state['source']=source
 before=raw(case);deny(lambda:call(case));assert raw(case)==before


@pytest.mark.parametrize('change',['selected','unselected','scale','signedzero','boolint','revision','order','source','binding'])
def test_final_whole_typed_snapshot_source_config_race_no_partial_quote(case,monkeypatch,change):
 if change in ('signedzero','boolint'):
  r=case.book.sheets[0].rows[6];r=replace(r,cells=tuple(core.Cell(c.column,Decimal('0.00') if change=='signedzero' else 1) if c.column=='value' else c for c in r.cells));case.book=replace(case.book,sheets=(replace(case.book.sheets[0],rows=case.book.sheets[0].rows[:6]+(r,)),));case.persist(case.book)
 actual=proc.quote_purchase
 def quote(**kw):
  result=actual(**kw)
  if change=='selected':alter(case,2,amount=Decimal(4))
  elif change in ('unselected','scale','signedzero','boolint'):
   r=case.book.sheets[0].rows[6];v={'unselected':'CHANGED','scale':Decimal('1.00'),'signedzero':Decimal('-0.00'),'boolint':True}[change];r=replace(r,cells=tuple(core.Cell(c.column,v) if c.column=='value' else c for c in r.cells));case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=case.book.sheets[0].rows[:6]+(r,)),)))
  elif change=='revision':case.persist(replace(case.book,revision='book-v2'))
  elif change=='order':case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=tuple(reversed(case.book.sheets[0].rows))),)))
  elif change=='source':case.state['source']=replace(case.source,revoked=True)
  else:case.reader._bindings=(case.batch_binding,case.tariff_binding,(),case.fx_bindings)
  return result
 monkeypatch.setattr(proc,'quote_purchase',quote);deny(lambda:call(case))


@pytest.mark.parametrize('bad',['list','extra','currency','quantity_bool','status','missing','component_id','component_amount','component_currency','factor','negative','unknown_shape'])
def test_malformed_detached_actual_quote_whole_safe_refusal(case,monkeypatch,bad):
 actual=proc.quote_purchase
 def quote(**kw):
  result=copy.deepcopy(actual(**kw))
  if bad=='list':return list(result)
  if bad=='extra':result['extra']='PRIVATE'
  elif bad=='currency':result['currency']='USD'
  elif bad=='quantity_bool':result['quantity']=True
  elif bad=='status':result['status']='approved'
  elif bad=='missing':result['missing']=('invented',)
  elif bad in ('component_id','component_amount','component_currency','factor'):
   index,key,value={'component_id':(0,'id','other'),'component_amount':(0,'source_amount',Decimal(999)),'component_currency':(0,'source_currency','EUR'),'factor':(0,'fx_target_per_source_unit',Decimal(0))}[bad];result['components'][index][key]=value
  elif bad=='negative':result['total_batch']=Decimal(-1)
  else:result['status']='unknown';result['missing']=('fees:incomplete_enumeration',)
  return result
 monkeypatch.setattr(proc,'quote_purchase',quote);deny(lambda:call(case))


def test_defaultdisabled_zero_callbacks_snapshots_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched source')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail);monkeypatch.setattr(case.s,'execute',fail)
 deny(lambda:r.read_quote(batch_id='BATCH',sku='SKU',variant='variant',quote_on=DAY,currency='RUB',now=NOW));assert case.state['source_calls']==0 and case.state['driver_calls']==0


def test_actual_driver_disabled_no_bypass_and_binding_caps(case):
 d=object.__new__(driver_module.SQLiteTableDriver);INIT(d,store=case.s,current_context=case.context)
 case.reader=reader(case,driver=d);deny(lambda:call(case));assert case.state['driver_calls']==0
 for args in ({'charge_bindings':case.charge_bindings*2},{'fx_bindings':case.fx_bindings*4},{'charge_bindings':list(case.charge_bindings)}):case.reader=reader(case,**args);deny(lambda:call(case))


def test_max_nine_input_rows_five_components_no_lookup_generated_freight(case,monkeypatch):
 rows=list(case.book.sheets[0].rows[:6]);fee=replace(rows[4],position=7,cells=tuple(core.Cell(c.column,'FEE2' if c.column=='cost_id' else c.value) for c in rows[4].cells));rows.append(fee)
 for position,ccy in ((8,'EUR'),(9,'CNY')):rows.append(row(position,from_currency=ccy,to_currency='RUB',target_per_source_unit=Decimal(1),observed_on=DAY.isoformat()))
 case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=tuple(rows)),)));case.charge_bindings=case.charge_bindings+(binding('cost_id','FEE2'),);case.fx_bindings=tuple(binding('from_currency',ccy) for ccy in ('USD','EUR','CNY'));case.reader=reader(case)
 actual=core.lookup_row;calls=[]
 def lookup(*a,**kw):calls.append(kw['identity']);return actual(*a,**kw)
 monkeypatch.setattr(core,'lookup_row',lookup);value=call(case)
 assert len(calls)==9 and len(value.observation_fingerprints)==9 and len(value.quote.components)==5


def test_two_public_snapshots_four_bounded_readonly_rows_and_unchanged_nonces(case,monkeypatch):
 actual=driver_module.SQLiteTableDriver.snapshot;calls=[]
 def snapshot(self):calls.append(self);return actual(self)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case);before=raw(case);trace=[]
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION):pytest.fail('reader issued SQL/write/transaction')
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(guard);case.s.db.set_trace_callback(trace.append);call(case);case.s.db.set_authorizer(None);case.s.db.set_trace_callback(None)
 assert len(calls)==2 and len(trace)==4 and all('CASE WHEN typeof(value)' in q and 'LIMIT 2' in q for q in trace)
 assert raw(case)==before and json.loads(before)['used_nonces']==[] and case.s.execute("SELECT value FROM runtime WHERE key='foreign'").fetchone()[0]=='UNTOUCHED'


@pytest.mark.parametrize('args',[{'quote_on':NOW},{'quote_on':DAY+timedelta(days=1)},{'currency':'rub'},{'now':NOW.replace(tzinfo=None)},{'variant':None}])
def test_explicit_request_date_currency_variant_and_aware_clock(case,args):deny(lambda:call(case,**args))
