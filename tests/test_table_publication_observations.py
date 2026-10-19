"""Actual durable channel heads/children -> coverage-aware publication_gap only."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json,sqlite3
import pytest
from goldway import table_publication_observations as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;m=module.marketing
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)
END=NOW.replace(hour=9);OBSERVED=NOW.replace(hour=10)
KEY=b'OPENLY-SYNTHETIC-PUBLICATION-TEST-KEY-0001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


def columns():return module.ObservationColumns('row','kind',module.HeadColumns('channel','present','observed','start','end','coverage'),module.PublicationColumns('id','channel','published'))
def stamp(day,hour=12):return datetime(2026,10,day,hour,tzinfo=timezone.utc).isoformat()
def row(technical,kind,position,**kw):return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in dict(row=technical,kind=kind,**kw).items()))
def head(channel,position,present=True,coverage='complete'):
 return row('h'+channel,'head',position,channel=channel,present=present,observed=OBSERVED.isoformat() if present else None,start=stamp(1) if present else None,end=END.isoformat() if present else None,coverage=coverage if present else None)
def initial_rows():return (head('A',1),head('B',2,coverage='partial'),head('C',3,present=False),row('pA','publication',4,id='aPub',channel='A',published=stamp(10)),row('pB','publication',5,id='bPub',channel='B',published=stamp(20)))
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='observations',columns=c.columns,policies=c.policies,enabled=True);d.update(kw);return module.TablePublicationReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-publications','synthetic-approval','observations','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('observations','sheet-v1',initial_rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.PublicationSource('SYNTHETIC','opaque-account',ctx.workbook_id,module.SourceRole.PUBLICATION_SAMPLE,('A','B','C'),1,datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,columns=columns(),policies=tuple(module.ChannelPolicy(channel,timedelta(days=7),timedelta(days=2)) for channel in ('A','B','C')));c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_publication(value):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('apply_to_fake_workbook',CORE_APPLY),('check_plan_baseline',CHECK)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   p=BUILD(writer,'observations',core.RowIdentity('row','pA'),(core.Cell('published',value),));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,p,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_publication=approved_publication
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
def call(c,**kw):return c.reader.read_gaps(now=kw.get('now',NOW))
def deny(fn):
 with pytest.raises(module.PublicationReaderUnavailable,match='^table_publication_observations_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,technical,**changes):
 records=[]
 for r in c.book.sheets[0].rows:
  if next(cell.value for cell in r.cells if cell.column=='row')==technical:r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells))
  records.append(r)
 return selected(c,records)


def test_actual_durable_three_channels_gap_partial_recent_missing_restart_approved_correction(case,monkeypatch):
 actual=m.publication_gap;calls=[];lookup=core.lookup_row;lookups=[]
 def gap(observation,**kw):calls.append((observation,kw));return actual(observation,**kw)
 def find(*a,**kw):lookups.append(kw['identity']);return lookup(*a,**kw)
 monkeypatch.setattr(m,'publication_gap',gap);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);first=call(case);a,b,c=first.gaps
 assert [kw['channel_id'] for obs,kw in calls]==['A','B','C'] and len(lookups)==5
 assert a.state=='gap' and a.window_publication_count==0 and a.as_of==END and a.window_start==END-timedelta(days=7)
 assert b.state=='recent_publication' and b.coverage=='partial' and b.window_publication_count is None
 assert c.state=='unknown' and c.coverage=='missing' and c.as_of is None and calls[-1][0] is None
 assert first.observations[0].observed_at==OBSERVED and first.observations[0].coverage_end==END and END!=NOW
 assert first.requires_review and not first.live_authorized and not first.monitoring_authorized and not first.reminder_authorized and raw(case)==before
 case.reopen();same=call(case);assert same.gaps==first.gaps and same.workbook_fingerprint==first.workbook_fingerprint
 receipt=case.approved_publication(stamp(20));assert receipt.state=='local_fake_committed';case.reopen();before=raw(case);updated=call(case)
 assert updated.gaps[0].state=='recent_publication' and updated.gaps[0].window_publication_count==1 and updated.workbook_fingerprint!=first.workbook_fingerprint and raw(case)==before
 assert json.loads(before)['used_nonces']==['synthetic-nonce'] and KEY.decode() not in repr(updated) and not hasattr(updated,'workbook')
 with pytest.raises(FrozenInstanceError):updated.monitoring_authorized=True


def test_same_source_later_now_ages_unknown_equality_fresh_not_rebased(case):
 at=END+timedelta(days=2);equal=call(case,now=at);stale=call(case,now=at+timedelta(microseconds=1))
 assert equal.gaps[0].state=='gap' and equal.gaps[1].state=='recent_publication'
 assert stale.gaps[0].state==stale.gaps[1].state=='unknown' and stale.gaps[0].window_publication_count is None
 assert stale.gaps[0].as_of==equal.gaps[0].as_of==END and stale.observations[0].observed_at==OBSERVED


@pytest.mark.parametrize('coverage,state,count',[('complete','gap',0),('partial','unknown',None),('unknown','unknown',None)])
def test_empty_declared_window_not_actual_account_silence(case,coverage,state,count):
 records=[r for r in case.book.sheets[0].rows if next(c.value for c in r.cells if c.column=='row')!='pA'];records=[replace(r,cells=tuple(core.Cell(c.column,coverage if c.column=='coverage' else c.value) for c in r.cells)) if next(c.value for c in r.cells if c.column=='row')=='hA' else r for r in records];selected(case,records)
 result=call(case);assert result.gaps[0].state==state and result.gaps[0].window_publication_count==count and not result.monitoring_authorized


@pytest.mark.parametrize('published,valid,state',[( (END-timedelta(days=7)).isoformat(),True,'recent_publication'),(END.isoformat(),False,None),(stamp(1),True,'gap'),('2026-10-21T11:00:00+02:00',False,None)])
def test_actual_half_open_publication_coverage_and_threshold_boundaries(case,published,valid,state):
 alter(case,'pA',published=published)
 if valid:assert call(case).gaps[0].state==state
 else:deny(lambda:call(case))


def test_too_short_complete_window_count_unknown_and_equivalent_offset_instants(case):
 b=alter(case,'hA',start=stamp(15));case.book=b;alter(case,'pA',published=stamp(16))
 result=call(case);assert result.gaps[0].state=='recent_publication' and result.gaps[0].window_publication_count is None
 b=alter(case,'hA',observed='2026-10-21T12:00:00+02:00',end='2026-10-21T11:00:00+02:00');case.book=b;alter(case,'pA',published=stamp(16))
 assert call(case).gaps[0].as_of==END


@pytest.mark.parametrize('change',['missing_head','duplicate_head','false_children','false_timestamp','unknown_presence','unbound_channel','wrong_pub_channel','duplicate_pub_id','duplicate_rowid','extra_body','missing_cell','duplicate_cell','kind','future_observation','future_coverage','reversed','naive','dateonly','huge_timestamp'])
def test_closed_heads_children_types_and_required_explicit_observations(case,change):
 records=list(case.book.sheets[0].rows)
 if change=='missing_head':records=[r for r in records if next(c.value for c in r.cells if c.column=='row')!='hC']
 elif change=='duplicate_head':records.append(replace(records[0],position=6,cells=tuple(core.Cell(c.column,'hA2' if c.column=='row' else c.value) for c in records[0].cells)))
 elif change=='false_children':records.append(row('pC','publication',6,id='cPub',channel='C',published=stamp(20)))
 elif change=='extra_body':records[0]=replace(records[0],cells=records[0].cells+(core.Cell('caption','BODY'),))
 elif change=='missing_cell':records[0]=replace(records[0],cells=tuple(c for c in records[0].cells if c.column!='present'))
 elif change=='duplicate_cell':
  state=json.loads(raw(case));state['workbook']['sheets'][0]['rows'][0]['cells'].append(['present',['bool',True]]);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
 else:
  technical,key,value={'false_timestamp':('hC','observed',OBSERVED.isoformat()),'unknown_presence':('hA','present',None),'unbound_channel':('hC','channel','OUTSIDE'),'wrong_pub_channel':('pA','channel','C'),'duplicate_pub_id':('pB','id','aPub'),'duplicate_rowid':('pA','row','hA'),'kind':('hA','kind','account'),'future_observation':('hA','observed',(NOW+timedelta(seconds=1)).isoformat()),'future_coverage':('hA','end',(NOW+timedelta(seconds=1)).isoformat()),'reversed':('hA','start',END.isoformat()),'naive':('hA','observed','2026-10-21T10:00:00'),'dateonly':('hA','observed','2026-10-21'),'huge_timestamp':('hA','observed','x'*41)}[change]
  if change=='duplicate_pub_id':
   # IDs are scoped per channel; duplicate only within the same channel is invalid.
   records.append(row('pA2','publication',6,id='aPub',channel='A',published=stamp(11)))
  else:records=[replace(r,cells=tuple(core.Cell(c.column,value if c.column==key else c.value) for c in r.cells)) if next(c.value for c in r.cells if c.column=='row')==technical else r for r in records]
 if change!='duplicate_cell':selected(case,records)
 deny(lambda:call(case))


def test_same_publication_id_in_distinct_channels_not_merged(case):alter(case,'pB',id='aPub');assert len(call(case).gaps)==3


@pytest.mark.parametrize('duration',[None,True,1,timedelta(0),timedelta(days=-1),timedelta(days=367)])
def test_threshold_and_maxage_explicit_positive_bounded_no_defaults(case,duration):
 policy=replace(case.policies[0]);object.__setattr__(policy,'threshold',duration);case.reader=reader(case,policies=(policy,)+case.policies[1:]);deny(lambda:call(case))
 policy=replace(case.policies[0]);object.__setattr__(policy,'max_observation_age',duration);case.reader=reader(case,policies=(policy,)+case.policies[1:]);deny(lambda:call(case))


def test_policy_exact_unique_channel_match_and_timestamp_underflow(case):
 for policies in ([],(),case.policies[:2],(case.policies[0],)*3):case.reader=reader(case,policies=policies);deny(lambda:call(case))
 # Actual core owns threshold underflow; no guessed start date.
 source=replace(case.source,valid_from=datetime.min.replace(tzinfo=timezone.utc));case.source=source;case.state['source']=source
 policies=tuple(module.ChannelPolicy(ch,timedelta(days=366),timedelta(days=366)) for ch in source.channel_ids);case.reader=reader(case,policies=policies)
 records=(row('hA','head',1,channel='A',present=True,observed='0001-01-02T12:00:00+00:00',start='0001-01-01T12:00:00+00:00',end='0001-01-02T12:00:00+00:00',coverage='complete'),head('B',2,present=False),head('C',3,present=False));selected(case,records)
 deny(lambda:call(case,now=datetime(1,1,3,tzinfo=timezone.utc)))


@pytest.mark.parametrize('change',['business','account','workbook','role','channels','revision','revoked','expired','future','callback','method','context'])
def test_current_source_method_and_driver_context_drift(case,change):
 if change=='callback':case.reader._callback=lambda:case.source
 elif change=='method':
  original=case.driver.snapshot;case.driver.snapshot=MethodType(lambda self:original(),case.driver)
 elif change=='context':call(case);case.state['context']=replace(case.ctx)
 else:
  key,value={'business':('business','OTHER'),'account':('account_scope','OTHER'),'workbook':('workbook_id','sqlite-book:'+'0'*64),'role':('role','publication_sample'),'channels':('channel_ids',('A','B')),'revision':('revision',2),'revoked':('revoked',True),'expired':('expires_at',NOW),'future':('valid_from',NOW+timedelta(hours=1))}[change];source=replace(case.source)
  if change=='role':object.__setattr__(source,key,value)
  else:source=replace(source,**{key:value})
  case.state['source']=source
 before=raw(case);deny(lambda:call(case));assert raw(case)==before


@pytest.mark.parametrize('change',['selected','unselected','scale','signedzero','boolint','revision','row_order','sheet_order','source','columns','policy'])
def test_whole_typed_final_snapshot_and_policy_drift_no_fallback(case,monkeypatch,change):
 book=case.book
 if change in ('signedzero','boolint'):
  other=book.sheets[1];r=other.rows[0];r=replace(r,cells=(r.cells[0],core.Cell('value',Decimal('0.00') if change=='signedzero' else 1)));book=replace(book,sheets=(book.sheets[0],replace(other,rows=(r,))));case.book=book;case.persist(book)
 actual=m.publication_gap;fired=[]
 def gap(*a,**kw):
  result=actual(*a,**kw)
  if fired:return result
  fired.append(True)
  if change=='selected':alter(case,'pA',published=stamp(20))
  elif change in ('unselected','scale','signedzero','boolint'):
   other=book.sheets[1];r=other.rows[0];v={'unselected':'CHANGED','scale':Decimal('1.00'),'signedzero':Decimal('-0.00'),'boolint':True}[change];r=replace(r,cells=(r.cells[0],core.Cell('value',v)));case.persist(replace(book,sheets=(book.sheets[0],replace(other,rows=(r,)))))
  elif change=='revision':case.persist(replace(book,revision='book-v2'))
  elif change=='row_order':case.persist(replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1])))
  elif change=='sheet_order':case.persist(replace(book,sheets=tuple(reversed(book.sheets))))
  elif change=='source':case.state['source']=replace(case.source,revoked=True)
  elif change=='columns':case.reader._columns=replace(case.columns)
  else:object.__setattr__(case.policies[0],'threshold',timedelta(days=8))
  return result
 monkeypatch.setattr(m,'publication_gap',gap);deny(lambda:call(case))


@pytest.mark.parametrize('bad',['type','channel','state','asof','window','coverage','count_bool','count_huge','last','source','missing_shape'])
def test_malformed_actual_gap_result_whole_safe_refusal(case,monkeypatch,bad):
 actual=m.publication_gap
 def gap(*a,**kw):
  result=actual(*a,**kw)
  if bad=='type':return {}
  if bad=='missing_shape':return replace(result,as_of=NOW) if result.coverage=='missing' else result
  changes={'channel':{'channel_id':'OUTSIDE'},'state':{'state':'send_reminder'},'asof':{'as_of':NOW},'window':{'window_start':END},'coverage':{'coverage':'invented'},'count_bool':{'window_publication_count':True},'count_huge':{'window_publication_count':100},'last':{'last_observed_publication':NOW},'source':{'source_ids':('foreign',)}}[bad]
  return replace(result,**changes)
 monkeypatch.setattr(m,'publication_gap',gap);deny(lambda:call(case))


def test_max24_rows21children_and_perchannel12_all_consumed(case,monkeypatch):
 records=[head('A',1),head('B',2),head('C',3,present=False)]
 for channel,count in (('A',12),('B',9)):
  for i in range(count):records.append(row('p'+channel+str(i),'publication',len(records)+1,id=str(i),channel=channel,published=stamp(10+i%10)))
 selected(case,records);actual=core.lookup_row;calls=[]
 def find(*a,**kw):calls.append(kw['identity']);return actual(*a,**kw)
 monkeypatch.setattr(core,'lookup_row',find);result=call(case)
 assert len(calls)==24 and len(result.observation_fingerprints)==24 and len(result.gaps)==3 and len(result.gaps[0].source_ids)==13


@pytest.mark.parametrize('change',['total25','perchannel13','children22','empty_heads'])
def test_caps_full_refusal_no_truncation(case,change):
 if change=='empty_heads':selected(case,())
 else:
  records=[head('A',1),head('B',2),head('C',3,present=False)]
  if change=='perchannel13':counts=(13,0)
  elif change=='children22':counts=(12,10)
  else:counts=(12,10)
  for ch,n in zip(('A','B'),counts):
   for i in range(n):records.append(row(ch+str(i),'publication',len(records)+1,id=str(i),channel=ch,published=stamp(10)))
  selected(case,records)
 deny(lambda:call(case))


def test_defaultdisabled_zero_callbacks_snapshot_sql(case,monkeypatch):
 r=reader(case,enabled=False)
 def fail(*a,**kw):pytest.fail('disabled touched source')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',fail);monkeypatch.setattr(case.s,'execute',fail);deny(lambda:r.read_gaps(now=NOW));assert case.state['source_calls']==case.state['driver_calls']==0


def test_actual_driver_disabled_missing_sheet_and_naive_now_refuse(case):
 driver=object.__new__(driver_module.SQLiteTableDriver);INIT(driver,store=case.s,current_context=case.context);case.reader=reader(case,driver=driver);deny(lambda:call(case));assert case.state['driver_calls']==0
 case.reader=reader(case,sheet_id='missing');deny(lambda:call(case))
 case.reader=reader(case);deny(lambda:call(case,now=NOW.replace(tzinfo=None)))


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
