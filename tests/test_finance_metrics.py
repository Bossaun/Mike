"""Synthetic deterministic metrics; no runtime fixtures or external clients."""
from datetime import date, datetime
from decimal import Decimal, ROUND_UP, localcontext
import pytest
from goldway.finance_metrics import Sale, CostFlag, compare_weeks, cost_flags, preview_promotion

MONDAY = date(2026, 10, 12)

def sale(id='s1', day=MONDAY, revenue='100', cogs='60', barter=False, currency='EUR'):
    return Sale(id, day, currency, revenue, cogs, barter)


def test_flags_keep_unknown_cost_zero_revenue_and_explicit_barter_distinct():
    records = [sale('loss',revenue='9.99',cogs='10'),sale('missing',cogs=None),
        sale('free',revenue='0',cogs='2'),sale('at-cost',revenue='10',cogs='10'),
        sale('barter',revenue='0',cogs='30',barter=True),sale('unknown-barter',cogs=None,barter=True)]
    assert cost_flags(records,currency='EUR') == (CostFlag('loss','under_cost'),
        CostFlag('missing','missing_cost'),CostFlag('free','under_cost'),CostFlag('unknown-barter','missing_cost'))
    assert records[2].barter is False


def test_weekly_known_cost_scope_coverage_and_barter_totals():
    records=[sale('prior',date(2026,10,5)),sale('current',revenue='200',cogs='120'),
        sale('unknown',revenue='50',cogs=None),sale('barter',revenue='9000',cogs='30',barter=True),
        sale('unknown-barter',revenue='8000',cogs=None,barter=True)]
    result=compare_weeks(records,week_start=MONDAY,currency='EUR'); current=result.current
    assert current.revenue == Decimal('250')
    assert (current.sale_count,current.known_cost_count,current.unknown_cost_count)==(2,1,1)
    assert current.cost_coverage == Decimal('0.5')
    assert (current.known_cost_revenue,current.known_cogs,current.gross_profit,current.gross_margin_pct)==tuple(map(Decimal,['200','120','80','40']))
    assert (current.barter_count,current.known_barter_cost,current.unknown_barter_cost_count)==(2,Decimal('30'),1)
    assert (result.revenue_delta,result.revenue_change_pct,result.known_scope_gross_profit_delta,result.gross_margin_change_pp)==tuple(map(Decimal,['150','150','40','0']))


def test_calendar_boundaries_start_inclusive_end_exclusive():
    records=[sale('before',date(2026,10,4),'500'),sale('prior-start',date(2026,10,5),'10'),
        sale('prior-end',date(2026,10,11),'20'),sale('current-start',MONDAY,'30'),
        sale('current-end',date(2026,10,18),'40'),sale('after',date(2026,10,19),'900')]
    result=compare_weeks(records,week_start=MONDAY,currency='EUR')
    assert (result.current.start,result.current.end)==(MONDAY,date(2026,10,19))
    assert (result.prior.start,result.prior.end)==(date(2026,10,5),MONDAY)
    assert result.current.revenue==Decimal('70') and result.prior.revenue==Decimal('30')


def test_missing_cost_is_not_zero_profit_empty_period_has_no_margin():
    result=compare_weeks([sale(cogs=None)],week_start=MONDAY,currency='EUR')
    assert result.current.revenue==Decimal('100') and result.current.unknown_cost_count==1
    assert result.current.cost_coverage==0 and result.current.gross_profit is None and result.current.gross_margin_pct is None
    assert result.prior.sale_count==0 and result.prior.cost_coverage is None and result.prior.gross_profit is None
    assert result.revenue_change_pct is None and result.known_scope_gross_profit_delta is None


def test_zero_revenue_normal_sale_can_have_loss_without_undefined_division():
    current=compare_weeks([sale(revenue='0',cogs='5')],week_start=MONDAY,currency='EUR').current
    assert current.gross_profit==Decimal('-5') and current.gross_margin_pct is None
    assert current.sale_count==1 and current.barter_count==0


def test_all_barter_has_known_cost_total_and_unknown_count_without_revenue():
    current=compare_weeks([sale(barter=True,revenue='999',cogs='12'),sale('missing',barter=True,cogs=None)],week_start=MONDAY,currency='EUR').current
    assert current.revenue==0 and current.sale_count==0 and current.gross_profit is None
    assert (current.barter_count,current.known_barter_cost,current.unknown_barter_cost_count)==(2,Decimal('12'),1)


@pytest.mark.parametrize('bad',[True,False,1,0,1.1,None,'',' 1','1 ','oops','NaN','sNaN','Infinity','-Infinity','-0.01',Decimal('NaN'),Decimal('sNaN'),Decimal('Infinity'),Decimal('-0.01'),'1e999999','1e-999999'])
@pytest.mark.parametrize('field',['revenue','cogs'])
def test_invalid_amounts_never_enter_metrics(bad,field):
    if field=='cogs' and bad is None: assert sale(cogs=None).cogs is None
    else:
        with pytest.raises(ValueError): sale(**{field:bad})


@pytest.mark.parametrize('currency',['eur','','EURO','EUR ',123,None])
def test_currency_is_explicit(currency):
    with pytest.raises(ValueError): sale(currency=currency)


@pytest.mark.parametrize('barter',['false',0,1,None])
def test_barter_requires_boolean(barter):
    with pytest.raises(ValueError): sale(barter=barter)


@pytest.mark.parametrize('day',['2026-10-12',datetime(2026,10,12),None])
def test_date_is_calendar_date_only(day):
    with pytest.raises(ValueError): sale(day=day)


@pytest.mark.parametrize('id',['',' ',' x','x ',None])
def test_required_unique_identity(id):
    with pytest.raises(ValueError): sale(id=id)


@pytest.mark.parametrize('calculate',['flags','weeks'])
def test_duplicate_ids_mixed_currency_and_untyped_records_rejected(calculate):
    def compute(records):
        return cost_flags(records,currency='EUR') if calculate=='flags' else compare_weeks(records,week_start=MONDAY,currency='EUR')
    with pytest.raises(ValueError,match='Duplicate'): compute([sale('same'),sale('same',date(2020,1,1))])
    with pytest.raises(ValueError,match='Mixed currencies'): compute([sale(),sale('other',date(2020,1,1),currency='USD')])
    with pytest.raises(ValueError): compute([{'id':'unvalidated'}])


def test_record_limit_bounds_consumption(monkeypatch):
    monkeypatch.setattr('goldway.finance_metrics.MAX_RECORDS',2)
    with pytest.raises(ValueError,match='Too many'): cost_flags((sale(str(i)) for i in range(3)),currency='EUR')


def test_explicit_monday_and_date_overflow():
    for bad in (date(2026,10,13),'2026-10-12',None,date.min):
        with pytest.raises(ValueError): compare_weeks([],week_start=bad,currency='EUR')


def test_promotion_uses_transaction_totals_with_fee_after_discount():
    preview=preview_promotion(currency='EUR',original_revenue='100',cogs='60',discount_pct='10',fee_pct='2',fixed_fee='1')
    assert (preview.discount_amount,preview.discounted_revenue,preview.fee_amount,preview.contribution)==tuple(map(Decimal,['10','90','2.8','27.2']))
    with localcontext() as ctx:
        ctx.prec=100
        assert preview.contribution_margin_pct==Decimal('27.2')*Decimal('100')/Decimal('90')


def test_full_discount_has_loss_and_no_margin():
    preview=preview_promotion(currency='EUR',original_revenue='100',cogs='60',discount_pct='100',fee_pct='2',fixed_fee='1')
    assert preview.discounted_revenue==0 and preview.fee_amount==1 and preview.contribution==Decimal('-61')
    assert preview.contribution_margin_pct is None


@pytest.mark.parametrize('field,bad',[('discount_pct','100.01'),('discount_pct','-1'),('fee_pct',True),('fee_pct','NaN'),('fixed_fee',None),('cogs',None),('original_revenue',1.0)])
def test_promotion_rejects_unknown_or_invalid_inputs(field,bad):
    params=dict(currency='EUR',original_revenue='100',cogs='60',discount_pct='0',fee_pct='0',fixed_fee='0');params[field]=bad
    with pytest.raises(ValueError): preview_promotion(**params)


def test_global_decimal_context_does_not_change_results():
    records=[sale(revenue='0.3',cogs='0.1')]
    baseline=compare_weeks(records,week_start=MONDAY,currency='EUR')
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_UP
        assert compare_weeks(records,week_start=MONDAY,currency='EUR')==baseline
        assert preview_promotion(currency='EUR',original_revenue='0.3',cogs='0.1',discount_pct='0',fee_pct='0',fixed_fee='0').contribution==Decimal('0.2')
