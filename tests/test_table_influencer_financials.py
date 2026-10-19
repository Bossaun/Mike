"""Actual durable metadata attribution+due cores; no financial or contact authority."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
from itertools import permutations
import json,sqlite3
import pytest
from goldway import table_influencer_financials as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;m=module.metrics
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)
WINDOW=m.Window(datetime(2026,10,10,tzinfo=timezone.utc),datetime(2026,10,20,tzinfo=timezone.utc))
KEY=b'OPENLY-SYNTHETIC-INFLUENCER-TEST-KEY-0001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


def columns():return module.FinancialColumns('row','kind',module.HeadColumns('target','start','end','currency','complete','limit_present','limit','limit_currency'),module.BindingColumns('key','participant','confirmed'),module.BindingColumns('key','participant','confirmed'),module.ShipmentColumns('id','alias','at','barter','cost','currency'),module.OrderColumns('id','at','promo1','promo2','alias','revenue','revenue_currency','margin','margin_currency'),module.RefundColumns('id','order','at','reduction','revenue_currency','margin_reduction','margin_currency'))
def stamp(day,hour=12):return datetime(2026,10,day,hour,tzinfo=timezone.utc).isoformat()
def row(technical,kind,position,**kw):return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in dict(row=technical,kind=kind,**kw).items()))
def ship(technical,position,**kw):
 d=dict(id=technical,alias='aliasA',at=stamp(12),barter=True,cost=Decimal('10.00'),currency='RUB');d.update(kw);return row(technical,'shipment',position,**d)
def order(technical,position,**kw):
 d=dict(id=technical,at=stamp(15),promo1='P1',promo2='P2',alias=None,revenue=100,revenue_currency='RUB',margin=Decimal('50.0'),margin_currency='RUB');d.update(kw);return row(technical,'order',position,**d)
def refund(technical,position,**kw):
 d=dict(id=technical,order='O0',at=stamp(16),reduction=20,revenue_currency='RUB',margin_reduction=5,margin_currency='RUB');d.update(kw);return row(technical,'refund',position,**d)
def initial_rows():return (row('head','head',1,target='A',start=WINDOW.start.isoformat(),end=WINDOW.end.isoformat(),currency='RUB',complete=True,limit_present=True,limit=Decimal('10.00'),limit_currency='RUB'),row('alias','alias',2,key='aliasA',participant='A',confirmed=True),row('promo1','promo',3,key='P1',participant='A',confirmed=True),row('promo2','promo',4,key='P2',participant='A',confirmed=True),ship('S1',5),order('O1',6),order('O0',7,at=stamp(1)),refund('R1',8))
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='metadata',columns=c.columns,enabled=True);d.update(kw);return module.TableInfluencerFinancialReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-influencer','synthetic-approval','metadata-book','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('metadata','sheet-v1',initial_rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.FinancialSource('SYNTHETIC','opaque-account',ctx.workbook_id,module.SourceRole.INFLUENCER_FINANCIAL_SAMPLE,('A','B','C'),1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,columns=columns());c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_cost(value):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('apply_to_fake_workbook',CORE_APPLY),('check_plan_baseline',CHECK)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   p=BUILD(writer,'metadata',core.RowIdentity('row','S1'),(core.Cell('cost',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_cost=approved_cost
 def fail(*a,**kw):pytest.fail('reader used constructor/approval/write/network/model/outbox path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',fail)
 for name in ('apply','build_plan'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,fail)
 for name in ('build_plan','validate_approval','check_plan_baseline','apply_to_fake_workbook','issue_local_approval'):monkeypatch.setattr(core,name,fail)
 monkeypatch.setattr(Store,'queue',fail)
 monkeypatch.setattr(m,'report_due_candidates',fail)
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close()


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]
def call(c,**kw):
 d=dict(influencer_id='A',window=WINDOW,currency='RUB',now=NOW);d.update(kw);return c.reader.read_financials(**d)
def deny(fn):
 with pytest.raises(module.InfluencerFinancialUnavailable,match='^table_influencer_financials_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,technical,**changes):
 records=[]
 for r in c.book.sheets[0].rows:
  if next(cell.value for cell in r.cells if cell.column=='row')==technical:r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells))
  records.append(r)
 return selected(c,records)


def test_actual_financial_once_two_promos_one_order_old_order_refund_restart_cost_correction(case,monkeypatch):
 actual=m.evaluate;calls=[];lookup=core.lookup_row;found=[]
 def assess(**kw):calls.append(kw);return actual(**kw)
 def find(*a,**kw):found.append(kw);return lookup(*a,**kw)
 monkeypatch.setattr(m,'evaluate',assess);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);out=call(case);r=out.result;f=r.financials
 assert len(calls)==1 and len(found)==8 and calls[0]['clicks']==calls[0]['reports']==() and calls[0]['records_complete'] is True
 assert r.order_ids==('O1',) and r.refund_ids==('R1',) and r.shipment_ids==('S1',) and f==m.Financials('known','RUB',Decimal(80),Decimal(45),Decimal(10),Decimal(35),Decimal('3.5'),())
 assert r.limit_state=='within_limit' and len(out.consulted_source_ids)==8 and len(r.source_ids)==7 and set(r.source_ids)<set(out.consulted_source_ids)
 assert out.requires_review and not any((out.live_authorized,out.financial_source_verified,out.agreed_limit_verified,out.spending_authorized)) and raw(case)==before
 case.reopen();assert call(case).result==r
 assert case.approved_cost(Decimal('20.00')).state=='local_fake_committed';case.reopen();new=call(case)
 assert new.result.financials.net_revenue==80 and new.result.financials.roi==Decimal('1.25') and new.result.limit_state=='exceeded' and new.workbook_fingerprint!=out.workbook_fingerprint
 assert json.loads(raw(case))['used_nonces']==['synthetic-nonce'] and KEY.decode() not in repr(new)
 with pytest.raises(FrozenInstanceError):new.spending_authorized=True

@pytest.mark.parametrize('technical,changes,state,cost,limit,reason',[
 ('head',{'complete':False},'unknown',None,'unknown','incomplete_observations'),('O1',{'revenue':None},'unknown',Decimal(10),'within_limit','missing_amount'),('O1',{'margin':None},'unknown',Decimal(10),'within_limit','missing_amount'),('R1',{'reduction':None},'unknown',Decimal(10),'within_limit','missing_amount'),('O1',{'revenue_currency':'USD'},'unknown',Decimal(10),'within_limit','currency_conflict'),('S1',{'currency':'USD'},'unknown',None,'unknown','currency_conflict'),('S1',{'cost':None},'unknown',None,'unknown','missing_amount'),('S1',{'barter':None},'unknown',None,'unknown','unknown_barter'),('S1',{'cost':0},'zero_denominator',Decimal(0),'within_limit','zero_barter_cost'),('head',{'limit':None},'known',Decimal(10),'unknown',None),('head',{'limit_currency':'USD'},'known',Decimal(10),'unknown',None)])
def test_actual_partial_known_cost_limit_unknown_money_and_zero_roi(case,technical,changes,state,cost,limit,reason):
 alter(case,technical,**changes);out=call(case);f=out.result.financials
 assert f.state==state and f.barter_cost==cost and out.result.limit_state==limit
 if reason:assert reason in f.reasons
 if state=='unknown':assert f.net_revenue is f.margin_after_refunds is f.net_after_barter is f.roi is None
 if state=='zero_denominator':assert f.roi is None and f.net_after_barter==45


def test_negative_margin_net_and_absent_limit_no_spend_authority(case):
 b=alter(case,'O1',margin=Decimal('-50.00'));case.book=b;alter(case,'head',limit_present=False,limit=None,limit_currency=None)
 out=call(case);assert out.barter_limit is None and out.result.financials.margin_after_refunds==-55 and out.result.financials.net_after_barter==-65 and out.result.financials.roi==Decimal('-6.5') and out.result.limit_state=='unknown'

@pytest.mark.parametrize('change',['unconfirmedalias','unknownalias','unconfirmedpromos','conflictingpromos','unknownsecond','referralconflict','missingparent','otherperson'])
def test_actual_exact_attribution_not_guessed(case,change):
 mapping={'unconfirmedalias':('alias',{'confirmed':False}),'unknownalias':('S1',{'alias':'unknown'}),'unconfirmedpromos':('promo1',{'confirmed':False}),'conflictingpromos':('promo2',{'participant':'B'}),'unknownsecond':('O1',{'promo2':'unknown'}),'referralconflict':('O1',{'alias':'unknown'}),'missingparent':('R1',{'order':'missing'}),'otherperson':('alias',{'participant':'B'})}
 technical,changes=mapping[change];alter(case,technical,**changes);r=call(case).result
 if change=='otherperson':assert r.shipment_ids==() and r.unattributed_shipment_ids==() and r.financials.barter_cost==0
 elif change in ('unconfirmedalias','unknownalias'):assert r.unattributed_shipment_ids==('S1',) and r.financials.state=='unknown'
 elif change=='missingparent':assert r.unattributed_refund_ids==('R1',) and r.financials.barter_cost==10
 else:assert 'O1' in r.unattributed_order_ids and r.financials.state=='unknown' and r.financials.barter_cost==10

@pytest.mark.parametrize('technical,changes',[
 ('head',{'target':'B'}),('head',{'currency':'USD'}),('head',{'start':stamp(11)}),('head',{'end':stamp(19)}),('head',{'complete':None}),('head',{'complete':1}),('head',{'limit_present':None}),('head',{'limit_present':False}),('head',{'limit':-1}),('head',{'limit':True}),('head',{'limit_currency':None}),('alias',{'participant':'foreign'}),('alias',{'confirmed':None}),('alias',{'key':' spaced'}),('S1',{'barter':1}),('S1',{'barter':False}),('S1',{'cost':-1}),('S1',{'cost':'10'}),('S1',{'cost':'=10'}),('S1',{'cost':True}),('S1',{'currency':None}),('S1',{'currency':'rub'}),('S1',{'at':'2026-10-12'}),('S1',{'at':'2026-10-12T12:00:00'}),('O1',{'revenue':-1}),('O1',{'revenue':True}),('O1',{'promo1':''}),('R1',{'reduction':-1}),('R1',{'margin_reduction':-1}),('R1',{'reduction':101}),('R1',{'at':stamp(1,1)}),('O1',{'id':'O0'})])
def test_closed_head_monetary_confirmation_chronology_refund_constraints(case,technical,changes):alter(case,technical,**changes);deny(lambda:call(case))


def test_half_open_selected_scope_and_identical_value_different_orders(case):
 b=alter(case,'O1',at=WINDOW.end.isoformat());case.book=b;b=alter(case,'S1',at=WINDOW.start.isoformat());case.book=b
 r=call(case).result;assert r.order_ids==() and r.shipment_ids==('S1',) and r.financials.net_revenue==-20 and r.financials.margin_after_refunds==-5
 case.persist(case.book);selected(case,case.book.sheets[0].rows+(order('O2',9),));r=call(case).result
 assert r.order_ids==('O2',) and r.financials.net_revenue==80

@pytest.mark.parametrize('change',['nohead','duplicatehead','noshipments','extrafield','missingcell','duplicatecell','duplicatealias','fourships','fiveorders','fourrefunds','eighteen','invaliddecimal','float'])
def test_all_row_schema_semantic_caps(case,change):
 rows=list(case.book.sheets[0].rows)
 if change=='nohead':rows=rows[1:]
 elif change=='duplicatehead':rows.append(replace(rows[0],position=9,cells=tuple(core.Cell(c.column,'head2' if c.column=='row' else c.value) for c in rows[0].cells)))
 elif change=='noshipments':rows=[r for r in rows if next(c.value for c in r.cells if c.column=='kind')!='shipment']
 elif change=='extrafield':rows[0]=replace(rows[0],cells=rows[0].cells+(core.Cell('bank','not-read'),))
 elif change=='missingcell':rows[0]=replace(rows[0],cells=rows[0].cells[:-1])
 elif change=='duplicatealias':rows.append(row('alias2','alias',9,key='aliasA',participant='A',confirmed=True))
 elif change=='fourships':rows+=list(ship('S'+str(i),i+9) for i in range(2,5))
 elif change=='fiveorders':rows+=list(order('O'+str(i),i+9) for i in range(2,5))
 elif change=='fourrefunds':rows+=list(refund('R'+str(i),i+9,reduction=0,margin_reduction=0) for i in range(2,5))
 elif change=='eighteen':rows+=list(ship('SX'+str(i),i+9) for i in range(10))
 else:
  data=json.loads(raw(case));cells=data['workbook']['sheets'][0]['rows'][4]['cells']
  if change=='duplicatecell':cells.append(['cost',['int','0']])
  else:
   for c in cells:
    if c[0]=='cost':c[1]=['decimal','1E-19'] if change=='invaliddecimal' else ['float',1.5]
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(data),case.ctx.runtime_key));deny(lambda:call(case));return
 selected(case,rows);deny(lambda:call(case))


def test_full_seventeen_rows_allconsulted_and_otherparticipant_source_not_selected(case):
 rows=list(case.book.sheets[0].rows)+[row('alias2','alias',9,key='aliasB',participant='B',confirmed=True),row('alias3','alias',10,key='aliasC',participant='C',confirmed=False),row('promo3','promo',11,key='P3',participant='B',confirmed=True),ship('S2',12,alias='aliasB'),ship('S3',13,alias='aliasA'),order('O2',14),order('O3',15,promo1='P3',promo2=None),refund('R2',16,reduction=0,margin_reduction=0),refund('R3',17,reduction=0,margin_reduction=0)]
 selected(case,rows);out=call(case)
 assert len(out.consulted_source_ids)==17 and out.result.shipment_ids==('S1','S3') and out.result.order_ids==('O1','O2') and set(out.result.source_ids)<set(out.consulted_source_ids)

@pytest.mark.parametrize('field,value',[('business','other'),('account_scope','other'),('revision',2),('timezone_name','Europe/Berlin'),('revoked',True),('expires_at',NOW),('valid_from',NOW+timedelta(seconds=1))])
def test_current_source_proof(case,field,value):case.state['source']=replace(case.source,**{field:value});deny(lambda:call(case))
@pytest.mark.parametrize('kw',[{'influencer_id':'foreign'},{'currency':'rub'},{'window':None},{'now':NOW.replace(tzinfo=None)},{'window':m.Window(WINDOW.start,NOW+timedelta(days=1))},{'window':m.Window(WINDOW.start-timedelta(days=367),WINDOW.end)}])
def test_explicit_window_target_currency(case,kw):deny(lambda:call(case,**kw))

@pytest.mark.parametrize('change',['scale','signedzero','boolint','unselected','revision','order','method','callback','schema','window'])
def test_fulltyped_source_input_method_race(case,monkeypatch,change):
 actual=m.evaluate
 def assess(**kw):
  out=actual(**kw)
  if change=='method':case.driver.snapshot=MethodType(lambda s:case.book,case.driver)
  elif change=='callback':case.reader._callback=lambda:case.source
  elif change=='schema':object.__setattr__(case.columns.head,'target_id','changed')
  elif change=='window':object.__setattr__(kw['window'],'end',NOW)
  else:
   book=case.book
   if change=='revision':book=replace(book,revision='new')
   elif change=='order':book=replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1]))
   elif change=='scale':book=alter(case,'S1',cost=Decimal('10.0'))
   elif change=='signedzero':book=alter(case,'head',limit=Decimal('-0.00'))
   else:
    v=True if change=='boolint' else Decimal('2.0');other=replace(book.sheets[1],rows=(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',v))),));book=replace(book,sheets=(book.sheets[0],other))
   case.persist(book)
  return out
 monkeypatch.setattr(m,'evaluate',assess)
 if change=='window':
  win=m.Window(WINDOW.start,WINDOW.end);deny(lambda:call(case,window=win))
 else:deny(lambda:call(case))

@pytest.mark.parametrize('change',['type','financialtype','currency','state','unknownshape','zeroshape','number','costnegative','roiNone','reasons','target','selected','outside','click','barter','unmeasurable','sources','limit'])
def test_malformed_detached_actual_result(case,monkeypatch,change):
 actual=m.evaluate
 def assess(**kw):
  r=actual(**kw);f=r.financials
  if change=='type':return None
  if change=='financialtype':return replace(r,financials=None)
  if change=='currency':return replace(r,financials=replace(f,currency='USD'))
  if change=='state':return replace(r,financials=replace(f,state='approved'))
  if change=='unknownshape':return replace(r,financials=replace(f,state='unknown',reasons=('missing_amount',)))
  if change=='zeroshape':return replace(r,financials=replace(f,state='zero_denominator'))
  if change=='number':return replace(r,financials=replace(f,roi=True))
  if change=='costnegative':return replace(r,financials=replace(f,barter_cost=Decimal(-1)))
  if change=='roiNone':return replace(r,financials=replace(f,roi=None))
  if change=='reasons':return replace(r,financials=replace(f,reasons=('fake_reason',)))
  if change=='target':return replace(r,influencer_id='B')
  if change=='selected':return replace(r,shipment_ids=('foreign',))
  if change=='outside':return replace(r,order_ids=('O0',))
  if change=='click':return replace(r,click_ids=('fake',))
  if change=='barter':return replace(r,barter_states=(('S1','confirmed'),))
  if change=='unmeasurable':return replace(r,unmeasurable_barter_ids=('foreign',))
  if change=='sources':return replace(r,source_ids=('fake-source',))
  return replace(r,limit_state='permission')
 monkeypatch.setattr(m,'evaluate',assess);deny(lambda:call(case))


def test_defaultoff_driveroff_missing_sheet_noeffects(case):
 before=raw(case);counts=case.state.copy();deny(lambda:reader(case,enabled=False).read_financials(influencer_id='A',window=WINDOW,currency='RUB',now=NOW));assert case.state==counts and raw(case)==before
 deny(lambda:reader(case,sheet_id='absent').read_financials(influencer_id='A',window=WINDOW,currency='RUB',now=NOW))
 d=object.__new__(driver_module.SQLiteTableDriver);INIT(d,store=case.s,current_context=case.context,enabled=False);deny(lambda:reader(case,driver=d).read_financials(influencer_id='A',window=WINDOW,currency='RUB',now=NOW))


def test_two_public_snapshots_fourruntimevalues_no_writes(case,monkeypatch):
 actual=driver_module.SQLiteTableDriver.snapshot;calls=[];reads=[]
 def snapshot(s):calls.append(1);return actual(s)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case)
 def authorizer(op,a,b,*rest):
  if op in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):pytest.fail('reader wrote SQL')
  if op==sqlite3.SQLITE_READ:reads.append((a,b))
  return sqlite3.SQLITE_OK
 before=raw(case);case.s.db.set_authorizer(authorizer);call(case);case.s.db.set_authorizer(None)
 assert len(calls)==2 and sum(1 for t,c in reads if t=='runtime' and c=='value')<=4 and raw(case)==before
