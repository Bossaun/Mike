"""I58-A local immutable baseline extraction and real apply authorization order.

Signing here exercises the existing fake-workbook unit boundary only; no human
approval or external sheet permission is created by this component or fixture.
"""
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import pytest
import goldway.table_change_plan as core

KEY=b'synthetic-unit-signing-key-32-bytes-long'


def book():
    row=core.Row(7,'r1',(core.Cell('key','EXACT-1'),core.Cell('price',Decimal('2.00')),
        core.Cell('note',' original '),core.Cell('flag',False),core.Cell('empty',None)))
    other=core.Row(8,'other1',(core.Cell('key','EXACT-2'),core.Cell('price',Decimal('9.00'))))
    return core.Workbook('local-book','w1',(core.Sheet('local-sheet','s1',(row,other)),))


def plan(workbook):
    return core.build_plan(workbook,workbook_id='local-book',sheet_id='local-sheet',
        identity=core.RowIdentity('key','EXACT-1'),desired=(core.Cell('price',Decimal('3.00')),))


def changed_row(w,**changes):
    sheet=w.sheets[0]
    return replace(w,sheets=(replace(sheet,rows=(replace(sheet.rows[0],**changes),sheet.rows[1])),))


def token(p):
    return core.issue_local_approval(p,decision='approve',approver='synthetic-unit-owner',
        nonce='unit-once',issued_at=100,expires_at=200,signing_key=KEY)


def apply(w,p,t,**changes):
    kwargs=dict(required_approver='synthetic-unit-owner',now=150,signing_key=KEY,
        used_nonces=frozenset(),next_workbook_revision='w2',next_sheet_revision='s2',next_row_revision='r2')
    kwargs.update(changes)
    return core.apply_to_fake_workbook(w,p,t,**kwargs)


def test_readonly_returns_existing_immutable_row_without_approval_or_side_effects(monkeypatch):
    w=book();p=plan(w);original=core.plan_digest(p)
    def forbidden(*a,**k):pytest.fail('readonly check reached approval, apply or external effect')
    import builtins,socket,time
    for name in ('issue_local_approval','validate_approval','apply_to_fake_workbook'):
        monkeypatch.setattr(core,name,forbidden)
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(time,'time',forbidden)
    row=core.check_plan_baseline(w,p)
    assert row is w.sheets[0].rows[0]
    assert core.plan_digest(p)==original and p.baseline is row
    with pytest.raises(FrozenInstanceError):row.position=10
    assert w.revision=='w1' and row.cells[1].value==Decimal('2.00')


@pytest.mark.parametrize('edit',[
    lambda w:replace(w,revision='w-manual'),
    lambda w:replace(w,sheets=(replace(w.sheets[0],revision='s-manual'),)),
    lambda w:changed_row(w,revision='r-manual'),
    lambda w:changed_row(w,position=10),
    lambda w:changed_row(w,cells=(w.sheets[0].rows[0].cells[0],core.Cell('price',Decimal('8.00')),*w.sheets[0].rows[0].cells[2:])),
    lambda w:changed_row(w,cells=(*w.sheets[0].rows[0].cells[:2],core.Cell('note','manual'),*w.sheets[0].rows[0].cells[3:])),
    lambda w:changed_row(w,cells=(*w.sheets[0].rows[0].cells,core.Cell('added','new'))),
    lambda w:changed_row(w,cells=w.sheets[0].rows[0].cells[:-1]),
    lambda w:changed_row(w,cells=tuple(reversed(w.sheets[0].rows[0].cells))),
])
def test_each_baseline_dimension_rejects_without_mutation(edit):
    w=book();p=plan(w);current=edit(w);before=core.plan_digest(p)
    with pytest.raises(core.ConflictError,match='Baseline changed: manual edit, row move or revision conflict'):
        core.check_plan_baseline(current,p)
    assert core.plan_digest(p)==before and w.sheets[0].rows[0].position==7
    assert current==edit(w)


@pytest.mark.parametrize('column,value',[('price',Decimal('2.000')),('flag',0),('empty',''),
    ('note','original'),('note',' original  ')])
def test_fullrow_type_scale_null_empty_and_spaces_remain_distinct(column,value):
    w=book();p=plan(w)
    cells=tuple(core.Cell(c.column,value) if c.column==column else c for c in w.sheets[0].rows[0].cells)
    with pytest.raises(core.ConflictError,match='Baseline changed'):
        core.check_plan_baseline(changed_row(w,cells=cells),p)


@pytest.mark.parametrize('edit',[
    lambda w:replace(w,id='wrong-book'),
    lambda w:replace(w,sheets=(replace(w.sheets[0],id='wrong-sheet'),)),
    lambda w:replace(w,sheets=(replace(w.sheets[0],rows=(w.sheets[0].rows[1],)),)),
    lambda w:replace(w,sheets=(replace(w.sheets[0],rows=()),)),
    lambda w:replace(w,sheets=(replace(w.sheets[0],rows=(*w.sheets[0].rows,replace(w.sheets[0].rows[0],position=9))),)),
    lambda w:changed_row(w,cells=(core.Cell('key','exact-1'),*w.sheets[0].rows[0].cells[1:])),
    lambda w:changed_row(w,cells=(core.Cell('key',' EXACT-1 '),*w.sheets[0].rows[0].cells[1:])),
    lambda w:changed_row(w,cells=(core.Cell('different-key','EXACT-1'),*w.sheets[0].rows[0].cells[1:])),
])
def test_missing_ambiguous_exact_identity_no_position_fallback(edit):
    w=book();p=plan(w)
    with pytest.raises(core.ConflictError,match='Target workbook/sheet/row is missing or ambiguous'):
        core.check_plan_baseline(edit(w),p)


@pytest.mark.parametrize('bad',[None,True,{},(),object()])
def test_readonly_plan_type_guard(bad):
    with pytest.raises(ValueError,match='Validated change plan required'):core.check_plan_baseline(book(),bad)

@pytest.mark.parametrize('bad',[None,True,{},(),object()])
def test_readonly_workbook_type_guard(bad):
    w=book()
    with pytest.raises(ValueError,match='Validated workbook and row identity required'):
        core.check_plan_baseline(bad,plan(w))


def test_unrelated_row_change_without_version_change_does_not_expand_baseline_scope():
    w=book();p=plan(w)
    current=replace(w,sheets=(replace(w.sheets[0],rows=(w.sheets[0].rows[0],replace(w.sheets[0].rows[1],revision='other2'))),))
    assert core.check_plan_baseline(current,p) is current.sheets[0].rows[0]


@pytest.mark.parametrize('invalid',['missing','actor','early','expired','replayed','signature','digest','key'])
def test_apply_invalid_approval_before_checker_even_with_unreadable_workbook(monkeypatch,invalid):
    w=book();p=plan(w);t=token(p);changes={}
    if invalid=='missing':t=None
    elif invalid=='actor':changes['required_approver']='other-unit-owner'
    elif invalid=='early':changes['now']=99
    elif invalid=='expired':changes['now']=200
    elif invalid=='replayed':changes['used_nonces']=frozenset({'unit-once'})
    elif invalid=='signature':t=replace(t,signature='0'*64)
    elif invalid=='digest':t=replace(t,plan_digest='0'*64)
    else:changes['signing_key']=b'another-unit-key-32-bytes-long!!!!'
    calls=[]
    def forbidden(*a,**k):
        calls.append('checker')
        pytest.fail('apply checked baseline before validating approval')
    monkeypatch.setattr(core,'check_plan_baseline',forbidden)
    with pytest.raises(core.ApprovalError):apply(object(),p,t,**changes)
    assert not calls


def test_valid_apply_uses_shared_checker_after_authorization_and_preserves_result(monkeypatch):
    w=book();p=plan(w);t=token(p);calls=[]
    validate=core.validate_approval;check=core.check_plan_baseline
    def validate_spy(*a,**k):
        result=validate(*a,**k);calls.append('authorized');return result
    def check_spy(*a,**k):
        assert calls==['authorized'];calls.append('checked');return check(*a,**k)
    monkeypatch.setattr(core,'validate_approval',validate_spy)
    monkeypatch.setattr(core,'check_plan_baseline',check_spy)
    result=apply(w,p,t)
    assert calls==['authorized','checked']
    assert result.workbook.revision=='w2' and result.workbook.sheets[0].revision=='s2'
    row=result.workbook.sheets[0].rows[0]
    assert row.position==7 and row.revision=='r2' and row.cells[1].value==Decimal('3.00')
    assert row.cells[:1]+row.cells[2:]==w.sheets[0].rows[0].cells[:1]+w.sheets[0].rows[0].cells[2:]
    assert result.workbook.sheets[0].rows[1] is w.sheets[0].rows[1]
    assert result.used_nonces==frozenset({'unit-once'}) and w.revision=='w1'
    with pytest.raises(core.ApprovalError,match='consumed'):
        apply(w,p,t,used_nonces=result.used_nonces)
    assert calls==['authorized','checked']


def test_baseline_conflict_precedes_next_revision_validation_and_never_consumes_nonce():
    w=book();p=plan(w);t=token(p);ledger=frozenset({'earlier-unit-nonce'})
    with pytest.raises(core.ConflictError,match='Baseline changed'):
        apply(changed_row(w,position=10),p,t,used_nonces=ledger,next_workbook_revision=None)
    assert ledger==frozenset({'earlier-unit-nonce'}) and w.revision=='w1'
    assert core.check_plan_baseline(w,p) is w.sheets[0].rows[0]


@pytest.mark.parametrize('name,value,error',[
    ('next_workbook_revision','w1',core.ConflictError),
    ('next_sheet_revision','s1',core.ConflictError),
    ('next_row_revision','r1',core.ConflictError),
    ('next_workbook_revision',None,ValueError),
])
def test_next_revision_guard_remains_after_successful_shared_check(monkeypatch,name,value,error):
    w=book();p=plan(w);t=token(p);check=core.check_plan_baseline;calls=[]
    def spy(*a,**k):calls.append('checked');return check(*a,**k)
    monkeypatch.setattr(core,'check_plan_baseline',spy)
    with pytest.raises(error):apply(w,p,t,**{name:value})
    assert calls==['checked'] and w.revision=='w1' and p.baseline.cells[1].value==Decimal('2.00')
