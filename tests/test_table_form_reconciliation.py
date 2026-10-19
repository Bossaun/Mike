"""Actual durable FORM/ACCOUNT -> existing reconciliation; no collection/send."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
from zoneinfo import ZoneInfo
import json
import sqlite3
import pytest
from goldway import table_form_reconciliation as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables
reports=module.reports
NOW=datetime(2026,10,19,16,tzinfo=timezone.utc)
PERIOD=reports.Period(date(2026,10,12),date(2026,10,19))
MONDAY=PERIOD.end
KEY=b'OPENLY-SYNTHETIC-FORM-ACCOUNT-TEST-KEY-0001'
ISSUE=core.issue_local_approval
APPLY=driver_module.SQLiteTableDriver.apply
BUILD=driver_module.SQLiteTableDriver.build_plan
VALIDATE=core.validate_approval
CORE_APPLY=core.apply_to_fake_workbook
CORE_BUILD=core.build_plan
CHECK=core.check_plan_baseline
INIT=driver_module.SQLiteTableDriver.__init__


def form_schema():return module.FormColumns('owner','metric','start','end','present','value','unit','currency','observed','submitted')
def account_schema():return module.AccountColumns('owner','metric','start','end','present','value','unit','currency','observed')
def definition(identifier='metric',**kw):
 d=dict(id=identifier,label='Synthetic metric',unit='count',currency=None,target=None,direction='informational');d.update(kw);return reports.MetricDefinition(**d)
def selector(identifier='main'):return module.RowSelector('sheet',core.RowIdentity('id',identifier))
def binding(identifier='metric',row_id='main'):return module.MetricPair(identifier,selector(row_id),selector(row_id))
def row(identifier='main',position=1,value=Decimal(5),form=True,**kw):
 d=dict(id=identifier,owner='opaque-owner',metric='metric',start=PERIOD.start.isoformat(),end=PERIOD.end.isoformat(),present=True,value=value,unit='count',currency=None,observed=PERIOD.end.isoformat(),unmapped=Decimal('1.0'))
 if form:d['submitted']=(NOW-timedelta(hours=2)).isoformat()
 d.update(kw);return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in d.items()))
def encoded(ctx,book,nonces=()):
 return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(form_driver=c.drivers[0],account_driver=c.drivers[1],current_sources=c.current,expected_sources=c.sources,bindings=c.bindings,form_columns=c.form_columns,account_columns=c.account_columns,enabled=True);d.update(kw);return module.TableFormReconciler(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 original=Store.__init__;stores=[];contexts=[];books=[];drivers=[];paths=[];callbacks=[]
 for index,kind in enumerate(('form','account')):
  path=tmp_path/(kind+'.sqlite');s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-'+kind,'synthetic-approval',kind,'synthetic-reviewer','synthetic-key',KEY)
  book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('sheet','sheet-v1',(row(form=index==0,value=Decimal(5 if index==0 else 3)),row('unselected',position=2,form=index==0))),))
  s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encoded(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'));stores.append(s);contexts.append(ctx);books.append(book);paths.append(path)
 state=dict(sources=None,contexts=contexts.copy(),source_calls=0,driver_calls=[0,0])
 for i in range(2):
  def callback(i=i):state['driver_calls'][i]+=1;return state['contexts'][i]
  callbacks.append(callback);drivers.append(driver_module.SQLiteTableDriver(store=stores[i],current_context=callback,enabled=True))
 sources=module.SourcePair(module.FormSource('SYNTHETIC',contexts[0].workbook_id,'opaque-owner',module.SourceRole.FORM,1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc)),module.FormSource('SYNTHETIC',contexts[1].workbook_id,'opaque-owner',module.SourceRole.ACCOUNT,1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc)))
 state['sources']=sources
 def current():state['source_calls']+=1;return state['sources']
 c=SimpleNamespace(stores=stores,contexts=contexts,books=books,drivers=drivers,paths=paths,callbacks=callbacks,state=state,current=current,sources=sources,bindings=(binding(),),form_columns=form_schema(),account_columns=account_schema());c.reader=reader(c)
 def persist(i,book,nonces=()):c.stores[i].execute('UPDATE runtime SET value=? WHERE key=?',(encoded(contexts[i],book,nonces),contexts[i].runtime_key))
 def reopen():
  for i in range(2):
   old=c.stores[i];c.stores[i]=object.__new__(Store);original(c.stores[i],str(paths[i]));old.db.close();c.drivers[i]=object.__new__(driver_module.SQLiteTableDriver);INIT(c.drivers[i],store=c.stores[i],current_context=callbacks[i],enabled=True)
  c.reader=reader(c)
 def approved_update(i,column,value):
  with monkeypatch.context() as fixture_write:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('check_plan_baseline',CHECK),('apply_to_fake_workbook',CORE_APPLY)):fixture_write.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.stores[i],current_context=callbacks[i],enabled=True,allow_fake_writes=True)
   plan=BUILD(writer,'sheet',core.RowIdentity('id','main'),(core.Cell(column,value),));token=ISSUE(plan,decision='approve',approver=contexts[i].required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,plan,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_update=approved_update
 def fail(*a,**kw):pytest.fail('reader used constructor/approval/write/network/model/outbox path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',fail)
 for name in ('build_plan','apply'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,fail)
 for name in ('issue_local_approval','validate_approval','apply_to_fake_workbook','build_plan','check_plan_baseline'):monkeypatch.setattr(core,name,fail)
 monkeypatch.setattr(Store,'queue',fail)
 import socket,httpx,builtins,subprocess
 monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 for s in c.stores:
  s.db.set_authorizer(None)
  if s.db.in_transaction:s.db.rollback()
  s.db.close()


def raw(c,i):return c.stores[i].execute('SELECT value FROM runtime WHERE key=?',(c.contexts[i].runtime_key,)).fetchone()[0]
def call(c,**kw):
 d=dict(period=PERIOD,definitions=(definition(),),monday=MONDAY,coverage_complete=True,coverage_observed_on=PERIOD.end,now=NOW);d.update(kw);return c.reader.read_reconciliation(**d)
def deny(fn):
 with pytest.raises(module.FormReconciliationUnavailable,match='^table_form_reconciliation_unavailable$'):fn()
def alter(c,i,**changes):
 b=c.books[i];r=b.sheets[0].rows[0];r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells));new=replace(b,sheets=(replace(b.sheets[0],rows=(r,)+b.sheets[0].rows[1:]),));c.persist(i,new);return new


def test_actual_persisted_pair_core_once_restart_approved_correction_changes_discrepancy(case,monkeypatch):
 calls=[];lookup=core.lookup_row;cutoff=reports.monday_form_cutoff;reconcile=reports.reconcile_forms
 def find(*a,**kw):calls.append('lookup');return lookup(*a,**kw)
 def due(**kw):calls.append('cutoff');return cutoff(**kw)
 def actual(**kw):calls.append('reconcile');assert kw['owners']==('opaque-owner',);return reconcile(**kw)
 monkeypatch.setattr(core,'lookup_row',find);monkeypatch.setattr(reports,'monday_form_cutoff',due);monkeypatch.setattr(reports,'reconcile_forms',actual)
 before=[raw(case,i) for i in range(2)];first=call(case);line=first.lines[0]
 assert calls==['cutoff','lookup','lookup','reconcile'] and line.difference==Decimal(2) and line.comparison=='discrepancy' and line.submission_state=='on_time' and line.question
 assert first.requires_review and not first.live_authorized and not first.collection_authorized and first.due_at.hour==15
 assert [raw(case,i) for i in range(2)]==before and not hasattr(first,'workbook') and KEY.decode() not in repr(first)
 case.reopen();same=call(case);assert same.lines==first.lines and same.account_fingerprint==first.account_fingerprint
 receipt=case.approved_update(1,'value',Decimal(5));assert receipt.state=='local_fake_committed'
 case.reopen();before=[raw(case,i) for i in range(2)];updated=call(case)
 assert updated.lines[0].comparison=='match' and updated.lines[0].difference==Decimal(0) and updated.lines[0].question is None
 assert updated.form_fingerprint==first.form_fingerprint and updated.account_fingerprint!=first.account_fingerprint and before==[raw(case,i) for i in range(2)]
 assert json.loads(before[1])['used_nonces']==['synthetic-nonce']
 with pytest.raises(FrozenInstanceError):updated.lines=()


@pytest.mark.parametrize('submitted,state',[(None,'unknown_time'),('2026-10-19T15:00:00+00:00','on_time'),('2026-10-19T18:00:00+03:00','on_time'),('2026-10-19T15:00:01+00:00','late')])
def test_actual_submission_unknown_cutoff_equality_absolute_iso_instants(case,submitted,state):
 alter(case,0,submitted=submitted);output=call(case)
 assert output.lines[0].submission_state==state
 if state=='unknown_time':assert output.lines[0].comparison=='unknown' and output.lines[0].difference is None


@pytest.mark.parametrize('side,complete,expected',[(0,True,'missing_form'),(1,True,'missing_account'),(0,False,'provisional')])
def test_explicit_absent_source_row_is_not_missing_lookup_or_employee_nonresponse(case,side,complete,expected):
 changes=dict(present=False,value=None,observed=None)
 if side==0:changes['submitted']=None
 alter(case,side,**changes);output=call(case,coverage_complete=complete);line=output.lines[0]
 assert line.comparison==expected and line.difference is None and line.question
 if side==0:assert line.submission_state=='missing_after_cutoff'
 assert not output.collection_authorized and len(output.observation_fingerprints)==2


def test_absent_before_cutoff_pending_not_late_and_no_padding(case):
 alter(case,0,present=False,value=None,observed=None,submitted=None)
 line=call(case,now=NOW.replace(hour=14)).lines[0]
 assert line.submission_state=='pending' and line.reported is None and line.comparison=='missing_form' and line.question is None


@pytest.mark.parametrize('left,right,expected',[(Decimal('-5.00'),Decimal(3),Decimal('-8.00')),(0,0,Decimal(0)),(Decimal('-0.00'),Decimal(0),Decimal('-0.00'))])
def test_actual_signed_decimal_difference_zero_and_scale(case,left,right,expected):
 alter(case,0,value=left);alter(case,1,value=right);line=call(case).lines[0]
 assert line.difference==expected and line.comparison==('match' if expected==0 else 'discrepancy')


@pytest.mark.parametrize('side',[0,1])
def test_present_unknown_value_not_absent_and_provisional_record_dates(case,side):
 alter(case,side,value=None);line=call(case).lines[0];assert line.comparison=='unknown' and line.difference is None
 alter(case,side,observed=PERIOD.start.isoformat());line=call(case).lines[0];assert line.comparison=='provisional'


def test_three_equal_valued_distinct_metric_pairs_no_aggregation_money(case):
 for side in range(2):
  rows=tuple(row(x,position=i+1,form=side==0,metric=x,value=Decimal(2),unit='money' if x=='b' else 'count',currency='RUB' if x=='b' else None) for i,x in enumerate(('a','b','c')))
  case.persist(side,replace(case.books[side],sheets=(replace(case.books[side].sheets[0],rows=rows),)))
 case.bindings=tuple(binding(x,x) for x in ('a','b','c'));case.reader=reader(case)
 result=call(case,definitions=(definition('a'),definition('b',unit='money',currency='RUB'),definition('c')))
 assert len(result.lines)==3 and all(line.comparison=='match' for line in result.lines) and len(result.observation_fingerprints)==6


@pytest.mark.parametrize('side',[0,1])
@pytest.mark.parametrize('change',['presence_none','presence_int','false_value','false_date','owner','metric','period','unit','currency_empty','observed_none','observed_future','observed_early'])
def test_closed_row_presence_scope_dates_currency_refuse_whole(case,side,change):
 values={'presence_none':{'present':None},'presence_int':{'present':0},'false_value':{'present':False},'false_date':{'present':False,'value':None},'owner':{'owner':'other'},'metric':{'metric':'other'},'period':{'end':'2026-10-20'},'unit':{'unit':'other'},'currency_empty':{'currency':''},'observed_none':{'observed':None},'observed_future':{'observed':'2026-10-20'},'observed_early':{'observed':'2026-10-11'}}
 alter(case,side,**values[change]);deny(lambda:call(case))


@pytest.mark.parametrize('value',[True,1.5,'5','=SUM(A1)',Decimal('NaN'),Decimal('Infinity'),Decimal('1e19'),Decimal('1e-19'),Decimal('1'*51),10**19])
def test_invalid_quantity_never_coerced_signed_legal_values_remain(case,value):
 try:alter(case,0,value=value)
 except ValueError:
  state=json.loads(raw(case,0));cells=state['workbook']['sheets'][0]['rows'][0]['cells'];next(c for c in cells if c[0]=='value')[1]=['float' if type(value) is float else 'int' if type(value) is int else 'decimal',str(value)];case.stores[0].execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.contexts[0].runtime_key))
 deny(lambda:call(case))


@pytest.mark.parametrize('submission',['2026-10-19T17:00:00+00:00','2026-10-19T14:00:00','19.10.2026','2026-10-19','',True])
def test_future_naive_or_guessed_submission_refuses(case,submission):alter(case,0,submitted=submission);deny(lambda:call(case))


@pytest.mark.parametrize('change',['target','direction','label','missing','duplicate','list','period','coverage_open','coverage_future','monday','now_naive'])
def test_strict_informational_definition_explicit_period_coverage_cutoff(case,change):
 args={}
 if change=='target':args['definitions']=(definition(target=Decimal(0)),)
 elif change=='direction':args['definitions']=(definition(direction='at_least'),)
 elif change=='label':args['definitions']=(definition(label='x'*121),)
 elif change=='missing':args['definitions']=(definition('other'),)
 elif change=='duplicate':args['definitions']=(definition(),definition())
 elif change=='list':args['definitions']=[definition()]
 elif change=='period':args['period']=reports.Period(date(2026,10,19),date(2026,10,20))
 elif change=='coverage_open':args['coverage_observed_on']=PERIOD.start
 elif change=='coverage_future':args['coverage_observed_on']=date(2026,10,20)
 elif change=='monday':args['monday']=MONDAY+timedelta(days=1)
 else:args['now']=NOW.replace(tzinfo=None)
 deny(lambda:call(case,**args))


@pytest.mark.parametrize('change',['missing_row','missing_sheet','duplicate_row','missing_cell','duplicate_cell','duplicate_binding','same_row_two_pairs'])
def test_selected_row_required_even_absent_and_exact_lookup_ambiguity(case,change):
 book=case.books[0];r=book.sheets[0].rows[0]
 if change=='missing_row':case.bindings=(replace(binding(),form=selector('missing')),)
 elif change=='missing_sheet':case.bindings=(replace(binding(),form=module.RowSelector('absent',core.RowIdentity('id','main'))),)
 elif change=='duplicate_row':book=replace(book,sheets=(replace(book.sheets[0],rows=(r,replace(r,position=4))),))
 elif change=='missing_cell':book=replace(book,sheets=(replace(book.sheets[0],rows=(replace(r,cells=tuple(c for c in r.cells if c.column!='present')),)),))
 elif change=='duplicate_cell':
  state=json.loads(raw(case,0));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['present',['bool',False]]);case.stores[0].execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.contexts[0].runtime_key))
 elif change=='duplicate_binding':case.bindings=(binding(),binding())
 else:
  case.bindings=(binding(),module.MetricPair('other',module.RowSelector('sheet',core.RowIdentity('owner','opaque-owner')),selector('unselected')));book=replace(book,sheets=(replace(book.sheets[0],rows=(r,)),))
 if change!='duplicate_cell':case.persist(0,book)
 case.reader=reader(case);deny(lambda:call(case,definitions=(definition(),definition('other')) if change=='same_row_two_pairs' else (definition(),)))


@pytest.mark.parametrize('side',[0,1])
@pytest.mark.parametrize('change',['business','owner','role','workbook','revision','timezone','revoked','expired','callback','method','context'])
def test_both_sources_current_scope_method_and_context_drift(case,side,change):
 if change=='callback':case.reader._callback=lambda:case.sources
 elif change=='method':
  original=case.drivers[side].snapshot;case.drivers[side].snapshot=MethodType(lambda self:original(),case.drivers[side])
 elif change=='context':call(case);case.state['contexts'][side]=replace(case.contexts[side])
 else:
  source=case.sources.form if side==0 else case.sources.account
  key,value={'business':('business','OTHER'),'owner':('owner_id','other-owner'),'role':('role',module.SourceRole.ACCOUNT if side==0 else module.SourceRole.FORM),'workbook':('workbook_id','sqlite-book:'+'0'*64),'revision':('revision',2),'timezone':('timezone_name','Asia/Tokyo'),'revoked':('revoked',True),'expired':('expires_at',NOW)}[change]
  pair=replace(case.sources);object.__setattr__(pair,'form' if side==0 else 'account',replace(source,**{key:value}));case.state['sources']=pair
 before=[raw(case,i) for i in range(2)];deny(lambda:call(case));assert before==[raw(case,i) for i in range(2)]


@pytest.mark.parametrize('side',[0,1])
@pytest.mark.parametrize('change',['selected','unmapped','scale','signedzero','boolint','revision','order'])
def test_four_full_typed_snapshot_race_each_source_no_partial_result(case,monkeypatch,side,change):
 before=alter(case,side,unmapped=Decimal('0.00') if change=='signedzero' else 1 if change=='boolint' else Decimal('1.0'));case.books[side]=before;actual=reports.reconcile_forms
 def reconcile(**kw):
  result=actual(**kw)
  if change in ('selected','unmapped','scale','signedzero','boolint'):
   field='value' if change=='selected' else 'unmapped';after=alter(case,side,**{field:{'selected':Decimal(9),'unmapped':'CHANGED','scale':Decimal('1.00'),'signedzero':Decimal('-0.00'),'boolint':True}[change]})
  elif change=='revision':after=replace(before,revision='book-v2')
  else:after=replace(before,sheets=(replace(before.sheets[0],rows=tuple(reversed(before.sheets[0].rows))),))
  case.persist(side,after);return result
 monkeypatch.setattr(reports,'reconcile_forms',reconcile);deny(lambda:call(case))


@pytest.mark.parametrize('change',['definition','period','source'])
def test_explicit_input_mutation_after_actual_core_refuses(case,monkeypatch,change):
 definitions=(definition(),);period=reports.Period(PERIOD.start,PERIOD.end);actual=reports.reconcile_forms
 def reconcile(**kw):
  result=actual(**kw)
  if change=='definition':object.__setattr__(definitions[0],'label','Changed label')
  elif change=='period':object.__setattr__(period,'start',period.start-timedelta(days=1))
  else:case.state['sources']=replace(case.sources,form=replace(case.sources.form,revoked=True))
  return result
 monkeypatch.setattr(reports,'reconcile_forms',reconcile);deny(lambda:call(case,definitions=definitions,period=period))


@pytest.mark.parametrize('bad',['list','owner','metric','comparison','submission','source','question','number'])
def test_malformed_actual_result_fixed_whole_refusal(case,monkeypatch,bad):
 actual=reports.reconcile_forms
 def reconcile(**kw):
  lines=actual(**kw)
  if bad=='list':return list(lines)
  changes={'owner':{'owner':'other'},'metric':{'metric_id':'other'},'comparison':{'comparison':'invented'},'submission':{'submission_state':'invented'},'source':{'sources':('ungranted',)},'question':{'question':'bad\nquestion'},'number':{'difference':Decimal('NaN')}}[bad]
  return (replace(lines[0],**changes),)
 monkeypatch.setattr(reports,'reconcile_forms',reconcile);deny(lambda:call(case))


def test_defaultdisabled_zero_callbacks_snapshots_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched source/driver')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail)
 for s in case.stores:monkeypatch.setattr(s,'execute',fail)
 deny(lambda:r.read_reconciliation(period=PERIOD,definitions=(definition(),),monday=MONDAY,coverage_complete=True,coverage_observed_on=PERIOD.end,now=NOW));assert case.state['source_calls']==0


@pytest.mark.parametrize('side',[0,1])
def test_disabled_actual_driver_and_same_workbook_not_bypassed(case,side):
 driver=object.__new__(driver_module.SQLiteTableDriver);INIT(driver,store=case.stores[side],current_context=case.callbacks[side]);case.reader=reader(case,**{'form_driver' if side==0 else 'account_driver':driver});deny(lambda:call(case));assert case.state['driver_calls'][side]==0


def test_distinct_driver_workbook_and_binding_cap_required(case):
 for args in ({'account_driver':case.drivers[0]},{'bindings':()},{'bindings':(binding(),)*4}):case.reader=reader(case,**args);deny(lambda:call(case))
 pair=replace(case.sources);object.__setattr__(pair,'account',replace(pair.account,workbook_id=pair.form.workbook_id));case.reader=reader(case,expected_sources=pair);deny(lambda:call(case))


def test_actual_berlin_dst_cutoff_utc_equivalent_boundary(case):
 period=reports.Period(date(2026,3,23),date(2026,3,30));monday=period.end;now=datetime(2026,3,30,14,tzinfo=timezone.utc)
 pair=module.SourcePair(replace(case.sources.form,timezone_name='Europe/Berlin'),replace(case.sources.account,timezone_name='Europe/Berlin'));case.sources=pair;case.state['sources']=pair;case.reader=reader(case)
 for i in range(2):
  changes=dict(start=period.start.isoformat(),end=period.end.isoformat(),observed=period.end.isoformat())
  if i==0:changes['submitted']='2026-03-30T13:00:00+00:00'
  alter(case,i,**changes)
 result=call(case,period=period,monday=monday,coverage_observed_on=monday,now=now)
 assert result.due_at.hour==15 and result.due_at.utcoffset()==timedelta(hours=2) and result.due_at.astimezone(timezone.utc).hour==13 and result.lines[0].submission_state=='on_time'


def test_four_public_snapshots_eight_bounded_readonly_transfers_unchanged_nonces(case,monkeypatch):
 actual=driver_module.SQLiteTableDriver.snapshot;calls=[]
 def snapshot(self):calls.append(self);return actual(self)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case);before=[raw(case,i) for i in range(2)];traces=[[],[]]
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:assert table=='runtime' and column in ('key','value')
  elif action not in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION):pytest.fail('reader SQL write/transaction')
  return sqlite3.SQLITE_OK
 for i,s in enumerate(case.stores):s.db.set_authorizer(guard);s.db.set_trace_callback(traces[i].append)
 call(case)
 for s in case.stores:s.db.set_authorizer(None);s.db.set_trace_callback(None)
 assert calls==[case.drivers[0],case.drivers[1],case.drivers[0],case.drivers[1]] and [len(x) for x in traces]==[4,4]
 assert all('CASE WHEN typeof(value)' in q and 'LIMIT 2' in q for trace in traces for q in trace)
 assert before==[raw(case,i) for i in range(2)] and all(json.loads(x)['used_nonces']==[] for x in before)


def test_actual_result_cannot_swap_other_bound_metric_provenance(case,monkeypatch):
 for side in range(2):
  rows=tuple(row(x,position=i+1,form=side==0,metric=x) for i,x in enumerate(('a','b')))
  case.persist(side,replace(case.books[side],sheets=(replace(case.books[side].sheets[0],rows=rows),)))
 case.bindings=(binding('a','a'),binding('b','b'));case.reader=reader(case);actual=reports.reconcile_forms
 def reconcile(**kw):
  lines=actual(**kw);return (replace(lines[0],sources=lines[1].sources),lines[1])
 monkeypatch.setattr(reports,'reconcile_forms',reconcile);deny(lambda:call(case,definitions=(definition('a'),definition('b'))))
