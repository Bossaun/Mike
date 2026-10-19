"""Actual persisted/reopened SQLite table -> real lookup and daily KPI core."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json
import sqlite3
import pytest
from goldway import table_kpi_reader as kpi
from goldway import operational_metrics_agenda as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence,reader_key
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher
from goldway.model import ScriptedModel
from goldway.work_context import work_id

core=kpi.tables
reports=module.reports
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
PERIOD=reports.Period(date(2026,10,18),date(2026,10,19))
KEY=b'EXPLICIT-SYNTHETIC-TEST-KEY-NOT-HUMAN-0001'
ISSUE=core.issue_local_approval
APPLY=driver_module.SQLiteTableDriver.apply
BUILD=driver_module.SQLiteTableDriver.build_plan
VALIDATE=core.validate_approval
CORE_APPLY=core.apply_to_fake_workbook
CORE_BUILD=core.build_plan
CHECK=core.check_plan_baseline
DRIVER_INIT=driver_module.SQLiteTableDriver.__init__


def columns():return kpi.ColumnSchema('owner','start','end','value','unit','currency','observed')

def definition(identifier='metric',**kw):
 d=dict(id=identifier,label='Synthetic metric',unit='count',currency=None,target=Decimal('2.00'),direction='at_least');d.update(kw);return reports.MetricDefinition(**d)

def row(identifier='row',position=1,value=Decimal('2.00'),**kw):
 cells=dict(id=identifier,owner='alen',start=PERIOD.start.isoformat(),end=PERIOD.end.isoformat(),value=value,unit='count',currency=None,observed=PERIOD.end.isoformat(),unmapped='inert =SUM(A1)');cells.update(kw)
 return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in cells.items()))

def encode(context,book,nonces=()):
 return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=context.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))

def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,bindings=c.bindings,columns=c.columns,enabled=True);d.update(kw);return kpi.TableKpiReader(**d)


def config():
 names=['test_operator','frost','alen','nastya','dasha_it']
 return dict(business='SYNTHETIC',employees={name:dict(name='Synthetic '+name,telegram_user_id=77+i) for i,name in enumerate(names)},admins=['test_operator'],timezone='UTC',team_intake={'recipients':names,'started_at':int(NOW.timestamp())-86400},team_roster_verified=True,business_account_user_id=99,allowed_live_recipients=names,live_send=False,recipients_verified=True,review_mode=True,allow_onboarding_replies=False,allow_registered_replies=False,sheets_write=False,daily_summary=False,intelligence={'enabled':True,'sources':[]},rules=[])

@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path))
 ctx=driver_module.TableContext('SYNTHETIC','fake-driver','synthetic-approval','book','synthetic-reviewer','synthetic-key',KEY)
 source=kpi.KpiSource('SYNTHETIC',ctx.workbook_id,'alen','UTC',1,NOW-timedelta(days=2),NOW+timedelta(days=2))
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('sheet','sheet-v1',(row(),row('zero',position=2,value=0),row('unknown',position=3,value=None,observed=None),row('unselected',position=4,value=None,unmapped=Decimal('-0.00')))),))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('unrelated-runtime','untouched'))
 state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 d=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 c=SimpleNamespace(s=s,path=path,ctx=ctx,source=source,book=book,columns=columns(),bindings=tuple(kpi.MetricBinding(metric,'sheet',core.RowIdentity('id',identity)) for metric,identity in (('metric','row'),('zero','zero'),('unknown','unknown'))),state=state,current=current,context=context,driver=d)
 c.reader=reader(c)
 hs=Store(':memory:');cfg=config();model=ScriptedModel({});e=Engine(hs,cfg,model,lambda:NOW.timestamp());c.hs=hs;c.cfg=cfg;c.e=e;c.model=model
 def seed(identifier='task',**kw):
  d=dict(business='SYNTHETIC',id=identifier,title='Synthetic task',owner='alen',deadline='2026-10-18',next_step='Synthetic next',criterion='Synthetic criterion',status='blocked',source_id='not-read-source',evidence='BODY-NOT-READ',created=NOW.timestamp()-3600);d.update(kw);hs.execute('INSERT INTO tasks('+','.join(d)+') VALUES('+','.join('?' for x in d)+')',tuple(d.values()));return identifier
 c.seed=seed;seed('task1');seed('task2');c.priorities=tuple(module.MetricPriority(key,p) for key,p in (('metric',0),('zero',5),('unknown',50)));c.agenda=agenda(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);original(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);DRIVER_INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c);c.agenda=agenda(c)
 c.persist=persist;c.reopen=reopen
 def write_value(value):
  # Fixture-only synthetic approval/application; reader never receives key/token.
  with monkeypatch.context() as host:
   host.setattr(core,'validate_approval',VALIDATE);host.setattr(core,'apply_to_fake_workbook',CORE_APPLY);host.setattr(core,'build_plan',CORE_BUILD);host.setattr(core,'check_plan_baseline',CHECK)
   writer=object.__new__(driver_module.SQLiteTableDriver);DRIVER_INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   plan=BUILD(writer,'sheet',core.RowIdentity('id','row'),(core.Cell('value',value),))
   token=ISSUE(plan,decision='approve',approver=ctx.required_approver,nonce='explicit-synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,plan,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.write_value=write_value
 def forbidden(*a,**kw):pytest.fail('reader used constructor/write/approval/model/network/body/outbox path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',forbidden)
 for name in ('build_plan','apply'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,forbidden)
 for name in ('issue_local_approval','validate_approval','apply_to_fake_workbook','build_plan','check_plan_baseline'):monkeypatch.setattr(core,name,forbidden)
 monkeypatch.setattr(Store,'queue',forbidden);monkeypatch.setattr(model,'propose',forbidden);monkeypatch.setattr(e.intelligence.sources,'refresh',forbidden)
 import socket,httpx,builtins,subprocess
 monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden);monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close();c.hs.db.close()


def agenda(c,**kw):
 d=dict(host=c.e,kpi_reader=c.reader,expected_kpi_source=c.source,priorities=c.priorities,enabled=True);d.update(kw);return module.OperationalMetricsAgenda(**d)
def call(c,**kw):
 d=dict(sender='test_operator',as_of=NOW,period=PERIOD,definitions=(definition(),definition('zero',label='Explicit zero'),definition('unknown',label='Unknown metric')),coverage_complete=True,coverage_observed_on=PERIOD.end,max_items=5);d.update(kw);return c.agenda.preview(**d)


def deny(fn):
 with pytest.raises(module.MetricsAgendaUnavailable,match='^operational_metrics_agenda_unavailable$'):fn()


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]


def alter(c,**changes):
 r=c.book.sheets[0].rows[0];cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells);new=replace(c.book,sheets=(replace(c.book.sheets[0],rows=(replace(r,cells=cells),)+c.book.sheets[0].rows[1:]),));c.persist(new);return new


def test_actual_engine_task_adapter_kpi_twopasses_coreagendaonce_allrefs_reopen_correction(case,monkeypatch):
 build=module.adapter.build_agenda_preview;daily=kpi.TableKpiReader.read_daily;proof=kpi.TableKpiReader.source_proof;coreagenda=reports.agenda_preview;calls=[]
 def task(*a,**kw):calls.append('task');return build(*a,**kw)
 def read(self,**kw):calls.append('read');return daily(self,**kw)
 def meta(self,**kw):calls.append('proof');return proof(self,**kw)
 def combined(**kw):calls.append('combined');return coreagenda(**kw)
 monkeypatch.setattr(module.adapter,'build_agenda_preview',task);monkeypatch.setattr(kpi.TableKpiReader,'read_daily',read);monkeypatch.setattr(kpi.TableKpiReader,'source_proof',meta);monkeypatch.setattr(reports,'agenda_preview',combined);case.agenda=agenda(case)
 before=case.hs.db.serialize(),raw(case);out=call(case)
 assert calls==['proof','task','read','proof','read','proof','task','combined']
 assert len(out.agenda.facts)==5 and out.agenda.omitted==0 and out.task_consulted_sources==tuple(work_id('SYNTHETIC',x) for x in ('task1','task2'))
 metrics=[f for f in out.agenda.facts if f.kind=='metric'];assert [f.status for f in metrics]==['provisional','provisional','unknown']
 assert out.kpi_report.lines[0].status=='known' and out.kpi_report.lines[1].value==0 and out.kpi_report.lines[2].value is None
 assert out.kpi_unobserved_metric_ids==('unknown',) and len(out.kpi_observation_fingerprints)==3 and len(out.kpi_consulted_sources)==4 and out.task_snapshot_digest
 assert out.requires_review and not any((out.live_authorized,out.staff_assessment_authorized,out.dispatch_authorized)) and before==(case.hs.db.serialize(),raw(case))
 case.reopen();assert call(case).kpi_report==out.kpi_report
 assert case.write_value(Decimal('3.50')).state=='local_fake_committed';case.reopen();new=call(case)
 assert new.kpi_report.lines[0].value==Decimal('3.50') and new.kpi_report.workbook_fingerprint!=out.kpi_report.workbook_fingerprint and new.kpi_report.observation_fingerprints!=out.kpi_report.observation_fingerprints
 assert KEY.decode() not in repr(new) and not case.hs.rows('SELECT * FROM outbox')
 with pytest.raises(FrozenInstanceError):new.dispatch_authorized=True


def test_all_consulted_omitted_tasks_and_metrics_proofs_retained(case):
 for i in range(3,7):case.seed('task'+str(i))
 out=call(case,max_items=1)
 assert len(out.agenda.facts)==1 and out.agenda.omitted==7 and out.task_sample.agenda.omitted>=0
 assert len(out.task_consulted_sources)>=5 and len(out.kpi_observation_fingerprints)==3 and out.kpi_unobserved_metric_ids==('unknown',) and len(out.kpi_consulted_sources)==4


def test_provisional_values_conservatively_provisional_and_actual_targetstate_retained(case):
 out=call(case,coverage_complete=False)
 assert [l.status for l in out.kpi_report.lines]==['provisional','provisional','unknown']
 assert all(l.target_state=='unknown' for l in out.kpi_report.lines)
 assert all(f.status!='confirmed' for f in out.agenda.facts if f.kind=='metric')
 assert all('status=' not in f.text and 'target_state=' not in f.text and 'None' not in f.text for f in out.agenda.facts if f.kind=='metric')
 assert any('нет наблюдения' in f.text for f in out.agenda.facts)


def test_public_kpi_metadata_proof_zero_sql_snapshots_and_disabled_preflight(case,monkeypatch):
 before=raw(case);counts=case.state.copy()
 def fail(*a,**kw):pytest.fail('metadata proof read SQL/snapshot')
 with monkeypatch.context() as patch:
  patch.setattr(case.s,'execute',fail);patch.setattr(case.s,'rows',fail)
  proof=case.reader.source_proof(now=NOW)
 assert type(proof) is kpi.KpiSourceProof and proof.source is case.source and len(proof.fingerprint)==64 and case.state['driver_calls']==counts['driver_calls'] and raw(case)==before
 disabled=reader(case,enabled=False)
 with monkeypatch.context() as patch:
  patch.setattr(case.s,'execute',fail);sourcecalls=case.state['source_calls']
  with pytest.raises(kpi.TableKpiUnavailable):disabled.source_proof(now=NOW)
  assert case.state['source_calls']==sourcecalls

@pytest.mark.parametrize('change',['sender','multadmins','business','owner','sourcebusiness','mismatchedsource','noIntel','wrongindexstore'])
def test_role_owner_bound_source_authorized_before_kpi_cell_transfer(case,monkeypatch,change):
 kw={}
 if change=='sender':kw['sender']='alen'
 elif change=='multadmins':case.cfg['admins'].append('alen')
 elif change=='business':case.cfg['business']='other'
 elif change=='owner':
  source=replace(case.source,owner_id='not-member');case.source=source;case.state['source']=source;case.reader=reader(case);case.agenda=agenda(case)
 elif change=='sourcebusiness':
  source=replace(case.source,business='other');case.source=source;case.state['source']=source;case.reader=reader(case);case.agenda=agenda(case)
 elif change=='mismatchedsource':case.agenda=agenda(case,expected_kpi_source=replace(case.source,owner_id='frost'))
 elif change=='noIntel':case.e.intelligence=None
 else:case.e.intelligence.index.store=case.s
 def fail(*a):pytest.fail('wrong role/scope read KPI cells')
 monkeypatch.setattr(case.s,'execute',fail);deny(lambda:call(case,**kw))

@pytest.mark.parametrize('kw',[{'max_items':0},{'max_items':6},{'max_items':True},{'period':None},{'definitions':[]},{'definitions':(definition(),)},{'coverage_complete':None},{'coverage_observed_on':NOW},{'as_of':NOW.replace(tzinfo=None)}])
def test_closed_explicit_input_before_reads(case,kw,monkeypatch):
 def fail(*a):pytest.fail('bad input read dependency')
 monkeypatch.setattr(case.s,'execute',fail);monkeypatch.setattr(case.hs,'execute',fail);deny(lambda:call(case,**kw))

@pytest.mark.parametrize('bad',['money','longlabel','longunit','floatTarget','intTarget','boolTarget','duplicate','missingpriority','prioritybool','prioritynegative'])
def test_explicit_nonfinancial_definitions_priorities_no_normalization(case,bad):
 defs=[definition(),definition('zero'),definition('unknown')]
 if bad=='money':defs[0]=definition(unit='money',currency='USD')
 elif bad=='longlabel':defs[0]=definition(label='x'*81)
 elif bad=='longunit':defs[0]=definition(unit='x'*33)
 elif bad in ('floatTarget','intTarget','boolTarget'):
  object.__setattr__(defs[0],'target',{'floatTarget':2.0,'intTarget':2,'boolTarget':True}[bad])
 elif bad=='duplicate':defs[1]=defs[0]
 elif bad=='missingpriority':case.agenda=agenda(case,priorities=(module.MetricPriority('metric',0),))
 elif bad=='prioritybool':
  p=module.MetricPriority('metric',0);object.__setattr__(p,'priority',True);case.agenda=agenda(case,priorities=(p,)+case.priorities[1:])
 else:
  p=module.MetricPriority('metric',0);object.__setattr__(p,'priority',-1);case.agenda=agenda(case,priorities=(p,)+case.priorities[1:])
 deny(lambda:call(case,definitions=tuple(defs)))

@pytest.mark.parametrize('field,value',[('revoked',True),('expires_at',NOW),('valid_from',NOW+timedelta(seconds=1)),('revision',2),('owner_id','frost')])
def test_current_source_drift_refuses(case,field,value):case.state['source']=replace(case.source,**{field:value});deny(lambda:call(case))

@pytest.mark.parametrize('field',['next_step','criterion','revision','title_tail','acl','deleted','status'])
def test_final_task_pass_after_kpi_callbacks_detects_hidden_drift(case,monkeypatch,field):
 case.hs.execute("UPDATE tasks SET title=? WHERE id='task1'",('S'*100,));actual=kpi.TableKpiReader.read_daily;count=[]
 def read(self,**kw):
  result=actual(self,**kw);count.append(1)
  if len(count)==2:
   if field=='acl':case.hs.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('SYNTHETIC','task1','[]'))
   elif field=='deleted':case.hs.execute("DELETE FROM tasks WHERE id='task1'")
   elif field=='revision':case.hs.execute("UPDATE tasks SET revision=revision+1 WHERE id='task1'")
   else:
    col='title' if field=='title_tail' else field;value='S'*80+'CHANGED' if field=='title_tail' else 'completed' if field=='status' else 'Changed hidden value';case.hs.execute('UPDATE tasks SET '+col+'=? WHERE id=?',(value,'task1'))
  return result
 monkeypatch.setattr(kpi.TableKpiReader,'read_daily',read);case.agenda=agenda(case);deny(lambda:call(case));assert len(count)==2


def test_final_task_pass_after_last_source_callback_catches_acl_loss(case):
 count=[]
 def source():
  count.append(1)
  # source proof calls first, after read1, after read2; each read makes4 source calls.
  if len(count)==11:case.hs.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('SYNTHETIC','task1','[]'))
  return case.state['source']
 case.reader=reader(case,current_source=source);case.agenda=agenda(case);deny(lambda:call(case));assert len(count)>=11

@pytest.mark.parametrize('change',['scale','signedzero','unmapped','revision','source','methodread','methodproof','config','configidentity','intelligence','index','connection','period','definition'])
def test_fulltyped_kpi_source_input_host_method_races(case,monkeypatch,change):
 actual=kpi.TableKpiReader.read_daily;calls=[];period=reports.Period(PERIOD.start,PERIOD.end);defs=(definition(),definition('zero'),definition('unknown'));old=case.hs.db
 def read(self,**kw):
  result=actual(self,**kw);calls.append(1)
  if len(calls)==1:
   if change=='scale':alter(case,value=Decimal('2.0'))
   elif change=='signedzero':
    row0=case.book.sheets[0].rows[1];changed=replace(row0,cells=tuple(core.Cell(c.column,Decimal('-0.00') if c.column=='value' else c.value) for c in row0.cells));case.persist(replace(case.book,sheets=(replace(case.book.sheets[0],rows=(case.book.sheets[0].rows[0],changed)+case.book.sheets[0].rows[2:]),)))
   elif change=='unmapped':alter(case,unmapped='changed')
   elif change=='revision':case.persist(replace(case.book,revision='new'))
   elif change=='source':case.state['source']=replace(case.source,revoked=True)
   elif change=='methodread':case.reader.read_daily=MethodType(lambda s,**kw:result,case.reader)
   elif change=='methodproof':case.reader.source_proof=MethodType(lambda s,**kw:kpi.KpiSourceProof(case.source,result.source_fingerprint),case.reader)
   elif change=='config':case.cfg['daily_summary']=True
   elif change=='configidentity':case.e.c=dict(case.cfg)
   elif change=='intelligence':case.e.intelligence=object()
   elif change=='index':case.e.intelligence.index=object()
   elif change=='connection':case.hs.db=case.s.db
   elif change=='period':object.__setattr__(period,'end',PERIOD.end+timedelta(days=1))
   elif change=='definition':object.__setattr__(defs[0],'target',Decimal('2.0'))
  return result
 monkeypatch.setattr(kpi.TableKpiReader,'read_daily',read);case.agenda=agenda(case)
 try:deny(lambda:call(case,period=period,definitions=defs))
 finally:case.hs.db=old

@pytest.mark.parametrize('bad',['prooftype','proofsource','proofdigest','kpitype','kpifingerprint','kpistaff','kpilines','kpirefs','kpiunobserved','tasktype','taskdigest','agendatype','agendaforeign','agendaomitted'])
def test_malformed_detached_public_adapter_outputs(case,monkeypatch,bad):
 source=kpi.TableKpiReader.source_proof;read=kpi.TableKpiReader.read_daily;build=module.adapter.build_agenda_preview;combine=reports.agenda_preview
 def proof(self,**kw):
  result=source(self,**kw)
  if bad=='prooftype':return None
  if bad=='proofsource':return replace(result,source=replace(case.source,owner_id='frost'))
  if bad=='proofdigest':return replace(result,fingerprint='bad')
  return result
 def daily(self,**kw):
  result=read(self,**kw)
  if bad=='kpitype':return None
  if bad=='kpifingerprint':return replace(result,source_fingerprint='bad')
  if bad=='kpistaff':object.__setattr__(result,'staff_assessment_authorized',True)
  if bad=='kpilines':return replace(result,lines=())
  if bad=='kpirefs':return replace(result,lines=(replace(result.lines[0],sources=()),)+result.lines[1:])
  if bad=='kpiunobserved':return replace(result,unobserved_metric_ids=('foreign',))
  return result
 def task(*a,**kw):
  result=build(*a,**kw)
  if bad=='tasktype':return None
  if bad=='taskdigest':return replace(result,snapshot_digest='bad')
  return result
 def agenda_core(**kw):
  result=combine(**kw)
  if bad=='agendatype':return None
  if bad=='agendaforeign':return replace(result,facts=(reports.AgendaFact('foreign','unknown','question','unknown',0,('source',)),))
  if bad=='agendaomitted':return replace(result,omitted=9)
  return result
 monkeypatch.setattr(kpi.TableKpiReader,'source_proof',proof);monkeypatch.setattr(kpi.TableKpiReader,'read_daily',daily);monkeypatch.setattr(module.adapter,'build_agenda_preview',task);monkeypatch.setattr(reports,'agenda_preview',agenda_core);case.agenda=agenda(case);deny(lambda:call(case))


def test_disabled_consumer_no_dependency_reads_and_metadata_source_disabled(case,monkeypatch):
 before=case.hs.db.serialize(),raw(case)
 def fail(*a,**kw):pytest.fail('disabledread')
 with monkeypatch.context() as patch:
  patch.setattr(case.s,'execute',fail);patch.setattr(case.hs,'execute',fail);case.agenda=agenda(case,enabled=False);deny(lambda:call(case))
 assert before==(case.hs.db.serialize(),raw(case))
 case.reader=reader(case,enabled=False);case.agenda=agenda(case);deny(lambda:call(case))


def test_authorized_task_acl_current_and_no_body_or_write_queries(case):
 case.e.intelligence.index.upsert(dict(source_id='synthetic',document_id='doc',title='Synthetic',url='https://example.invalid/synthetic',updated_at=None,text='PRIVATE-KB-BODY-NOT-READ',readers=[reader_key('SYNTHETIC','test_operator')],observed_at=NOW.timestamp()-1,permission_expires_at=NOW.timestamp()+3600,confirmed=True))
 ref=next(iter(case.e.intelligence.index.allowed_ids(reader_key('SYNTHETIC','test_operator'),now=NOW.timestamp())));case.hs.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('SYNTHETIC','task1',json.dumps([ref])))
 before=case.hs.db.serialize(),raw(case);counts=[]
 def auth(op,a,b,*rest):
  if op in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):pytest.fail('consumer wroteSQL')
  if op==sqlite3.SQLITE_READ:
   assert not (a=='tasks' and b in ('evidence','source_id')) and not (a in ('sources','knowledge_documents','knowledge_chunks') and b in ('body','text'));counts.append((a,b))
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(auth);case.hs.db.set_authorizer(auth);out=call(case);case.s.db.set_authorizer(None);case.hs.db.set_authorizer(None)
 assert work_id('SYNTHETIC','task1') in out.task_consulted_sources and sum(1 for table,col in counts if table=='runtime' and col=='value')<=8 and before==(case.hs.db.serialize(),raw(case))
 case.e.intelligence.index.revoke_source('synthetic');out=call(case);assert work_id('SYNTHETIC','task1') not in out.task_consulted_sources
