"""Pure synthetic reporting projections; no reads, dispatch, clock or KPI guesses.

Periods are half-open [start, end). Source/coverage assertions come from a trusted
caller, not inferred from row presence. Precomputed metrics are not recalculated
as finance results. This module does not grant access to any report recipient.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import re
from zoneinfo import ZoneInfo


_CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
_MAX_RECORDS = 1000


def _id(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9._:/-]{1,200}', value) is None:
        raise ValueError('Explicit bounded identifier required')
    return value


def _text(value, maximum=300):
    if (type(value) is not str or not value.strip() or len(value) > maximum
            or len(value.splitlines()) != 1 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('Bounded single-line text required')
    return value


def _decimal(value):
    if (type(value) is not Decimal or not value.is_finite()
            or len(value.as_tuple().digits) > 50 or abs(value.as_tuple().exponent) > 18):
        raise ValueError('Finite bounded Decimal required; no coercion or floats')
    return value


def _day(value):
    if type(value) is not date:
        raise ValueError('Explicit date required')
    return value


def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError('Explicit timezone-aware datetime required')
    restored = value.astimezone(timezone.utc).astimezone(value.tzinfo)
    if restored.replace(tzinfo=None) != value.replace(tzinfo=None):
        raise ValueError('Nonexistent local clock time')
    return value


def _records(values, cls, maximum=_MAX_RECORDS):
    if (type(values) is not tuple or len(values) > maximum
            or any(type(value) is not cls for value in values)):
        raise ValueError('Bounded immutable tuple of validated records required')
    return values


def _owners(values):
    if type(values) is not tuple or not 1 <= len(values) <= 50:
        raise ValueError('Explicit bounded expected-owner tuple required')
    for value in values:
        _id(value)
    if len(set(values)) != len(values):
        raise ValueError('Duplicate expected owner')


def _unique(values):
    if len(values) != len(set(values)):
        raise ValueError('Duplicate observation identity')


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def __post_init__(self):
        _day(self.start); _day(self.end)
        if not 1 <= (self.end-self.start).days <= 366:
            raise ValueError('Period must be positive and at most366 days')


@dataclass(frozen=True)
class Coverage:
    period: Period
    complete: bool
    observed_on: date
    source_id: str

    def __post_init__(self):
        if type(self.period) is not Period or type(self.complete) is not bool:
            raise ValueError('Explicit period and boolean coverage required')
        _day(self.observed_on); _id(self.source_id)
        if self.complete and self.observed_on < self.period.end:
            raise ValueError('An open period cannot be declared complete')


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    label: str
    unit: str
    currency: str | None
    target: Decimal | None
    direction: str  # at_least, at_most, informational

    def __post_init__(self):
        _id(self.id); _text(self.label); _id(self.unit)
        if self.currency is not None and (type(self.currency) is not str or
                re.fullmatch('[A-Z]{3}', self.currency) is None):
            raise ValueError('Explicit currency code required')
        if (self.unit == 'money') != (self.currency is not None):
            raise ValueError('Money metrics require currency; nonmoney metrics cannot carry currency')
        if self.target is not None:
            _decimal(self.target)
        if self.direction not in ('at_least','at_most','informational'):
            raise ValueError('Explicit target direction required')


@dataclass(frozen=True)
class MetricObservation:
    owner: str
    metric_id: str
    period: Period
    value: Decimal | None
    unit: str
    currency: str | None
    observed_on: date
    source_id: str

    def __post_init__(self):
        _id(self.owner); _id(self.metric_id); _id(self.unit); _id(self.source_id)
        _day(self.observed_on)
        if type(self.period) is not Period:
            raise ValueError('Explicit observation period required')
        if self.value is not None:
            _decimal(self.value)
        if self.currency is not None and (type(self.currency) is not str or
                re.fullmatch('[A-Z]{3}', self.currency) is None):
            raise ValueError('Explicit observation currency required')
        if (self.unit == 'money') != (self.currency is not None):
            raise ValueError('Observation money/unit currency mismatch')


@dataclass(frozen=True)
class MetricLine:
    owner: str
    metric_id: str
    value: Decimal | None
    target: Decimal | None
    unit: str
    currency: str | None
    status: str  # known, unknown, provisional
    target_state: str  # met, missed, informational, unknown
    sources: tuple[str, ...]


def _metric_inputs(definitions, owners, observations, coverage):
    _records(definitions, MetricDefinition, 20); _owners(owners)
    _records(observations, MetricObservation)
    if type(coverage) is not Coverage:
        raise ValueError('Explicit coverage required')
    _unique([item.id for item in definitions])
    by_id = {item.id: item for item in definitions}
    rows = {}
    for row in observations:
        if row.owner not in owners or row.metric_id not in by_id:
            raise ValueError('Observation outside explicit owner/metric scope')
        definition = by_id[row.metric_id]
        if (row.period != coverage.period or row.observed_on > coverage.observed_on
                or row.observed_on < row.period.start):
            raise ValueError('Observation period/date conflict')
        if row.unit != definition.unit or row.currency != definition.currency:
            raise ValueError('Metric unit/currency conflict; no implicit conversion')
        key = (row.owner,row.metric_id)
        if key in rows:
            raise ValueError('Duplicate metric observation; aggregation must be explicit')
        rows[key] = row
    return by_id, rows


def kpi_snapshot(*, definitions: tuple[MetricDefinition, ...], owners: tuple[str, ...],
                 observations: tuple[MetricObservation, ...], coverage: Coverage,
                 require_daily: bool) -> tuple[MetricLine, ...]:
    """Use caller-defined metrics/targets. Missing owner/metric cells stay unknown."""
    if type(coverage) is not Coverage:
        raise ValueError('Explicit coverage required')
    if type(require_daily) is not bool or (require_daily and (coverage.period.end-coverage.period.start).days != 1):
        raise ValueError('Daily KPI needs an explicit single-day period')
    by_id, rows = _metric_inputs(definitions,owners,observations,coverage)
    output = []
    for owner in owners:
        for definition in definitions:
            row = rows.get((owner,definition.id))
            value = row.value if row else None
            status = 'unknown' if value is None else 'known' if (
                coverage.complete and row.observed_on >= coverage.period.end) else 'provisional'
            target_state = 'unknown'
            if status == 'known':
                if definition.direction == 'informational':
                    target_state = 'informational'
                elif definition.target is not None:
                    met = value >= definition.target if definition.direction == 'at_least' else value <= definition.target
                    target_state = 'met' if met else 'missed'
            output.append(MetricLine(owner,definition.id,value,definition.target,definition.unit,
                definition.currency,status,target_state,(coverage.source_id,)+((row.source_id,) if row else ())))
    return tuple(output)


@dataclass(frozen=True)
class FormValue:
    observation: MetricObservation
    submitted_at: datetime | None

    def __post_init__(self):
        if type(self.observation) is not MetricObservation:
            raise ValueError('Validated form metric observation required')
        if self.submitted_at is not None:
            _aware(self.submitted_at)


@dataclass(frozen=True)
class FormLine:
    owner: str
    metric_id: str
    reported: Decimal | None
    accounted: Decimal | None
    difference: Decimal | None
    comparison: str
    submission_state: str
    question: str | None
    sources: tuple[str, ...]


def _local_slot(day, zone_name, hour):
    _day(day)
    if type(zone_name) is not str:
        raise ValueError('Explicit IANA time zone required')
    zone = ZoneInfo(zone_name)
    return _aware(datetime.combine(day,time(hour),zone))


def monday_form_cutoff(*, monday: date, timezone_name: str) -> datetime:
    if _day(monday).weekday() != 0:
        raise ValueError('Explicit Monday required')
    return _local_slot(monday,timezone_name,15)


def reconcile_forms(*, definitions: tuple[MetricDefinition, ...], owners: tuple[str, ...],
                    forms: tuple[FormValue, ...], account: tuple[MetricObservation, ...],
                    coverage: Coverage, due_at: datetime, as_of: datetime) -> tuple[FormLine, ...]:
    """Pure before/after-cutoff questions, not notifications or form intake.

    Matching is exact owner+metric+period, with declared unit/currency equality.
    No missing form/account record becomes zero. Incomplete coverage prevents
    final numeric differences. A recorded value without submission time is not
    a proven submitted form and does not support a final comparison.
    """
    _aware(due_at); _aware(as_of); _records(forms,FormValue)
    if type(coverage) is not Coverage:
        raise ValueError('Explicit coverage required')
    if coverage.observed_on > as_of.astimezone(due_at.tzinfo).date():
        raise ValueError('Future coverage observation')
    _, accounts = _metric_inputs(definitions,owners,account,coverage)
    _, reported = _metric_inputs(definitions,owners,tuple(item.observation for item in forms),coverage)
    form_by_key = {(item.observation.owner,item.observation.metric_id):item for item in forms}
    now_utc, due_utc = as_of.astimezone(timezone.utc), due_at.astimezone(timezone.utc)
    for form in forms:
        if form.submitted_at is not None and form.submitted_at.astimezone(timezone.utc) > now_utc:
            raise ValueError('Future form submission')
    result = []
    for owner in owners:
        for definition in definitions:
            key = owner,definition.id
            form, left, right = form_by_key.get(key),reported.get(key),accounts.get(key)
            a, b = left.value if left else None, right.value if right else None
            submission = ('missing_after_cutoff' if now_utc >= due_utc else 'pending') if form is None else (
                'unknown_time' if form.submitted_at is None else
                'on_time' if form.submitted_at.astimezone(timezone.utc) <= due_utc else 'late')
            difference, question = None,None
            if (not coverage.complete or (left and left.observed_on < coverage.period.end)
                    or (right and right.observed_on < coverage.period.end)
                    or (form and form.submitted_at is not None
                        and form.submitted_at.astimezone(due_at.tzinfo).date() < coverage.period.end)):
                comparison = 'provisional'
            elif left is None:
                comparison = 'missing_form'
            elif right is None:
                comparison = 'missing_account'
            elif a is None or b is None or submission == 'unknown_time':
                comparison = 'unknown'
            else:
                with localcontext(_CONTEXT):
                    difference = a-b
                comparison = 'match' if difference == 0 else 'discrepancy'
            if comparison == 'discrepancy':
                question = 'Уточните расхождение по показателю «'+definition.label+'» между бланком и учётом.'
            elif submission == 'missing_after_cutoff':
                question = 'К сроку нет подтверждённого бланка по показателю «'+definition.label+'». Уточните статус.'
            elif comparison in ('missing_account','unknown'):
                question = 'Нужны подтверждённые данные по показателю «'+definition.label+'».'
            sources = (coverage.source_id,)+((left.source_id,) if left else ())+((right.source_id,) if right else ())
            result.append(FormLine(owner,definition.id,a,b,difference,comparison,submission,question,sources))
    return tuple(result)


@dataclass(frozen=True)
class TaskOutcome:
    id: str
    owner: str
    created_on: date
    due_on: date | None
    completion_state: str  # completed, not_completed, unknown
    completed_on: date | None
    observed_on: date
    source_id: str

    def __post_init__(self):
        _id(self.id); _id(self.owner); _id(self.source_id)
        _day(self.created_on); _day(self.observed_on)
        if self.created_on > self.observed_on:
            raise ValueError('Task observed before creation')
        if self.due_on is not None:
            _day(self.due_on)
        if self.completion_state not in ('completed','not_completed','unknown'):
            raise ValueError('Explicit completion state required')
        if self.completion_state == 'completed':
            _day(self.completed_on)
            if not self.created_on <= self.completed_on <= self.observed_on:
                raise ValueError('Completion date outside observed task history')
        elif self.completed_on is not None:
            raise ValueError('Only completed state can declare completion date')


@dataclass(frozen=True)
class MonthlyLine:
    owner: str
    status: str
    observed_tasks: int
    unknown_due: int
    unknown_completion: int
    due_in_period: int | None
    completed_in_period: int | None
    incomplete_at_period_end: int | None
    overdue_at_period_end: int | None
    late_completions: int | None
    sources: tuple[str, ...]


def monthly_snapshot(*, owners: tuple[str, ...], records: tuple[TaskOutcome, ...],
                     coverage: Coverage) -> tuple[MonthlyLine, ...]:
    """Historical task-event inputs, not inferences from a current task snapshot.

    due cohort: deadline in month. Completed count: completion date in month.
    Incomplete at month end: due cohort completed later or explicitly not completed.
    Overdue at the exclusive month-end boundary: this incomplete due cohort,
    including the now-finished last calendar day. Late completions: due cohort
    completed after its deadline AND before month end (not future-month events).
    Missing due/completion evidence prevents a final monthly aggregate.
    """
    _owners(owners); _records(records,TaskOutcome)
    if type(coverage) is not Coverage:
        raise ValueError('Explicit coverage required')
    period = coverage.period
    next_month = date(period.start.year+1,1,1) if period.start.month == 12 else date(period.start.year,period.start.month+1,1)
    if period.start.day != 1 or period.end != next_month:
        raise ValueError('Monthly report requires one exact calendar month')
    _unique([row.id for row in records])
    if any(row.owner not in owners or row.observed_on > coverage.observed_on for row in records):
        raise ValueError('Task outside explicit report observation/owner scope')
    output = []
    for owner in owners:
        rows = [row for row in records if row.owner == owner and row.created_on < period.end]
        unknown_due = sum(row.due_on is None for row in rows)
        unknown_completion = sum(row.completion_state == 'unknown' for row in rows)
        final = (coverage.complete and not unknown_due and not unknown_completion
                 and all(row.observed_on >= period.end for row in rows))
        cohort = [row for row in rows if row.due_on is not None and period.start <= row.due_on < period.end]
        incomplete = [row for row in cohort if row.completed_on is None or row.completed_on >= period.end]
        sources = tuple(dict.fromkeys((coverage.source_id,*(row.source_id for row in rows))))
        output.append(MonthlyLine(owner,'final' if final else 'provisional',len(rows),unknown_due,unknown_completion,
            len(cohort) if final else None,
            sum(row.completed_on is not None and period.start <= row.completed_on < period.end for row in rows) if final else None,
            len(incomplete) if final else None,
            len(incomplete) if final else None,
            sum(row.completed_on is not None and row.due_on < row.completed_on < period.end for row in cohort) if final else None,
            sources))
    return tuple(output)


@dataclass(frozen=True)
class AgendaFact:
    id: str
    text: str
    kind: str  # metric, overdue, flag, question
    status: str  # confirmed, provisional, unknown
    priority: int
    sources: tuple[str, ...]

    def __post_init__(self):
        _id(self.id); _text(self.text)
        if self.kind not in ('metric','overdue','flag','question') or self.status not in ('confirmed','provisional','unknown'):
            raise ValueError('Explicit agenda kind and fact status required')
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError('Explicit bounded caller priority required')
        if type(self.sources) is not tuple or not 1 <= len(self.sources) <= 10:
            raise ValueError('Explicit bounded provenance required')
        for source in self.sources:
            _id(source)
        _unique(self.sources)


@dataclass(frozen=True)
class AgendaPreview:
    as_of: datetime
    facts: tuple[AgendaFact, ...]
    omitted: int

    def __post_init__(self):
        _aware(self.as_of); _records(self.facts,AgendaFact,50)
        _unique([fact.id for fact in self.facts])
        if type(self.omitted) is not int or not 0 <= self.omitted <= _MAX_RECORDS:
            raise ValueError('Bounded explicit omitted count required')

    @property
    def requires_review(self):
        return True

    @property
    def live_send_authorized(self):
        return False


def agenda_preview(*, facts: tuple[AgendaFact, ...], as_of: datetime, max_items: int) -> AgendaPreview:
    _aware(as_of); _records(facts,AgendaFact)
    _unique([fact.id for fact in facts])
    if type(max_items) is not int or not 1 <= max_items <= 50:
        raise ValueError('Explicit bounded agenda limit required')
    ordered = tuple(sorted(facts,key=lambda fact:(fact.priority,fact.id)))
    return AgendaPreview(as_of,ordered[:max_items],max(0,len(ordered)-max_items))


@dataclass(frozen=True)
class DigestPreview:
    scheduled_for: datetime
    generated_at: datetime
    due_today: bool
    lines: tuple[str, ...]
    sources: tuple[str, ...]

    @property
    def requires_review(self):
        return True

    @property
    def live_send_authorized(self):
        return False


def digest_preview(*, agenda: AgendaPreview, day: date, timezone_name: str) -> DigestPreview:
    """At most six physical lines; 09:00 is metadata, never a dispatch schedule."""
    if type(agenda) is not AgendaPreview:
        raise ValueError('Validated agenda preview required')
    slot = _local_slot(day,timezone_name,9)
    local_now = agenda.as_of.astimezone(slot.tzinfo)
    needs_footer = len(agenda.facts)+agenda.omitted > 5
    selected = agenda.facts[:4 if needs_footer else 5]
    lines = ['GOLDWAY · '+day.isoformat()+' · предварительный обзор']
    sources = []
    for fact in selected:
        label = {'confirmed':'по источнику','provisional':'неполные данные','unknown':'нет данных'}[fact.status]
        lines.append('['+label+'] '+fact.text+' · '+fact.sources[0])
        sources.extend(fact.sources)
    omitted = len(agenda.facts)-len(selected)+agenda.omitted
    if omitted:
        lines.append('Ещё пунктов для проверки: '+str(omitted))
    if not selected and not omitted:
        lines.append('Нет переданных пунктов; это не подтверждение отсутствия проблем.')
    return DigestPreview(slot,agenda.as_of,local_now.date()==day and local_now>=slot,
                         tuple(lines),tuple(dict.fromkeys(sources)))
