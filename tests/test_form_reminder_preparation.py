"""Actual C07 registration + approved durable form/account sources; no sends."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,date,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import copy,json,sqlite3
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
from goldway.intelligence import reader_key
from goldway import table_sqlite_driver as driver
from goldway import table_change_plan as tables
from goldway import table_form_reconciliation as forms
from goldway import form_reminder_preparation as m
from goldway import task_card_command as cards
from goldway import operational_reports as reports

NOW=datetime(2026,10,19,16,tzinfo=timezone.utc)
PERIOD=reports.Period(date(2026,10,12),date(2026,10,19))
KEY=b'OPENLY-SYNTHETIC-FORM-REMINDER-FIXTURE-KEY'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']


def configuration():
 return dict(business='GOLDWAY',timezone='UTC',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=False,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[]),scheduling=dict(enabled=True))
class NoModel:
 def propose(self,*a,**k):pytest.fail('unexpected model')


def encode(ctx,book):
 return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,tables._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=[]),ensure_ascii=False,sort_keys=True,separators=(',',':'))


def workbook(ctx,side):
 cells=dict(id='main',owner='alen',metric='metric',start=PERIOD.start.isoformat(),end=PERIOD.end.isoformat(),present=True,value=Decimal('5.00') if side==0 else Decimal('3.0'),unit='count',currency=None,observed=PERIOD.end.isoformat(),unmapped=Decimal('1.0'))
 if side==0:cells['submitted']=(NOW-timedelta(hours=2)).isoformat()
 return tables.Workbook(ctx.workbook_id,'book-v1',(tables.Sheet('sheet','sheet-v1',(tables.Row(1,'row-v1',tuple(tables.Cell(k,v) for k,v in cells.items())),)),))


def setup(tmp_path):
 paths=[tmp_path/(x+'.sqlite') for x in ('host','form','account')];stores=[Store(str(p)) for p in paths];clock=[NOW.timestamp()-3*3600]
 e=Engine(stores[0],configuration(),NoModel(),lambda:clock[0]);s=stores[0]
 s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',('GOLDWAY','source','NO SOURCE BODY READ','NO URL READ'))
 s.execute('INSERT INTO tasks(business,id,public_number,title,owner,deadline,next_step,criterion,status,source_id,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('GOLDWAY','task',1,'Explicit task, not classified as a report','alen','2026-10-19','Step','Criterion','confirmed','source','NO EVIDENCE READ',clock[0]))
 s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY','task','source'));s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','task','[]'))
 registered=e.scheduler.register_series('task','test_operator',dict(frequency='weekly',weekdays=[0],time='17:00',timezone='UTC'),'explicit-fixture-registration',clock[0])
 contexts=[driver.TableContext('GOLDWAY','fixture-'+x,'synthetic-review','book-'+x,'synthetic-reviewer','key',KEY) for x in ('form','account')]
 books=[workbook(ctx,i) for i,ctx in enumerate(contexts)]
 for i in range(2):stores[i+1].execute('INSERT INTO runtime VALUES(?,?)',(contexts[i].runtime_key,encode(contexts[i],books[i])))
 sources=forms.SourcePair(*(forms.FormSource('GOLDWAY',contexts[i].workbook_id,'alen',forms.SourceRole.FORM if i==0 else forms.SourceRole.ACCOUNT,1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc)) for i in range(2)))
 holder=dict(sources=sources,source_calls=0,hook=None)
 def current():
  holder['source_calls']+=1
  if holder['hook']:holder['hook'](holder['source_calls'])
  return holder['sources']
 callbacks=[lambda i=i:contexts[i] for i in range(2)]
 def make_reader():
  drivers=[driver.SQLiteTableDriver(store=stores[i+1],current_context=callbacks[i],enabled=True) for i in range(2)]
  selector=forms.RowSelector('sheet',tables.RowIdentity('id','main'))
  return forms.TableFormReconciler(form_driver=drivers[0],account_driver=drivers[1],current_sources=current,expected_sources=sources,bindings=(forms.MetricPair('metric',selector,selector),),form_columns=forms.FormColumns('owner','metric','start','end','present','value','unit','currency','observed','submitted'),account_columns=forms.AccountColumns('owner','metric','start','end','present','value','unit','currency','observed'),enabled=True)
 c=SimpleNamespace(e=e,s=s,stores=stores,paths=paths,clock=clock,contexts=contexts,books=books,sources=sources,holder=holder,current=current,make_reader=make_reader,series=registered['id'],callbacks=callbacks)
 c.reader=make_reader();clock[0]=NOW.timestamp()
 return c


@pytest.fixture
def c(tmp_path,monkeypatch):
 value=setup(tmp_path)
 def fail(*a,**k):pytest.fail('unexpected network/model/tick/send/registration')
 import socket,httpx
 monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 value.e._context=fail;value.e.intelligence.sources.refresh=fail
 for name in ('tick','register_series','schedule_check'):monkeypatch.setattr(value.e.scheduler,name,fail)
 yield value
 for s in value.stores:s.db.set_authorizer(None);s.db.close()


def consumer(c,**kw):
 args=dict(engine=c.e,reconciler=c.reader,expected_sources=c.sources,binding=m.ReportReminderBinding(1,c.series,('metric',)),enabled=True);args.update(kw)
 return m.FormReminderPreparation(**args)

def call(c,component=None,scope=None,**kw):
 args=dict(period=PERIOD,definitions=(reports.MetricDefinition('metric','Synthetic metric','count',None,None,'informational'),),monday=PERIOD.end,coverage_complete=True,coverage_observed_on=PERIOD.end);args.update(kw)
 return (component or consumer(c)).prepare(dict(sender='test_operator',channel='bot',source_id='telegram:1:12') if scope is None else scope,**args)

def raw(c):return tuple(tuple(s.execute('SELECT key,value FROM runtime ORDER BY key').fetchall()) for s in c.stores)
def row_values(c,side,**changes):
 b=c.books[side];r=b.sheets[0].rows[0];new=replace(b,sheets=(replace(b.sheets[0],rows=(replace(r,cells=tuple(tables.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells)),)),))
 c.stores[side+1].execute('UPDATE runtime SET value=? WHERE key=?',(encode(c.contexts[side],new),c.contexts[side].runtime_key));c.books[side]=new

def install_document(c):
 c.e.intelligence.index.upsert(dict(source_id='doc-source',document_id='doc',title='NO TITLE READ',url='https://example.invalid/no',text='NO BODY READ',readers=[reader_key('GOLDWAY',x) for x in ('test_operator','alen')],observed_at=NOW.timestamp()-100,permission_expires_at=NOW.timestamp()+100,confirmed=True))
 ref=c.s.execute('SELECT id FROM knowledge_chunks').fetchone()[0]
 c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps([ref]),));return ref

def readonly(c):
 reads=[]
 def guard(action,table,column,*unused):
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
  if action==sqlite3.SQLITE_READ:
   reads.append((table,column))
   if (table=='tasks' and column=='evidence') or (table=='sources' and column in ('body','link')) or (table in ('knowledge_documents','knowledge_chunks') and column in ('title','text','url')):return sqlite3.SQLITE_DENY
  return sqlite3.SQLITE_OK
 for s in c.stores:s.db.set_authorizer(guard)
 return reads


def block_cells(c,monkeypatch):
 def fail(*a,**k):pytest.fail('workbook SQL reached before current gate')
 for store in c.stores[1:]:monkeypatch.setattr(store,'execute',fail)


def wrap_read(c,callback):
 actual=c.reader.read_reconciliation;calls=[]
 def read(self,**kw):
  value=actual(**kw);calls.append(1)
  if len(calls)==2:callback()
  return value
 c.reader.read_reconciliation=MethodType(read,c.reader)


def test_actual_registered_slot_sources_both_core_passes_no_writes(c,monkeypatch):
 calls=[];actual=reports.reconcile_forms;next_slot=m.reminder_times.next_occurrence
 def reconcile(**kw):calls.append('core');return actual(**kw)
 def slot(*a,**kw):calls.append('slot');return next_slot(*a,**kw)
 monkeypatch.setattr(reports,'reconcile_forms',reconcile);monkeypatch.setattr(m.reminder_times,'next_occurrence',slot)
 component=consumer(c);before=raw(c);series=c.s.rows('SELECT * FROM c07_series');changes=[s.db.total_changes for s in c.stores];reads=readonly(c)
 result=call(c,component)
 assert calls==['slot','core','core'] and len(result.questions)==1
 assert result.questions==(result.report.lines[0].question,) and result.report.lines[0].difference==Decimal('2.00')
 assert result.stored_slot.hour==17 and result.state=='questions_prepared'
 assert result.requires_review and not result.delivery_authorized and not result.live_authorized and not result.collection_authorized
 assert raw(c)==before and c.s.rows('SELECT * FROM c07_series')==series and changes==[s.db.total_changes for s in c.stores]
 assert not c.s.rows('SELECT * FROM outbox') and ('tasks','title') in reads
 assert 'согласие сотрудника' in result.text and c.series not in result.text
 with pytest.raises(FrozenInstanceError):result.questions=()


def test_actual_approved_account_correction_file_reopen_suppresses_question(c):
 first=call(c)
 writer=driver.SQLiteTableDriver(store=c.stores[2],current_context=c.callbacks[1],enabled=True,allow_fake_writes=True)
 plan=writer.build_plan('sheet',tables.RowIdentity('id','main'),(tables.Cell('value',Decimal('5.00')),))
 token=tables.issue_local_approval(plan,decision='approve',approver=c.contexts[1].required_approver,nonce='synthetic-correction',issued_at=900,expires_at=1100,signing_key=KEY)
 writer.apply(plan,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 oldseries=c.s.rows('SELECT * FROM c07_series')
 for s in c.stores:s.db.close()
 c.stores[:]=[Store(str(path)) for path in c.paths];c.s=c.stores[0];c.e=Engine(c.s,configuration(),NoModel(),lambda:c.clock[0]);c.reader=c.make_reader()
 updated=call(c)
 assert first.questions and updated.questions==() and updated.state=='no_current_question'
 assert updated.report.lines[0].comparison=='match' and updated.report.lines[0].difference==0
 assert 'не подтверждает завершение' in updated.text and updated.stored_slot==first.stored_slot and c.s.rows('SELECT * FROM c07_series')==oldseries


@pytest.mark.parametrize('kind',['missing','provisional_missing','unknown_time','pending','matched','unknown_value'])
def test_actual_form_state_distinctions_not_new_classifier(c,kind):
 args={}
 if kind in ('missing','provisional_missing','pending'):row_values(c,0,present=False,value=None,observed=None,submitted=None)
 if kind=='provisional_missing':args['coverage_complete']=False
 if kind=='unknown_time':row_values(c,0,submitted=None)
 if kind=='unknown_value':row_values(c,0,value=None)
 if kind=='pending':c.clock[0]=NOW.timestamp()-2*3600
 if kind=='matched':row_values(c,1,value=Decimal('5.00'))
 value=call(c,**args);line=value.report.lines[0]
 if kind=='provisional_missing':assert line.comparison=='provisional' and line.submission_state=='missing_after_cutoff' and value.questions
 elif kind=='unknown_time':assert line.submission_state=='unknown_time' and line.comparison=='unknown' and value.questions
 elif kind in ('pending','matched'):assert not value.questions
 elif kind=='missing':assert line.submission_state=='missing_after_cutoff' and line.reported is None and value.questions
 else:assert line.comparison=='unknown' and value.questions


@pytest.mark.parametrize('kind',['disabled','employee','wrong_uid','group','no_intel','no_scheduler','config','owner','business','bound_reader_business'])
def test_pre_cell_business_owner_host_gate(c,kind,monkeypatch):
 args={};scope=dict(sender='test_operator',channel='bot',source_id='telegram:1:12')
 if kind=='disabled':args['enabled']=False
 elif kind=='employee':scope.update(sender='alen',source_id='telegram:3:12')
 elif kind=='wrong_uid':scope['source_id']='telegram:333:12'
 elif kind=='group':scope['source_id']='telegram:-100:12'
 elif kind=='no_intel':c.e.intelligence=None
 elif kind=='no_scheduler':c.e.scheduler=None
 elif kind=='config':c.e.c['admins'].append('alen')
 elif kind in ('owner','business','bound_reader_business'):
  changes={'owner_id':'frost'} if kind=='owner' else {'business':'OTHER'}
  changed=forms.SourcePair(replace(c.sources.form,**changes),replace(c.sources.account,**changes))
  if kind=='bound_reader_business':
   c.holder['sources']=changed
   original=c.reader.source_proof
   def proof(self,**kw):return forms.FormSourceProof(changed,'0'*64)
   c.reader.source_proof=MethodType(proof,c.reader)
  else:args['expected_sources']=changed
 block_cells(c,monkeypatch)
 component=consumer(c,**args)
 with pytest.raises(m.FormReminderUnavailable):call(c,component,scope=scope)


@pytest.mark.parametrize('kind',['paused','missing','duplicate','actor','owner','rule_bad','rule_extra','rule_timezone','rule_slot','snapshot_bad','snapshot_big','snapshot_key','snapshot_source','next_past','future_source'])
def test_existing_exact_registered_series_required_before_cells(c,kind,monkeypatch):
 if kind=='missing':c.s.execute('DELETE FROM c07_series')
 elif kind=='duplicate':c.s.execute("INSERT INTO c07_series SELECT business,'another',task_id,actor,owner,rule,snapshot,next_at,source_at,created,state,reason FROM c07_series")
 else:
  column,value={'paused':('state','paused'),'actor':('actor','frost'),'owner':('owner','frost'),'rule_bad':('rule','{'),'rule_extra':('rule',json.dumps(dict(frequency='weekly',weekdays=[0],time='17:00',timezone='UTC',extra=True))),'rule_timezone':('rule',json.dumps(dict(frequency='weekly',weekdays=[0],time='17:00',timezone='Europe/Moscow'))),'rule_slot':('rule',json.dumps(dict(frequency='weekly',weekdays=[0],time='18:00',timezone='UTC'))),'snapshot_bad':('snapshot','{'),'snapshot_big':('snapshot','x'*32769),'snapshot_key':('snapshot','{}'),'snapshot_source':('snapshot',json.dumps(dict(title='Explicit task, not classified as a report',owner='alen',deadline='2026-10-19',next_step='Step',criterion='Criterion',source_id='source',evidence=None,source_ids=['missing']))),'next_past':('next_at',NOW.timestamp()-1),'future_source':('source_at',NOW.timestamp()+1)}[kind]
  c.s.execute('UPDATE c07_series SET '+column+'=?',(value,))
 block_cells(c,monkeypatch)
 with pytest.raises(m.FormReminderUnavailable):call(c)


@pytest.mark.parametrize('kind',['task_revision','task_criterion','task_owner','source','doc_owner','doc_admin','series_pause','series_rule','series_slot','policy','config_identity','intel_identity','input'])
def test_late_second_reconciliation_callback_current_proof_denies(c,kind):
 if kind.startswith('doc'):install_document(c)
 definitions=[reports.MetricDefinition('metric','Synthetic metric','count',None,None,'informational')]
 def mutate():
  if kind=='task_revision':c.s.execute('UPDATE tasks SET revision=revision+1')
  elif kind=='task_criterion':c.s.execute("UPDATE tasks SET criterion='Changed'")
  elif kind=='task_owner':c.s.execute("UPDATE tasks SET owner='frost'")
  elif kind=='source':c.s.execute('UPDATE sources SET deleted=1')
  elif kind in ('doc_owner','doc_admin'):c.s.execute('DELETE FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY','alen' if kind=='doc_owner' else 'test_operator'),))
  elif kind=='series_pause':c.s.execute("UPDATE c07_series SET state='paused'")
  elif kind=='series_rule':c.s.execute('UPDATE c07_series SET rule=?',(json.dumps(dict(frequency='weekly',weekdays=[0],time='18:00',timezone='UTC')),))
  elif kind=='series_slot':c.s.execute('UPDATE c07_series SET next_at=next_at+604800')
  elif kind=='policy':c.e.c['allowed_live_recipients'].remove('frost')
  elif kind=='config_identity':c.e.c=copy.deepcopy(c.e.c)
  elif kind=='intel_identity':c.e.intelligence=copy.copy(c.e.intelligence)
  else:object.__setattr__(definitions[0],'label','Changed')
 wrap_read(c,mutate);component=consumer(c)
 with pytest.raises(m.FormReminderUnavailable):call(c,component,definitions=tuple(definitions))


@pytest.mark.parametrize('who',['alen','test_operator'])
def test_both_current_actor_and_owner_acl_before_cells(c,who,monkeypatch):
 install_document(c);c.s.execute('DELETE FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY',who),))
 block_cells(c,monkeypatch)
 with pytest.raises(m.FormReminderUnavailable):call(c)


def test_public_source_proof_is_metadata_only_and_disabled_no_callbacks(c,monkeypatch):
 for store in c.stores:monkeypatch.setattr(store,'execute',lambda *a,**k:pytest.fail('metadata proof performed SQL'))
 proof=c.reader.source_proof(now=NOW)
 assert type(proof) is forms.FormSourceProof and proof.sources==c.sources and len(proof.fingerprint)==64
 c.reader._enabled=False;before=c.holder['source_calls']
 with pytest.raises(forms.FormReconciliationUnavailable):c.reader.source_proof(now=NOW)
 assert c.holder['source_calls']==before


def test_four_public_task_builds_eight_snapshots_shared_owner_metadata_cap(c,monkeypatch):
 readers=[];snapshots=[];actual_reader=m._Reader;actual_snapshot=cards._snapshot
 class Reader(actual_reader):
  def __init__(self,*a):super().__init__(*a);readers.append(self)
 def snapshot(*a,**k):snapshots.append(a[0]);return actual_snapshot(*a,**k)
 monkeypatch.setattr(m,'_Reader',Reader);monkeypatch.setattr(cards,'_Reader',Reader);monkeypatch.setattr(cards,'_snapshot',snapshot)
 call(c)
 assert len(readers)==3 and len(snapshots)==8
 assert snapshots[2] is snapshots[3] is snapshots[6] is snapshots[7] is readers[0]
 assert sum(r.bytes for r in readers)<=12*1024*1024
 import goldway.monthly_task_history as history
 monkeypatch.setattr(history,'MAX_BYTES',1)
 with pytest.raises(m.FormReminderUnavailable):call(c)


@pytest.mark.parametrize('kw',[
 {'definitions':[]},{'definitions':()},{'coverage_complete':1},{'monday':date(2026,10,20)},
 {'definitions':(reports.MetricDefinition('other','Other','count',None,None,'informational'),)},
 {'coverage_observed_on':date(2026,10,20)},
 {'definitions':(reports.MetricDefinition('metric','Metric','count',None,Decimal(1),'at_least'),)},
])
def test_closed_explicit_typed_inputs_no_defaults(c,kw):
 with pytest.raises(m.FormReminderUnavailable):call(c,**kw)


@pytest.mark.parametrize('who',['alen','test_operator'])
def test_last_host_clock_callback_revokes_either_actor_before_final_task_phase(c,who):
 install_document(c);actual=c.e.now;calls=[]
 def clock():
  calls.append(1)
  if len(calls)==3:c.s.execute('DELETE FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY',who),))
  return actual()
 c.e.now=clock
 with pytest.raises(m.FormReminderUnavailable):call(c)
 assert len(calls)==3


@pytest.mark.parametrize('method',['source_proof','read_reconciliation'])
def test_captured_public_method_replacement_during_reconciliation_denied(c,method):
 def change():setattr(c.reader,method,lambda *a,**k:None)
 wrap_read(c,change);component=consumer(c)
 with pytest.raises(m.FormReminderUnavailable):call(c,component)


def test_source_callback_before_last_table_pass_loses_owner_acl(c):
 install_document(c);finished=[];actual=c.reader.read_reconciliation
 def read(self,**kw):
  value=actual(**kw);finished.append(1);return value
 c.reader.read_reconciliation=MethodType(read,c.reader)
 def hook(_):
  if len(finished)==1:c.s.execute('DELETE FROM knowledge_readers WHERE reader=?',(reader_key('GOLDWAY','alen'),))
 c.holder['hook']=hook
 with pytest.raises(m.FormReminderUnavailable):call(c)


def test_actual_report_scale_drift_same_numeric_value_is_not_equal(c):
 original=c.reader.read_reconciliation;calls=[]
 def read(self,**kw):
  value=original(**kw);calls.append(1)
  if len(calls)==2:return replace(value,lines=(replace(value.lines[0],reported=Decimal('5.0')),))
  return value
 c.reader.read_reconciliation=MethodType(read,c.reader)
 with pytest.raises(m.FormReminderUnavailable):call(c)


def test_three_metric_questions_and_all_actual_sources(c):
 bindings=[];definitions=[]
 for side in range(2):
  book=c.books[side];base=book.sheets[0].rows[0]
  rows=[]
  for i,metric in enumerate(('a','b','c')):
   row=replace(base,position=i+1,cells=tuple(tables.Cell(cell.column,metric if cell.column in ('id','metric') else cell.value) for cell in base.cells))
   rows.append(row)
  changed=replace(book,sheets=(replace(book.sheets[0],rows=tuple(rows)),))
  c.stores[side+1].execute('UPDATE runtime SET value=? WHERE key=?',(encode(c.contexts[side],changed),c.contexts[side].runtime_key))
 for metric in ('a','b','c'):
  select=forms.RowSelector('sheet',tables.RowIdentity('id',metric));bindings.append(forms.MetricPair(metric,select,select));definitions.append(reports.MetricDefinition(metric,'Metric '+metric,'count',None,None,'informational'))
 # Reinitialize the existing actual reader explicitly with the new fixture mapping.
 old=c.reader
 c.reader=forms.TableFormReconciler(form_driver=old._drivers[0],account_driver=old._drivers[1],current_sources=c.current,expected_sources=c.sources,bindings=tuple(bindings),form_columns=old._columns[0],account_columns=old._columns[1],enabled=True)
 result=call(c,consumer(c,binding=m.ReportReminderBinding(1,c.series,('a','b','c'))),definitions=tuple(definitions))
 assert len(result.questions)==3 and result.questions==tuple(line.question for line in result.report.lines)
 assert set(source for line in result.report.lines for source in line.sources)<=set(result.evidence_ids)


def test_existing_metadata_api_old_read_behavior_remains_compatible(c):
 proof=c.reader.source_proof(now=NOW)
 actual=c.reader.read_reconciliation(period=PERIOD,definitions=(reports.MetricDefinition('metric','Synthetic metric','count',None,None,'informational'),),monday=PERIOD.end,coverage_complete=True,coverage_observed_on=PERIOD.end,now=NOW)
 result=call(c)
 assert result.report==actual and result.source_proof==proof


def test_default_disabled_component_no_clock_or_sql(c,monkeypatch):
 def fail(*a,**k):pytest.fail('disabled component touched dependency')
 c.e.now=fail
 for s in c.stores:monkeypatch.setattr(s,'execute',fail)
 component=consumer(c,enabled=False)
 with pytest.raises(m.FormReminderUnavailable):call(c,component)


def test_new_current_source_link_not_in_registered_snapshot_denied_before_cells(c,monkeypatch):
 c.s.execute('INSERT INTO sources VALUES(?,?,?,?,0)',('GOLDWAY','extra-source','NO BODY','NO LINK'))
 c.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY','task','extra-source'))
 block_cells(c,monkeypatch)
 with pytest.raises(m.FormReminderUnavailable):call(c)


@pytest.mark.parametrize('kind',['duplicate','empty','extra','missing','duplicate_key'])
def test_registered_source_array_exact_closed_identity(c,kind,monkeypatch):
 original=c.s.execute('SELECT snapshot FROM c07_series').fetchone()[0];value=json.loads(original)
 if kind=='duplicate':value['source_ids']=['source','source']
 elif kind=='empty':value['source_ids']=[]
 elif kind=='extra':value['source_ids']=['source','missing']
 elif kind=='missing':value['source_ids']=[]
 else:
  # Duplicate known key also removes evidence: key count alone must not pass.
  original=json.dumps({k:v for k,v in value.items() if k!='evidence'})[:-1]+',"title":"Explicit task, not classified as a report"}' 
 c.s.execute('UPDATE c07_series SET snapshot=?',(original if kind=='duplicate_key' else json.dumps(value),))
 block_cells(c,monkeypatch)
 with pytest.raises(m.FormReminderUnavailable):call(c)


def test_clock_elapsed_forward_allowed_but_saved_slot_stale_not_advanced(c):
 actual=c.e.now;calls=[]
 def elapsed():calls.append(1);return actual()+len(calls)/10
 c.e.now=elapsed
 result=call(c);assert result.stored_slot.hour==17 and len(calls)==3
 c.clock[0]=NOW.timestamp()+3601
 before=c.s.rows('SELECT * FROM c07_series')
 with pytest.raises(m.FormReminderUnavailable):call(c)
 assert c.s.rows('SELECT * FROM c07_series')==before


@pytest.mark.parametrize('kind',['expiry','revoke'])
def test_late_current_source_callback_permission_loss_denied(c,kind):
 actual=c.reader.source_proof;calls=[]
 def proof(self,**kw):calls.append(1);return actual(**kw)
 c.reader.source_proof=MethodType(proof,c.reader)
 def hook(_):
  if len(calls)==2:
   changed=replace(c.sources.form,expires_at=NOW) if kind=='expiry' else replace(c.sources.form,revoked=True)
   c.holder['sources']=replace(c.sources,form=changed)
 c.holder['hook']=hook
 with pytest.raises(m.FormReminderUnavailable):call(c)


def test_detached_report_oversize_question_is_whole_refusal(c):
 actual=c.reader.read_reconciliation
 def read(self,**kw):
  value=actual(**kw);return replace(value,lines=(replace(value.lines[0],question='x'*501),))
 c.reader.read_reconciliation=MethodType(read,c.reader)
 with pytest.raises(m.FormReminderUnavailable):call(c)


def test_final_current_source_callback_cell_correction_is_seen_by_last_data_pass(c):
 actual=c.reader.source_proof;proof_calls=[];corrected=[]
 def proof(self,**kw):proof_calls.append(1);return actual(**kw)
 c.reader.source_proof=MethodType(proof,c.reader)
 def callback(_):
  if len(proof_calls)==2 and not corrected:
   corrected.append(1);row_values(c,1,value=Decimal('5.00'))
 c.holder['hook']=callback
 with pytest.raises(m.FormReminderUnavailable):call(c)
 assert corrected==[1] and len(proof_calls)==2
 assert c.holder['sources']==c.sources


def test_final_external_clock_cell_correction_is_seen_by_last_data_pass(c):
 actual=c.e.now;calls=[];corrected=[]
 def clock():
  calls.append(1)
  if len(calls)==3:
   corrected.append(1);row_values(c,1,value=Decimal('5.00'))
  return actual()
 c.e.now=clock
 with pytest.raises(m.FormReminderUnavailable):call(c)
 assert corrected==[1] and len(calls)==3
 assert c.holder['sources']==c.sources


def test_final_clock_backward_before_last_data_pass_denied(c):
 actual=c.e.now;calls=[]
 def clock():calls.append(1);return actual()-1 if len(calls)==3 else actual()
 c.e.now=clock
 with pytest.raises(m.FormReminderUnavailable):call(c)


def test_final_clock_permission_expiry_uses_fresh_sample_before_last_read(c):
 install_document(c);actual=c.e.now;calls=[]
 def clock():
  calls.append(1)
  return actual()+101 if len(calls)>=3 else actual()
 c.e.now=clock
 with pytest.raises(m.FormReminderUnavailable):call(c)
 assert len(calls)==3
