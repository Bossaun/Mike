"""Pure, bounded influencer observations. No lookup, messaging or runtime hooks.

Exact confirmed identity keys only. Financial records use one reporting currency;
currency conflicts and incomplete observations cannot produce final totals.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
from enum import Enum
import re
from typing import Iterable

_CONTEXT = Context(prec=100)
_MAX_RECORDS = 1000


def _id(value):
    if type(value) is not str or not value or value != value.strip() or len(value) > 200:
        raise ValueError('Explicit nonblank identifier/source required')


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('Explicit timezone-aware timestamp required')
    return value.astimezone(timezone.utc)


def _currency(value):
    if type(value) is not str or re.fullmatch('[A-Z]{3}', value) is None:
        raise ValueError('Explicit uppercase three-letter currency required')


def _flag(value):
    if value is not None and type(value) is not bool:
        raise ValueError('Confirmation requires True, False or None (unknown)')


def _records(values, cls):
    result = {}
    for index, value in enumerate(values):
        if index >= _MAX_RECORDS or type(value) is not cls:
            raise ValueError('Invalid or excessive records')
        if value.id in result and result[value.id] != value:
            raise ValueError('Conflicting duplicate record identity')
        result[value.id] = value
    return tuple(result.values())


@dataclass(frozen=True)
class Money:
    amount: Decimal | None
    currency: str

    def __post_init__(self):
        _currency(self.currency)
        value = self.amount
        if value is not None and (type(value) is not Decimal or not value.is_finite()
                or len(value.as_tuple().digits) > 50 or abs(value.as_tuple().exponent) > 18):
            raise ValueError('Finite Decimal with at most 50 digits/exponent ±18 or None required')


def _money(value, *, nonnegative=False):
    if type(value) is not Money:
        raise ValueError('Explicit Money, including currency for unknown amounts, required')
    if nonnegative and value.amount is not None and value.amount < 0:
        raise ValueError('Revenue, refund reductions, costs and limits must be nonnegative')


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def __post_init__(self):
        if _utc(self.start) >= _utc(self.end):
            raise ValueError('Window must have a positive explicit duration')

    def contains(self, at):
        return _utc(self.start) <= _utc(at) < _utc(self.end)


@dataclass(frozen=True)
class Binding:
    id: str  # exact alias or promo code; separate maps, no normalization
    influencer_id: str
    confirmed: bool
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.influencer_id, self.source_id): _id(value)
        if type(self.confirmed) is not bool:
            raise ValueError('Binding confirmation must be explicit')


def _bindings(values):
    result = {}
    rows = _records(values, Binding)
    for row in rows:
        if row.confirmed:
            result[row.id] = row.influencer_id
    return result, rows


class BarterState(Enum):
    CONFIRMED = 'confirmed'
    NOT_BARTER = 'not_barter'
    UNKNOWN = 'unknown'


def classify_barter(confirmed: bool | None) -> BarterState:
    """A zero amount, nickname or shipment never substitutes for confirmation."""
    _flag(confirmed)
    return BarterState.UNKNOWN if confirmed is None else BarterState.CONFIRMED if confirmed else BarterState.NOT_BARTER


@dataclass(frozen=True)
class Shipment:
    id: str
    recipient_alias: str
    shipped_at: datetime
    barter_confirmed: bool | None
    barter_cost: Money
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.recipient_alias, self.source_id): _id(value)
        _utc(self.shipped_at); _flag(self.barter_confirmed); _money(self.barter_cost, nonnegative=True)
        if self.barter_confirmed is False and self.barter_cost.amount not in (None, Decimal('0')):
            raise ValueError('Positive barter cost conflicts with explicit non-barter shipment')


@dataclass(frozen=True)
class Order:
    id: str
    promo_codes: tuple[str, ...]
    referral_alias: str | None
    occurred_at: datetime
    revenue_before_refunds: Money
    margin_before_refunds_and_barter: Money
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.source_id): _id(value)
        if type(self.promo_codes) is not tuple or len(self.promo_codes) > _MAX_RECORDS:
            raise ValueError('Explicit bounded promo-code tuple required')
        for code in self.promo_codes: _id(code)
        if self.referral_alias is not None: _id(self.referral_alias)
        _utc(self.occurred_at); _money(self.revenue_before_refunds, nonnegative=True)
        _money(self.margin_before_refunds_and_barter)


@dataclass(frozen=True)
class Refund:
    id: str
    order_id: str
    occurred_at: datetime
    revenue_reduction: Money
    margin_reduction: Money
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.order_id, self.source_id): _id(value)
        _utc(self.occurred_at); _money(self.revenue_reduction, nonnegative=True); _money(self.margin_reduction, nonnegative=True)


@dataclass(frozen=True)
class Click:
    id: str
    promo_code: str
    occurred_at: datetime
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.promo_code, self.source_id): _id(value)
        _utc(self.occurred_at)


@dataclass(frozen=True)
class PlacementReport:
    id: str
    shipment_id: str
    observed_at: datetime
    result_present: bool
    source_id: str

    def __post_init__(self):
        for value in (self.id, self.shipment_id, self.source_id): _id(value)
        _utc(self.observed_at)
        if type(self.result_present) is not bool:
            raise ValueError('Report result presence must be explicit')


@dataclass(frozen=True)
class DueCandidate:
    shipment_id: str
    recipient_alias: str
    due_at: datetime
    source_id: str


def _latest_reports(ships, observations, now, *, inclusive=True):
    by_ship = {ship.id: ship for ship in ships}
    latest = {}
    for report in observations:
        if report.shipment_id not in by_ship:
            raise ValueError('Report references an absent shipment')
        if _utc(report.observed_at) < _utc(by_ship[report.shipment_id].shipped_at):
            raise ValueError('Report predates shipment')
        if _utc(report.observed_at) < now or (inclusive and _utc(report.observed_at) == now):
            previous = latest.get(report.shipment_id)
            if previous and _utc(report.observed_at) == _utc(previous.observed_at) and report.result_present != previous.result_present:
                raise ValueError('Conflicting simultaneous placement reports')
            if previous is None or _utc(report.observed_at) > _utc(previous.observed_at):
                latest[report.shipment_id] = report
    return latest


def report_due_candidates(shipments: Iterable[Shipment], reports: Iterable[PlacementReport], *, now: datetime) -> tuple[DueCandidate, ...]:
    """Elapsed 168 hours in UTC; candidates only, never an outbound request."""
    now = _utc(now)
    ships, observations = _records(shipments, Shipment), _records(reports, PlacementReport)
    latest = _latest_reports(ships, observations, now)
    result = []
    for ship in ships:
        try:
            due = _utc(ship.shipped_at) + timedelta(days=7)
        except OverflowError:
            raise ValueError('Seven-day due timestamp outside supported range') from None
        if now >= due and not (ship.id in latest and latest[ship.id].result_present):
            result.append(DueCandidate(ship.id, ship.recipient_alias, due, ship.source_id))
    return tuple(result)


@dataclass(frozen=True)
class Financials:
    state: str  # known, unknown, zero_denominator
    currency: str
    net_revenue: Decimal | None
    margin_after_refunds: Decimal | None
    barter_cost: Decimal | None
    net_after_barter: Decimal | None
    roi: Decimal | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class InfluencerMetrics:
    influencer_id: str
    window: Window
    shipment_ids: tuple[str, ...]
    click_ids: tuple[str, ...]
    order_ids: tuple[str, ...]
    refund_ids: tuple[str, ...]
    unattributed_order_ids: tuple[str, ...]
    unattributed_shipment_ids: tuple[str, ...]
    unattributed_click_ids: tuple[str, ...]
    unattributed_refund_ids: tuple[str, ...]
    barter_states: tuple[tuple[str, BarterState], ...]
    unmeasurable_barter_ids: tuple[str, ...]
    financials: Financials
    limit_state: str  # unknown, within_limit, exceeded
    source_ids: tuple[str, ...]


def evaluate(*, influencer_id: str, window: Window, currency: str,
             aliases: Iterable[Binding], promos: Iterable[Binding],
             shipments: Iterable[Shipment], clicks: Iterable[Click], orders: Iterable[Order],
             refunds: Iterable[Refund], reports: Iterable[PlacementReport],
             records_complete: bool, barter_limit: Money | None) -> InfluencerMetrics:
    """One supplied half-open period; exact aliases/promos and one order identity.

    Revenue/margin are before the separately supplied refunds; only refund
    reductions inside this window are subtracted. Older orders may provide
    attribution for a current-period refund, without repeating their revenue.
    No final totals when completeness/attribution/money/currency is unresolved.
    ROI=(margin-after-refunds−barter-cost)/barter-cost. No spend means undefined
    ROI, not zero/infinite ROI. Limits apply only to this supplied window.
    """
    _id(influencer_id); _currency(currency)
    if type(window) is not Window or type(records_complete) is not bool:
        raise ValueError('Explicit window and completeness flag required')
    if barter_limit is not None: _money(barter_limit, nonnegative=True)
    alias_map, alias_rows = _bindings(aliases)
    promo_map, promo_rows = _bindings(promos)
    ships, click_rows = _records(shipments, Shipment), _records(clicks, Click)
    order_rows, refund_rows, report_rows = _records(orders, Order), _records(refunds, Refund), _records(reports, PlacementReport)
    order_map = {order.id: order for order in order_rows}
    with localcontext(_CONTEXT):
        for order in order_rows:
            known_refunds = [r.revenue_reduction.amount for r in refund_rows
                             if r.order_id == order.id and r.revenue_reduction.amount is not None
                             and r.revenue_reduction.currency == order.revenue_before_refunds.currency]
            if (order.revenue_before_refunds.amount is not None
                    and sum(known_refunds, Decimal('0')) > order.revenue_before_refunds.amount):
                raise ValueError('Refund revenue reductions exceed original order revenue')
    attributed = {}
    for order in order_rows:
        people = {promo_map.get(code) for code in order.promo_codes}
        if order.referral_alias is not None: people.add(alias_map.get(order.referral_alias))
        attributed[order.id] = next(iter(people)) if len(people) == 1 and None not in people else None
    selected_orders = [o for o in order_rows if window.contains(o.occurred_at) and attributed[o.id] == influencer_id]
    unresolved_orders = [o for o in order_rows if window.contains(o.occurred_at) and attributed[o.id] is None]
    selected_ships = [s for s in ships if window.contains(s.shipped_at) and alias_map.get(s.recipient_alias) == influencer_id]
    unresolved_ships = [s for s in ships if window.contains(s.shipped_at) and s.recipient_alias not in alias_map]
    selected_clicks = [c for c in click_rows if window.contains(c.occurred_at) and promo_map.get(c.promo_code) == influencer_id]
    unresolved_clicks = [c for c in click_rows if window.contains(c.occurred_at) and c.promo_code not in promo_map]
    selected_refunds, unresolved_refunds = [], []
    for refund in refund_rows:
        order = order_map.get(refund.order_id)
        if order is not None and _utc(refund.occurred_at) < _utc(order.occurred_at):
            raise ValueError('Refund predates order')
        if window.contains(refund.occurred_at):
            if order is None or attributed[order.id] is None:
                unresolved_refunds.append(refund)
            elif attributed[order.id] == influencer_id:
                selected_refunds.append(refund)
    reasons = set()
    cost_reasons = set()
    if not records_complete:
        reasons.add('incomplete_observations'); cost_reasons.add('incomplete_observations')
    if unresolved_ships: cost_reasons.add('unresolved_attribution')
    if unresolved_orders or unresolved_ships or unresolved_refunds: reasons.add('unresolved_attribution')
    states = tuple((s.id, classify_barter(s.barter_confirmed)) for s in selected_ships)
    if any(state is BarterState.UNKNOWN for _, state in states):
        reasons.add('unknown_barter'); cost_reasons.add('unknown_barter')
    # Validate all reports, then use only observations known by the window end.
    latest = _latest_reports(ships, report_rows, _utc(window.end), inclusive=False)
    reported = {key for key, report in latest.items() if report.result_present}
    tracked = influencer_id in promo_map.values()
    unmeasurable = tuple(s.id for s in selected_ships if s.barter_confirmed is True and not tracked and s.id not in reported)
    amounts = [o.revenue_before_refunds for o in selected_orders] + [o.margin_before_refunds_and_barter for o in selected_orders]
    amounts += [r.revenue_reduction for r in selected_refunds] + [r.margin_reduction for r in selected_refunds]
    costs = [s.barter_cost for s in selected_ships if s.barter_confirmed is True]
    for money in amounts:
        if money.amount is None: reasons.add('missing_amount')
        if money.currency != currency: reasons.add('currency_conflict')
    for money in costs:
        if money.amount is None: cost_reasons.add('missing_amount')
        if money.currency != currency: cost_reasons.add('currency_conflict')
    reasons |= cost_reasons
    with localcontext(_CONTEXT):
        cost = None if cost_reasons else sum((m.amount for m in costs), Decimal('0'))
        if reasons:
            financials = Financials('unknown', currency, None, None, cost, None, None, tuple(sorted(reasons)))
        else:
            revenue = sum((o.revenue_before_refunds.amount for o in selected_orders), Decimal('0')) - sum((r.revenue_reduction.amount for r in selected_refunds), Decimal('0'))
            margin = sum((o.margin_before_refunds_and_barter.amount for o in selected_orders), Decimal('0')) - sum((r.margin_reduction.amount for r in selected_refunds), Decimal('0'))
            net = margin - cost
            financials = Financials('zero_denominator' if cost == 0 else 'known', currency, revenue, margin, cost, net,
                                   None if cost == 0 else net / cost, () if cost else ('zero_barter_cost',))
    if cost is None or barter_limit is None or barter_limit.amount is None or barter_limit.currency != currency:
        limit_state = 'unknown'
    else:
        limit_state = 'exceeded' if financials.barter_cost > barter_limit.amount else 'within_limit'
    sources = {row.source_id for row in [*selected_ships, *selected_clicks, *selected_orders, *selected_refunds]}
    sources |= {order_map[row.order_id].source_id for row in selected_refunds}
    sources |= {row.source_id for row in (*alias_rows, *promo_rows) if row.confirmed and row.influencer_id == influencer_id}
    sources |= {row.source_id for row in report_rows if row.shipment_id in {s.id for s in selected_ships} and window.contains(row.observed_at)}
    return InfluencerMetrics(influencer_id, window, tuple(s.id for s in selected_ships), tuple(c.id for c in selected_clicks),
        tuple(o.id for o in selected_orders), tuple(r.id for r in selected_refunds), tuple(o.id for o in unresolved_orders),
        tuple(s.id for s in unresolved_ships), tuple(c.id for c in unresolved_clicks), tuple(r.id for r in unresolved_refunds),
        states, unmeasurable, financials, limit_state, tuple(sorted(sources)))
