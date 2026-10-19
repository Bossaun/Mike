"""Actual durable channel heads/children -> coverage-aware publication_gap only."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json,sqlite3
import pytest
from goldway import table_campaign_report as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;m=module.marketing
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)
END=NOW.replace(hour=9);OBSERVED=NOW.replace(hour=10)
KEY=b'OPENLY-SYNTHETIC-CAMPAIGN-TEST-KEY-000001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


START=datetime(2026,10,14,12,tzinfo=timezone.utc)
END=START+timedelta(days=7)
def columns():return module.CampaignColumns('row','kind',module.HeadColumns('campaign','start','end'),module.DefinitionColumns('campaign','key','label','definition','unit'),module.ObservationColumns('id','campaign','key','unit','value','start','end','observed','coverage'))
def row(technical,kind,position,**kw):return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in dict(row=technical,kind=kind,**kw).items()))
def definition(key,position):return row('d'+key,'definition',position,campaign='campaign',key=key,label='<'+key+'>',definition='Supplied count '+key,unit='count')
def observation(key,position,value=0,coverage='partial'):return row('o'+key,'observation',position,id='obs'+key,campaign='campaign',key=key,unit='count',value=value,start=START.isoformat(),end=END.isoformat(),observed=NOW.isoformat(),coverage=coverage)
def initial_rows():return (row('head','head',1,campaign='campaign',start=START.isoformat(),end=END.isoformat()),definition('A',2),definition('B',3),definition('C',4),observation('A',5,Decimal('0.00')),observation('B',6,7,'unknown'))
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='observations',columns=c.columns,enabled=True);d.update(kw);return module.TableCampaignReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-publications','synthetic-approval','observations','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('observations','sheet-v1',initial_rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.CampaignSource('SYNTHETIC','opaque-account',ctx.workbook_id,module.SourceRole.CAMPAIGN_SAMPLE,'campaign',1,datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
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
   p=BUILD(writer,'observations',core.RowIdentity('row','oA'),(core.Cell('value',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_value=approved_value
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
def call(c,**kw):return c.reader.read_campaign(campaign_id=kw.get('campaign_id','campaign'),now=kw.get('now',NOW))
def deny(fn):
 with pytest.raises(module.CampaignReaderUnavailable,match='^table_campaign_report_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,technical,**changes):
 records=[]
 for r in c.book.sheets[0].rows:
  if next(cell.value for cell in r.cells if cell.column=='row')==technical:r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells))
  records.append(r)
 return selected(c,records)


def test_actual_durable_report_renderer_once_partial_unknown_missing_restart_correction(case,monkeypatch):
 actual=m.campaign_report;render=m.render_campaign_report;calls=[];renders=[];lookup=core.lookup_row;found=[]
 def report(*a,**kw):calls.append(a);return actual(*a,**kw)
 def draft(r):renders.append(r);return render(r)
 def find(*a,**kw):found.append(kw);return lookup(*a,**kw)
 monkeypatch.setattr(m,'campaign_report',report);monkeypatch.setattr(m,'render_campaign_report',draft);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);out=call(case);a,b,c=out.report.rows
 assert len(calls)==len(renders)==1 and len(found)==6 and renders[0] is out.report
 assert a.value.as_tuple()==Decimal('0.00').as_tuple() and a.coverage=='partial'
 assert b.value==7 and b.coverage=='unknown' and c.value is None and c.coverage=='missing'
 assert '<A>: 0.00 count' in out.draft.text and '[нет наблюдения]' in out.draft.text and out.report.period_state=='closed'
 assert out.requires_review and not out.live_authorized and not out.report_delivery_authorized and raw(case)==before
 case.reopen();assert call(case).draft==out.draft
 assert case.approved_value(Decimal('12.0')).state=='local_fake_committed';case.reopen();updated=call(case)
 assert updated.report.rows[0].value==12 and '12.0 count' in updated.draft.text and updated.workbook_fingerprint!=out.workbook_fingerprint
 assert KEY.decode() not in repr(updated) and json.loads(raw(case))['used_nonces']==['synthetic-nonce']
 with pytest.raises(FrozenInstanceError):updated.live_authorized=True

@pytest.mark.parametrize('now,state',[(START-timedelta(seconds=1),'before_start'),(START,'in_progress'),(END-timedelta(microseconds=1),'in_progress'),(END,'closed')])
def test_empty_observations_period_state_not_schedule(case,now,state):
 selected(case,case.book.sheets[0].rows[:4]);out=call(case,now=now)
 assert out.report.period_state==state and all(r.value is None for r in out.report.rows) and out.draft.source_ids==()

@pytest.mark.parametrize('value',[0,10**9,Decimal('0'),Decimal('-0.00'),Decimal('1E+9'),Decimal('2.000')])
def test_exact_integral_count_positive(case,value):alter(case,'oA',value=value);assert call(case).report.rows[0].value==value
@pytest.mark.parametrize('value',[True,False,None,'0','=1+1',Decimal('-1'),-1,10**9+1,Decimal('0.1'),Decimal('1E-19'),Decimal('1E+19')])
def test_invalid_count_whole_refusal(case,value):
 if type(value) is Decimal and abs(value.as_tuple().exponent)>18:
  data=json.loads(raw(case));cells=data['workbook']['sheets'][0]['rows'][4]['cells']
  for cell in cells:
   if cell[0]=='value':cell[1]=['decimal',str(value)]
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(data),case.ctx.runtime_key))
 else:alter(case,'oA',value=value)
 deny(lambda:call(case))

@pytest.mark.parametrize('technical,changes',[
 ('head',{'campaign':'foreign'}),('dA',{'campaign':'foreign'}),('oA',{'campaign':'foreign'}),('dA',{'key':'B'}),('oA',{'key':'foreign'}),('oA',{'id':'obsB'}),('oA',{'key':'B'}),('dA',{'unit':'USD'}),('oA',{'unit':'money'}),('oA',{'start':(START+timedelta(seconds=1)).isoformat()}),('oA',{'end':(END+timedelta(seconds=1)).isoformat()}),('oA',{'observed':(NOW+timedelta(seconds=1)).isoformat()}),('oA',{'observed':(START-timedelta(seconds=1)).isoformat()}),('oA',{'coverage':'invalid'}),('oA',{'coverage':None}),('head',{'start':'2026-10-14'}),('head',{'end':'2026-10-21T12:00:00'}),('head',{'end':(END+timedelta(seconds=1)).isoformat()}),('dA',{'label':''}),('dA',{'label':'x\ny'}),('dA',{'label':'😀'*41}),('dA',{'definition':'x'*301}),('dA',{'definition':'x\ttext'}),('oA',{'row':'dA'})])
def test_closed_exact_join_timestamp_text_inputs(case,technical,changes):alter(case,technical,**changes);deny(lambda:call(case))

def test_complete_before_end_and_equivalent_offsets(case):
 b=alter(case,'oA',observed=(END-timedelta(seconds=1)).isoformat(),coverage='complete');deny(lambda:call(case))
 case.persist(case.book);b=alter(case,'head',start='2026-10-14T14:00:00+02:00',end='2026-10-21T14:00:00+02:00');assert call(case).report.campaign.start==START

@pytest.mark.parametrize('change',['nohead','twoheads','nodefs','extra','missingcell','duplicatecell','eightrows','fourdefs','fourobservations'])
def test_whole_selected_row_set_caps(case,change):
 rows=list(case.book.sheets[0].rows)
 if change=='nohead':rows=rows[1:]
 elif change=='twoheads':rows.append(replace(rows[0],position=7,cells=tuple(core.Cell(c.column,'head2' if c.column=='row' else c.value) for c in rows[0].cells)))
 elif change=='nodefs':rows=[rows[0]]+rows[4:]
 elif change=='extra':rows[0]=replace(rows[0],cells=rows[0].cells+(core.Cell('body','private'),))
 elif change=='missingcell':rows[1]=replace(rows[1],cells=rows[1].cells[:-1])
 elif change=='duplicatecell':
  data=json.loads(raw(case));data['workbook']['sheets'][0]['rows'][0]['cells'].append(['campaign',['str','campaign']]);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(data),case.ctx.runtime_key));deny(lambda:call(case));return
 elif change=='eightrows':rows+=[definition('D',7),observation('D',8)]
 elif change=='fourdefs':rows=[rows[0]]+rows[1:4]+[definition('D',7)]
 elif change=='fourobservations':rows=[rows[0],rows[1]]+[observation(str(i),i+3) for i in range(4)]
 selected(case,rows);deny(lambda:call(case))

def test_full_seven_rows_complete_zero_not_missing(case):
 selected(case,case.book.sheets[0].rows+(observation('C',7,0,'complete'),));out=call(case)
 assert len(out.report.rows)==3 and out.report.rows[-1].value==0 and out.report.rows[-1].coverage=='complete' and len(out.row_fingerprints)==7

@pytest.mark.parametrize('field,value',[('business','other'),('account_scope','other'),('revision',2),('campaign_id','other'),('revoked',True),('expires_at',NOW),('valid_from',NOW+timedelta(seconds=1))])
def test_current_source_drift(case,field,value):case.state['source']=replace(case.source,**{field:value});deny(lambda:call(case))

@pytest.mark.parametrize('change',['scale','signedzero','boolint','unselected','revision','order','method','callback','schema'])
def test_full_typed_snapshot_and_current_host_proof(case,monkeypatch,change):
 actual=m.render_campaign_report
 def render(r):
  out=actual(r)
  if change in ('method','callback','schema'):
   if change=='method':case.driver.snapshot=MethodType(lambda s:case.book,case.driver)
   elif change=='callback':case.reader._callback=lambda:case.source
   else:object.__setattr__(case.columns.head,'start','changed')
  else:
   book=case.book
   if change=='revision':book=replace(book,revision='new')
   elif change=='order':book=replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1]))
   elif change in ('scale','signedzero'):book=alter(case,'oA',value=Decimal('0.0') if change=='scale' else Decimal('-0.00'))
   else:
    v=True if change=='boolint' else Decimal('2.0');other=replace(book.sheets[1],rows=(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',v))),));book=replace(book,sheets=(book.sheets[0],other))
   case.persist(book)
  return out
 monkeypatch.setattr(m,'render_campaign_report',render);deny(lambda:call(case))

@pytest.mark.parametrize('change',['report_type','campaign','rows','state','rowvalue','coverage','source','observed','draft_type','draft_refs','draft_flag','draft_long','draft_bytes'])
def test_malformed_detached_core_output_whole_refusal(case,monkeypatch,change):
 actual=m.campaign_report;render=m.render_campaign_report
 def report(*a,**kw):
  r=actual(*a,**kw)
  if change=='report_type':return None
  if change=='campaign':return replace(r,campaign=replace(r.campaign,id='other'))
  if change=='rows':return replace(r,rows=())
  if change=='state':return replace(r,period_state='invalid')
  if change in ('rowvalue','coverage','source','observed'):
   k,v={'rowvalue':('value',Decimal('1')),'coverage':('coverage','complete'),'source':('source_id','other'),'observed':('observed_at',START)}[change];return replace(r,rows=(replace(r.rows[0],**{k:v}),)+r.rows[1:])
  return r
 def draft(r):
  d=render(r)
  if change=='draft_type':return None
  if change=='draft_refs':return replace(d,source_ids=())
  if change=='draft_flag':object.__setattr__(d,'live_authorized',True)
  if change=='draft_long':return replace(d,text='x'*3501)
  if change=='draft_bytes':return replace(d,text='漢'*3000)
  return d
 monkeypatch.setattr(m,'campaign_report',report);monkeypatch.setattr(m,'render_campaign_report',draft);deny(lambda:call(case))


def test_default_disabled_no_effects_and_requested_scope(case):
 before=raw(case);calls=case.state.copy();deny(lambda:reader(case,enabled=False).read_campaign(campaign_id='campaign',now=NOW));assert case.state==calls and raw(case)==before
 deny(lambda:call(case,campaign_id='other'));deny(lambda:call(case,now=NOW.replace(tzinfo=None)))

def test_two_public_snapshots_four_runtime_transfers_no_writes(case,monkeypatch):
 actual=driver_module.SQLiteTableDriver.snapshot;calls=[];reads=[]
 def snapshot(s):calls.append(1);return actual(s)
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case)
 def authorizer(op,a,b,*rest):
  if op in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):pytest.fail('reader wrote SQL')
  if op==sqlite3.SQLITE_READ:reads.append((a,b))
  return sqlite3.SQLITE_OK
 before=raw(case);case.s.db.set_authorizer(authorizer);call(case);case.s.db.set_authorizer(None)
 assert len(calls)==2 and sum(1 for t,c in reads if t=='runtime' and c=='value')<=4 and raw(case)==before
