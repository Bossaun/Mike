"""NEW synthetic I66 -> actual payment evidence/state; no live accounting."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
import copy,json,sqlite3
import pytest
from goldway import table_payment_review as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;rules=module.rules
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)
KEY=b'OPENLY-SYNTHETIC-ACCOUNTING-TEST-KEY-00001'
STORE_INIT=Store.__init__;DRIVER_INIT=driver_module.SQLiteTableDriver.__init__
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan
VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
PAYLOAD=dict(sender='test_operator',channel='bot',source_id='telegram:1:10')

class NoModel:
 def propose(self,*a,**k):pytest.fail('real model forbidden')

def config():
 return dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[]))

def columns():return module.PaymentColumns('evidence','invoice','customer','channel','thread','conversation_source','observed','status','payment','currency')
def row(**changes):
 values=dict(evidence='synthetic-record',invoice='invoice-A',customer='customer-A',channel='synthetic-chat',thread='thread-A',conversation_source='conversation-account',observed=(NOW-timedelta(hours=1)).isoformat(),status='unconfirmed',payment=None,currency='USD');values.update(changes)
 return core.Row(1,'row-v1',tuple(core.Cell(k,v) for k,v in values.items()))
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))

def reader(c,**kw):
 args=dict(engine=c.e,driver=c.driver,current_source=c.current,expected_source=c.source,binding=c.binding,columns=c.columns,enabled=True);args.update(kw);return module.TablePaymentReview(**args)
def read(c,**kw):
 args=dict(window=c.window,now=NOW);args.update(kw);return c.reader.read_payment(PAYLOAD,**args)
def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]
def deny(fn):
 with pytest.raises(module.PaymentReviewUnavailable,match='^table_payment_review_unavailable$'):fn()

@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic-accounting.sqlite';s=Store(str(path));engine_store=Store(':memory:');cfg=config();clock=[NOW.timestamp()];e=Engine(engine_store,cfg,NoModel(),lambda:clock[0])
 ctx=driver_module.TableContext('GOLDWAY','accounting-fixture','synthetic-approval','one-invoice','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('accounting','sheet-v1',(row(),)),))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 conversation=rules.Conversation('customer-A','synthetic-chat','thread-A','conversation-account')
 source=module.AccountingSource('GOLDWAY',ctx.workbook_id,'accounting-observation','synthetic-ledger',module.SourceRole.ACCOUNTING_PAYMENT_RECORD,1,NOW-timedelta(days=1),NOW+timedelta(days=1),False,'test_operator',True)
 state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
 def current():state['source_calls']+=1;return state['source']
 def context():state['driver_calls']+=1;return state['context']
 driver=driver_module.SQLiteTableDriver(store=s,current_context=context,enabled=True)
 binding=module.InvoiceBinding(conversation,'invoice-A','synthetic-ledger','accounting',core.RowIdentity('invoice','invoice-A'))
 c=SimpleNamespace(s=s,engine_store=engine_store,path=path,e=e,cfg=cfg,clock=clock,ctx=ctx,book=book,driver=driver,current=current,context=context,state=state,source=source,binding=binding,columns=columns(),window=rules.Window(NOW-timedelta(days=1),NOW+timedelta(days=1)));c.reader=reader(c)
 def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(ctx,book,nonces),ctx.runtime_key))
 def reopen():
  old=c.s;c.s=object.__new__(Store);STORE_INIT(c.s,str(path));old.db.close();c.driver=object.__new__(driver_module.SQLiteTableDriver);DRIVER_INIT(c.driver,store=c.s,current_context=context,enabled=True);c.reader=reader(c)
 def approved_status(status):
  with monkeypatch.context() as fixture:
   for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),('apply_to_fake_workbook',CORE_APPLY),('check_plan_baseline',CHECK)):fixture.setattr(core,name,actual)
   writer=object.__new__(driver_module.SQLiteTableDriver);DRIVER_INIT(writer,store=c.s,current_context=context,enabled=True,allow_fake_writes=True)
   plan=BUILD(writer,'accounting',binding.identity,(core.Cell('status',status),));token=ISSUE(plan,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
   return APPLY(writer,plan,token,now=1000,next_workbook_revision='book-v2',next_sheet_revision='sheet-v2',next_row_revision='row-v2')
 c.persist=persist;c.reopen=reopen;c.approved_status=approved_status
 def forbidden(*a,**k):pytest.fail('consumer used constructor/approval/write/provider/model/send path')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher,driver_module.SQLiteTableDriver):monkeypatch.setattr(cls,'__init__',forbidden)
 for name in ('apply','build_plan'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,forbidden)
 for name in ('issue_local_approval','build_plan','validate_approval','check_plan_baseline','apply_to_fake_workbook'):monkeypatch.setattr(core,name,forbidden)
 monkeypatch.setattr(Store,'queue',forbidden)
 import socket,httpx,subprocess
 monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
 yield c
 c.s.db.set_authorizer(None)
 if c.s.db.in_transaction:c.s.db.rollback()
 c.s.db.close();engine_store.db.close()


def selected(c,rows,sheets=None):
 book=replace(c.book,sheets=sheets or (replace(c.book.sheets[0],rows=tuple(rows)),));c.persist(book);return book
def alter(c,**changes):return selected(c,(row(**changes),))


def test_actual_core_once_public_snapshot_twice_exact_lookup_current_private(case,monkeypatch):
 c=case;states=[];snapshots=[];lookups=[];actual=rules.payment_state;snapshot=driver_module.SQLiteTableDriver.snapshot;lookup=core.lookup_row
 def assess(evidence,**kw):states.append((evidence,kw));return actual(evidence,**kw)
 def take(self):snapshots.append(self);return snapshot(self)
 def find(*a,**kw):lookups.append(kw);return lookup(*a,**kw)
 monkeypatch.setattr(rules,'payment_state',assess);monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',take);monkeypatch.setattr(core,'lookup_row',find);c.reader=reader(c)
 before=raw(c);changes=c.s.db.total_changes;engine_changes=c.engine_store.db.total_changes;out=read(c)
 assert len(states)==1 and len(snapshots)==2 and len(lookups)==1
 assert type(states[0][0]) is rules.PaymentEvidence and states[0][0] is out.evidence and states[0][1]==dict(now=NOW)
 assert out.state=='unconfirmed_recorded' and out.date_missing and out.currency=='USD'
 assert '«не подтверждено»' in out.text and 'Пустая дата сама по себе не означает неоплату' in out.text
 assert out.requires_review and not any((out.live_authorized,out.payment_confirmation_authorized,out.debt_assessment_authorized,out.customer_contact_authorized,out.source_independently_verified,out.currency_reconciled))
 assert out.scope=='selected_invoice_record_only' and raw(c)==before and c.s.db.total_changes==changes and c.engine_store.db.total_changes==engine_changes
 assert not any(v in out.text for v in ('customer-A','invoice-A',KEY.decode(),c.ctx.workbook_id,'paid_recorded','unconfirmed_recorded'))
 assert c.state['source_calls']==2
 with pytest.raises(FrozenInstanceError):out.state='paid_recorded'

@pytest.mark.parametrize('status',[None,'',' ','paid','unconfirmed'])
@pytest.mark.parametrize('payment',[None,'',' \t '])
def test_blank_payment_date_preserves_actual_explicit_status_never_inferred_debt(case,status,payment):
 alter(case,status=status,payment=payment);out=read(case)
 assert out.state==('unknown' if status is None or not status.strip() else status+'_recorded')
 assert out.date_missing and out.evidence.payment_at is None and out.evidence.status==(None if status is None or not status.strip() else status)
 assert not out.debt_assessment_authorized and not out.payment_confirmation_authorized

@pytest.mark.parametrize('status',['paid','unconfirmed',None])
def test_present_date_does_not_invent_status_or_confirm_settlement(case,status):
 alter(case,status=status,payment=(NOW-timedelta(hours=2)).isoformat());out=read(case)
 assert out.state==('unknown' if status is None else status+'_recorded') and not out.date_missing
 assert not out.payment_confirmation_authorized and 'не подтверждает поступление денег' in out.text

@pytest.mark.parametrize('kind',['unreliable','future_observation','payment_after_observation'])
def test_unknown_branches_are_actual_core_not_adapter_classifier(case,monkeypatch,kind):
 c=case
 if kind=='unreliable':c.source=replace(c.source,reliable_accounting=False);c.state['source']=c.source;c.reader=reader(c)
 elif kind=='future_observation':alter(c,status='paid',observed=(NOW+timedelta(seconds=1)).isoformat())
 else:alter(c,status='paid',payment=NOW.isoformat())
 actual=rules.payment_state;seen=[]
 def assess(evidence,**kw):seen.append(evidence);return actual(evidence,**kw)
 monkeypatch.setattr(rules,'payment_state',assess);c.reader=reader(c);out=read(c)
 assert out.state=='unknown' and len(seen)==1
 if kind=='unreliable':assert seen[0].reliable_accounting is False
 if kind=='future_observation':assert seen[0].at>NOW

@pytest.mark.parametrize('currency',[None,'',' ','USD','RUB','XXX'])
def test_currency_is_only_record_provenance_not_amount_or_fx_authority(case,currency):
 alter(case,currency=currency);out=read(case);assert out.currency==(None if currency is None or not currency.strip() else currency)
 assert not out.currency_reconciled and 'сверка сумм не выполнялась' in out.text


def test_actual_approved_existing_cell_correction_file_close_reopen(case):
 alter(case,status=None);before=read(case);assert before.state=='unknown';case.reopen();assert read(case).state=='unknown'
 assert case.approved_status('unconfirmed').state=='local_fake_committed';case.reopen();after=read(case)
 assert after.state=='unconfirmed_recorded' and after.workbook_fingerprint!=before.workbook_fingerprint and after.row_fingerprint!=before.row_fingerprint
 assert json.loads(raw(case))['used_nonces']==['synthetic-nonce']

@pytest.mark.parametrize('kind',['disabled','revoked','expired','future_source','source_business','source_reviewer','source_scope','role','wrong_uid','group','no_intel'])
def test_permission_source_denial_before_any_snapshot(case,monkeypatch,kind):
 c=case;payload=dict(PAYLOAD)
 if kind=='revoked':c.state['source']=replace(c.source,revoked=True)
 elif kind=='expired':c.clock[0]=(c.source.expires_at+timedelta(seconds=1)).timestamp()
 elif kind=='future_source':c.clock[0]=(c.source.valid_from-timedelta(seconds=1)).timestamp()
 elif kind=='source_business':c.source=replace(c.source,business='OTHER');c.state['source']=c.source
 elif kind=='source_reviewer':c.source=replace(c.source,allowed_reviewer='frost');c.state['source']=c.source
 elif kind=='source_scope':c.source=replace(c.source,account_scope='OTHER');c.state['source']=c.source
 elif kind=='role':c.cfg['admins']=['frost']
 elif kind=='wrong_uid':payload['source_id']='telegram:2:10'
 elif kind=='group':payload['source_id']='telegram-group:-1:10'
 elif kind=='no_intel':c.e.intelligence=None
 def forbidden(*a,**kw):pytest.fail('pre-denied source read')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',forbidden);c.reader=reader(c,enabled=kind!='disabled')
 deny(lambda:c.reader.read_payment(payload,window=c.window,now=NOW))

@pytest.mark.parametrize('kind',['customer','channel','thread','conversation_source','invoice','missing','duplicate','extra_row','extra_sheet','extra_column'])
def test_exact_closed_invoice_profile_no_customer_merge_or_subset(case,monkeypatch,kind):
 c=case
 if kind in ('customer','channel','thread','conversation_source','invoice'):alter(c,**{kind:'different'})
 elif kind=='missing':selected(c,())
 elif kind=='duplicate':selected(c,(row(),replace(row(),position=2)))
 elif kind=='extra_row':selected(c,(row(),replace(row(invoice='invoice-B'),position=2)))
 elif kind=='extra_sheet':selected(c,(row(),),sheets=(c.book.sheets[0],core.Sheet('foreign','r',(core.Row(1,'r',(core.Cell('id','x'),)),))))
 else:alter(c,bank_requisite='UNSUPPORTED_SYNTHETIC_FIELD')
 monkeypatch.setattr(rules,'payment_state',lambda *a,**kw:pytest.fail('invalid profile reached payment core'));c.reader=reader(c)
 deny(lambda:read(c))

@pytest.mark.parametrize('changes',[{'observed':None},{'observed':''},{'observed':'2026-10-21'},{'observed':'2026-10-21T11:00:00'},{'payment':'2026-10-21'},{'payment':False},{'status':True},{'status':'debt'},{'status':'paid '},{'currency':'usd'},{'currency':1},{'currency':'USD EUR'},{'evidence':'x'*129}])
def test_malformed_dates_status_currency_identifiers_whole_refusal(case,changes):alter(case,**changes);deny(lambda:read(case))

@pytest.mark.parametrize('kind',['start_inclusive','end_exclusive','outside','long_window','date_instead_of_time','future_asof'])
def test_explicit_half_open_observation_window_and_asof(case,kind):
 c=case
 if kind=='start_inclusive':alter(c,observed=c.window.start.isoformat());assert read(c).state=='unconfirmed_recorded';return
 if kind=='end_exclusive':alter(c,observed=c.window.end.isoformat())
 elif kind=='outside':alter(c,observed=(c.window.start-timedelta(seconds=1)).isoformat())
 if kind=='long_window':deny(lambda:read(c,window=rules.Window(NOW-timedelta(days=367),NOW+timedelta(days=1))));return
 if kind=='date_instead_of_time':deny(lambda:read(c,now=NOW.date()));return
 if kind=='future_asof':deny(lambda:read(c,now=NOW+timedelta(seconds=1)));return
 deny(lambda:read(c))

@pytest.mark.parametrize('where',['source','clock'])
def test_final_external_callback_changes_row_before_last_snapshot_refuses(case,monkeypatch,where):
 c=case;source_calls=[];clock_calls=[]
 def current():
  source_calls.append(1)
  if where=='source' and len(source_calls)==2:alter(c,status='paid')
  return c.source
 def clock():
  clock_calls.append(1)
  if where=='clock' and len(clock_calls)==2:alter(c,status='paid')
  return NOW.timestamp()
 c.e.now=clock;c.reader=reader(c,current_source=current)
 monkeypatch.setattr(rules,'payment_state',lambda *a,**kw:pytest.fail('stale row reached core'));c.reader=reader(c,current_source=current)
 deny(lambda:read(c));assert len(source_calls)==len(clock_calls)==2

@pytest.mark.parametrize('kind',['revision','position','none_to_blank','currency','observation','workbook_revision'])
def test_two_full_tagged_snapshots_detect_equal_semantics_and_metadata_drift(case,monkeypatch,kind):
 c=case;actual=driver_module.SQLiteTableDriver.snapshot;calls=[]
 def snapshot(self):
  book=actual(self);calls.append(book)
  if len(calls)==1:
   if kind=='workbook_revision':book=replace(c.book,revision='book-v2')
   elif kind in ('revision','position'):
    r=replace(row(),**({'revision':'row-v2'} if kind=='revision' else {'position':2}));book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=(r,)),))
   else:book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=(row(**{'none_to_blank':{'payment':''},'currency':{'currency':None},'observation':{'observed':(NOW-timedelta(minutes=30)).isoformat()}}[kind]),)),))
   c.persist(book)
  return calls[-1]
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);c.reader=reader(c);deny(lambda:read(c));assert len(calls)==2

@pytest.mark.parametrize('kind',['config','intel','driver_method','source_callback','core','binding','columns','payload'])
def test_identity_input_method_drift_after_first_snapshot_fails(case,monkeypatch,kind):
 c=case;actual=driver_module.SQLiteTableDriver.snapshot;calls=[];payload=dict(PAYLOAD)
 def snapshot(self):
  value=actual(self);calls.append(1)
  if kind=='config':c.e.c=copy.deepcopy(c.cfg)
  elif kind=='intel':c.e.intelligence=copy.copy(c.e.intelligence)
  elif kind=='driver_method':monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',lambda self:value)
  elif kind=='source_callback':c.reader._callback=lambda:c.source
  elif kind=='core':monkeypatch.setattr(rules,'payment_state',lambda *a,**kw:'paid_recorded')
  elif kind=='binding':object.__setattr__(c.binding,'invoice_id','different')
  elif kind=='columns':object.__setattr__(c.columns,'status','different')
  else:payload['source_id']='telegram:2:10'
  return value
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);c.reader=reader(c)
 deny(lambda:c.reader.read_payment(payload,window=c.window,now=NOW));assert len(calls)==1


def test_public_snapshot_logical_transfer_four_bounded_driver_reads_no_consumer_sql(case,monkeypatch):
 c=case;actual=c.s.execute;reads=[]
 def execute(sql,args=()):
  assert sql.startswith('SELECT CASE WHEN typeof(value)') and 'FROM runtime WHERE key=? LIMIT 2' in sql
  result=actual(sql,args)
  class Cursor:
   def fetchall(self):
    values=result.fetchall();reads.extend(v[0] for v in values);return values
  return Cursor()
 monkeypatch.setattr(c.s,'execute',execute);monkeypatch.setattr(c.engine_store,'execute',lambda *a,**kw:pytest.fail('consumer SQL'))
 assert read(c).state=='unconfirmed_recorded' and len(reads)==4 and sum(len(v.encode()) for v in reads)<=1024*1024


def test_default_disabled_constructor_no_callback_or_snapshot(case,monkeypatch):
 def forbidden(*a,**kw):pytest.fail('disabled effect')
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',forbidden);case.e.now=forbidden
 adapter=reader(case,current_source=forbidden,enabled=False);deny(lambda:adapter.read_payment(PAYLOAD,window=case.window,now=NOW))

@pytest.mark.parametrize('kind',['payload','window'])
def test_final_source_callback_input_withdrawal_denies_before_second_snapshot(case,monkeypatch,kind):
 c=case;payload=dict(PAYLOAD);calls=[];snapshots=[];actual=driver_module.SQLiteTableDriver.snapshot
 def snapshot(self):snapshots.append(1);return actual(self)
 def current():
  calls.append(1)
  if len(calls)==2:
   if kind=='payload':payload['source_id']='telegram:2:10'
   else:object.__setattr__(c.window,'end',c.window.end+timedelta(days=1))
  return c.source
 monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);c.reader=reader(c,current_source=current)
 deny(lambda:c.reader.read_payment(payload,window=c.window,now=NOW));assert len(snapshots)==1 and len(calls)==2
