"""Finite synthetic joins/ROI/report candidates without running application code."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

import pytest

from goldway.influencer_metrics import (
    BarterState, Binding, Click, Money, Order, PlacementReport, Refund, Shipment,
    Window, classify_barter, evaluate, report_due_candidates,
)

D = Decimal
UTC = timezone.utc


def at(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def money(value, currency='RUB'):
    return Money(None if value is None else D(value), currency)


def inputs(**changes):
    values = dict(influencer_id='person-A', window=Window(at(1), datetime(2026, 11, 1, 12, tzinfo=UTC)),
        currency='RUB', aliases=(Binding('@alice', 'person-A', True, 'alias-a'),
            Binding('@alice.second', 'person-A', True, 'alias-a2'), Binding('@al1ce', 'person-B', True, 'alias-b')),
        promos=(Binding('A1', 'person-A', True, 'promo-a'), Binding('A2', 'person-A', True, 'promo-a2'),
            Binding('B1', 'person-B', True, 'promo-b')),
        shipments=(Shipment('ship', '@alice', at(5), True, money('10'), 'shipment-source'),),
        clicks=(Click('click', 'A1', at(6), 'click-source'),),
        orders=(Order('order', ('A1', 'A2', 'A1'), '@alice.second', at(7), money('100'), money('40'), 'order-source'),),
        refunds=(Refund('refund', 'order', at(10), money('20'), money('8'), 'refund-source'),),
        reports=(), records_complete=True, barter_limit=money('10'))
    values.update(changes)
    return values


def test_join_refunds_net_margin_roi_and_exact_sources():
    result = evaluate(**inputs())
    assert result.order_ids == ('order',) and result.click_ids == ('click',) and result.shipment_ids == ('ship',)
    assert result.refund_ids == ('refund',) and result.barter_states == (('ship', BarterState.CONFIRMED),)
    assert result.financials.state == 'known'
    assert (result.financials.net_revenue, result.financials.margin_after_refunds,
        result.financials.barter_cost, result.financials.net_after_barter, result.financials.roi) == tuple(map(D, ('80', '32', '10', '22', '2.2')))
    assert result.limit_state == 'within_limit' and result.unmeasurable_barter_ids == ()
    assert all(source in result.source_ids for source in ('shipment-source', 'order-source', 'refund-source', 'alias-a2', 'promo-a2'))


def test_many_codes_and_repeated_identical_rows_never_duplicate_orders_refunds_or_clicks():
    values = inputs()
    for field in ('orders', 'refunds', 'clicks', 'shipments'):
        values[field] *= 3
    result = evaluate(**values)
    assert len(result.order_ids) == len(result.refund_ids) == len(result.shipment_ids) == len(result.click_ids) == 1
    assert result.financials.net_after_barter == D('22')


def test_similar_aliases_are_different_people_without_fuzzy_or_case_merging():
    other = Order('other', ('B1',), '@al1ce', at(8), money('200'), money('70'), 'other-source')
    values = inputs(); values['orders'] += (other,)
    assert evaluate(**values).order_ids == ('order',)
    values['influencer_id'] = 'person-B'
    result = evaluate(**values)
    assert result.order_ids == ('other',) and result.financials.net_revenue == 200
    values = inputs(shipments=(replace(inputs()['shipments'][0], recipient_alias='@ALICE'),))
    result = evaluate(**values)
    assert result.shipment_ids == () and result.unattributed_shipment_ids == ('ship',)
    assert result.financials.state == 'unknown'


@pytest.mark.parametrize('codes,alias', [(('A1', 'B1'), None), (('A1', 'MISSING'), None), ((), None), (('A1',), '@al1ce')])
def test_unresolved_or_conflicting_attribution_does_not_credit_either_person(codes, alias):
    order = replace(inputs()['orders'][0], promo_codes=codes, referral_alias=alias)
    result = evaluate(**inputs(orders=(order,)))
    assert result.order_ids == () and result.unattributed_order_ids == ('order',)
    assert result.unattributed_refund_ids == ('refund',) and result.financials.roi is None


@pytest.mark.parametrize('field', ['orders', 'aliases', 'promos', 'refunds'])
def test_conflicting_duplicate_ids_are_rejected_instead_of_merged(field):
    values = inputs()
    row = values[field][0]
    if field == 'orders': changed = replace(row, revenue_before_refunds=money('101'))
    elif field in ('aliases', 'promos'): changed = replace(row, influencer_id='person-B')
    else: changed = replace(row, revenue_reduction=money('21'))
    values[field] += (changed,)
    with pytest.raises(ValueError, match='Conflicting duplicate'): evaluate(**values)


def test_unconfirmed_bindings_do_not_authorize_identity():
    values = inputs(aliases=(Binding('@alice', 'person-A', False, 'candidate'),))
    result = evaluate(**values)
    assert result.unattributed_shipment_ids == ('ship',) and result.financials.state == 'unknown'


def test_zero_order_is_not_barter_without_confirmation():
    order = replace(inputs()['orders'][0], revenue_before_refunds=money('0'), margin_before_refunds_and_barter=money('0'))
    ship = replace(inputs()['shipments'][0], barter_confirmed=None, barter_cost=money('0'))
    result = evaluate(**inputs(orders=(order,), refunds=(), shipments=(ship,)))
    assert classify_barter(None) is BarterState.UNKNOWN and result.barter_states == (('ship', BarterState.UNKNOWN),)
    assert result.financials.state == 'unknown' and result.limit_state == 'unknown'
    explicit = evaluate(**inputs(shipments=(replace(ship, barter_confirmed=False),), refunds=(), orders=(order,)))
    assert explicit.barter_states == (('ship', BarterState.NOT_BARTER),)
    assert explicit.financials.state == 'zero_denominator' and explicit.financials.roi is None


def test_measurability_requires_confirmed_tracking_or_current_result_report():
    values = inputs(promos=(), orders=(), refunds=(), clicks=())
    assert evaluate(**values).unmeasurable_barter_ids == ('ship',)
    report = PlacementReport('report', 'ship', at(8), True, 'report-source')
    assert evaluate(**{**values, 'reports': (report,)}).unmeasurable_barter_ids == ()
    withdrawn = PlacementReport('withdrawn', 'ship', at(9), False, 'withdrawn-source')
    assert evaluate(**{**values, 'reports': (report, withdrawn)}).unmeasurable_barter_ids == ('ship',)
    future = replace(report, observed_at=values['window'].end)
    assert evaluate(**{**values, 'reports': (future,)}).unmeasurable_barter_ids == ('ship',)
    assert evaluate(**inputs()).unmeasurable_barter_ids == ()


def test_seven_day_exact_boundary_and_report_presence_only_returns_candidates():
    ship = inputs()['shipments'][0]
    due = ship.shipped_at + timedelta(days=7)
    assert report_due_candidates([ship], [], now=due - timedelta(microseconds=1)) == ()
    candidate, = report_due_candidates([ship], [], now=due)
    assert candidate.shipment_id == 'ship' and candidate.due_at == due and candidate.recipient_alias == '@alice'
    present = PlacementReport('r', 'ship', due, True, 'report')
    assert report_due_candidates([ship], [present], now=due) == ()
    assert len(report_due_candidates([ship], [replace(present, result_present=False)], now=due)) == 1
    assert len(report_due_candidates([ship], [replace(present, observed_at=due + timedelta(seconds=1))], now=due)) == 1


def test_seven_days_are_elapsed_utc_hours_across_dst_not_host_calendar():
    zone = ZoneInfo('America/New_York')
    ship = replace(inputs()['shipments'][0], shipped_at=datetime(2026, 3, 1, 12, tzinfo=zone))
    assert report_due_candidates([ship], [], now=datetime(2026, 3, 8, 12, 30, tzinfo=zone)) == ()
    candidate, = report_due_candidates([ship], [], now=datetime(2026, 3, 8, 13, tzinfo=zone))
    assert candidate.due_at == datetime(2026, 3, 8, 17, tzinfo=UTC)


def test_invalid_report_links_chronology_and_simultaneous_conflict_rejected():
    ship = inputs()['shipments'][0]
    report = PlacementReport('r', 'ship', at(8), True, 'report')
    for reports in [(replace(report, shipment_id='absent'),), (replace(report, observed_at=at(4)),),
                    (report, replace(report, id='conflict', result_present=False))]:
        with pytest.raises(ValueError): report_due_candidates([ship], reports, now=at(15))
    with pytest.raises(ValueError): report_due_candidates([ship], [], now=datetime(2026, 10, 15))


@pytest.mark.parametrize('field', ['revenue', 'margin', 'refund_revenue', 'refund_margin', 'cost'])
def test_missing_amounts_never_produce_complete_roi_and_budget_is_independent(field):
    values = inputs()
    if field == 'revenue': values['orders'] = (replace(values['orders'][0], revenue_before_refunds=money(None)),)
    if field == 'margin': values['orders'] = (replace(values['orders'][0], margin_before_refunds_and_barter=money(None)),)
    if field == 'refund_revenue': values['refunds'] = (replace(values['refunds'][0], revenue_reduction=money(None)),)
    if field == 'refund_margin': values['refunds'] = (replace(values['refunds'][0], margin_reduction=money(None)),)
    if field == 'cost': values['shipments'] = (replace(values['shipments'][0], barter_cost=money(None)),)
    result = evaluate(**values)
    assert result.financials.state == 'unknown' and result.financials.roi is None
    assert result.limit_state == ('unknown' if field == 'cost' else 'within_limit')


@pytest.mark.parametrize('field', ['revenue', 'margin', 'refund', 'cost'])
def test_currency_conflict_never_converts_or_adds_unlike_money(field):
    values = inputs()
    if field == 'revenue': values['orders'] = (replace(values['orders'][0], revenue_before_refunds=money('100', 'USD')),)
    if field == 'margin': values['orders'] = (replace(values['orders'][0], margin_before_refunds_and_barter=money('40', 'USD')),)
    if field == 'refund': values['refunds'] = (replace(values['refunds'][0], revenue_reduction=money('20', 'USD')),)
    if field == 'cost': values['shipments'] = (replace(values['shipments'][0], barter_cost=money('10', 'USD')),)
    result = evaluate(**values)
    assert result.financials.state == 'unknown' and 'currency_conflict' in result.financials.reasons
    assert result.financials.net_after_barter is None and result.financials.roi is None


def test_negative_margin_and_explicit_zero_denominator_states():
    order = replace(inputs()['orders'][0], margin_before_refunds_and_barter=money('-3'))
    result = evaluate(**inputs(orders=(order,)))
    assert result.financials.net_after_barter == D('-21') and result.financials.roi == D('-2.1')
    zero = replace(inputs()['shipments'][0], barter_cost=money('0'))
    result = evaluate(**inputs(shipments=(zero,)))
    assert result.financials.state == 'zero_denominator' and result.financials.net_after_barter == 32 and result.financials.roi is None
    result = evaluate(**inputs(records_complete=False))
    assert result.financials.state == 'unknown' and result.financials.barter_cost is None and result.limit_state == 'unknown'


def test_refund_of_older_order_reduces_this_period_without_repeating_revenue():
    old = replace(inputs()['orders'][0], occurred_at=datetime(2026, 9, 10, tzinfo=UTC), source_id='older-order-source')
    result = evaluate(**inputs(orders=(old,), shipments=()))
    assert result.order_ids == () and result.refund_ids == ('refund',)
    assert result.financials.net_revenue == -20 and result.financials.margin_after_refunds == -8
    assert 'older-order-source' in result.source_ids


def test_refund_unknown_link_and_excess_or_predating_refund_are_not_silently_ignored():
    refund = inputs()['refunds'][0]
    result = evaluate(**inputs(refunds=(replace(refund, order_id='absent'),)))
    assert result.unattributed_refund_ids == ('refund',) and result.financials.state == 'unknown'
    for changed in (replace(refund, occurred_at=at(6)), replace(refund, revenue_reduction=money('101'))):
        with pytest.raises(ValueError): evaluate(**inputs(refunds=(changed,)))


def test_explicit_limit_and_half_open_window_only_no_hardcoded_budget():
    old = replace(inputs()['shipments'][0], id='old', shipped_at=datetime(2026, 9, 30, tzinfo=UTC), barter_cost=money('999'))
    end = replace(inputs()['shipments'][0], id='end', shipped_at=inputs()['window'].end, barter_cost=money('999'))
    values = inputs(); values['shipments'] += (old, end)
    assert evaluate(**values).financials.barter_cost == 10
    assert evaluate(**{**values, 'barter_limit': money('9')}).limit_state == 'exceeded'
    assert evaluate(**{**values, 'barter_limit': None}).limit_state == 'unknown'
    assert evaluate(**{**values, 'barter_limit': money('10', 'USD')}).limit_state == 'unknown'
    assert evaluate(**{**values, 'barter_limit': money(None)}).limit_state == 'unknown'
    unknown_click = Click('unknown', 'MISSING', at(10), 'unknown-source')
    assert evaluate(**inputs(clicks=(unknown_click,))).unattributed_click_ids == ('unknown',)


@pytest.mark.parametrize('value', [True, 1, 1.2, '1', D('NaN'), D('Infinity'), D('1E+1000000000'), D('1E-19'), D('1' * 51)])
def test_money_bounds_before_arithmetic(value):
    with pytest.raises(ValueError): Money(value, 'RUB')


def test_negative_non_margin_money_and_implicit_identity_or_time_are_rejected():
    values = inputs()
    for build in (lambda: replace(values['orders'][0], revenue_before_refunds=money('-1')),
                  lambda: replace(values['shipments'][0], barter_cost=money('-1')),
                  lambda: replace(values['refunds'][0], margin_reduction=money('-1')),
                  lambda: Binding(' ', 'person-A', True, 'source'),
                  lambda: replace(values['shipments'][0], shipped_at=datetime(2026, 10, 5)),
                  lambda: replace(values['shipments'][0], barter_confirmed='yes'),
                  lambda: replace(values['shipments'][0], barter_confirmed=False)):
        with pytest.raises(ValueError): build()
    with pytest.raises(ValueError): evaluate(**inputs(barter_limit=money('-1')))
    with pytest.raises(ValueError): evaluate(**inputs(records_complete=None))


def test_record_inputs_are_bounded_even_when_duplicates_would_dedupe():
    consumed = []
    order = inputs()['orders'][0]
    def rows():
        for number in range(1002):
            consumed.append(number)
            yield order
    with pytest.raises(ValueError): evaluate(**inputs(orders=rows()))
    assert len(consumed) == 1001
    with pytest.raises(ValueError): replace(order, promo_codes=('A1',) * 1001)


def test_context_independent_immutable_and_no_external_operations(monkeypatch):
    import builtins
    import socket
    import time
    values = inputs()
    expected = evaluate(**values)
    def forbidden(*args, **kwargs): pytest.fail('Pure metrics attempted external operation')
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(time, 'time', forbidden)
    with localcontext() as context:
        context.prec = 2
        assert evaluate(**values) == expected
    with pytest.raises(FrozenInstanceError): values['orders'][0].id = 'changed'
