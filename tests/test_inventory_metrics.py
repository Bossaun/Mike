"""Synthetic inventory arithmetic; no application or operational data."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from decimal import Decimal, localcontext

import pytest

from goldway.inventory_metrics import (
    UNKNOWN, CatalogObservation, CoverageState, ItemKey, PurchasePlan,
    RecountState, SalesWindow, StockKey, StockKind, StockSnapshot,
    Thresholds, completeness, coverage, procurement_preview, recount, stock_flags,
)


D = Decimal
DAY = date(2026, 10, 16)
KEY = StockKey(ItemKey('SYNTH-1', 'blue'), 'warehouse-A', 'batch-1')


def stock(units='60', *, key=KEY, kind=StockKind.FREE, **changes):
    value = units if units is UNKNOWN else D(units)
    return replace(StockSnapshot(key, value, 'pieces', DAY, 'synthetic-stock', kind), **changes)


def sales(sold='90', *, days=90, **changes):
    value = sold if sold is UNKNOWN else D(sold)
    return replace(SalesWindow(KEY, value, days, 'pieces', DAY, 'synthetic-sales'), **changes)


def test_explicit_90_day_sales_and_month_convention():
    assert coverage(stock(), sales(), month_days=D('30')).months == D('2')
    assert coverage(stock(), sales(), month_days=D('20')).months == D('3')
    assert coverage(stock(), sales(days=45), month_days=D('30')).months == D('1')


@pytest.mark.parametrize('stock_units,sold,state,reasons', [
    (UNKNOWN, '0', CoverageState.UNKNOWN, ('stock',)),
    ('0', UNKNOWN, CoverageState.UNKNOWN, ('sales',)),
    (UNKNOWN, UNKNOWN, CoverageState.UNKNOWN, ('stock', 'sales')),
    ('10', '0', CoverageState.INFINITE, ()),
    ('0', '0', CoverageState.UNKNOWN, ('zero_stock_and_zero_sales',)),
])
def test_unknown_and_zero_demand_are_distinct(stock_units, sold, state, reasons):
    result = coverage(stock(stock_units), sales(sold), month_days=D('30'))
    assert result.state is state and result.months is None and result.reasons == reasons


def test_actual_zero_stock_is_finite_zero_when_sales_are_known():
    result = coverage(stock('0'), sales(), month_days=D('30'))
    assert result.state is CoverageState.FINITE and result.months == 0


@pytest.mark.parametrize('field,value', [
    ('unit', 'boxes'), ('observed_on', date(2026, 10, 15)),
    ('key', replace(KEY, location='warehouse-B')),
    ('key', replace(KEY, item=ItemKey('SYNTH-1', 'red'))),
])
def test_coverage_never_combines_different_scopes(field, value):
    with pytest.raises(ValueError): coverage(stock(), sales(**{field: value}), month_days=D('30'))


@pytest.mark.parametrize('value', [D('0'), D('-1'), D('NaN'), D('Infinity'), UNKNOWN, 30, 30.0, True])
def test_month_days_must_be_explicit_valid_decimal(value):
    with pytest.raises(ValueError): coverage(stock(), sales(), month_days=value)


@pytest.mark.parametrize('days', [0, -1, True, D('90'), 90.0])
def test_sales_window_rejects_implicit_or_invalid_days(days):
    with pytest.raises(ValueError): sales(days=days)


def test_only_explicit_free_stock_is_used_not_book_or_physical():
    for kind in (StockKind.BOOK, StockKind.PHYSICAL):
        with pytest.raises(ValueError): coverage(stock(kind=kind), sales(), month_days=D('30'))
        with pytest.raises(ValueError): procurement_preview(stock(kind=kind), PurchasePlan(KEY, D('2'), 'pieces', DAY, 'plan'))


def test_flags_have_explicit_thresholds_and_exact_strict_boundaries():
    limits = Thresholds(D('1'), D('3'), 90)
    for amount, low, over in [('15', True, False), ('30', False, False), ('90', False, False), ('120', False, True)]:
        result = stock_flags(stock(amount), sales(), month_days=D('30'), thresholds=limits)
        assert result.low_stock is low and result.overstock is over and result.no_sales is False
    result = stock_flags(stock('9' * 40), sales('1' + '0' * 40, days=30),
                         month_days=D('30'), thresholds=limits)
    assert result.coverage.months == D('1') and result.low_stock is True


def test_no_sales_requires_observed_zero_and_explicit_minimum_window():
    limits = Thresholds(D('1'), D('3'), 90)
    assert stock_flags(stock(), sales('0'), month_days=D('30'), thresholds=limits).no_sales is True
    assert stock_flags(stock(), sales('0', days=89), month_days=D('30'), thresholds=limits).no_sales is UNKNOWN
    assert stock_flags(stock(), sales(UNKNOWN), month_days=D('30'), thresholds=limits).no_sales is UNKNOWN
    flags = stock_flags(stock(), sales('0'), month_days=D('30'), thresholds=limits)
    assert flags.coverage.state is CoverageState.INFINITE and flags.overstock is True and flags.low_stock is False
    flags = stock_flags(stock(UNKNOWN), sales(), month_days=D('30'), thresholds=limits)
    assert flags.low_stock is UNKNOWN and flags.overstock is UNKNOWN


@pytest.mark.parametrize('low,over,days', [('1', '1', 90), ('2', '1', 90), ('-1', '3', 90), ('1', '3', 0)])
def test_invalid_thresholds_rejected(low, over, days):
    with pytest.raises(ValueError): Thresholds(D(low), D(over), days)


@pytest.mark.parametrize('value', [D('-0.1'), D('NaN'), D('sNaN'), D('Infinity'), 2, 2.1, True, None])
def test_invalid_snapshot_and_sales_quantities_rejected(value):
    with pytest.raises(ValueError): StockSnapshot(KEY, value, 'pieces', DAY, 'source', StockKind.FREE)
    with pytest.raises(ValueError): SalesWindow(KEY, value, 90, 'pieces', DAY, 'source')
    with pytest.raises(ValueError): PurchasePlan(KEY, value, 'pieces', DAY, 'source')


def test_explicit_provenance_date_units_and_identity_required():
    for field, value in [('unit', ''), ('source_id', ''), ('observed_on', datetime(2026, 10, 16))]:
        with pytest.raises(ValueError): stock(**{field: value})
    for sku in ('', ' SKU '):
        with pytest.raises(ValueError): ItemKey(sku)
    assert StockKey(ItemKey('SYNTH-unknown')).location is UNKNOWN
    assert StockKey(ItemKey('SYNTH-unknown')).item.variant is UNKNOWN
    with pytest.raises(TypeError): bool(UNKNOWN)
    with pytest.raises(FrozenInstanceError): stock().units = D('0')


@pytest.mark.parametrize('physical,expected', [('12.125', '2.125'), ('8.5', '-1.5'), ('10', '0')])
def test_signed_recount_is_physical_minus_book(physical, expected):
    book = stock('10', kind=StockKind.BOOK, source_id='book')
    counted = stock(physical, kind=StockKind.PHYSICAL, source_id='count')
    row, = recount([book], [counted])
    assert row.delta == D(expected) and row.state is RecountState.COMPARABLE
    assert row.key == KEY and row.book_source == 'book' and row.physical_source == 'count'


def test_missing_rows_and_unobserved_counts_never_become_zero():
    book = stock(kind=StockKind.BOOK)
    physical = stock(UNKNOWN, kind=StockKind.PHYSICAL)
    row, = recount([book], [physical])
    assert row.delta is UNKNOWN and row.physical_units is UNKNOWN and row.state is RecountState.UNKNOWN_QUANTITY
    row, = recount([book], [])
    assert row.physical_units is UNKNOWN and row.delta is UNKNOWN and row.state is RecountState.MISSING_PHYSICAL
    row, = recount([], [physical])
    assert row.book_units is UNKNOWN and row.delta is UNKNOWN and row.state is RecountState.MISSING_BOOK


@pytest.mark.parametrize('changed', [
    replace(KEY, item=ItemKey('OTHER-SKU', 'blue')), replace(KEY, item=ItemKey('SYNTH-1', 'red')),
    replace(KEY, location='warehouse-B'), replace(KEY, batch='batch-2'),
])
def test_recount_requires_exact_sku_variant_location_and_batch(changed):
    result = recount([stock(kind=StockKind.BOOK)], [stock(key=changed, kind=StockKind.PHYSICAL)])
    assert len(result) == 2 and all(row.delta is UNKNOWN for row in result)


@pytest.mark.parametrize('key', [replace(KEY, location=UNKNOWN), replace(KEY, batch=UNKNOWN),
                               replace(KEY, item=ItemKey('SYNTH-1'))])
def test_unknown_dimensions_do_not_authorize_a_recount_join(key):
    row, = recount([stock(key=key, kind=StockKind.BOOK)], [stock(key=key, kind=StockKind.PHYSICAL)])
    assert row.delta is UNKNOWN and row.state is RecountState.UNRESOLVED_KEY


@pytest.mark.parametrize('key', [replace(KEY, location=UNKNOWN), replace(KEY, batch=UNKNOWN),
                               replace(KEY, item=ItemKey('SYNTH-1'))])
def test_matching_unknown_dimensions_never_authorize_coverage_or_procurement(key):
    current = stock(key=key)
    observed_sales = sales(key=key)
    result = coverage(current, observed_sales, month_days=D('30'))
    assert result.state is CoverageState.UNKNOWN and result.months is None and result.reasons == ('scope',)
    flags = stock_flags(current, observed_sales, month_days=D('30'), thresholds=Thresholds(D('1'), D('3'), 90))
    assert flags.low_stock is UNKNOWN and flags.overstock is UNKNOWN and flags.no_sales is UNKNOWN
    plan = PurchasePlan(key, D('2'), 'pieces', DAY, 'request')
    assert procurement_preview(current, plan).expected_free_units is UNKNOWN


@pytest.mark.parametrize('value', [D('1E+19'), D('1E-19'), D('1' * 51), D('1E+1000000000')])
def test_decimal_metadata_bounds_reject_before_fraction_or_exact_arithmetic(value, monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('Invalid metadata reached arithmetic')
    monkeypatch.setattr('goldway.inventory_metrics.Fraction', forbidden)
    monkeypatch.setattr('goldway.inventory_metrics._exact_sum', forbidden)
    with pytest.raises(ValueError): StockSnapshot(KEY, value, 'pieces', DAY, 'source', StockKind.FREE)
    with pytest.raises(ValueError): SalesWindow(KEY, value, 90, 'pieces', DAY, 'source')
    with pytest.raises(ValueError): PurchasePlan(KEY, value, 'pieces', DAY, 'source')
    with pytest.raises(ValueError): Thresholds(D('1'), value, 90)
    with pytest.raises(ValueError): coverage(stock(), sales(), month_days=value)


def test_supported_decimal_boundary_and_explicit_window_bounds():
    assert stock('1E+18').units == D('1E+18')
    assert stock('1E-18').units == D('1E-18')
    assert stock('1' * 50).units == D('1' * 50)
    assert sales(days=36600).days == 36600
    with pytest.raises(ValueError): sales(days=36601)
    with pytest.raises(ValueError): Thresholds(D('1'), D('3'), 36601)
    with pytest.raises(ValueError): coverage(stock(), sales(), month_days=D('36601'))


@pytest.mark.parametrize('consumer', ['book', 'physical', 'catalog'])
def test_observation_iterables_are_bounded_before_full_materialization(consumer):
    consumed = []
    def observations():
        for number in range(1002):
            consumed.append(number)
            item = ItemKey(f'BOUNDED-{number}', 'blue')
            if consumer == 'catalog':
                yield CatalogObservation(item, False, None, DAY, 'source')
            else:
                kind = StockKind.BOOK if consumer == 'book' else StockKind.PHYSICAL
                yield stock(key=StockKey(item, 'warehouse-A', 'batch-1'), kind=kind)
    with pytest.raises(ValueError):
        if consumer == 'catalog': completeness(observations())
        elif consumer == 'book': recount(observations(), [])
        else: recount([], observations())
    assert len(consumed) == 1001


def test_recount_rejects_duplicates_mixed_units_dates_and_wrong_kinds():
    book, physical = stock(kind=StockKind.BOOK), stock(kind=StockKind.PHYSICAL)
    for books, counts in [([book, book], [physical]), ([book], [physical, physical]),
        ([book], [replace(physical, unit='boxes')]), ([book], [replace(physical, observed_on=date(2026, 10, 15))]),
        ([stock()], [physical])]:
        with pytest.raises(ValueError): recount(books, counts)


def test_catalog_zero_price_is_present_unknown_and_explicit_missing_are_separate():
    one, two, three = ItemKey('ONE', 'blue'), ItemKey('TWO', 'red'), ItemKey('THREE', 'green')
    observations = [CatalogObservation(one, False, D('0'), DAY, 'catalog'),
        CatalogObservation(two, True, None, DAY, 'catalog'),
        CatalogObservation(three, UNKNOWN, UNKNOWN, DAY, 'catalog')]
    result = completeness(observations)
    assert result.missing_photo_count == 1 and result.missing_photos == (one,)
    assert result.missing_price_count == 1 and result.missing_prices == (two,)
    assert result.unknown_photos == (three,) and result.unknown_prices == (three,)
    with pytest.raises(ValueError): completeness([observations[0], observations[0]])
    with pytest.raises(ValueError): completeness([observations[0], replace(observations[1], observed_on=date(2026, 10, 15))])
    with pytest.raises(ValueError): CatalogObservation(one, None, D('0'), DAY, 'catalog')
    with pytest.raises(ValueError): CatalogObservation(one, False, False, DAY, 'catalog')


def test_procurement_preview_exact_arithmetic_and_preserved_inputs():
    current = stock('1.234567890123456789')
    plan = PurchasePlan(KEY, D('0.000000000000000001'), 'pieces', DAY, 'request')
    with localcontext() as context:
        context.prec = 2
        preview = procurement_preview(current, plan)
        row, = recount([stock('10.123456789', kind=StockKind.BOOK)], [stock('10.123456790', kind=StockKind.PHYSICAL)])
    assert preview.expected_free_units == D('1.234567890123456790')
    assert preview.stock is current and preview.plan is plan and row.delta == D('0.000000001')
    assert current.units == D('1.234567890123456789')


@pytest.mark.parametrize('stock_units,planned', [(UNKNOWN, D('2')), ('2', UNKNOWN)])
def test_procurement_unknown_current_or_buy_quantity_remains_unknown(stock_units, planned):
    plan = PurchasePlan(KEY, planned, 'pieces', DAY, 'request')
    assert procurement_preview(stock(stock_units), plan).expected_free_units is UNKNOWN


@pytest.mark.parametrize('field,value', [('unit', 'kg'), ('key', replace(KEY, batch='next-batch')),
                                      ('observed_on', date(2026, 10, 17))])
def test_procurement_never_converts_units_joins_other_batch_or_guesses_date(field, value):
    plan = replace(PurchasePlan(KEY, D('2'), 'pieces', DAY, 'request'), **{field: value})
    with pytest.raises(ValueError): procurement_preview(stock(), plan)


def test_calculations_never_open_files_call_network_or_read_wall_clock(monkeypatch):
    import builtins
    import socket
    import time
    def forbidden(*args, **kwargs): pytest.fail('Pure arithmetic attempted an external operation')
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(time, 'time', forbidden)
    assert coverage(stock(), sales(), month_days=D('30')).months == D('2')
    assert stock_flags(stock(), sales(), month_days=D('30'), thresholds=Thresholds(D('1'), D('3'), 90)).no_sales is False
    assert recount([stock(kind=StockKind.BOOK)], [stock(kind=StockKind.PHYSICAL)])[0].delta == 0
    assert completeness([CatalogObservation(KEY.item, False, None, DAY, 'catalog')]).missing_price_count == 1
    assert procurement_preview(stock(), PurchasePlan(KEY, D('2'), 'pieces', DAY, 'request')).expected_free_units == 62
