from decimal import Decimal, localcontext

import pytest

from goldway.procurement_metrics import (Charge, FreightTariff, FXRate, Money, Weight,
    assess_logistics, assess_weight, freight_from_weight, quote_purchase)


def charge(id, kind, amount, currency='EUR', basis='batch', includes=()):
    return Charge(id, kind, Money(amount, currency), basis, includes)


def quote(charges=None, **changes):
    values = dict(quantity=10, currency='EUR',
                  charges=charges if charges is not None else [
                      charge('purchase', 'purchase', '2', basis='per_unit'),
                      charge('freight', 'freight', '5'),
                      charge('commission', 'commission', '1'),
                      charge('packing', 'fee', '0.20', basis='per_unit')],
                  fx_rates=[], fees_complete=True, transaction_kind='purchase')
    values.update(changes)
    return quote_purchase(**values)


def test_explicit_unit_and_batch_breakdown_no_hidden_quantization():
    result = quote()
    assert result['status'] == 'known'
    assert result['total_batch'] == Decimal('28')
    assert result['per_unit'] == Decimal('2.8')
    assert [x['batch_amount'] for x in result['components']] == list(map(Decimal, ['20','5','1','2']))
    assert result['quantity'] == 10
    assert result['quantity_basis'] == 'count_of_identical_units_in_one_batch'
    assert result['components'][0]['source_basis'] == 'per_unit'


def test_weight_tariff_and_direct_fx_full_quote():
    freight = freight_from_weight('freight', Weight('250', 'g', 'per_unit', 'gross'),
                                  FreightTariff(Money('8', 'USD'), 'kg', 'gross'))
    assert freight.money.amount == Decimal('2')
    result = quote([charge('purchase','purchase','100','RUB'), freight,
                    charge('commission','commission','0')],
                   fx_rates=[FXRate('RUB','EUR','0.01'),FXRate('USD','EUR','0.9')])
    assert result['total_batch'] == Decimal('19')
    assert result['per_unit'] == Decimal('1.9')
    assert result['components'][1]['fx_target_per_source_unit'] == Decimal('0.9')


@pytest.mark.parametrize(('weight','tariff_unit','rate','expected'), [
    (Weight('2','kg','batch','net'),'g','0.001','2'),
    (Weight('250','g','per_unit','net'),'kg','8','2'),
    (Weight('0','kg','batch','net'),'kg','8','0'),
])
def test_freight_units_and_basis_are_explicit(weight,tariff_unit,rate,expected):
    result = freight_from_weight('f',weight,FreightTariff(Money(rate,'EUR'),tariff_unit,'net'))
    assert result.money.amount == Decimal(expected)
    assert result.basis == weight.basis


@pytest.mark.parametrize(('weight_amount','rate'), [(None,'2'),('2',None),(None,None)])
def test_missing_weight_or_tariff_not_zero(weight_amount,rate):
    result = freight_from_weight('f',Weight(weight_amount,'kg','batch','gross'),
                                  FreightTariff(Money(rate,'EUR'),'kg','gross'))
    assert result.money.amount is None
    quoted = quote([charge('p','purchase','10'),result,charge('c','commission','0')])
    assert quoted['status']=='unknown' and quoted['total_batch'] is None


def test_billable_kind_mismatch_not_silently_converted():
    with pytest.raises(ValueError,match='Net/gross'):
        freight_from_weight('f',Weight('1','kg','batch','net'),FreightTariff(Money('2','EUR'),'kg','gross'))


@pytest.mark.parametrize('missing_kind',['purchase','freight','commission'])
def test_missing_required_role_unknown(missing_kind):
    records=[charge(kind,kind,'1') for kind in ('purchase','freight','commission') if kind!=missing_kind]
    result=quote(records)
    assert result['status']=='unknown' and result['total_batch'] is None and result['per_unit'] is None
    assert 'charge:'+missing_kind in result['missing']


def test_explicit_zero_commission_and_no_fees_is_complete():
    result=quote([charge('p','purchase','10'),charge('f','freight','0'),charge('c','commission','0')])
    assert result['status']=='known' and result['total_batch']==Decimal('10')


def test_missing_amount_and_incomplete_fees_hide_total_but_label_subtotal():
    result=quote([charge('p','purchase','10'),charge('f','freight',None),charge('c','commission','0')],fees_complete=False)
    assert result['total_batch'] is None and result['per_unit'] is None
    assert result['known_components_subtotal']==Decimal('10')
    assert set(result['missing'])=={'amount:f','fees:incomplete_enumeration'}


@pytest.mark.parametrize('values',[
    [charge('p','purchase','10'),charge('p','fee','1')],
    [charge('p','purchase','10'),charge('p2','purchase','1')],
    [charge('c','commission','1'),charge('f','fee','1',includes=('c',))],
    [charge('bundle1','commission','1',includes=('economic-fee',)),charge('bundle2','fee','1',includes=('economic-fee',))],
])
def test_duplicate_or_included_economic_cost_rejected(values):
    with pytest.raises(ValueError,match='Duplicate|overlapping'):
        quote(values)


def test_commission_bundle_is_counted_once_when_included_fee_not_separate():
    result=quote([charge('p','purchase','10'),charge('f','freight','1'),
                  charge('c','commission','3',includes=('service-fee',))])
    assert result['total_batch']==Decimal('14')
    assert result['components'][2]['included_cost_ids']==('service-fee',)


@pytest.mark.parametrize('rates',[[],[FXRate('EUR','USD','2')],
    [FXRate('USD','GBP','1'),FXRate('GBP','EUR','1')]])
def test_no_missing_inverse_or_triangulated_fx(rates):
    with pytest.raises(ValueError,match='direct FX'):
        quote([charge('p','purchase','1','USD')],fx_rates=rates)


def test_duplicate_fx_pair_rejected_even_if_same_rate():
    with pytest.raises(ValueError,match='Duplicate FX'):
        quote(fx_rates=[FXRate('USD','EUR','1'),FXRate('USD','EUR','1')])


@pytest.mark.parametrize('bad',[True,False,0.1,1,-1,'-1','NaN','Infinity','-Infinity','', ' 1','1 ', 'bad',Decimal('NaN'),Decimal('Infinity')])
def test_invalid_money_decimal_and_weight_values_rejected(bad):
    with pytest.raises(ValueError):Money(bad,'EUR')
    with pytest.raises(ValueError):Weight(bad,'kg','batch','net')


@pytest.mark.parametrize('bad',[0,'0',True,0.5,'-1','NaN','Infinity'])
def test_bad_fx_rejected(bad):
    with pytest.raises(ValueError):FXRate('USD','EUR',bad)


@pytest.mark.parametrize('bad',[0,-1,True,False,Decimal('2'),'2',2.0,10**9+1])
def test_quantity_rejects_non_count_and_returns(bad):
    with pytest.raises(ValueError):quote(quantity=bad)


@pytest.mark.parametrize('kind',['return','credit_note','sale',None])
def test_returns_and_other_transaction_kinds_explicitly_unsupported(kind):
    with pytest.raises(ValueError,match='returns unsupported'):quote(transaction_kind=kind)


@pytest.mark.parametrize('bad',[None,0,'true'])
def test_fee_completeness_not_inferred(bad):
    with pytest.raises(ValueError):quote(fees_complete=bad)


@pytest.mark.parametrize('bad',['eur','EU','EURO','€',' EUR',None])
def test_currency_must_be_explicit_code(bad):
    with pytest.raises(ValueError):Money('1',bad)


@pytest.mark.parametrize('kwargs',[{'basis':'unit'},{'basis':None},{'kind':'unknown'}])
def test_charge_role_and_basis_not_guessed(kwargs):
    values=dict(id='x',kind='fee',money=Money('1','EUR'),basis='batch');values.update(kwargs)
    with pytest.raises(ValueError):Charge(**values)


@pytest.mark.parametrize('kwargs',[{'unit':'гр'},{'unit':'lb'},{'unit':None},{'basis':'pack'},{'kind':None}])
def test_weight_units_basis_and_kind_not_guessed(kwargs):
    values=dict(amount='1',unit='kg',basis='batch',kind='net');values.update(kwargs)
    with pytest.raises(ValueError):Weight(**values)


def test_context_independent_and_inputs_immutable():
    rates=[FXRate('USD','EUR','0.123456789')]
    charges=[charge('p','purchase','3','USD'),charge('f','freight','1'),charge('c','commission','0')]
    before=repr(charges)
    expected=quote(charges,fx_rates=rates,quantity=3)
    with localcontext() as context:
        context.prec=2
        assert quote(charges,fx_rates=rates,quantity=3)==expected
    assert repr(charges)==before


@pytest.mark.parametrize(('actual','expected','threshold','status'),[
    (Weight('250','g','per_unit','net'),Weight('2','kg','batch','net'),'0.25','within_threshold'),
    (Weight('251','g','per_unit','net'),Weight('2','kg','batch','net'),'0.25','flag'),
    (Weight('1','kg','batch','net'),Weight('2','kg','batch','net'),'0.4','flag'),
    (Weight(None,'g','batch','net'),Weight('1','kg','batch','net'),'0.2','unknown'),
    (Weight('1','kg','batch','gross'),Weight('1','kg','batch','net'),'0','unknown'),
    (Weight('1','kg','batch','net'),Weight('0','kg','batch','net'),'0.2','unknown'),
    (Weight('1','kg','batch','net'),Weight('1','kg','batch','net'),None,'unknown'),
])
def test_weight_flag_known_missing_kind_and_threshold(actual,expected,threshold,status):
    assert assess_weight(actual=actual,expected=expected,max_relative_deviation=threshold,quantity=10)['status']==status


@pytest.mark.parametrize(('purchase','freight','threshold','status','ratio'),[
    (charge('p','purchase','2',basis='per_unit'),charge('f','freight','10'),'0.5','within_threshold','0.5'),
    (charge('p','purchase','2',basis='per_unit'),charge('f','freight','11'),'0.5','flag','0.55'),
    (charge('p','purchase',None),charge('f','freight','1'),'0.5','unknown',None),
    (charge('p','purchase','0'),charge('f','freight','1'),'0.5','unknown',None),
    (charge('p','purchase','2'),charge('f','freight','1'),None,'unknown',None),
])
def test_logistics_ratio_uses_explicit_quantity_basis(purchase,freight,threshold,status,ratio):
    result=assess_logistics(purchase=purchase,freight=freight,quantity=10,max_freight_to_purchase_ratio=threshold)
    assert result['status']==status
    assert result['ratio']==(None if ratio is None else Decimal(ratio))


def test_logistics_does_not_guess_currency_or_roles():
    with pytest.raises(ValueError,match='Mixed currencies'):
        assess_logistics(purchase=charge('p','purchase','1','USD'),freight=charge('f','freight','1'),quantity=1,max_freight_to_purchase_ratio='1')
    with pytest.raises(ValueError):
        assess_logistics(purchase=Money('1','EUR'),freight=Money('1','EUR'),quantity=1,max_freight_to_purchase_ratio='1')


@pytest.mark.parametrize(('purchase','freight'),[
    (charge('same','purchase','110'),charge('same','freight','10')),
    (charge('p','purchase','110',includes=('f',)),charge('f','freight','10')),
    (charge('p','purchase','110'),charge('f','freight','10',includes=('p',))),
    (charge('p','purchase','110',includes=('shared',)),charge('f','freight','10',includes=('shared',))),
])
def test_logistics_rejects_shared_or_included_economic_costs(purchase,freight):
    with pytest.raises(ValueError,match='Overlapping purchase/freight'):
        assess_logistics(purchase=purchase,freight=freight,quantity=1,max_freight_to_purchase_ratio='0.5')


@pytest.mark.parametrize('threshold',[True,0.1,'-1','NaN'])
def test_thresholds_must_be_explicit_finite_nonnegative_decimals(threshold):
    with pytest.raises(ValueError):
        assess_weight(actual=Weight('1','kg','batch','net'),expected=Weight('1','kg','batch','net'),quantity=1,max_relative_deviation=threshold)
    with pytest.raises(ValueError):
        assess_logistics(purchase=charge('p','purchase','1'),freight=charge('f','freight','1'),quantity=1,max_freight_to_purchase_ratio=threshold)
