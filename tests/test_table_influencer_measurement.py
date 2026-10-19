"""Actual durable metadata attribution+due cores; no financial or contact authority."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
from itertools import permutations
import json,sqlite3
import pytest
from goldway import table_influencer_measurement as module
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


def columns():return module.MeasurementColumns('row','kind',module.BindingColumns('key','participant','confirmed'),module.BindingColumns('key','participant','confirmed'),module.ShipmentColumns('id','alias','at','barter'),module.ClickColumns('id','promo','at'),module.OrderColumns('id','at','promo1','promo2','alias'),module.RefundColumns('id','order','at'),module.ReportColumns('id','shipment','at','present'))
def stamp(day,hour=12):return datetime(2026,10,day,hour,tzinfo=timezone.utc).isoformat()
def row(technical,kind,position,**kw):return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in dict(row=technical,kind=kind,**kw).items()))
def initial_rows():return (row('alias','alias',1,key='aliasA',participant='A',confirmed=True),row('promo1','promo',2,key='P1',participant='A',confirmed=True),row('promo2','promo',3,key='P2',participant='A',confirmed=True),row('ship1','shipment',4,id='S1',alias='aliasA',at=stamp(12),barter=True),row('ship2','shipment',5,id='S2',alias='unknownAlias',at=stamp(14),barter=None),row('click1','click',6,id='C1',promo='P1',at=stamp(15)),row('click2','click',7,id='C2',promo='unknownPromo',at=stamp(15)),row('order1','order',8,id='O1',at=stamp(15),promo1='P1',promo2='P2',alias=None),row('oldorder','order',9,id='O0',at=stamp(1),promo1='P1',promo2=None,alias=None),row('refund','refund',10,id='R1',order='O0',at=stamp(16)),row('report','report',11,id='RP1',shipment='S1',at=stamp(13),present=False))
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='metadata',columns=c.columns,enabled=True);d.update(kw);return module.TableInfluencerMeasurementReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-influencer','synthetic-approval','metadata-book','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('metadata','sheet-v1',initial_rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.MeasurementSource('SYNTHETIC','opaque-account',ctx.workbook_id,module.SourceRole.INFLUENCER_SAMPLE,('A','B','C'),1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,columns=columns());c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_report(value):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('apply_to_fake_workbook',CORE_APPLY),('check_plan_baseline',CHECK)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   p=BUILD(writer,'metadata',core.RowIdentity('row','report'),(core.Cell('present',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_report=approved_report
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
 d=dict(influencer_id='A',window=WINDOW,currency='RUB',report_after_hours=168,now=NOW);d.update(kw);return c.reader.read_measurement(**d)
def deny(fn):
 with pytest.raises(module.InfluencerMeasurementUnavailable,match='^table_influencer_measurement_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,technical,**changes):
 records=[]
 for r in c.book.sheets[0].rows:
  if next(cell.value for cell in r.cells if cell.column=='row')==technical:r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells))
  records.append(r)
 return selected(c,records)


def test_actual_durable_attribution_click_old_order_refund_due_restart_approved_report(case,monkeypatch):
 calls=[];evaluate=m.evaluate;due=m.report_due_candidates;lookup=core.lookup_row;lookups=[]
 def actual(**kw):
  calls.append(('evaluate',kw));assert kw['records_complete'] is False and kw['barter_limit'] is None
  for s in kw['shipments']:assert s.barter_cost.amount is None and s.barter_cost.currency=='RUB'
  for o in kw['orders']:assert o.revenue_before_refunds.amount is None and o.margin_before_refunds_and_barter.amount is None
  for r in kw['refunds']:assert r.revenue_reduction.amount is None and r.margin_reduction.amount is None
  return evaluate(**kw)
 def candidates(*a,**kw):calls.append(('due',(a,kw)));return due(*a,**kw)
 def find(*a,**kw):lookups.append(kw['identity']);return lookup(*a,**kw)
 monkeypatch.setattr(m,'evaluate',actual);monkeypatch.setattr(m,'report_due_candidates',candidates);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);first=call(case);p=first.measurement
 assert [x[0] for x in calls]==['evaluate','due'] and len(lookups)==11 and p.shipment_ids==('S1',) and p.order_ids==('O1',) and p.click_ids==('C1',) and p.refund_ids==('R1',)
 assert p.unattributed_shipment_ids==('S2',) and p.unattributed_click_ids==('C2',) and p.barter_states==( ('S1',m.BarterState.CONFIRMED), )
 assert [x.shipment_id for x in first.due]==['S1'] and first.due[0].recipient_alias=='aliasA'
 assert first.requires_review and not first.live_authorized and not first.request_authorized and not hasattr(p,'financials') and not hasattr(first,'workbook') and raw(case)==before
 assert len(calls[1][1][0][0])==1 and calls[1][1][0][0][0].id=='S1'
 case.reopen();same=call(case);assert same.measurement==p and same.due==first.due
 receipt=case.approved_report(True);assert receipt.state=='local_fake_committed';case.reopen();before=raw(case);updated=call(case)
 assert updated.due==() and updated.measurement.order_ids==('O1',) and updated.workbook_fingerprint!=first.workbook_fingerprint and raw(case)==before
 assert json.loads(before)['used_nonces']==['synthetic-nonce'] and KEY.decode() not in repr(updated)
 with pytest.raises(FrozenInstanceError):updated.request_authorized=True


@pytest.mark.parametrize('change',['unconfirmed_alias','unknown_alias','unconfirmed_promos','conflicting_promos','unknown_second_promo','alias_promo_conflict'])
def test_actual_exact_confirmed_mapping_not_guessed_or_counted_twice(case,change):
 records=list(case.book.sheets[0].rows)
 changes={}
 if change=='unconfirmed_alias':changes={'alias':{'confirmed':False}}
 elif change=='unknown_alias':changes={'ship1':{'alias':'unmapped'}}
 elif change=='unconfirmed_promos':changes={'promo1':{'confirmed':False},'promo2':{'confirmed':False}}
 elif change=='conflicting_promos':changes={'promo2':{'participant':'B'}}
 elif change=='unknown_second_promo':changes={'order1':{'promo2':'unknown'}}
 else:changes={'order1':{'alias':'unknownAlias'}}
 for i,r in enumerate(records):
  technical=next(c.value for c in r.cells if c.column=='row')
  if technical in changes:records[i]=replace(r,cells=tuple(core.Cell(c.column,changes[technical].get(c.column,c.value)) for c in r.cells))
 selected(case,records);p=call(case).measurement
 if change in ('unconfirmed_alias','unknown_alias'):assert 'S1' in p.unattributed_shipment_ids and 'S1' not in p.shipment_ids
 else:assert p.order_ids==() and 'O1' in p.unattributed_order_ids


def test_period_measurement_window_end_report_can_close_current_due_only(case):
 records=[]
 for r in case.book.sheets[0].rows:
  technical=next(c.value for c in r.cells if c.column=='row')
  changes={'confirmed':False} if technical in ('promo1','promo2') else {'at':WINDOW.end.isoformat(),'present':True} if technical=='report' else {}
  records.append(replace(r,cells=tuple(core.Cell(c.column,changes.get(c.column,c.value)) for c in r.cells)))
 selected(case,records);result=call(case)
 assert result.measurement.unmeasurable_barter_ids==('S1',) and result.due==() and 'O1' in result.measurement.unattributed_order_ids


def test_unknown_barter_remains_unknown_and_missing_measurement_not_efficacy(case):
 alter(case,'ship1',barter=None);result=call(case)
 assert result.measurement.barter_states==( ('S1',m.BarterState.UNKNOWN), ) and result.measurement.unmeasurable_barter_ids==()


@pytest.mark.parametrize('present,at,has_due',[(False,stamp(19),True),(True,stamp(19),False),(True,stamp(22),False)])
def test_latest_report_current_true_then_false_and_future_handling(case,present,at,has_due):
 records=list(case.book.sheets[0].rows);records.append(row('newreport','report',12,id='RP2',shipment='S1',at=at,present=present))
 records=[replace(r,cells=tuple(core.Cell(c.column,True if c.column=='present' else c.value) for c in r.cells)) if next(c.value for c in r.cells if c.column=='row')=='report' else r for r in records]
 selected(case,records);result=call(case);assert bool(result.due) is has_due


def test_utc_exact168_equality_explicit_interval_only(case):
 window=m.Window(datetime(2026,10,10,tzinfo=timezone.utc),datetime(2026,10,19,12,tzinfo=timezone.utc))
 equality=datetime(2026,10,19,12,tzinfo=timezone.utc);assert len(call(case,window=window,now=equality).due)==1
 before=equality-timedelta(microseconds=1);short=m.Window(window.start,before);assert call(case,window=short,now=before).due==()
 for interval in (None,True,167,169,168.0,'168'):deny(lambda:call(case,report_after_hours=interval))


@pytest.mark.parametrize('order',tuple(permutations(range(3))))
def test_all_report_permutations_old_simultaneous_conflict_not_hidden_by_newer(case,order):
 base=[r for r in case.book.sheets[0].rows if next(c.value for c in r.cells if c.column=='kind')!='report']
 reports=[row('r'+str(i),'report',12+i,id='RP'+str(i),shipment='S1',at=stamp(14 if i<2 else 15),present=i!=1) for i in range(3)]
 selected(case,base+[reports[i] for i in order]);deny(lambda:call(case))


@pytest.mark.parametrize('order',tuple(permutations(range(3))))
def test_all_report_permutations_future_conflicts_retained_not_new_rejection(case,order,monkeypatch):
 base=[r for r in case.book.sheets[0].rows if next(c.value for c in r.cells if c.column=='kind')!='report']
 reports=[row('r'+str(i),'report',12+i,id='RP'+str(i),shipment='S1',at=stamp(22 if i<2 else 23),present=i!=1) for i in range(3)]
 selected(case,base+[reports[i] for i in order]);actual=m.evaluate;observed=[]
 def spy(**kw):observed.extend(kw['reports']);return actual(**kw)
 monkeypatch.setattr(m,'evaluate',spy);result=call(case)
 assert len(observed)==3 and [r.observed_at for r in observed]==sorted(r.observed_at for r in observed) and len(result.due)==1


@pytest.mark.parametrize('change',['report_before_ship','report_absent_ship','refund_before_order','unknown_refund_parent','unknown_click','older_ship'])
def test_actual_parent_chronology_and_unresolved_or_out_of_window_retained(case,change):
 changes={'report_before_ship':('report',{'at':stamp(11)}),'report_absent_ship':('report',{'shipment':'missing'}),'refund_before_order':('refund',{'at':'2026-09-30T12:00:00+00:00'}),'unknown_refund_parent':('refund',{'order':'missing'}),'unknown_click':('click1',{'promo':'unknown'}),'older_ship':('ship1',{'at':stamp(1)})};technical,data=changes[change];alter(case,technical,**data)
 if change in ('report_before_ship','report_absent_ship','refund_before_order'):deny(lambda:call(case))
 else:
  result=call(case)
  if change=='unknown_refund_parent':assert result.measurement.refund_ids==() and result.measurement.unattributed_refund_ids==('R1',)
  elif change=='unknown_click':assert 'C1' in result.measurement.unattributed_click_ids
  else:assert result.measurement.shipment_ids==() and result.due==()


@pytest.mark.parametrize('change',['unknown_kind','extra_amount','missing_cell','duplicate_cell','duplicate_rowid','duplicate_semantic','bad_confirmation','bad_barter','bad_presence','outsider_participant','naive','dateonly','bad_timestamp','huge_timestamp','bad_iso'])
def test_closed_metadata_only_shape_ids_flags_and_timestamps(case,change):
 records=list(case.book.sheets[0].rows)
 if change=='extra_amount':records[0]=replace(records[0],cells=records[0].cells+(core.Cell('price',Decimal(5)),))
 elif change=='missing_cell':records[0]=replace(records[0],cells=tuple(c for c in records[0].cells if c.column!='confirmed'))
 elif change=='duplicate_cell':
  state=json.loads(raw(case));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['confirmed',['bool',True]]);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 elif change=='duplicate_rowid':records[1]=replace(records[1],cells=tuple(core.Cell(c.column,'alias' if c.column=='row' else c.value) for c in records[1].cells))
 elif change=='duplicate_semantic':records[2]=replace(records[2],cells=tuple(core.Cell(c.column,'P1' if c.column=='key' else c.value) for c in records[2].cells))
 else:
  technical,key,value={'unknown_kind':('alias','kind','body'),'bad_confirmation':('alias','confirmed',None),'bad_barter':('ship1','barter',0),'bad_presence':('report','present',None),'outsider_participant':('alias','participant','OUTSIDE'),'naive':('ship1','at','2026-10-12T12:00:00'),'dateonly':('ship1','at','2026-10-12'),'bad_timestamp':('ship1','at','not-time'),'huge_timestamp':('ship1','at','x'*41),'bad_iso':('ship1','at','2026-10-12T12:00:00.0000000+00:00')}[change]
  records=[replace(r,cells=tuple(core.Cell(c.column,value if c.column==key else c.value) for c in r.cells)) if next(c.value for c in r.cells if c.column=='row')==technical else r for r in records]
 if change!='duplicate_cell':selected(case,records)
 deny(lambda:call(case))


@pytest.mark.parametrize('kind,cap',[('alias',3),('promo',3),('shipment',3),('click',4),('order',4),('refund',3),('report',4)])
def test_per_kind_capacity_whole_refusal(case,kind,cap):
 original=next(r for r in case.book.sheets[0].rows if next(c.value for c in r.cells if c.column=='kind')==kind);records=[]
 for i in range(cap+1):
  changes={'row':'tech'+str(i),'key' if kind in ('alias','promo') else 'id':'SEM'+str(i)}
  records.append(replace(original,position=i+1,cells=tuple(core.Cell(c.column,changes.get(c.column,c.value)) for c in original.cells)))
 selected(case,records);deny(lambda:call(case))


def test_total24_cap_no_truncation_empty_sample_unknown_not_finance(case):
 selected(case,());result=call(case);assert result.measurement.shipment_ids==result.measurement.order_ids==result.due==() and not hasattr(result.measurement,'financials')
 selected(case,[row('t'+str(i),'click',i+1,id='id'+str(i),promo='unknown',at=stamp(15)) for i in range(25)]);deny(lambda:call(case))


@pytest.mark.parametrize('change',['business','account','workbook','role','participants','revision','timezone','revoked','expired','future','callback','method','context'])
def test_current_source_method_and_driver_context_drift(case,change):
 if change=='callback':case.reader._callback=lambda:case.source
 elif change=='method':
  original=case.driver.snapshot;case.driver.snapshot=MethodType(lambda self:original(),case.driver)
 elif change=='context':call(case);case.state['context']=replace(case.ctx)
 else:
  key,value={'business':('business','OTHER'),'account':('account_scope','OTHER'),'workbook':('workbook_id','sqlite-book:'+'0'*64),'role':('role','influencer_sample'),'participants':('participant_ids',('A','B')),'revision':('revision',2),'timezone':('timezone_name','Asia/Tokyo'),'revoked':('revoked',True),'expired':('expires_at',NOW),'future':('valid_from',NOW+timedelta(hours=1))}[change];source=replace(case.source)
  if change=='role':object.__setattr__(source,key,value)
  else:source=replace(source,**{key:value})
  case.state['source']=source
 before=raw(case);deny(lambda:call(case));assert raw(case)==before


@pytest.mark.parametrize('change',['selected','unselected','scale','signedzero','boolint','revision','row_order','sheet_order','source','columns','window'])
def test_whole_typed_final_snapshot_and_input_drift_no_fallback(case,monkeypatch,change):
 book=case.book
 if change in ('signedzero','boolint'):
  other=book.sheets[1];r=other.rows[0];r=replace(r,cells=(r.cells[0],core.Cell('value',Decimal('0.00') if change=='signedzero' else 1)));book=replace(book,sheets=(book.sheets[0],replace(other,rows=(r,))));case.book=book;case.persist(book)
 window=m.Window(WINDOW.start,WINDOW.end);actual=m.report_due_candidates
 def due(*a,**kw):
  result=actual(*a,**kw)
  if change=='selected':alter(case,'report',present=True)
  elif change in ('unselected','scale','signedzero','boolint'):
   other=book.sheets[1];r=other.rows[0];v={'unselected':'CHANGED','scale':Decimal('1.00'),'signedzero':Decimal('-0.00'),'boolint':True}[change];r=replace(r,cells=(r.cells[0],core.Cell('value',v)));case.persist(replace(book,sheets=(book.sheets[0],replace(other,rows=(r,)))))
  elif change=='revision':case.persist(replace(book,revision='book-v2'))
  elif change=='row_order':case.persist(replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1])))
  elif change=='sheet_order':case.persist(replace(book,sheets=tuple(reversed(book.sheets))))
  elif change=='source':case.state['source']=replace(case.source,revoked=True)
  elif change=='columns':case.reader._columns=replace(case.columns)
  else:object.__setattr__(window,'end',window.end-timedelta(hours=1))
  return result
 monkeypatch.setattr(m,'report_due_candidates',due);deny(lambda:call(case,window=window))


@pytest.mark.parametrize('bad',['result_type','participant','window','foreign_id','out_of_window','financial_value','financial_known','limit','state','sources','due_type','due_id','due_recipient','due_future'])
def test_malformed_actual_result_projection_does_not_expose_finance_or_make_request(case,monkeypatch,bad):
 evaluate=m.evaluate;due=m.report_due_candidates
 def actual(**kw):
  result=evaluate(**kw)
  if bad=='result_type':return {}
  if bad=='participant':return replace(result,influencer_id='B')
  if bad=='window':return replace(result,window=m.Window(WINDOW.start,WINDOW.end-timedelta(hours=1)))
  if bad=='foreign_id':return replace(result,shipment_ids=('foreign',))
  if bad=='out_of_window':return replace(result,order_ids=('O0',))
  if bad=='financial_value':return replace(result,financials=replace(result.financials,barter_cost=Decimal(0)))
  if bad=='financial_known':return replace(result,financials=replace(result.financials,state='known'))
  if bad=='limit':return replace(result,limit_state='within_limit')
  if bad=='state':return replace(result,barter_states=(('S1','confirmed'),))
  if bad=='sources':return replace(result,source_ids=('foreign',))
  return result
 def candidates(*a,**kw):
  result=due(*a,**kw)
  if bad=='due_type':return list(result)
  if bad=='due_id':return (replace(result[0],shipment_id='S2'),)
  if bad=='due_recipient':return (replace(result[0],recipient_alias='unapproved'),)
  if bad=='due_future':return (replace(result[0],due_at=NOW+timedelta(hours=1)),)
  return result
 monkeypatch.setattr(m,'evaluate',actual);monkeypatch.setattr(m,'report_due_candidates',candidates);deny(lambda:call(case))


def test_defaultdisabled_zero_callbacks_snapshot_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched source')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail);monkeypatch.setattr(case.s,'execute',fail)
 deny(lambda:r.read_measurement(influencer_id='A',window=WINDOW,currency='RUB',report_after_hours=168,now=NOW));assert case.state['source_calls']==case.state['driver_calls']==0


def test_actual_driver_disabled_no_enabling_or_bypass(case):
 driver=object.__new__(driver_module.SQLiteTableDriver);INIT(driver,store=case.s,current_context=case.context);case.reader=reader(case,driver=driver);deny(lambda:call(case));assert case.state['driver_calls']==0


@pytest.mark.parametrize('args',[{'influencer_id':'OUTSIDE'},{'currency':'rub'},{'now':NOW.replace(tzinfo=None)},{'window':m.Window(WINDOW.start,NOW+timedelta(seconds=1))},{'window':m.Window(NOW-timedelta(days=367),NOW)}])
def test_explicit_participant_currency_window_and_aware_clock(case,args):deny(lambda:call(case,**args))


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


def test_all24_supported_metadata_rows_and_three_due_no_truncation(case,monkeypatch):
 records=[]
 def add(technical,kind,**kw):records.append(row(technical,kind,len(records)+1,**kw))
 for i in range(3):add('a'+str(i),'alias',key='alias'+str(i),participant='A',confirmed=True)
 for i in range(3):add('p'+str(i),'promo',key='P'+str(i),participant='A',confirmed=True)
 for i in range(3):add('s'+str(i),'shipment',id='S'+str(i),alias='alias'+str(i),at=stamp(12+i),barter=True)
 for i in range(4):add('c'+str(i),'click',id='C'+str(i),promo='P0',at=stamp(15))
 for i in range(4):add('o'+str(i),'order',id='O'+str(i),at=stamp(15),promo1='P0',promo2='P1',alias=None)
 for i in range(3):add('f'+str(i),'refund',id='F'+str(i),order='O0',at=stamp(16))
 for i in range(4):add('r'+str(i),'report',id='R'+str(i),shipment='S0' if i<3 else 'S1',at=stamp(15+i),present=False)
 assert len(records)==24;selected(case,records);lookup=core.lookup_row;calls=[]
 def find(*a,**kw):calls.append(kw['identity']);return lookup(*a,**kw)
 monkeypatch.setattr(core,'lookup_row',find);result=call(case)
 assert len(calls)==24 and len(result.observation_fingerprints)==24 and len(result.due)==3 and len(result.measurement.order_ids)==4 and len(result.measurement.source_ids)==24
