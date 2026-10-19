"""Actual durable channel heads/children -> coverage-aware publication_gap only."""
from dataclasses import replace,FrozenInstanceError
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace,MethodType
import json,sqlite3
import pytest
from goldway import table_catalog_completeness as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

core=module.tables;m=module.inventory
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)
END=NOW.replace(hour=9);OBSERVED=NOW.replace(hour=10)
KEY=b'OPENLY-SYNTHETIC-CAMPAIGN-TEST-KEY-000001'
ISSUE=core.issue_local_approval;APPLY=driver_module.SQLiteTableDriver.apply;BUILD=driver_module.SQLiteTableDriver.build_plan;VALIDATE=core.validate_approval;CORE_APPLY=core.apply_to_fake_workbook;CORE_BUILD=core.build_plan;CHECK=core.check_plan_baseline;INIT=driver_module.SQLiteTableDriver.__init__


DAY=date(2026,10,21)
def columns():return module.CatalogColumns('record','sku','variant','day','photo','state','amount','currency')
def row(technical,position,sku=None,variant='standard',photo=True,state='known',amount=0,currency='USD',day=None):return core.Row(position,'row-v1',tuple(core.Cell(k,v) for k,v in dict(record=technical,sku=sku or technical,variant=variant,day=day or DAY.isoformat(),photo=photo,state=state,amount=amount,currency=currency).items()))
def initial_rows():return (row('A',1,photo=False,state='missing',amount=None),row('B',2,photo=None,state='unknown',amount=None),row('C',3,amount=Decimal('0.00')))
def encode(ctx,book,nonces=()):return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=ctx.namespace,workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,core._value(c.value)] for c in r.cells]) for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),ensure_ascii=False,sort_keys=True,separators=(',',':'))
def reader(c,**kw):
 d=dict(driver=c.driver,current_source=c.current,expected_source=c.source,sheet_id='observations',columns=c.columns,enabled=True);d.update(kw);return module.TableCatalogReader(**d)


@pytest.fixture
def case(tmp_path,monkeypatch):
 path=tmp_path/'synthetic.sqlite';original=Store.__init__;s=Store(str(path));ctx=driver_module.TableContext('SYNTHETIC','fake-publications','synthetic-approval','observations','synthetic-reviewer','synthetic-key',KEY)
 book=core.Workbook(ctx.workbook_id,'book-v1',(core.Sheet('observations','sheet-v1',initial_rows()),core.Sheet('unselected','other-v1',(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',Decimal('1.0')))),))))
 s.execute('INSERT INTO runtime VALUES(?,?)',(ctx.runtime_key,encode(ctx,book)));s.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
 source=module.CatalogSource('SYNTHETIC','opaque-catalog',ctx.workbook_id,module.SourceRole.CATALOG_SAMPLE,1,'UTC',datetime(2026,1,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc));state=dict(source=source,context=ctx,source_calls=0,driver_calls=0)
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
   p=BUILD(writer,'observations',core.RowIdentity('record','A'),(core.Cell('photo',True),core.Cell('state','known'),core.Cell('amount',value)));token=ISSUE(p,decision='approve',approver=ctx.required_approver,nonce='synthetic-nonce',issued_at=900,expires_at=1100,signing_key=KEY)
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
def call(c,**kw):return c.reader.read_catalog(observed_on=kw.get('observed_on',DAY),currency=kw.get('currency','USD'),now=kw.get('now',NOW))
def deny(fn):
 with pytest.raises(module.CatalogReaderUnavailable,match='^table_catalog_completeness_unavailable$'):fn()
def selected(c,records):
 book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(records)),c.book.sheets[1]));c.persist(book);return book
def alter(c,technical,**changes):
 records=[]
 for r in c.book.sheets[0].rows:
  if next(cell.value for cell in r.cells if cell.column=='record')==technical:r=replace(r,cells=tuple(core.Cell(cell.column,changes.get(cell.column,cell.value)) for cell in r.cells))
  records.append(r)
 return selected(c,records)


def test_actual_catalog_all_lists_all_refs_zero_restart_correction(case,monkeypatch):
 actual=m.completeness;calls=[];lookup=core.lookup_row;found=[]
 def assess(records):calls.append(records);return actual(records)
 def find(*a,**kw):found.append(kw);return lookup(*a,**kw)
 monkeypatch.setattr(m,'completeness',assess);monkeypatch.setattr(core,'lookup_row',find)
 before=raw(case);out=call(case);a,b,c=[m.ItemKey(x,'standard') for x in 'ABC']
 assert len(calls)==1 and len(found)==3 and calls[0][1].photo_present is m.UNKNOWN and calls[0][1].price is m.UNKNOWN
 assert out.completeness==m.Completeness((a,),(a,),(b,),(b,)) and calls[0][2].price.as_tuple()==Decimal('0.00').as_tuple()
 assert len(out.references)==out.selected_count==3 and {r.item for r in out.references}=={a,b,c} and raw(case)==before
 assert out.requires_review and not any((out.live_authorized,out.whole_catalog_verified,out.photo_content_verified,out.price_verified))
 case.reopen();assert call(case).completeness==out.completeness
 assert case.approved_value(Decimal('0.00')).state=='local_fake_committed';case.reopen();new=call(case)
 assert new.completeness.missing_photos==new.completeness.missing_prices==() and new.completeness.unknown_prices==(b,) and new.workbook_fingerprint!=out.workbook_fingerprint
 assert len(new.references)==3 and KEY.decode() not in repr(new) and json.loads(raw(case))['used_nonces']==['synthetic-nonce']
 with pytest.raises(FrozenInstanceError):new.price_verified=True

@pytest.mark.parametrize('kind',['empty','allknown','allunknown','twelve','sameSKUdifferentvariant'])
def test_complete_sample_boundaries_no_wholecatalog_claim(case,kind):
 rows=() if kind=='empty' else tuple(row(str(i),i+1,photo=None,state='unknown',amount=None) for i in range(12)) if kind=='twelve' else tuple(row(str(i),i+1,photo=None if kind=='allunknown' else True,state='unknown' if kind=='allunknown' else 'known',amount=None if kind=='allunknown' else 0,sku='same' if kind=='sameSKUdifferentvariant' else str(i),variant=str(i)) for i in range(3))
 selected(case,rows);out=call(case)
 assert out.selected_count==len(rows) and len(out.references)==len(rows) and not out.whole_catalog_verified
 if kind=='empty':assert out.completeness==m.Completeness((),(),(),())
 if kind in ('allknown','sameSKUdifferentvariant'):assert out.completeness==m.Completeness((),(),(),())
 if kind in ('allunknown','twelve'):assert len(out.completeness.unknown_photos)==len(rows)

@pytest.mark.parametrize('amount',[0,10**18,Decimal('0'),Decimal('-0.00'),Decimal('1.50'),Decimal('1E-18')])
def test_known_prices_include_zero_and_fraction_no_classifier_copy(case,amount):alter(case,'C',amount=amount);out=call(case);assert m.ItemKey('C','standard') not in out.completeness.missing_prices

@pytest.mark.parametrize('changes',[
 {'sku':None},{'sku':''},{'sku':' spaced'},{'sku':'x'*129},{'variant':None},{'variant':''},{'variant':' v '},{'photo':0},{'photo':'False'},{'state':None},{'state':'KNOWN'},{'state':'missing','amount':0},{'state':'unknown','amount':Decimal('0')},{'state':'known','amount':None},{'amount':True},{'amount':'0'},{'amount':'=1+1'},{'amount':-1},{'amount':Decimal('-0.01')},{'amount':10**18+1},{'currency':'usd'},{'currency':'USDD'},{'currency':None},{'currency':'EUR'},{'day':'2026-10-20'},{'day':'2026-10-22'},{'day':'2026-10-21T12:00:00+00:00'},{'day':'2026-1-01'},{'record':'A'}])
def test_exact_known_id_date_photo_price_currency_shape(case,changes):
 if changes.get('amount')==10**18+1:
  data=json.loads(raw(case))
  for cell in data['workbook']['sheets'][0]['rows'][2]['cells']:
   if cell[0]=='amount':cell[1]=['int',str(10**18+1)]
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(data),case.ctx.runtime_key))
 else:alter(case,'C',**changes)
 deny(lambda:call(case))

@pytest.mark.parametrize('change',['missing','duplicatecell','extra','duplicatepair','thirteen','invaliddecimal','float'])
def test_closed_rows_and_caps(case,change):
 rows=list(case.book.sheets[0].rows)
 if change=='missing':rows[0]=replace(rows[0],cells=rows[0].cells[:-1])
 elif change=='extra':rows[0]=replace(rows[0],cells=rows[0].cells+(core.Cell('image_url','not-read'),))
 elif change=='duplicatepair':rows[1]=row('B',2,sku='A')
 elif change=='thirteen':rows=[row(str(i),i+1) for i in range(13)]
 else:
  data=json.loads(raw(case));cells=data['workbook']['sheets'][0]['rows'][2]['cells']
  if change=='duplicatecell':cells.append(['amount',['int',0]])
  else:
   for c in cells:
    if c[0]=='amount':c[1]=['decimal','1E-19'] if change=='invaliddecimal' else ['float',1.5]
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(data),case.ctx.runtime_key));deny(lambda:call(case));return
 selected(case,rows);deny(lambda:call(case))


def test_unknown_missing_all_none_currency_and_timezone_date(case):
 rows=tuple(row(str(i),i+1,photo=None,state='missing' if i==0 else 'unknown',amount=None,currency=None) for i in range(2));selected(case,rows)
 out=call(case,currency=None);assert out.currency is None and len(out.completeness.missing_prices)==1 and len(out.completeness.unknown_prices)==1
 case.persist(case.book);source=replace(case.source,timezone='Pacific/Kiritimati');case.source=source;case.state['source']=source;case.reader=reader(case)
 assert call(case,now=datetime(2026,10,20,12,tzinfo=timezone.utc)).observed_on==DAY
 deny(lambda:call(case,now=datetime(2026,10,20,9,tzinfo=timezone.utc)))

@pytest.mark.parametrize('currency',['usd','US',1,True,' USD','USDX'])
def test_caller_currency_invalid_even_empty_sample(case,currency):selected(case,());deny(lambda:call(case,currency=currency))
@pytest.mark.parametrize('day',[None,NOW,'2026-10-21',date(2026,10,22)])
def test_explicit_date_not_guessed(case,day):deny(lambda:call(case,observed_on=day))

@pytest.mark.parametrize('field,value',[('business','other'),('catalog_scope','other'),('revision',2),('timezone','Europe/Berlin'),('revoked',True),('expires_at',NOW),('valid_from',NOW+timedelta(seconds=1))])
def test_current_source_proof(case,field,value):case.state['source']=replace(case.source,**{field:value});deny(lambda:call(case))

@pytest.mark.parametrize('change',['scale','signedzero','boolint','unselected','revision','order','method','callback','schema'])
def test_full_typed_snapshot_and_host_races(case,monkeypatch,change):
 actual=m.completeness
 def assess(records):
  out=actual(records)
  if change=='method':case.driver.snapshot=MethodType(lambda s:case.book,case.driver)
  elif change=='callback':case.reader._callback=lambda:case.source
  elif change=='schema':object.__setattr__(case.columns,'sku','changed')
  else:
   book=case.book
   if change=='revision':book=replace(book,revision='new')
   elif change=='order':book=replace(book,sheets=(replace(book.sheets[0],rows=tuple(reversed(book.sheets[0].rows))),book.sheets[1]))
   elif change in ('scale','signedzero'):book=alter(case,'C',amount=Decimal('0.0') if change=='scale' else Decimal('-0.00'))
   else:
    v=True if change=='boolint' else Decimal('2.0');other=replace(book.sheets[1],rows=(core.Row(1,'other-row',(core.Cell('id','unrelated'),core.Cell('value',v))),));book=replace(book,sheets=(book.sheets[0],other))
   case.persist(book)
  return out
 monkeypatch.setattr(m,'completeness',assess);deny(lambda:call(case))

@pytest.mark.parametrize('change',['type','list','foreign','duplicate','overlap','unknownkey'])
def test_malformed_detached_actual_projection(case,monkeypatch,change):
 actual=m.completeness
 def assess(records):
  out=actual(records)
  if change=='type':return None
  if change=='list':return replace(out,missing_photos=list(out.missing_photos))
  if change=='foreign':return replace(out,missing_photos=(m.ItemKey('foreign','v'),))
  if change=='duplicate':return replace(out,missing_photos=out.missing_photos*2)
  if change=='overlap':return replace(out,unknown_photos=out.missing_photos)
  return replace(out,unknown_prices=(m.ItemKey('C',m.UNKNOWN),))
 monkeypatch.setattr(m,'completeness',assess);deny(lambda:call(case))


def test_defaultoff_invalid_driver_missing_sheet_no_effects(case):
 before=raw(case);calls=case.state.copy();deny(lambda:reader(case,enabled=False).read_catalog(observed_on=DAY,currency='USD',now=NOW));assert case.state==calls and raw(case)==before
 deny(lambda:reader(case,sheet_id='absent').read_catalog(observed_on=DAY,currency='USD',now=NOW))
 driver=object.__new__(driver_module.SQLiteTableDriver);INIT(driver,store=case.s,current_context=case.context,enabled=False)
 deny(lambda:reader(case,driver=driver).read_catalog(observed_on=DAY,currency='USD',now=NOW));deny(lambda:call(case,now=NOW.replace(tzinfo=None)))


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
