"""Payload-free local trace views and offline caller-priced profile comparisons.

No runtime imports, prompt/response parsing, string redaction, provider requests,
configuration change or permission grant. A future adapter must map only safe
fixed categories/counters, never pass raw events, config or exception text.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import Enum
import re


_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_MAX_TOKENS = 10_000_000


class RequestKind(Enum):
    CONVERSATION='conversation'; QUESTION='question'; TASK='task'
    IDEA='idea'; MEETING='meeting'; REPORT='report'; REMINDER='reminder'; UNKNOWN='unknown'


class Intent(Enum):
    CONVERSATION='conversation'; TASK_ACTION='task_action'; IDEA='idea'; MEETING='meeting'; UNKNOWN='unknown'


class Operation(Enum):
    CREATE_TASK='create_task'; UPDATE_TASK='update_task'; SCHEDULE_CHECK='schedule_check'
    CANCEL_CHECK='cancel_check'; CREATE_SERIES='create_series'; PAUSE_SERIES='pause_series'


class ExecutionSource(Enum):
    MODEL='model'; DETERMINISTIC='deterministic'; UNKNOWN='unknown'


class Validation(Enum):
    PASSED='passed'; REJECTED='rejected'; NOT_RUN='not_run'; UNKNOWN='unknown'


class Processing(Enum):
    PENDING='pending'; DONE='done'; FAILED='failed'; UNKNOWN='unknown'


class DeliveryState(Enum):
    PENDING='pending'; RETRY='retry'; BLOCKED='blocked'; SENDING='sending'
    SENT='sent'; UNKNOWN='unknown'; CANCELLED='cancelled'


class SafeReason(Enum):
    NONE='none'; UNKNOWN='unknown'; EXTERNAL_ERROR='external_error'
    MODEL_BUDGET='model_budget'; MODEL_CONTEXT='model_context'; MODEL_TRUNCATED='model_truncated'
    INVALID_INTELLIGENCE='invalid_intelligence'; DIALOGUE_STATE='dialogue_state'
    KNOWLEDGE_CITATIONS='knowledge_citations'; KNOWLEDGE_PERMISSION='knowledge_permission'
    WORK_EVIDENCE_CHANGED='work_evidence_changed'; CITATION_BUDGET='citation_budget'
    PROVENANCE_BUDGET='provenance_budget'; TASK_VALIDATION='task_validation'


def _enum(value, cls):
    if type(value) is not cls:
        raise ValueError('Fixed safe enum required; raw text is unsupported')


def _count(value, maximum, *, unknown=False):
    if unknown and value is None:
        return
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Bounded nonnegative integer counter required')


def _tuple(values, cls, maximum):
    if (type(values) is not tuple or len(values) > maximum
            or any(type(value) is not cls for value in values)):
        raise ValueError('Bounded immutable tuple of strict typed records required')


def _decimal(value):
    if (type(value) is not Decimal or not value.is_finite() or value < 0
            or len(value.as_tuple().digits) > 50 or abs(value.as_tuple().exponent) > 18):
        raise ValueError('Explicit bounded nonnegative Decimal required')


def _currency(value):
    if type(value) is not str or re.fullmatch('[A-Z]{3}',value) is None:
        raise ValueError('Explicit uppercase currency required')


def _metadata(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9._:/-]{1,100}',value) is None:
        raise ValueError('Bounded nonsecret profile/source metadata required')


def _date(value):
    if type(value) is not date:
        raise ValueError('Explicit price/comparison date required')


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None

    def __post_init__(self):
        _count(self.input_tokens,_MAX_TOKENS,unknown=True)
        _count(self.output_tokens,_MAX_TOKENS,unknown=True)


@dataclass(frozen=True)
class TraceRecord:
    request_kind: RequestKind
    intent: Intent
    execution_source: ExecutionSource
    proposed_ops: tuple[Operation, ...]
    validation: Validation
    applied_count: int | None
    queued_count: int | None
    delivery_states: tuple[DeliveryState, ...]
    processing: Processing
    usage: TokenUsage
    reason: SafeReason

    def __post_init__(self):
        for value,cls in ((self.request_kind,RequestKind),(self.intent,Intent),
                          (self.execution_source,ExecutionSource),(self.validation,Validation),
                          (self.processing,Processing),(self.reason,SafeReason)):
            _enum(value,cls)
        _tuple(self.proposed_ops,Operation,20)
        _tuple(self.delivery_states,DeliveryState,1000)
        if type(self.usage) is not TokenUsage:
            raise ValueError('Strict token counters required')
        _count(self.applied_count,20,unknown=True)
        _count(self.queued_count,1000,unknown=True)
        if self.queued_count is not None and len(self.delivery_states) > self.queued_count:
            raise ValueError('Delivery observations exceed explicitly queued messages')
        if self.validation is Validation.REJECTED and self.applied_count not in (0,None):
            raise ValueError('Rejected validation cannot assert applied actions')
        if (self.execution_source is ExecutionSource.MODEL and self.applied_count is not None
                and self.applied_count > len(self.proposed_ops)):
            raise ValueError('Applied model actions exceed proposed operations')


@dataclass(frozen=True)
class SafeTraceView:
    proposed_count: int
    applied_count: int | None
    queued_count: int | None
    delivery_counts: tuple[tuple[DeliveryState,int], ...]
    unobserved_delivery_count: int | None
    processing: Processing
    reason: SafeReason
    usage: TokenUsage
    lines: tuple[str, ...]


def _display(value):
    return 'нет данных' if value is None else str(value)


def trace_views(records: tuple[TraceRecord, ...]) -> tuple[SafeTraceView, ...]:
    """Each delivery state is one current message observation, not attempt history.

    DONE means event processing only; it never means sent or actual employee
    completion. Unknown counters remain unknown, including usage not reported by
    a provider. Runtime zero placeholders must not be passed as actual known zero.
    """
    _tuple(records,TraceRecord,1000)
    output=[]
    for row in records:
        counts=tuple((state,row.delivery_states.count(state)) for state in DeliveryState)
        unobserved=None if row.queued_count is None else row.queued_count-len(row.delivery_states)
        operations=', '.join(op.value for op in row.proposed_ops) if row.proposed_ops else 'нет предложенных операций'
        lines=(
            'Вид запроса: '+row.request_kind.value+'; намерение: '+row.intent.value+'; путь: '+row.execution_source.value+'.',
            'Предложено: '+str(len(row.proposed_ops))+' ('+operations+'); проверка: '+row.validation.value+'.',
            'Применено: '+_display(row.applied_count)+'; поставлено в очередь: '+_display(row.queued_count)+'.',
            'Доставка: '+', '.join(state.value+'='+str(count) for state,count in counts)+'; не наблюдалось='+_display(unobserved)+'.',
            'Токены: вход='+_display(row.usage.input_tokens)+', выход='+_display(row.usage.output_tokens)+
            '; обработка события='+row.processing.value+'; причина='+row.reason.value+'.')
        output.append(SafeTraceView(len(row.proposed_ops),row.applied_count,row.queued_count,
            counts,unobserved,row.processing,row.reason,row.usage,lines))
    return tuple(output)


class Provider(Enum):
    OPENAI='openai'; ANTHROPIC='anthropic'; COMPATIBLE='compatible'; LOCAL='local'; UNKNOWN='unknown'


class BudgetScope(Enum):
    DAY='day'; SCENARIO='scenario'


@dataclass(frozen=True)
class RateCard:
    input_per_million: Decimal | None
    output_per_million: Decimal | None
    currency: str
    as_of: date | None
    source: str | None

    def __post_init__(self):
        _currency(self.currency)
        for value in (self.input_per_million,self.output_per_million):
            if value is not None:
                _decimal(value)
        if self.as_of is not None:
            _date(self.as_of)
        if self.source is not None:
            _metadata(self.source)


@dataclass(frozen=True)
class ModelProfile:
    label: str
    provider: Provider
    context_limit_tokens: int
    output_limit_tokens: int
    rates: RateCard

    def __post_init__(self):
        _metadata(self.label); _enum(self.provider,Provider)
        _count(self.context_limit_tokens,_MAX_TOKENS)
        _count(self.output_limit_tokens,_MAX_TOKENS)
        if not 0 < self.output_limit_tokens <= self.context_limit_tokens:
            raise ValueError('Positive explicit output/context limits required')
        if type(self.rates) is not RateCard:
            raise ValueError('Explicit rate card required')


@dataclass(frozen=True)
class Workload:
    input_tokens: int
    reserved_output_tokens: int
    calls: int
    scope: BudgetScope

    def __post_init__(self):
        _count(self.input_tokens,_MAX_TOKENS)
        _count(self.reserved_output_tokens,_MAX_TOKENS)
        _count(self.calls,10000); _enum(self.scope,BudgetScope)
        if self.calls == 0:
            raise ValueError('Explicit positive call count required')


@dataclass(frozen=True)
class Budget:
    amount: Decimal | None
    currency: str
    scope: BudgetScope

    def __post_init__(self):
        if self.amount is not None:
            _decimal(self.amount)
        _currency(self.currency); _enum(self.scope,BudgetScope)


@dataclass(frozen=True)
class ProfileEstimate:
    label: str
    provider: Provider
    context_required_tokens: int
    context_fits: bool
    output_fits: bool
    estimated_per_call: Decimal | None
    estimated_all_calls: Decimal | None
    budget_state: str  # within_supplied_budget, exceeds_supplied_budget, unknown
    currency: str
    price_as_of: date | None
    price_source: str | None
    price_age_days: int | None
    missing: tuple[str, ...]


def compare_profiles(*, profiles: tuple[ModelProfile, ...], workload: Workload,
                     budget: Budget, comparison_as_of: date) -> tuple[ProfileEstimate, ...]:
    """No ranking/recommendation or runtime change; supplied-rate scenario only.

    Input includes all caller-accounted system/history/schema tokens. Context
    requires input PLUS reserved output; cost uses that reserved output ceiling.
    This is not an invoice, current price lookup, measured model quality, or
    proof a provider/profile is usable by the runtime. All arithmetic/currency
    and period scope is explicit; no FX or day/scenario budget conversion.
    """
    _tuple(profiles,ModelProfile,20); _date(comparison_as_of)
    if type(workload) is not Workload or type(budget) is not Budget:
        raise ValueError('Explicit workload and budget required')
    if len({p.label for p in profiles}) != len(profiles):
        raise ValueError('Duplicate profile label')
    if workload.scope is not budget.scope:
        raise ValueError('Workload and budget period scopes differ')
    output=[]
    for profile in profiles:
        rates=profile.rates
        if rates.currency != budget.currency:
            raise ValueError('Currency conflict; no automatic FX')
        if rates.as_of is not None and rates.as_of > comparison_as_of:
            raise ValueError('Future rate-card observation')
        missing=[]
        if rates.input_per_million is None:missing.append('input_rate')
        if rates.output_per_million is None:missing.append('output_rate')
        if rates.as_of is None:missing.append('price_date')
        if rates.source is None:missing.append('price_source')
        per_call=all_calls=None
        if not missing:
            with localcontext(_CONTEXT):
                per_call=(workload.input_tokens*rates.input_per_million+
                          workload.reserved_output_tokens*rates.output_per_million)/Decimal('1000000')
                all_calls=per_call*workload.calls
        if budget.amount is None:missing.append('budget')
        budget_state='unknown' if all_calls is None or budget.amount is None else (
            'within_supplied_budget' if all_calls <= budget.amount else 'exceeds_supplied_budget')
        required=workload.input_tokens+workload.reserved_output_tokens
        output.append(ProfileEstimate(profile.label,profile.provider,required,
            required <= profile.context_limit_tokens,workload.reserved_output_tokens <= profile.output_limit_tokens,
            per_call,all_calls,budget_state,rates.currency,rates.as_of,rates.source,
            None if rates.as_of is None else (comparison_as_of-rates.as_of).days,tuple(missing)))
    return tuple(output)
