"""Pure inventory calculations on explicit, immutable observations.

No imports of the running application, storage, network, model or clock.
UNKNOWN is unobserved, not zero. Flags describe supplied inputs, not approval
to move stock, start a warehouse recount or place a purchase.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from enum import Enum
from fractions import Fraction
from typing import Iterable


class Unknown(Enum):
    UNKNOWN = 'unknown'

    def __bool__(self):
        raise TypeError('UNKNOWN has no truth value; inspect it explicitly')


UNKNOWN = Unknown.UNKNOWN
Quantity = Decimal | Unknown
Dimension = str | Unknown
_MAX_DIGITS = 50
_MAX_EXPONENT = 18
_MAX_DAYS = 36600
_MAX_RECORDS = 1000


def _label(value, field):
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f'{field} must be an explicit nonblank label')


def _dimension(value, field):
    if value is not UNKNOWN:
        _label(value, field)


def _quantity(value, field):
    if value is UNKNOWN:
        return
    if (not isinstance(value, Decimal) or not value.is_finite() or value < 0
            or len(value.as_tuple().digits) > _MAX_DIGITS
            or abs(value.as_tuple().exponent) > _MAX_EXPONENT):
        raise ValueError(f'{field} requires a finite nonnegative Decimal or UNKNOWN')


def _observation(observed_on, source_id):
    if type(observed_on) is not date:
        raise ValueError('observed_on requires an explicit date, not a clock timestamp')
    _label(source_id, 'source_id')


def _days(value, field):
    if type(value) is not int or not 1 <= value <= _MAX_DAYS:
        raise ValueError(f'{field} requires explicit integer days in the supported range')


def _records(values, cls):
    result = []
    for value in values:
        if len(result) >= _MAX_RECORDS or not isinstance(value, cls):
            raise ValueError('Invalid or excessive observation records')
        result.append(value)
    return tuple(result)


def _known_key(key):
    return all(value is not UNKNOWN for value in (key.item.variant, key.location, key.batch))


@dataclass(frozen=True)
class ItemKey:
    sku: str
    variant: Dimension = UNKNOWN

    def __post_init__(self):
        _label(self.sku, 'sku')
        _dimension(self.variant, 'variant')


@dataclass(frozen=True)
class StockKey:
    item: ItemKey
    location: Dimension = UNKNOWN
    batch: Dimension = UNKNOWN

    def __post_init__(self):
        if not isinstance(self.item, ItemKey):
            raise ValueError('StockKey requires an explicit ItemKey')
        _dimension(self.location, 'location')
        _dimension(self.batch, 'batch')


class StockKind(Enum):
    FREE = 'free'
    BOOK = 'book'
    PHYSICAL = 'physical'


@dataclass(frozen=True)
class StockSnapshot:
    key: StockKey
    units: Quantity
    unit: str
    observed_on: date
    source_id: str
    kind: StockKind

    def __post_init__(self):
        if not isinstance(self.key, StockKey) or not isinstance(self.kind, StockKind):
            raise ValueError('Snapshot requires a StockKey and explicit StockKind')
        _quantity(self.units, 'units')
        _label(self.unit, 'unit')
        _observation(self.observed_on, self.source_id)


@dataclass(frozen=True)
class SalesWindow:
    key: StockKey
    sold_units: Quantity
    days: int
    unit: str
    observed_on: date
    source_id: str

    def __post_init__(self):
        if not isinstance(self.key, StockKey):
            raise ValueError('Sales window requires a StockKey')
        _quantity(self.sold_units, 'sold_units')
        _days(self.days, 'Sales window')
        _label(self.unit, 'unit')
        _observation(self.observed_on, self.source_id)


class CoverageState(Enum):
    FINITE = 'finite'
    INFINITE = 'positive_stock_zero_sales'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Coverage:
    state: CoverageState
    months: Decimal | None
    reasons: tuple[str, ...] = ()


def _same_scope(left, right):
    if left.key != right.key or left.unit != right.unit or left.observed_on != right.observed_on:
        raise ValueError('Observations require exactly matching key, unit and observed date')
    return _known_key(left.key)


def _coverage_ratio(stock, sales, month_days):
    if stock.kind is not StockKind.FREE:
        raise ValueError('Coverage requires explicit free stock; no reservations are inferred')
    scope_known = _same_scope(stock, sales)
    _quantity(month_days, 'month_days')
    if month_days is UNKNOWN or not 0 < month_days <= _MAX_DAYS:
        raise ValueError('month_days must be an explicit positive Decimal')
    if not scope_known or stock.units is UNKNOWN or sales.sold_units is UNKNOWN or sales.sold_units == 0:
        return None
    return Fraction(stock.units) * sales.days / (Fraction(sales.sold_units) * Fraction(month_days))


def coverage(stock: StockSnapshot, sales: SalesWindow, *, month_days: Decimal) -> Coverage:
    """Stock / observed sales rate; finite display uses 28 significant digits.

    days and month_days are supplied explicitly. No 90-day/month convention,
    missing-stock zero, future demand or location is supplied by this function.
    """
    ratio = _coverage_ratio(stock, sales, month_days)
    missing = tuple(name for name, value in (('stock', stock.units), ('sales', sales.sold_units)) if value is UNKNOWN)
    if not _known_key(stock.key):
        missing += ('scope',)
    if missing:
        return Coverage(CoverageState.UNKNOWN, None, missing)
    if sales.sold_units == 0:
        return (Coverage(CoverageState.INFINITE, None) if stock.units > 0 else
                Coverage(CoverageState.UNKNOWN, None, ('zero_stock_and_zero_sales',)))
    with localcontext() as context:
        context.prec = 28
        months = Decimal(ratio.numerator) / Decimal(ratio.denominator)
    return Coverage(CoverageState.FINITE, months)


@dataclass(frozen=True)
class Thresholds:
    low_months: Decimal
    overstock_months: Decimal
    no_sales_days: int

    def __post_init__(self):
        for field in ('low_months', 'overstock_months'):
            _quantity(getattr(self, field), field)
            if getattr(self, field) is UNKNOWN:
                raise ValueError('Thresholds must be explicitly configured')
        if self.low_months >= self.overstock_months:
            raise ValueError('low_months must be below overstock_months')
        _days(self.no_sales_days, 'no_sales_days')


@dataclass(frozen=True)
class StockFlags:
    coverage: Coverage
    low_stock: bool | Unknown
    overstock: bool | Unknown
    no_sales: bool | Unknown


def stock_flags(stock: StockSnapshot, sales: SalesWindow, *, month_days: Decimal,
                thresholds: Thresholds) -> StockFlags:
    result = coverage(stock, sales, month_days=month_days)
    low = over = UNKNOWN
    if result.state is CoverageState.FINITE:
        ratio = _coverage_ratio(stock, sales, month_days)
        low, over = ratio < Fraction(thresholds.low_months), ratio > Fraction(thresholds.overstock_months)
    elif result.state is CoverageState.INFINITE:
        low, over = False, True
    no_sales = (UNKNOWN if not _known_key(sales.key) or sales.sold_units is UNKNOWN else False if sales.sold_units > 0 else
                True if sales.days >= thresholds.no_sales_days else UNKNOWN)
    return StockFlags(result, low, over, no_sales)


class RecountState(Enum):
    COMPARABLE = 'comparable'
    MISSING_BOOK = 'missing_book'
    MISSING_PHYSICAL = 'missing_physical'
    UNKNOWN_QUANTITY = 'unknown_quantity'
    UNRESOLVED_KEY = 'unresolved_key'


@dataclass(frozen=True)
class RecountLine:
    key: StockKey
    unit: str
    observed_on: date
    book_units: Quantity
    physical_units: Quantity
    delta: Quantity
    state: RecountState
    book_source: str | Unknown
    physical_source: str | Unknown


def _exact_sum(left, right):
    # Addition/subtraction must not depend on the caller's Decimal precision.
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted()) - min(left.as_tuple().exponent, right.as_tuple().exponent) + 2
        return left + right


def recount(book: Iterable[StockSnapshot], physical: Iterable[StockSnapshot]) -> tuple[RecountLine, ...]:
    """Signed physical-minus-book. Unknown dimensions never authorize a join.

    A recount sample must use one explicit unit and date. Unmatched rows and
    unobserved physical quantities remain UNKNOWN; neither becomes zero.
    """
    def index(rows, kind):
        result = {}
        for row in _records(rows, StockSnapshot):
            if row.kind is not kind:
                raise ValueError('Recount requires explicit book and physical snapshots')
            if row.key in result:
                raise ValueError('Duplicate snapshot key; aggregation must be explicit')
            result[row.key] = row
        return result
    books, counts = index(book, StockKind.BOOK), index(physical, StockKind.PHYSICAL)
    rows = [*books.values(), *counts.values()]
    if len({row.unit for row in rows}) > 1 or len({row.observed_on for row in rows}) > 1:
        raise ValueError('Recount cannot mix units or observation dates')
    result = []
    for key in dict.fromkeys([*books, *counts]):
        b, p = books.get(key), counts.get(key)
        observed = b if b is not None else p
        delta = UNKNOWN
        if b is None:
            state = RecountState.MISSING_BOOK
        elif p is None:
            state = RecountState.MISSING_PHYSICAL
        elif b.units is UNKNOWN or p.units is UNKNOWN:
            state = RecountState.UNKNOWN_QUANTITY
        elif not _known_key(key):
            state = RecountState.UNRESOLVED_KEY
        else:
            state, delta = RecountState.COMPARABLE, _exact_sum(p.units, b.units.copy_negate())
        result.append(RecountLine(key, observed.unit, observed.observed_on,
            b.units if b is not None else UNKNOWN, p.units if p is not None else UNKNOWN,
            delta, state, b.source_id if b is not None else UNKNOWN, p.source_id if p is not None else UNKNOWN))
    return tuple(result)


@dataclass(frozen=True)
class CatalogObservation:
    item: ItemKey
    photo_present: bool | Unknown
    price: Decimal | None | Unknown
    observed_on: date
    source_id: str

    def __post_init__(self):
        if not isinstance(self.item, ItemKey) or (type(self.photo_present) is not bool and self.photo_present is not UNKNOWN):
            raise ValueError('Catalog photo requires explicit True, False or UNKNOWN')
        if self.price is not None:
            _quantity(self.price, 'price')
        _observation(self.observed_on, self.source_id)


@dataclass(frozen=True)
class Completeness:
    missing_photos: tuple[ItemKey, ...]
    missing_prices: tuple[ItemKey, ...]
    unknown_photos: tuple[ItemKey, ...]
    unknown_prices: tuple[ItemKey, ...]

    @property
    def missing_photo_count(self):
        return len(self.missing_photos)

    @property
    def missing_price_count(self):
        return len(self.missing_prices)


def completeness(items: Iterable[CatalogObservation]) -> Completeness:
    rows = _records(items, CatalogObservation)
    if len({row.item for row in rows}) != len(rows):
        raise ValueError('Duplicate SKU/variant catalog observation')
    if len({row.observed_on for row in rows}) > 1:
        raise ValueError('Catalog sample cannot mix observation dates')
    return Completeness(tuple(row.item for row in rows if row.photo_present is False),
        tuple(row.item for row in rows if row.price is None),
        tuple(row.item for row in rows if row.photo_present is UNKNOWN),
        tuple(row.item for row in rows if row.price is UNKNOWN))


@dataclass(frozen=True)
class PurchasePlan:
    key: StockKey
    units: Quantity
    unit: str
    observed_on: date
    source_id: str

    def __post_init__(self):
        if not isinstance(self.key, StockKey):
            raise ValueError('Purchase plan requires a StockKey')
        _quantity(self.units, 'planned units')
        _label(self.unit, 'unit')
        _observation(self.observed_on, self.source_id)


@dataclass(frozen=True)
class ProcurementPreview:
    stock: StockSnapshot
    plan: PurchasePlan
    expected_free_units: Quantity


def procurement_preview(stock: StockSnapshot, plan: PurchasePlan) -> ProcurementPreview:
    """Arithmetic preview only. Does not assume reservations, receipts or sales."""
    if stock.kind is not StockKind.FREE:
        raise ValueError('Procurement preview requires explicit current free stock')
    scope_known = _same_scope(stock, plan)
    expected = UNKNOWN if not scope_known or stock.units is UNKNOWN or plan.units is UNKNOWN else _exact_sum(stock.units, plan.units)
    return ProcurementPreview(stock, plan, expected)
