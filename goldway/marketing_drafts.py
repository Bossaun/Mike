"""Pure marketing drafts from supplied observations; no publishing or inference."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

COVERAGE = ('complete','partial','unknown')


class DraftError(ValueError):
    pass


def _text(value, limit=300):
    if type(value) is not str or not value.strip() or len(value)>limit:
        raise DraftError('invalid_bounded_text')


def _id(value):
    _text(value,128)
    if value!=value.strip() or any(ord(c)<32 for c in value):raise DraftError('invalid_identifier')


def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None:raise DraftError('aware_timestamp_required')
    return value.astimezone(timezone.utc)


def _duration(value):
    if type(value) is not timedelta or not timedelta(0)<value<=timedelta(days=366):raise DraftError('invalid_duration')


def _tuple(value,cls,maximum,minimum=0):
    if type(value) is not tuple or not minimum<=len(value)<=maximum or any(type(x) is not cls for x in value):raise DraftError('invalid_bounded_records')


def _unique(records,key='id'):
    if len({getattr(r,key) for r in records})!=len(records):raise DraftError('duplicate_record')


def _coverage(value):
    if type(value) is not str or value not in COVERAGE:raise DraftError('invalid_coverage')


def _decimal(value):
    if type(value) not in (str,Decimal) or (type(value) is str and (not value or len(value)>100 or value!=value.strip())):raise DraftError('explicit_decimal_required')
    try:result=Decimal(value)
    except InvalidOperation:raise DraftError('invalid_decimal') from None
    if not result.is_finite() or len(result.as_tuple().digits)>50 or abs(result.as_tuple().exponent)>18:raise DraftError('invalid_decimal')
    return result


@dataclass(frozen=True)
class Publication:
    id: str
    channel_id: str
    published_at: datetime
    source_id: str

    def __post_init__(self):
        _id(self.id);_id(self.channel_id);_id(self.source_id)
        object.__setattr__(self,'published_at',_aware(self.published_at))


@dataclass(frozen=True)
class PublicationObservation:
    channel_id: str
    observed_at: datetime
    coverage_start: datetime
    coverage_end: datetime
    coverage: str
    source_id: str
    publications: tuple[Publication,...]

    def __post_init__(self):
        _id(self.channel_id);_id(self.source_id);_coverage(self.coverage)
        for name in ('observed_at','coverage_start','coverage_end'):object.__setattr__(self,name,_aware(getattr(self,name)))
        if not self.coverage_start<self.coverage_end<=self.observed_at:raise DraftError('invalid_coverage_window')
        _tuple(self.publications,Publication,200);_unique(self.publications)
        if any(p.channel_id!=self.channel_id or not self.coverage_start<=p.published_at<self.coverage_end for p in self.publications):raise DraftError('publication_outside_coverage')


@dataclass(frozen=True)
class PublicationGap:
    channel_id: str
    state: str
    as_of: datetime | None
    window_start: datetime | None
    coverage: str
    window_publication_count: int | None
    last_observed_publication: datetime | None
    source_ids: tuple[str,...]
    review_required: bool = field(default=True,init=False)
    live_authorized: bool = field(default=False,init=False)


def publication_gap(observation: PublicationObservation | None, *, channel_id: str,
                    now: datetime, threshold: timedelta, max_observation_age: timedelta) -> PublicationGap:
    """Absent publication only within fully observed [as_of-threshold,as_of).

    as_of is coverage_end, never silently now. An observation lag remains visible.
    Missing/stale/partial or too-short coverage cannot prove absence.
    """
    _id(channel_id);now=_aware(now);_duration(threshold);_duration(max_observation_age)
    if observation is None:return PublicationGap(channel_id,'unknown',None,None,'missing',None,None,())
    if type(observation) is not PublicationObservation:raise DraftError('validated_observation_required')
    if observation.channel_id!=channel_id:raise DraftError('wrong_channel')
    if observation.observed_at>now:raise DraftError('future_observation')
    try:start=observation.coverage_end-threshold
    except OverflowError:raise DraftError('window_outside_supported_dates') from None
    recent=[p for p in observation.publications if start<=p.published_at<observation.coverage_end]
    fresh=now-observation.observed_at<=max_observation_age and now-observation.coverage_end<=max_observation_age
    state=('unknown' if not fresh else 'recent_publication' if recent else
           'gap' if observation.coverage=='complete' and observation.coverage_start<=start else 'unknown')
    latest=max((p.published_at for p in observation.publications),default=None)
    refs=tuple(sorted({observation.source_id}|{p.source_id for p in observation.publications}))
    count=len(recent) if observation.coverage=='complete' and observation.coverage_start<=start and fresh else None
    return PublicationGap(channel_id,state,observation.coverage_end,start,observation.coverage,count,latest,refs)


@dataclass(frozen=True)
class Asset:
    id: str
    description: str
    source_id: str

    def __post_init__(self):
        _id(self.id);_text(self.description,200);_id(self.source_id)


@dataclass(frozen=True)
class DesignBrief:
    id: str
    goal: str | None
    audience: str | None
    format: str | None
    deadline: datetime | None
    assets: tuple[Asset,...]
    assets_confirmed: bool
    unknown_fields: frozenset[str]

    def __post_init__(self):
        _id(self.id)
        expected=set()
        for name in ('goal','audience','format'):
            value=getattr(self,name)
            if value is None:expected.add(name)
            else:_text(value,100 if name=='format' else 1000)
        if self.deadline is None:expected.add('deadline')
        else:object.__setattr__(self,'deadline',_aware(self.deadline))
        _tuple(self.assets,Asset,20);_unique(self.assets)
        if type(self.assets_confirmed) is not bool:raise DraftError('explicit_assets_confirmation_required')
        if not self.assets_confirmed:expected.add('assets')
        if type(self.unknown_fields) is not frozenset or self.unknown_fields!=frozenset(expected):raise DraftError('unknown_fields_mismatch')


@dataclass(frozen=True)
class EditableDraft:
    text: str
    source_ids: tuple[str,...]
    review_required: bool = field(default=True,init=False)
    live_authorized: bool = field(default=False,init=False)


def render_design_brief(brief: DesignBrief) -> EditableDraft:
    if type(brief) is not DesignBrief:raise DraftError('validated_brief_required')
    lines=['ТЗ дизайнеру — черновик, требует проверки.',
        'Цель: '+(brief.goal if brief.goal is not None else '[уточнить]'),
        'Аудитория: '+(brief.audience if brief.audience is not None else '[уточнить]'),
        'Формат: '+(brief.format if brief.format is not None else '[уточнить]'),
        'Срок: '+(brief.deadline.isoformat() if brief.deadline else '[уточнить]'),
        'Материалы:']
    lines.extend(f'- {asset.description} [источник: {asset.source_id}]' for asset in brief.assets)
    if not brief.assets:lines.append('[не предоставлены]' if not brief.assets_confirmed else '[подтверждено: материалов нет]')
    lines.append('Неизвестные поля: '+(', '.join(sorted(brief.unknown_fields)) if brief.unknown_fields else 'нет по переданным данным'))
    return EditableDraft('\n'.join(lines),tuple(sorted({a.source_id for a in brief.assets})))


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    definition: str
    unit: str

    def __post_init__(self):
        _id(self.key);_text(self.label,100);_text(self.definition,500);_text(self.unit,64)


@dataclass(frozen=True)
class Campaign:
    id: str
    start: datetime
    end: datetime
    metrics: tuple[MetricDefinition,...]

    def __post_init__(self):
        _id(self.id)
        object.__setattr__(self,'start',_aware(self.start));object.__setattr__(self,'end',_aware(self.end))
        if not timedelta(0)<self.end-self.start<=timedelta(days=366):raise DraftError('invalid_campaign_period')
        _tuple(self.metrics,MetricDefinition,20,1);_unique(self.metrics,'key')


@dataclass(frozen=True)
class MetricObservation:
    id: str
    campaign_id: str
    metric_key: str
    unit: str
    value: str | Decimal
    start: datetime
    end: datetime
    observed_at: datetime
    coverage: str
    source_id: str

    def __post_init__(self):
        for value in (self.id,self.campaign_id,self.metric_key,self.source_id):_id(value)
        _text(self.unit,64);_coverage(self.coverage)
        object.__setattr__(self,'value',_decimal(self.value))
        for name in ('start','end','observed_at'):object.__setattr__(self,name,_aware(getattr(self,name)))
        if self.start>=self.end or self.observed_at<self.start:raise DraftError('invalid_metric_period')
        if self.coverage=='complete' and self.observed_at<self.end:raise DraftError('incomplete_time_coverage')


@dataclass(frozen=True)
class MetricRow:
    definition: MetricDefinition
    value: Decimal | None
    coverage: str
    source_id: str | None
    observed_at: datetime | None


@dataclass(frozen=True)
class CampaignReport:
    campaign: Campaign
    as_of: datetime
    period_state: str
    rows: tuple[MetricRow,...]
    review_required: bool = field(default=True,init=False)
    live_authorized: bool = field(default=False,init=False)


def campaign_report(campaign: Campaign, observations: tuple[MetricObservation,...], *, now: datetime) -> CampaignReport:
    """Project source-reported metrics for one exact [start,end); no math/ROI.

    Definitions/units are caller supplied, not inferred. Duplicate observations
    for a metric fail closed rather than selecting a preferred source/value.
    """
    if type(campaign) is not Campaign:raise DraftError('validated_campaign_required')
    now=_aware(now);_tuple(observations,MetricObservation,20);_unique(observations);_unique(observations,'metric_key')
    definitions={metric.key:metric for metric in campaign.metrics}
    for item in observations:
        definition=definitions.get(item.metric_key)
        if item.campaign_id!=campaign.id or definition is None:raise DraftError('foreign_or_unknown_metric')
        if item.unit!=definition.unit:raise DraftError('metric_unit_mismatch')
        if item.start!=campaign.start or item.end!=campaign.end:raise DraftError('metric_period_mismatch')
        if item.observed_at>now:raise DraftError('future_metric_observation')
    by_key={item.metric_key:item for item in observations}
    rows=tuple(MetricRow(definition,by_key[definition.key].value,by_key[definition.key].coverage,
                        by_key[definition.key].source_id,by_key[definition.key].observed_at)
        if definition.key in by_key else MetricRow(definition,None,'missing',None,None)
        for definition in campaign.metrics)
    state='before_start' if now<campaign.start else 'in_progress' if now<campaign.end else 'closed'
    return CampaignReport(campaign,now,state,rows)


def render_campaign_report(report: CampaignReport) -> EditableDraft:
    if type(report) is not CampaignReport:raise DraftError('validated_campaign_report_required')
    lines=['Итоги акции — черновик, требует проверки.',
        f'Период [включая начало, исключая конец): {report.campaign.start.isoformat()} — {report.campaign.end.isoformat()}',
        'Состояние периода: '+report.period_state]
    for row in report.rows:
        value='[нет наблюдения]' if row.value is None else str(row.value)
        lines.append(f'{row.definition.label}: {value} {row.definition.unit}; покрытие: {row.coverage}.')
        lines.append('Определение: '+row.definition.definition)
        if row.source_id:lines.append('Источник: '+row.source_id)
    return EditableDraft('\n'.join(lines),tuple(sorted({r.source_id for r in report.rows if r.source_id})))
