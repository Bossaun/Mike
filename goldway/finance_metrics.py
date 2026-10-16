"""Pure, currency-scoped metrics over explicit records; no integration hooks.

Sale amounts are nonnegative transaction totals. Negative/return records are
unsupported and must be reconciled separately. See RESULT.md for scope/formulas.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re
from typing import Iterable

MAX_RECORDS = 10000
_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal('0')
_HUNDRED = Decimal('100')


def _currency(value: str) -> str:
    if type(value) is not str or re.fullmatch(r'[A-Z]{3}', value) is None:
        raise ValueError('Currency requires an explicit three-letter uppercase code')
    return value


def _day(value: date) -> date:
    if type(value) is not date:
        raise ValueError('Date must be a date without time or timezone')
    return value


def _amount(value: str | Decimal) -> Decimal:
    if type(value) not in (str, Decimal) or (type(value) is str and (not value or len(value) > 100 or value != value.strip())):
        raise ValueError('Amounts must be finite money strings or Decimal values')
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid monetary amount') from None
    if (not amount.is_finite() or amount < 0 or len(amount.as_tuple().digits) > 50
            or abs(amount.as_tuple().exponent) > 18):
        raise ValueError('Amount must be nonnegative and within supported decimal precision')
    return amount


def _percentage(value: str | Decimal) -> Decimal:
    result = _amount(value)
    if result > _HUNDRED:
        raise ValueError('Percentage must be between zero and 100')
    return result


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    with localcontext(_CONTEXT):
        return numerator / denominator


@dataclass(frozen=True)
class Sale:
    id: str
    date: date
    currency: str
    revenue: str | Decimal
    cogs: str | Decimal | None
    barter: bool = False

    def __post_init__(self):
        if type(self.id) is not str or not self.id.strip() or self.id != self.id.strip() or len(self.id) > 200:
            raise ValueError('Sale ID must be a nonempty identifier')
        _day(self.date)
        _currency(self.currency)
        if type(self.barter) is not bool:
            raise ValueError('Barter flag must be an explicit boolean')
        object.__setattr__(self, 'revenue', _amount(self.revenue))
        if self.cogs is not None:
            object.__setattr__(self, 'cogs', _amount(self.cogs))


@dataclass(frozen=True)
class CostFlag:
    sale_id: str
    kind: str  # missing_cost or under_cost; zero revenue never implies barter


def _records(records: Iterable[Sale], currency: str) -> tuple[Sale, ...]:
    _currency(currency)
    collected, ids = [], set()
    for record in records:
        if len(collected) >= MAX_RECORDS:
            raise ValueError('Too many sale records')
        if type(record) is not Sale:
            raise ValueError('Records must be validated Sale instances')
        if record.currency != currency:
            raise ValueError('Mixed currencies require separate datasets; no implicit FX')
        if record.id in ids:
            raise ValueError('Duplicate sale ID')
        ids.add(record.id)
        collected.append(record)
    return tuple(collected)


def cost_flags(records: Iterable[Sale], *, currency: str) -> tuple[CostFlag, ...]:
    flags = []
    for sale in _records(records, currency):
        if sale.cogs is None:
            flags.append(CostFlag(sale.id, 'missing_cost'))
        elif not sale.barter and sale.revenue < sale.cogs:
            flags.append(CostFlag(sale.id, 'under_cost'))
    return tuple(flags)


@dataclass(frozen=True)
class WeekMetrics:
    start: date
    end: date  # exclusive
    currency: str
    sale_count: int  # excludes explicit barter records
    revenue: Decimal
    known_cost_count: int
    unknown_cost_count: int
    cost_coverage: Decimal | None  # known normal-sale count / normal-sale count
    known_cost_revenue: Decimal
    known_cogs: Decimal
    gross_profit: Decimal | None  # only the known-cost normal-sale scope
    gross_margin_pct: Decimal | None  # same scope; None if its revenue is zero
    barter_count: int
    known_barter_cost: Decimal
    unknown_barter_cost_count: int


def _week(records: tuple[Sale, ...], start: date, currency: str) -> WeekMetrics:
    end = start + timedelta(days=7)
    selected = [sale for sale in records if start <= sale.date < end]
    normal = [sale for sale in selected if not sale.barter]
    known = [sale for sale in normal if sale.cogs is not None]
    barter = [sale for sale in selected if sale.barter]
    with localcontext(_CONTEXT):
        revenue = sum((sale.revenue for sale in normal), _ZERO)
        known_revenue = sum((sale.revenue for sale in known), _ZERO)
        known_cogs = sum((sale.cogs for sale in known), _ZERO)
        profit = known_revenue - known_cogs if known else None
        margin = _ratio(profit * _HUNDRED, known_revenue) if profit is not None else None
        barter_cost = sum((sale.cogs for sale in barter if sale.cogs is not None), _ZERO)
    return WeekMetrics(start, end, currency, len(normal), revenue, len(known),
        len(normal) - len(known), _ratio(Decimal(len(known)), Decimal(len(normal))),
        known_revenue, known_cogs, profit, margin, len(barter), barter_cost,
        sum(sale.cogs is None for sale in barter))


@dataclass(frozen=True)
class WeekComparison:
    current: WeekMetrics
    prior: WeekMetrics
    revenue_delta: Decimal
    revenue_change_pct: Decimal | None
    known_scope_gross_profit_delta: Decimal | None
    gross_margin_change_pp: Decimal | None


def compare_weeks(records: Iterable[Sale], *, week_start: date, currency: str) -> WeekComparison:
    """Compare explicit ISO Monday weeks [start,start+7d), never wall time."""
    _day(week_start)
    if week_start.weekday() != 0:
        raise ValueError('Week start must be an explicit Monday')
    sales = _records(records, currency)
    try:
        current = _week(sales, week_start, currency)
        prior = _week(sales, week_start - timedelta(days=7), currency)
    except OverflowError:
        raise ValueError('Week boundaries exceed supported dates') from None
    with localcontext(_CONTEXT):
        delta = current.revenue - prior.revenue
        change_pct = _ratio(delta * _HUNDRED, prior.revenue)
        profit_delta = (current.gross_profit - prior.gross_profit
            if current.gross_profit is not None and prior.gross_profit is not None else None)
        margin_delta = (current.gross_margin_pct - prior.gross_margin_pct
            if current.gross_margin_pct is not None and prior.gross_margin_pct is not None else None)
    return WeekComparison(current, prior, delta, change_pct, profit_delta, margin_delta)


@dataclass(frozen=True)
class PromotionPreview:
    currency: str
    original_revenue: Decimal
    discounted_revenue: Decimal
    discount_amount: Decimal
    cogs: Decimal
    fee_amount: Decimal
    contribution: Decimal  # after specified fees only; not net profit
    contribution_margin_pct: Decimal | None


def preview_promotion(*, currency: str, original_revenue: str | Decimal,
                      cogs: str | Decimal, discount_pct: str | Decimal,
                      fee_pct: str | Decimal, fixed_fee: str | Decimal) -> PromotionPreview:
    """One transaction's totals in one currency; fee_pct applies after discount.

    Every fee/discount input is explicit, including zero. No per-unit/quantity,
    FX, tax, freight, overhead or recommendation is inferred.
    """
    _currency(currency)
    original, cost, fixed = map(_amount, (original_revenue, cogs, fixed_fee))
    discount, fee = map(_percentage, (discount_pct, fee_pct))
    with localcontext(_CONTEXT):
        discount_amount = original * discount / _HUNDRED
        discounted = original - discount_amount
        fees = fixed + discounted * fee / _HUNDRED
        contribution = discounted - cost - fees
        margin = _ratio(contribution * _HUNDRED, discounted)
    return PromotionPreview(currency, original, discounted, discount_amount,
                            cost, fees, contribution, margin)
