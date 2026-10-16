"""Pure purchase quotes from explicitly typed costs, weights and direct FX.

No lookup, tax/profit inference, unit guessing, integration or side effect.
Quantity is a count of identical units in one batch, not mass or pack count.
Decimals use a private precision-100 context; no currency quantization is implied.
"""
from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re
from typing import Iterable


_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal('0')
_ONE = Decimal('1')
_MAX_RECORDS = 1000
_BASES = {'per_unit', 'batch'}
_WEIGHT_UNITS = {'g': Decimal('0.001'), 'kg': _ONE}


def _decimal(value, *, positive=False):
    if type(value) not in (str, Decimal) or (type(value) is str and
            (not value or len(value) > 100 or value != value.strip())):
        raise ValueError('Explicit finite decimal string or Decimal required')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid decimal') from None
    if (not result.is_finite() or result < 0 or (positive and result == 0)
            or len(result.as_tuple().digits) > 50 or abs(result.as_tuple().exponent) > 18):
        raise ValueError('Decimal outside nonnegative supported range')
    return result


def _currency(value):
    if type(value) is not str or re.fullmatch(r'[A-Z]{3}', value) is None:
        raise ValueError('Explicit uppercase three-letter currency required')
    return value


def _identifier(value):
    if type(value) is not str or not value or value != value.strip() or len(value) > 200:
        raise ValueError('Explicit nonempty cost identifier required')
    return value


def _quantity(value):
    if type(value) is not int or not 1 <= value <= 10**9:
        raise ValueError('Quantity must be a positive integer count of units')
    return value


def _choice(value, allowed, label):
    if type(value) is not str or value not in allowed:
        raise ValueError('Explicit ' + label + ' required')
    return value


def _records(values, cls):
    result = []
    for value in values:
        if type(value) is not cls or len(result) >= _MAX_RECORDS:
            raise ValueError('Invalid or excessive records')
        result.append(value)
    return tuple(result)


@dataclass(frozen=True)
class Money:
    amount: str | Decimal | None
    currency: str

    def __post_init__(self):
        _currency(self.currency)
        if self.amount is not None:
            object.__setattr__(self, 'amount', _decimal(self.amount))


@dataclass(frozen=True)
class Charge:
    id: str
    kind: str  # purchase, freight, commission, fee
    money: Money
    basis: str  # per_unit or batch, always explicit
    included_cost_ids: tuple[str, ...] = ()

    def __post_init__(self):
        _identifier(self.id)
        _choice(self.kind, {'purchase', 'freight', 'commission', 'fee'}, 'charge kind')
        _choice(self.basis, _BASES, 'amount basis')
        if (type(self.money) is not Money or type(self.included_cost_ids) is not tuple
                or len(self.included_cost_ids) > _MAX_RECORDS):
            raise ValueError('Validated Money and explicit tuple of included costs required')
        for value in self.included_cost_ids:
            _identifier(value)
        if (self.id in self.included_cost_ids or
                len(set(self.included_cost_ids)) != len(self.included_cost_ids)):
            raise ValueError('Duplicate included cost identity')


@dataclass(frozen=True)
class FXRate:
    from_currency: str
    to_currency: str
    target_per_source_unit: str | Decimal

    def __post_init__(self):
        _currency(self.from_currency)
        _currency(self.to_currency)
        if self.from_currency == self.to_currency:
            raise ValueError('FX requires different explicit currency pair')
        object.__setattr__(self, 'target_per_source_unit',
                           _decimal(self.target_per_source_unit, positive=True))


@dataclass(frozen=True)
class Weight:
    amount: str | Decimal | None
    unit: str  # only g or kg; no inferred units
    basis: str  # per_unit or batch
    kind: str  # net or gross; no inferred packaging/volumetric mass

    def __post_init__(self):
        _choice(self.unit, _WEIGHT_UNITS, 'weight unit')
        _choice(self.basis, _BASES, 'weight basis')
        _choice(self.kind, {'net', 'gross'}, 'net/gross weight kind')
        if self.amount is not None:
            object.__setattr__(self, 'amount', _decimal(self.amount))


@dataclass(frozen=True)
class FreightTariff:
    money_per_weight_unit: Money
    weight_unit: str
    billable_kind: str

    def __post_init__(self):
        if type(self.money_per_weight_unit) is not Money:
            raise ValueError('Validated tariff Money required')
        _choice(self.weight_unit, _WEIGHT_UNITS, 'tariff weight unit')
        _choice(self.billable_kind, {'net', 'gross'}, 'billable weight kind')


def freight_from_weight(id: str, weight: Weight, tariff: FreightTariff) -> Charge:
    """Cost retains the explicitly supplied weight's unit/batch quantity basis.

    Only g↔kg scaling is automatic. Net→gross/volumetric conversion is unsupported.
    Missing weight/rate yields an unknown amount, never a zero charge.
    """
    if type(weight) is not Weight or type(tariff) is not FreightTariff:
        raise ValueError('Validated weight and freight tariff required')
    if weight.kind != tariff.billable_kind:
        raise ValueError('Net/gross mismatch; billable weight must be explicit')
    rate = tariff.money_per_weight_unit
    amount = None
    if weight.amount is not None and rate.amount is not None:
        with localcontext(_CONTEXT):
            tariff_weight = weight.amount * _WEIGHT_UNITS[weight.unit] / _WEIGHT_UNITS[tariff.weight_unit]
            amount = tariff_weight * rate.amount
    return Charge(id, 'freight', Money(amount, rate.currency), weight.basis)


def quote_purchase(*, quantity: int, currency: str, charges: Iterable[Charge],
                   fx_rates: Iterable[FXRate], fees_complete: bool,
                   transaction_kind: str) -> dict:
    """Quote a purchase batch; returns/credit notes are explicitly unsupported.

    Required roles: exactly one purchase, freight and commission. To declare no
    commission supply an explicit zero charge. Missing roles/amounts or incomplete
    fee enumeration yield status=unknown and total/per_unit=None. Direct FX into
    the output currency is mandatory for foreign charges: no inverse/triangulation.
    Each id denotes an underlying economic charge, not just an arbitrary row;
    included_cost_ids declares charges already bundled into this amount. Any
    overlap rejects the quote instead of silently counting a commission twice.
    Unstated economic overlaps cannot be inferred by this pure calculator.
    """
    _quantity(quantity)
    _currency(currency)
    if type(fees_complete) is not bool or transaction_kind != 'purchase':
        raise ValueError('Explicit fee completeness and purchase kind required; returns unsupported')
    records = _records(charges, Charge)
    rates = _records(fx_rates, FXRate)
    by_pair = {}
    for rate in rates:
        pair = (rate.from_currency, rate.to_currency)
        if pair in by_pair:
            raise ValueError('Duplicate FX pair')
        by_pair[pair] = rate.target_per_source_unit
    roles, seen_costs = {}, set()
    for charge in records:
        covered = {charge.id, *charge.included_cost_ids}
        if seen_costs & covered:
            raise ValueError('Duplicate or overlapping economic charge; commission may already be included')
        seen_costs |= covered
        roles[charge.kind] = roles.get(charge.kind, 0) + 1
        if charge.kind != 'fee' and roles[charge.kind] > 1:
            raise ValueError('Duplicate singleton charge role')
    missing = ['charge:' + role for role in ('purchase', 'freight', 'commission') if not roles.get(role)]
    if not fees_complete:
        missing.append('fees:incomplete_enumeration')
    breakdown = []
    with localcontext(_CONTEXT):
        known_subtotal = _ZERO
        for charge in records:
            factor = _ONE if charge.money.currency == currency else by_pair.get((charge.money.currency, currency))
            if factor is None:
                raise ValueError('Missing explicit direct FX; incomplete FX is unsupported')
            batch = per_unit = None
            if charge.money.amount is None:
                missing.append('amount:' + charge.id)
            else:
                converted = charge.money.amount * factor
                batch = converted * quantity if charge.basis == 'per_unit' else converted
                per_unit = batch / quantity
                known_subtotal += batch
            breakdown.append({'id': charge.id, 'kind': charge.kind, 'source_amount': charge.money.amount,
                              'source_currency': charge.money.currency, 'source_basis': charge.basis,
                              'included_cost_ids': charge.included_cost_ids,
                              'fx_target_per_source_unit': factor, 'batch_amount': batch,
                              'per_unit_amount': per_unit})
        return {'status': 'unknown' if missing else 'known', 'currency': currency,
                'quantity': quantity, 'quantity_basis': 'count_of_identical_units_in_one_batch',
                'components': tuple(breakdown), 'missing': tuple(missing),
                'known_components_subtotal': known_subtotal,
                'total_batch': None if missing else known_subtotal,
                'per_unit': None if missing else known_subtotal / quantity}


def assess_weight(*, actual: Weight, expected: Weight,
                  max_relative_deviation: str | Decimal | None, quantity: int) -> dict:
    """Compare same-kind net/net or gross/gross batch masses; threshold supplied.

    Relative deviation is abs(actual−expected)/expected. A zero expected mass is
    not a usable relative baseline. Missing threshold/weight and net/gross mismatch
    produce unknown rather than a fabricated pass or warning.
    """
    _quantity(quantity)
    if type(actual) is not Weight or type(expected) is not Weight:
        raise ValueError('Validated actual and expected Weight required')
    threshold = None if max_relative_deviation is None else _decimal(max_relative_deviation)
    if actual.kind != expected.kind:
        return {'status': 'unknown', 'reason': 'incomparable_net_gross', 'relative_deviation': None}
    if actual.amount is None or expected.amount is None or threshold is None:
        return {'status': 'unknown', 'reason': 'missing_weight_or_threshold', 'relative_deviation': None}
    with localcontext(_CONTEXT):
        masses = [w.amount * _WEIGHT_UNITS[w.unit] * (quantity if w.basis == 'per_unit' else 1)
                  for w in (actual, expected)]
        if masses[1] == 0:
            return {'status': 'unknown', 'reason': 'zero_expected_weight', 'relative_deviation': None}
        deviation = abs(masses[0] - masses[1]) / masses[1]
        return {'status': 'flag' if deviation > threshold else 'within_threshold',
                'reason': 'caller_threshold', 'actual_batch_kg': masses[0],
                'expected_batch_kg': masses[1], 'relative_deviation': deviation,
                'max_relative_deviation': threshold}


def assess_logistics(*, purchase: Charge, freight: Charge, quantity: int,
                     max_freight_to_purchase_ratio: str | Decimal | None) -> dict:
    """Compare explicitly based purchase/freight amounts in the same currency.

    Source basis is normalized by the supplied batch quantity. FX normalization
    belongs to quote_purchase. No company threshold is built in.
    """
    _quantity(quantity)
    if (type(purchase) is not Charge or type(freight) is not Charge
            or purchase.kind != 'purchase' or freight.kind != 'freight'):
        raise ValueError('Validated explicitly based purchase and freight charges required')
    if {purchase.id, *purchase.included_cost_ids} & {freight.id, *freight.included_cost_ids}:
        raise ValueError('Overlapping purchase/freight costs cannot establish an independent logistics ratio')
    if purchase.money.currency != freight.money.currency:
        raise ValueError('Mixed currencies require explicit normalization before logistics comparison')
    threshold = None if max_freight_to_purchase_ratio is None else _decimal(max_freight_to_purchase_ratio)
    if purchase.money.amount is None or freight.money.amount is None or threshold is None:
        return {'status': 'unknown', 'reason': 'missing_amount_or_threshold', 'ratio': None}
    if purchase.money.amount == 0:
        return {'status': 'unknown', 'reason': 'zero_purchase_baseline', 'ratio': None}
    with localcontext(_CONTEXT):
        batch_purchase = purchase.money.amount * (quantity if purchase.basis == 'per_unit' else 1)
        batch_freight = freight.money.amount * (quantity if freight.basis == 'per_unit' else 1)
        ratio = batch_freight / batch_purchase
        return {'status': 'flag' if ratio > threshold else 'within_threshold',
                'reason': 'caller_threshold', 'ratio': ratio,
                'max_freight_to_purchase_ratio': threshold, 'currency': purchase.money.currency,
                'quantity_basis': 'batch', 'quantity': quantity}
