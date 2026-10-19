"""Actual durable supplied transaction scenarios; no live finance/provider access."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
import json
import sqlite3

import pytest

from goldway import table_promotion_scenario as module
from goldway import table_sqlite_driver as driver_module
from goldway.store import Store

tables=module.tables;finance=module.finance
NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
KEY=b'OPEN-SYNTHETIC-PROMOTION-FIXTURE-KEY-0001'
INIT=driver_module.SQLiteTableDriver.__init__
BUILD=driver_module.SQLiteTableDriver.build_plan
APPLY=driver_module.SQLiteTableDriver.apply
ISSUE=tables.issue_local_approval
CORE_BUILD=tables.build_plan
VALIDATE=tables.validate_approval
CHECK=tables.check_plan_baseline
CORE_APPLY=tables.apply_to_fake_workbook


def columns():return module.PromotionColumns('scenario','scope','currency','original','cogs','discount','fee','fixed')


def row(identifier='s1',position=1,**changes):
    data=dict(scenario=identifier,scope=module.ONE_TRANSACTION_SAME_VOLUME,currency='RUB',
              original=Decimal('100.00'),cogs=Decimal('60.0'),discount=Decimal(10),fee=Decimal(5),fixed=Decimal(1))
    data.update(changes)
    return tables.Row(position,'row-v1',tuple(tables.Cell(key,value) for key,value in data.items()))


def encode(context,book,nonces=()):
    return json.dumps(dict(format='sqlite-table-driver-v1',version=1,namespace=context.namespace,
        workbook=dict(id=book.id,revision=book.revision,sheets=[dict(id=s.id,revision=s.revision,
            rows=[dict(position=r.position,revision=r.revision,cells=[[c.column,tables._value(c.value)] for c in r.cells])
                  for r in s.rows]) for s in book.sheets]),used_nonces=list(nonces)),
        sort_keys=True,separators=(',',':'))


def reader(c,**changes):
    args=dict(driver=c.driver,current_source=c.current,expected_source=c.source,business='SYNTHETIC',
              owner_id='synthetic-owner',sheet_id='scenarios',bindings=c.bindings,columns=c.columns,enabled=True)
    args.update(changes);return module.TablePromotionScenarioReader(**args)


@pytest.fixture
def case(tmp_path,monkeypatch):
    original=Store.__init__;path=tmp_path/'synthetic.sqlite';store=Store(str(path))
    context=driver_module.TableContext('SYNTHETIC','synthetic-driver','synthetic-approval',
                                      'synthetic-book','synthetic-reviewer','synthetic-key',KEY)
    book=tables.Workbook(context.workbook_id,'book-v1',(
        tables.Sheet('scenarios','sheet-v1',(row(),row('s2',position=2,cogs=None))),
        tables.Sheet('unselected','extra-v1',(tables.Row(1,'extra-row',(
            tables.Cell('id','other'),tables.Cell('value',Decimal('1.0')))),))))
    store.execute('INSERT INTO runtime VALUES(?,?)',(context.runtime_key,encode(context,book)))
    store.execute('INSERT INTO runtime VALUES(?,?)',('foreign','UNTOUCHED'))
    source=module.PromotionSource('SYNTHETIC','synthetic-owner','declared-scenario-set',context.workbook_id,
        1,NOW-timedelta(days=1),NOW+timedelta(days=1))
    state=dict(source=source,context=context,source_calls=0)
    def current():state['source_calls']+=1;return state['source']
    def current_context():return state['context']
    driver=driver_module.SQLiteTableDriver(store=store,current_context=current_context,enabled=True)
    c=SimpleNamespace(s=store,path=path,ctx=context,context=current_context,state=state,source=source,current=current,
        driver=driver,book=book,columns=columns(),bindings=tuple(module.ScenarioBinding(i,tables.RowIdentity('scenario',i)) for i in ('s1','s2')))
    c.reader=reader(c)
    def persist(book,nonces=()):c.s.execute('UPDATE runtime SET value=? WHERE key=?',(encode(context,book,nonces),context.runtime_key))
    def reopen():
        old_connection=c.s.db;old_connection.close()
        c.s=object.__new__(Store);original(c.s,str(path))
        with pytest.raises(sqlite3.ProgrammingError):old_connection.execute('SELECT 1')
        c.driver=object.__new__(driver_module.SQLiteTableDriver);INIT(c.driver,store=c.s,current_context=current_context,enabled=True)
        c.reader=reader(c)
    def correct_discount(value):
        with monkeypatch.context() as fixture:
            for name,actual in (('build_plan',CORE_BUILD),('validate_approval',VALIDATE),
                                ('check_plan_baseline',CHECK),('apply_to_fake_workbook',CORE_APPLY)):
                fixture.setattr(tables,name,actual)
            writer=object.__new__(driver_module.SQLiteTableDriver)
            INIT(writer,store=c.s,current_context=current_context,enabled=True,allow_fake_writes=True)
            plan=BUILD(writer,'scenarios',tables.RowIdentity('scenario','s1'),(tables.Cell('discount',value),))
            token=ISSUE(plan,decision='approve',approver=context.required_approver,nonce='synthetic-nonce',
                        issued_at=900,expires_at=1100,signing_key=KEY)
            return APPLY(writer,plan,token,now=1000,next_workbook_revision='book-v2',
                         next_sheet_revision='sheet-v2',next_row_revision='row-v2')
    c.persist=persist;c.reopen=reopen;c.correct_discount=correct_discount
    def forbidden(*a,**k):pytest.fail('Consumer used a constructor/key/approval/write/provider/send path')
    monkeypatch.setattr(Store,'__init__',forbidden);monkeypatch.setattr(Store,'queue',forbidden)
    monkeypatch.setattr(driver_module.SQLiteTableDriver,'__init__',forbidden)
    for name in ('build_plan','apply'):monkeypatch.setattr(driver_module.SQLiteTableDriver,name,forbidden)
    for name in ('build_plan','validate_approval','check_plan_baseline','apply_to_fake_workbook','issue_local_approval'):
        monkeypatch.setattr(tables,name,forbidden)
    import socket,urllib.request
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    yield c
    c.s.db.set_authorizer(None);c.s.db.close()


def raw(c):return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.ctx.runtime_key,)).fetchone()[0]
def call(c,**changes):
    args=dict(currency='RUB',now=NOW);args.update(changes);return c.reader.read_scenarios(**args)
def deny(function):
    with pytest.raises(module.PromotionScenarioUnavailable,match='^table_promotion_scenario_unavailable$'):function()
def select(c,rows):
    book=replace(c.book,sheets=(replace(c.book.sheets[0],rows=tuple(rows)),c.book.sheets[1]));c.persist(book);return book


def test_actual_core_once_per_known_row_preserves_unknown_provenance_and_file_reopen(case,monkeypatch):
    calls=[];actual=finance.preview_promotion;find=tables.lookup_row;found=[]
    def spy(**args):
        result=actual(**args);calls.append((args,result));return result
    def lookup(*a,**kw):found.append(kw['identity']);return find(*a,**kw)
    monkeypatch.setattr(finance,'preview_promotion',spy);monkeypatch.setattr(tables,'lookup_row',lookup)
    before=raw(case);report=call(case);known,unknown=report.scenarios
    assert len(calls)==1 and known.result is calls[0][1] and len(found)==2
    assert known.result.discounted_revenue==Decimal(90) and known.result.fee_amount==Decimal('5.5')
    assert known.result.contribution==Decimal('24.5') and known.result.original_revenue.as_tuple()==Decimal('100.00').as_tuple()
    assert unknown.result is None and unknown.missing==('cogs',) and dict(unknown.inputs)['cogs'] is None
    assert report.source_ids==tuple(r.source_id for r in report.scenarios) and len(set(report.source_ids))==2
    assert report.requires_review and not report.live_authorized and not report.launch_authorized and not report.accounting_facts_verified
    assert raw(case)==before and not hasattr(report,'workbook') and KEY.decode() not in repr(report)
    case.reopen();same=call(case);assert same.scenarios==report.scenarios
    receipt=case.correct_discount(Decimal(20));assert receipt.state=='local_fake_committed'
    case.reopen();after_raw=raw(case);updated=call(case)
    assert updated.scenarios[0].result.discounted_revenue==Decimal(80)
    assert updated.scenarios[0].result.contribution==Decimal(15) and updated.workbook_fingerprint!=report.workbook_fingerprint
    assert raw(case)==after_raw and json.loads(after_raw)['used_nonces']==['synthetic-nonce']
    with pytest.raises(FrozenInstanceError):updated.launch_authorized=True


@pytest.mark.parametrize('changes,contribution,margin',[({},Decimal('24.5'),True),
    ({'original':Decimal(0),'cogs':Decimal(0),'discount':Decimal(0),'fee':Decimal(0),'fixed':Decimal(0)},Decimal(0),False),
    ({'discount':Decimal(100)},Decimal(-61),False),
    ({'cogs':Decimal(200)},Decimal('-115.5'),True),
    ({'discount':Decimal(0),'fee':Decimal(100),'fixed':Decimal(3)},Decimal(-63),True)])
def test_actual_signed_contribution_and_zero_denominator(case,changes,contribution,margin):
    select(case,(row(**changes),));case.reader=reader(case,bindings=case.bindings[:1]);result=call(case).scenarios[0].result
    assert result.contribution==contribution and (result.contribution_margin_pct is not None)==margin


@pytest.mark.parametrize('field',['original','cogs','discount','fee','fixed','currency'])
def test_each_explicit_missing_field_remains_unknown_without_calling_core(case,monkeypatch,field):
    select(case,(row(**{field:None}),));case.reader=reader(case,bindings=case.bindings[:1])
    monkeypatch.setattr(finance,'preview_promotion',lambda **k:pytest.fail('Unknown row called core'))
    value=call(case).scenarios[0]
    expected={'original':'original_revenue','discount':'discount_pct','fee':'fee_pct','fixed':'fixed_fee'}.get(field,field)
    assert value.result is None and value.missing==(expected,) and value.source_id


def test_unknown_currency_is_not_filled_from_expected_currency_or_zero(case,monkeypatch):
    select(case,(row(currency=None,original=None,cogs=None,discount=None,fee=None,fixed=None),))
    case.reader=reader(case,bindings=case.bindings[:1]);value=call(case,currency=None).scenarios[0]
    assert value.currency is None and len(value.missing)==6 and all(v is None for _,v in value.inputs)


@pytest.mark.parametrize('field',['original','cogs','discount','fee','fixed'])
@pytest.mark.parametrize('value',[0,True,False,1.5,'10','=SUM(A1)',Decimal('-1'),Decimal('NaN'),
    Decimal('Infinity'),Decimal('1E+1000000000'),Decimal('1E-19'),Decimal('1'*51)])
def test_nondecimal_or_out_of_bound_values_whole_refuse_even_when_other_field_missing(case,field,value):
    try:select(case,(row(cogs=None,**{field:value}) if field!='cogs' else row(cogs=value),))
    except ValueError:
        state=json.loads(raw(case));cell=next(c for c in state['workbook']['sheets'][0]['rows'][0]['cells'] if c[0]==field)
        cell[1]=['float' if type(value) is float else 'decimal',str(value)]
        case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),case.ctx.runtime_key))
    case.reader=reader(case,bindings=case.bindings[:1]);deny(lambda:call(case))


@pytest.mark.parametrize('changes',[{'discount':Decimal('100.0001')},{'fee':Decimal(101)},
    {'scope':'PER_UNIT'},{'scope':None},{'currency':'USD'},{'currency':'rub'},{'currency':''}])
def test_scope_percentage_and_exact_currency_contract(case,changes):
    select(case,(row(**changes),));case.reader=reader(case,bindings=case.bindings[:1]);deny(lambda:call(case))


def test_invalid_second_row_refuses_before_any_core_call(case,monkeypatch):
    select(case,(row(),row('s2',position=2,discount=True)))
    monkeypatch.setattr(finance,'preview_promotion',lambda **k:pytest.fail('Invalid whole input called core'))
    deny(lambda:call(case))


@pytest.mark.parametrize('change',['wrong_business','wrong_owner','revoked','expired','future','revision','callback_type'])
def test_current_source_and_host_scope_refuse_before_snapshot(case,monkeypatch,change):
    args={}
    if change=='wrong_business':args['business']='FOREIGN'
    elif change=='wrong_owner':args['owner_id']='foreign-owner'
    elif change=='callback_type':case.state['source']=object()
    else:
        values={'revoked':dict(revoked=True),'expired':dict(expires_at=NOW),
                'future':dict(valid_from=NOW+timedelta(seconds=1)), 'revision':dict(revision=2)}[change]
        case.state['source']=replace(case.source,**values)
    monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',lambda *a:pytest.fail('Unauthorized cell transfer'))
    case.reader=reader(case,**args);deny(lambda:call(case))


def test_disabled_is_no_read_and_invalid_now_refuses(case):
    case.reader=reader(case,enabled=False);deny(lambda:call(case));assert case.state['source_calls']==0
    case.reader=reader(case);deny(lambda:call(case,now=NOW.replace(tzinfo=None)))


@pytest.mark.parametrize('bindings',[(),('wrong',),tuple(module.ScenarioBinding('s1',tables.RowIdentity('scenario','s1')) for _ in range(2)),
    tuple(module.ScenarioBinding(str(i),tables.RowIdentity('scenario',str(i))) for i in range(4))])
def test_closed_selection_bounds_duplicates_and_types(case,bindings):
    case.reader=reader(case,bindings=bindings);deny(lambda:call(case))


def test_missing_ambiguous_extra_or_wrong_identity_row_refuses(case):
    select(case,(row(),row('s1',position=2)));deny(lambda:call(case))
    select(case,(row(),));deny(lambda:call(case))
    r=row();r=replace(r,cells=r.cells+(tables.Cell('extra','not schema'),));select(case,(r,))
    case.reader=reader(case,bindings=case.bindings[:1]);deny(lambda:call(case))


@pytest.mark.parametrize('change',['selected_scale','unselected_scale','revision','source','mapping','selection','method'])
def test_full_typed_snapshot_source_method_and_host_drift_refuse(case,monkeypatch,change):
    original=driver_module.SQLiteTableDriver.snapshot;count=[0]
    def snapshot(driver):
        book=original(driver);returned=book;count[0]+=1
        if count[0]==1:
            if change in ('selected_scale','unselected_scale','revision'):
                if change=='selected_scale':
                    r=book.sheets[0].rows[0];r=replace(r,cells=tuple(tables.Cell(c.column,Decimal('100.0') if c.column=='original' else c.value) for c in r.cells))
                    book=replace(book,sheets=(replace(book.sheets[0],rows=(r,book.sheets[0].rows[1])),book.sheets[1]))
                elif change=='unselected_scale':
                    r=book.sheets[1].rows[0];r=replace(r,cells=(r.cells[0],tables.Cell('value',Decimal('1.00'))))
                    book=replace(book,sheets=(book.sheets[0],replace(book.sheets[1],rows=(r,))))
                else:book=replace(book,revision='book-changed')
                case.persist(book)
            elif change=='source':case.state['source']=replace(case.source,revision=2)
            elif change=='mapping':case.reader._columns=replace(case.columns)
            elif change=='selection':case.reader._bindings=tuple(reversed(case.bindings))
            else:monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',lambda self:book)
        return returned
    monkeypatch.setattr(driver_module.SQLiteTableDriver,'snapshot',snapshot);case.reader=reader(case)
    deny(lambda:call(case))


def test_exact_four_raw_state_reads_one_mib_ceiling_and_no_writes(case,monkeypatch):
    actual=driver_module.SQLiteTableDriver._full;values=[]
    def full(driver,context):
        value=actual(driver,context);values.append(len(value.encode()));return value
    monkeypatch.setattr(driver_module.SQLiteTableDriver,'_full',full)
    def authorizer(action,*a):
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_CREATE_TABLE):return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    case.s.db.set_authorizer(authorizer)
    try:report=call(case)
    finally:case.s.db.set_authorizer(None)
    assert len(values)==4 and sum(values)<=1048576 and len(report.scenarios)==2


def test_detached_core_result_type_currency_input_or_number_refuses(case,monkeypatch):
    actual=finance.preview_promotion
    class Currency(str):pass
    for mutation in (lambda r:object(),lambda r:replace(r,currency='USD'),
                     lambda r:replace(r,currency=Currency('RUB')),
                     lambda r:replace(r,original_revenue=Decimal('100.0')),lambda r:replace(r,contribution='bad')):
        monkeypatch.setattr(finance,'preview_promotion',lambda **kw:mutation(actual(**kw)))
        deny(lambda:call(case))


def test_three_explicit_scenarios_are_all_consumed_without_aggregation(case,monkeypatch):
    select(case,(row(),row('s2',position=2,cogs=Decimal(20)),row('s3',position=3,fixed=Decimal(0))))
    case.reader=reader(case,bindings=tuple(module.ScenarioBinding(i,tables.RowIdentity('scenario',i)) for i in ('s1','s2','s3')))
    calls=[];actual=finance.preview_promotion
    def preview(**kw):calls.append(kw);return actual(**kw)
    monkeypatch.setattr(finance,'preview_promotion',preview)
    result=call(case)
    assert len(calls)==3 and len(result.scenarios)==3 and len(result.source_ids)==3
    assert [r.scenario_id for r in result.scenarios]==['s1','s2','s3'] and not hasattr(result,'total')
