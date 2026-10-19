"""Actual durable channel heads/children -> coverage-aware publication_gap only."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json,sqlite3
import pytest
from goldway import table_procurement_assessment as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;m=module.procurement
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)
END=NOW.replace(hour=9);OBSERVED=NOW.replace(hour=10)
KEY=b'OPENLY-SYNTHETIC-CAMPAIGN-TEST-KEY-000001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


DAY=date(2026,10,21)
def columns():return module.AssessmentColumns(module.CommonColumns('record','role','batch','sku','variant','day'),module.BatchColumns('quantity','quantity_unit','wt','ft','independent'),module.WeightColumns('amount','unit','basis','kind'),module.ChargeColumns('cost','amount','currency','basis','inc1','inc2','inc3','inc4'))
def row(technical,role,position,batch='A',**kw):return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in dict(record=technical,role=role,batch=batch,sku='SKU'+batch,variant='standard',day=DAY.isoformat(),**kw).items()))
def batch_rows(batch,position=1):
 return (row(batch+'h','batch',position,batch,quantity=2,quantity_unit='count_of_identical_units_in_one_batch',wt=Decimal('0.10'),ft=Decimal('0.2'),independent=True),row(batch+'m','measured',position+1,batch,amount=Decimal('1100'),unit='g',basis='per_unit',kind='net'),row(batch+'r','reference',position+2,batch,amount=2,unit='kg',basis='batch',kind='net'),row(batch+'p','purchase',position+3,batch,cost='buy'+batch,amount=100,currency='USD',basis='per_unit',inc1=None,inc2=None,inc3=None,inc4=None),row(batch+'f','freight',position+4,batch,cost='ship'+batch,amount=Decimal('40'),currency='USD',basis='batch',inc1=None,inc2=None,inc3=None,inc4=None))
def initial_rows():return batch_rows('A')+batch_rows('B',6)
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='observations',columns=c.columns,enabled=True);d.update(kw);return module.TableProcurementAssessmentReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-publications','synthetic-approval','observations','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('observations','sheet-v1',initial_rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.AssessmentSource('SYNTHETIC','opaque-measurements',ctx.workbook_id,module.SourceRole.PROCUREMENT_ASSESSMENT_SAMPLE,1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,columns=columns());c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_value(value):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('apply_to_fake_workbook',CORE_APPLY),('check_plan_baseline',CHECK)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   p=BUILD(writer,'observations',core.RowIdentity('record','Am'),(core.Cell('amount',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_value=approved_value
 def fail(*a,**kw):pytest.fail('reader used constructor/approval/write/network/model/outbox path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',fail)
 for name in ('apply','build_plan'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,fail)
 for name in ('build_plan','validate_approval','check_plan_baseline','apply_to_fake_workbook','issue_local_approval'):monkeypatch.setattr(core,name,fail)
 for name in ('quote_purchase','freight_from_weight','FXRate'):monkeypatch.setattr(m,name,fail)
 monkeypatch.setattr(Store,'queue',fail)
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]
def call(c,**kw):return c.reader.read_assessments(observed_on=kw.get('observed_on',DAY),now=kw.get('now',NOW))
def deny(fn):
 with pytest.raises(module.AssessmentReaderUnavailable,match='^table_procurement_assessment_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,technical,**changes):
 records=[]
 for r in c.book.sheets[0].rows:
  if next(cell.value for cell in r.cells if cell.column=='record')==technical:r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells))
  records.append(r)
 return selected(c,records)


def test_actual_two_batches_both_cores_once_equality_refs_restart_correction(case,monkeypatch):
 w=m.assess_weight;l=m.assess_logistics;wc=[];lc=[];lookup=core.lookup_row;found=[]
 def weight(**kw):wc.append(kw);return w(**kw)
 def logistics(**kw):lc.append(kw);return l(**kw)
 def find(*a,**kw):found.append(kw);return lookup(*a,**kw)
 monkeypatch.setattr(m,'assess_weight',weight);monkeypatch.setattr(m,'assess_logistics',logistics);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);out=call(case)
 assert len(wc)==len(lc)==2 and len(found)==10
 assert wc[0]['actual'].unit=='g' and wc[0]['expected'].basis=='batch'
 assert out.batches[0].weight['status']=='within_threshold' and out.batches[0].weight['relative_deviation']==Decimal('0.1')
 assert out.batches[0].logistics['status']=='within_threshold' and out.batches[0].logistics['ratio']==Decimal('0.2')
 assert len(out.batches)==2 and all(len(b.source_ids)==5 for b in out.batches) and raw(case)==before
 assert out.requires_review and not any((out.live_authorized,out.business_norm_verified,out.purchase_authorized))
 with pytest.raises(TypeError):out.batches[0].weight['status']='flag'
 case.reopen();assert call(case).batches==out.batches
 assert case.approved_value(Decimal('1200')).state=='local_fake_committed';case.reopen();changed=call(case)
 assert changed.batches[0].weight['status']=='flag' and changed.batches[0].logistics==out.batches[0].logistics and changed.workbook_fingerprint!=out.workbook_fingerprint
 assert json.loads(raw(case))['used_nonces']==['synthetic-nonce'] and KEY.decode() not in repr(changed)
 with pytest.raises(FrozenInstanceError):changed.purchase_authorized=True

@pytest.mark.parametrize('technical,changes,wstate,lstate,wreason,lreason',[
 ('Am',{'amount':None},'unknown','within_threshold','missing_weight_or_threshold','caller_threshold'),('Ar',{'amount':0},'unknown','within_threshold','zero_expected_weight','caller_threshold'),('Am',{'kind':'gross'},'unknown','within_threshold','incomparable_net_gross','caller_threshold'),('Ah',{'wt':None},'unknown','within_threshold','missing_weight_or_threshold','caller_threshold'),('Ap',{'amount':None},'within_threshold','unknown','caller_threshold','missing_amount_or_threshold'),('Ap',{'amount':0},'within_threshold','unknown','caller_threshold','zero_purchase_baseline'),('Af',{'amount':None},'within_threshold','unknown','caller_threshold','missing_amount_or_threshold'),('Ah',{'ft':None},'within_threshold','unknown','caller_threshold','missing_amount_or_threshold'),('Am',{'amount':1200},'flag','within_threshold','caller_threshold','caller_threshold'),('Af',{'amount':41},'within_threshold','flag','caller_threshold','caller_threshold')])
def test_independent_actual_unknown_zero_netgross_and_strictgreater(case,technical,changes,wstate,lstate,wreason,lreason):
 alter(case,technical,**changes);out=call(case).batches[0]
 assert (out.weight['status'],out.logistics['status'],out.weight['reason'],out.logistics['reason'])==(wstate,lstate,wreason,lreason)
 if wstate=='unknown':assert set(out.weight)=={'status','reason','relative_deviation'}
 if lstate=='unknown':assert set(out.logistics)=={'status','reason','ratio'}


def test_zero_threshold_zero_actual_and_distinct_batch_currency_no_total(case):
 b=alter(case,'Am',amount=1000);case.book=b;b=alter(case,'Ah',wt=Decimal('0'),ft=Decimal('-0.00'));case.book=b;b=alter(case,'Af',amount=0);case.book=b;b=alter(case,'Bp',currency='EUR');case.book=b;alter(case,'Bf',currency='EUR')
 out=call(case);assert out.batches[0].weight['status']==out.batches[0].logistics['status']=='within_threshold' and out.batches[1].logistics['currency']=='EUR' and not hasattr(out,'total')

@pytest.mark.parametrize('technical,changes',[
 ('Ah',{'quantity':0}),('Ah',{'quantity':True}),('Ah',{'quantity':Decimal('2')}),('Ah',{'quantity':10**9+1}),('Ah',{'quantity_unit':'kg'}),('Ah',{'independent':False}),('Ah',{'independent':None}),('Ah',{'independent':1}),('Ah',{'wt':0}),('Ah',{'ft':'0.2'}),('Ah',{'wt':True}),('Ah',{'wt':Decimal('-1')}),('Am',{'amount':True}),('Am',{'amount':'1100'}),('Am',{'amount':'=1100'}),('Am',{'amount':-1}),('Am',{'unit':'lb'}),('Am',{'kind':'unknown'}),('Am',{'basis':'package'}),('Ap',{'currency':'usd'}),('Af',{'currency':'EUR'}),('Af',{'cost':'buyA'}),('Af',{'inc1':'buyA'}),('Ap',{'inc1':'shipA'}),('Ap',{'inc1':'same','inc2':'same'}),('Ap',{'inc1':'same'}),('Am',{'sku':'other'}),('Am',{'variant':None}),('Am',{'batch':'foreign'}),('Am',{'day':'2026-10-20'}),('Am',{'day':'2026-10-22'}),('Am',{'day':None}),('Am',{'record':'Ah'})])
def test_closed_quantities_threshold_amount_identity_date_costs(case,technical,changes):
 if changes=={'inc1':'same'}:
  b=alter(case,'Af',inc2='same');case.book=b
 alter(case,technical,**changes);deny(lambda:call(case))

@pytest.mark.parametrize('change',['missingrole','duplicaterole','extra','missingcell','duplicatecell','fourbatches','empty','invaliddecimal','float'])
def test_exact_complete_five_roles_and_caps(case,change):
 rows=list(case.book.sheets[0].rows)
 if change=='missingrole':rows=rows[1:]
 elif change=='duplicaterole':rows[0]=replace(rows[0],cells=tuple(core.Cell(c.column,'measured' if c.column=='role' else c.value) for c in rows[0].cells))
 elif change=='extra':rows[0]=replace(rows[0],cells=rows[0].cells+(core.Cell('message','private'),))
 elif change=='missingcell':rows[0]=replace(rows[0],cells=rows[0].cells[:-1])
 elif change=='fourbatches':rows=list(batch_rows('A')+batch_rows('B',6)+batch_rows('C',11)+batch_rows('D',16))
 elif change=='empty':rows=[]
 else:
  data=json.loads(raw(case));cells=data['workbook']['sheets'][0]['rows'][1]['cells']
  if change=='duplicatecell':cells.append(['amount',['int','0']])
  else:
   for c in cells:
    if c[0]=='amount':c[1]=['decimal','1E-19'] if change=='invaliddecimal' else ['float',1.5]
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(data),case.ctx.runtime_key));deny(lambda:call(case));return
 selected(case,rows);deny(lambda:call(case))


def test_all_fifteen_rows_three_batches_and_all_nullable_slots(case):
 rows=batch_rows('A')+batch_rows('B',6)+batch_rows('C',11);selected(case,rows);out=call(case)
 assert len(out.batches)==3 and sum(len(b.source_ids) for b in out.batches)==15
 b=alter(case,'Ap',inc1='one',inc3='two');assert call(case).batches[0].logistics['status']=='within_threshold'

@pytest.mark.parametrize('field,value',[('business','other'),('observation_scope','other'),('revision',2),('timezone','Europe/Berlin'),('revoked',True),('expires_at',NOW),('valid_from',NOW+timedelta(seconds=1))])
def test_current_source_proof(case,field,value):case.state['source']=replace(case.source,**{field:value});deny(lambda:call(case))

@pytest.mark.parametrize('day',[None,NOW,'2026-10-21',date(2026,10,22)])
def test_explicit_date_type_future_refuse(case,day):deny(lambda:call(case,observed_on=day))

def test_source_timezone_future_date_and_unknown_threshold_are_not_defaults(case):
 source=replace(case.source,timezone='Pacific/Kiritimati');case.source=source;case.state['source']=source;case.reader=reader(case)
 assert len(call(case,now=datetime(2026,10,20,12,tzinfo=timezone.utc)).batches)==2
 deny(lambda:call(case,now=datetime(2026,10,20,9,tzinfo=timezone.utc)))

@pytest.mark.parametrize('change',['scale','signedzero','boolint','unselected','revision','order','method','callback','schema'])
def test_fulltyped_current_snapshot_host_drift(case,monkeypatch,change):
 actual=m.assess_logistics
 def assess(**kw):
  out=actual(**kw)
  if change=='method':case.driver.snapshot=MethodType(lambda s:case.book,case.driver)
  elif change=='callback':case.reader._callback=lambda:case.source
  elif change=='schema':object.__setattr__(case.columns.common,'sku','changed')
  else:
   book=case.book
   if change=='revision':book=replace(book,revision='new')
   elif change=='order':book=replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1]))
   elif change=='scale':book=alter(case,'Ah',wt=Decimal('0.1'))
   elif change=='signedzero':book=alter(case,'Am',amount=Decimal('-0.00'))
   else:
    v=True if change=='boolint' else Decimal('2.0');other=replace(book.sheets[1],rows=(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',v))),));book=replace(book,sheets=(book.sheets[0],other))
   case.persist(book)
  return out
 monkeypatch.setattr(m,'assess_logistics',assess);deny(lambda:call(case))

@pytest.mark.parametrize('kind,change',[('weight','type'),('weight','missing'),('weight','extra'),('weight','status'),('weight','reason'),('weight','number'),('weight','threshold'),('logistics','type'),('logistics','extra'),('logistics','quantity'),('logistics','currency'),('logistics','basis'),('logistics','unknownextra')])
def test_malformed_detached_closed_dicts(case,monkeypatch,kind,change):
 name='assess_weight' if kind=='weight' else 'assess_logistics';actual=getattr(m,name)
 def assess(**kw):
  out=actual(**kw)
  if change=='type':return None
  if change=='missing':out.pop('reason')
  elif change=='extra':out['total']=0
  elif change=='status':out['status']='approved'
  elif change=='reason':out['reason']='real_norm'
  elif change=='number':out['relative_deviation']=True
  elif change=='threshold':out['max_relative_deviation']=Decimal('0.2')
  elif change=='quantity':out['quantity']=True
  elif change=='currency':out['currency']='EUR'
  elif change=='basis':out['quantity_basis']='per_unit'
  elif change=='unknownextra':out={'status':'unknown','reason':'missing_amount_or_threshold','ratio':None,'currency':'USD'}
  return out
 monkeypatch.setattr(m,name,assess);deny(lambda:call(case))


def test_defaultoff_driveroff_missing_sheet_and_naive_now(case):
 before=raw(case);counts=case.state.copy();deny(lambda:reader(case,enabled=False).read_assessments(observed_on=DAY,now=NOW));assert case.state==counts and raw(case)==before
 deny(lambda:reader(case,sheet_id='absent').read_assessments(observed_on=DAY,now=NOW))
 driver=object.__new__(driver_module.SQLiteTableDriver);INIT(driver,store=case.s,current_context=case.context,enabled=False);deny(lambda:reader(case,driver=driver).read_assessments(observed_on=DAY,now=NOW));deny(lambda:call(case,now=NOW.replace(tzinfo=None)))


def test_two_public_snapshots_four_runtime_transfers_no_mutation(case,monkeypatch):
 actual=driver_module.SQLiteTableDriver.snapshot;calls=[];reads=[]
 def snapshot(s):calls.append(1);return actual(s)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case)
 def authorizer(op,a,b,*rest):
  if op in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):pytest.fail('reader wrote SQL')
  if op==sqlite3.SQLITE_READ:reads.append((a,b))
  return sqlite3.SQLITE_OK
 before=raw(case);case.s.db.set_authorizer(authorizer);call(case);case.s.db.set_authorizer(None)
 assert len(calls)==2 and sum(1 for t,c in reads if t=='runtime' and c=='value')<=4 and raw(case)==before
