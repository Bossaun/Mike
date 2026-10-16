"""Pure meeting review over caller-confirmed structured input; no NLP or actions.

Spans prove exact source quoting, not semantic extraction or a person's consent.
All preview tasks remain proposed. Reported completion is not external verification.
"""
from dataclasses import dataclass
from datetime import date, timedelta


class ReviewError(ValueError):
    pass


def _text(value, limit=300):
    if type(value) is not str or not value.strip() or len(value)>limit:
        raise ReviewError('invalid_bounded_text')


def _id(value):
    _text(value,128)
    if value != value.strip() or any(ord(c)<32 for c in value):
        raise ReviewError('invalid_identifier')


def _day(value):
    if type(value) is not date:
        raise ReviewError('explicit_calendar_date_required')


def _tuple(value, cls, maximum=100, minimum=0):
    if type(value) is not tuple or not minimum<=len(value)<=maximum or any(type(x) is not cls for x in value):
        raise ReviewError('invalid_bounded_records')


def _unique(records):
    if len({record.id for record in records}) != len(records):
        raise ReviewError('duplicate_record_id')


@dataclass(frozen=True)
class Source:
    id: str
    business: str
    on: date
    lines: tuple[str,...]

    def __post_init__(self):
        _id(self.id);_id(self.business);_day(self.on)
        _tuple(self.lines,str,1000,1)
        if any(len(line)>2000 or '\n' in line or '\r' in line for line in self.lines):
            raise ReviewError('invalid_source_lines')
        try:
            if len('\n'.join(self.lines).encode('utf-8'))>64000:raise ReviewError('source_too_large')
        except UnicodeError:raise ReviewError('invalid_source_unicode') from None


@dataclass(frozen=True)
class Span:
    source_id: str
    first: int
    last: int
    quote: str

    def __post_init__(self):
        _id(self.source_id);_text(self.quote,8000)
        if type(self.first) is not int or type(self.last) is not int or not 1<=self.first<=self.last<=1000 or self.last-self.first>=20:
            raise ReviewError('invalid_line_span')


def _spans(spans):
    _tuple(spans,Span,3,1)
    if len(set(spans))!=len(spans):raise ReviewError('duplicate_span')


@dataclass(frozen=True)
class BoundValue:
    fact_key: str
    value: str
    span: Span
    confirmed: bool

    def __post_init__(self):
        _id(self.fact_key);_text(self.value)
        if type(self.span) is not Span or type(self.confirmed) is not bool:raise ReviewError('invalid_bound_value')


@dataclass(frozen=True)
class Agreement:
    id: str
    fact_key: str
    value: str
    spans: tuple[Span,...]
    confirmed_decision: bool
    owner: BoundValue | None = None
    deadline: BoundValue | None = None

    def __post_init__(self):
        _id(self.id);_id(self.fact_key);_text(self.value);_spans(self.spans)
        if type(self.confirmed_decision) is not bool:raise ReviewError('explicit_decision_flag_required')
        for bound in (self.owner,self.deadline):
            if bound is not None and type(bound) is not BoundValue:raise ReviewError('invalid_bound_value')
            if bound and not any(span.source_id==bound.span.source_id and span.first<=bound.span.first<=bound.span.last<=span.last for span in self.spans):
                raise ReviewError('field_outside_agreement_span')
        if self.deadline and self.deadline.confirmed:
            try:
                if date.fromisoformat(self.deadline.value).isoformat()!=self.deadline.value:raise ValueError()
            except ValueError:raise ReviewError('confirmed_deadline_requires_iso_date') from None


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    spans: tuple[Span,...]
    resolution: BoundValue | None = None

    def __post_init__(self):
        _id(self.id);_text(self.text);_spans(self.spans)
        if self.resolution is not None and type(self.resolution) is not BoundValue:raise ReviewError('invalid_resolution')


@dataclass(frozen=True)
class Claim:
    id: str
    fact_key: str
    value: str
    spans: tuple[Span,...]
    confirmed: bool

    def __post_init__(self):
        _id(self.id);_id(self.fact_key);_text(self.value);_spans(self.spans)
        if type(self.confirmed) is not bool:raise ReviewError('explicit_claim_flag_required')


def _source_map(sources,business):
    _tuple(sources,Source,50,1);_unique(sources)
    if any(source.business!=business for source in sources):raise ReviewError('wrong_business_source')
    return {source.id:source for source in sources}


def _validate_spans(spans,sources,on=None):
    for span in spans:
        source=sources.get(span.source_id)
        if (source is None or span.last>len(source.lines) or (on is not None and source.on!=on)
                or '\n'.join(source.lines[span.first-1:span.last])!=span.quote):
            raise ReviewError('source_span_mismatch')


@dataclass(frozen=True)
class Contradiction:
    fact_key: str
    values: tuple[str,...]
    spans: tuple[Span,...]


@dataclass(frozen=True)
class ProposedTask:
    agreement_id: str
    title: str
    owner: str | None
    deadline: date | None
    spans: tuple[Span,...]
    missing_fields: tuple[str,...]
    conflicted_fields: tuple[str,...]
    status: str = 'proposed'


@dataclass(frozen=True)
class Meeting:
    id: str
    business: str
    on: date
    sources: tuple[Source,...]
    agreements: tuple[Agreement,...]
    questions: tuple[Question,...] = ()
    claims: tuple[Claim,...] = ()

    def __post_init__(self):
        _id(self.id);_id(self.business);_day(self.on)
        sources=_source_map(self.sources,self.business)
        for records,cls in ((self.agreements,Agreement),(self.questions,Question),(self.claims,Claim)):
            _tuple(records,cls);_unique(records)
        if len(self.agreements)+len(self.questions)+len(self.claims)>100:raise ReviewError('meeting_too_large')
        _unique(self.agreements+self.questions+self.claims)
        for record in self.agreements+self.questions+self.claims:
            _validate_spans(record.spans,sources,self.on)
        for agreement in self.agreements:
            for bound in (agreement.owner,agreement.deadline):
                if bound:_validate_spans((bound.span,),sources,self.on)
        for question in self.questions:
            if question.resolution:_validate_spans((question.resolution.span,),sources,self.on)


@dataclass(frozen=True)
class MeetingReview:
    meeting: Meeting
    proposed_tasks: tuple[ProposedTask,...]
    unresolved_questions: tuple[Question,...]
    contradictions: tuple[Contradiction,...]
    tentative_agreement_ids: tuple[str,...]


def review_meeting(meeting: Meeting) -> MeetingReview:
    if type(meeting) is not Meeting:raise ReviewError('validated_meeting_required')
    facts={}
    def add(key,value,spans):facts.setdefault(key,[]).append((value,spans))
    for claim in meeting.claims:
        if claim.confirmed:add(claim.fact_key,claim.value,claim.spans)
    for question in meeting.questions:
        if question.resolution and question.resolution.confirmed:
            bound=question.resolution
            add(bound.fact_key,bound.value,(bound.span,))
    for agreement in meeting.agreements:
        if not agreement.confirmed_decision:continue
        add(agreement.fact_key,agreement.value,agreement.spans)
        for bound in (agreement.owner,agreement.deadline):
            if bound and bound.confirmed:add(bound.fact_key,bound.value,(bound.span,))
    contradictions=tuple(Contradiction(key,tuple(sorted({v for v,_ in values})),
        tuple(dict.fromkeys(span for _,spans in values for span in spans)))
        for key,values in sorted(facts.items()) if len({v for v,_ in values})>1)
    conflicts={item.fact_key for item in contradictions}
    previews=[]
    for agreement in meeting.agreements:
        if not agreement.confirmed_decision or agreement.fact_key in conflicts:continue
        owner=None;deadline=None;missing=[];conflicted=[]
        for name,bound in (('owner',agreement.owner),('deadline',agreement.deadline)):
            if bound and bound.fact_key in conflicts:conflicted.append(name)
            elif bound and bound.confirmed:
                if name=='owner':owner=bound.value
                else:deadline=date.fromisoformat(bound.value)
            else:missing.append(name)
        previews.append(ProposedTask(agreement.id,agreement.value,owner,deadline,agreement.spans,tuple(missing),tuple(conflicted)))
    return MeetingReview(meeting,tuple(previews),tuple(q for q in meeting.questions if not q.resolution or not q.resolution.confirmed or q.resolution.fact_key in conflicts),
        contradictions,tuple(a.id for a in meeting.agreements if not a.confirmed_decision))


@dataclass(frozen=True)
class TaskRecord:
    id: str
    business: str
    agreement_id: str
    recorded_status: str
    spans: tuple[Span,...]

    def __post_init__(self):
        _id(self.id);_id(self.business);_id(self.agreement_id);_spans(self.spans)
        if type(self.recorded_status) is not str or self.recorded_status not in ('proposed','confirmed','in_progress','blocked','completed'):raise ReviewError('invalid_task_status')


@dataclass(frozen=True)
class ResultRecord:
    id: str
    business: str
    task_id: str
    on: date
    outcome: str
    spans: tuple[Span,...]

    def __post_init__(self):
        _id(self.id);_id(self.business);_id(self.task_id);_day(self.on);_spans(self.spans)
        if type(self.outcome) is not str or self.outcome not in ('completed','in_progress','blocked'):raise ReviewError('invalid_result_outcome')


@dataclass(frozen=True)
class TaskProgress:
    task_id: str
    recorded_status: str
    result_state: str
    result_ids: tuple[str,...]
    spans: tuple[Span,...]


@dataclass(frozen=True)
class AgreementProgress:
    agreement_id: str
    state: str
    tasks: tuple[TaskProgress,...]


def compare_week(meeting: Meeting, *, start: date, end: date,
                 tasks: tuple[TaskRecord,...], results: tuple[ResultRecord,...],
                 sources: tuple[Source,...]) -> tuple[AgreementProgress,...]:
    """Compare this supplied meeting's decisions using result dates [start,end).

    Exactly seven days, no wall clock or meeting selection. Most recent explicit
    result date wins; different outcomes on that same date remain a conflict.
    """
    if type(meeting) is not Meeting:raise ReviewError('validated_meeting_required')
    _day(start);_day(end)
    if end-start!=timedelta(days=7) or meeting.on>=end:raise ReviewError('invalid_week_window')
    _tuple(tasks,TaskRecord);_tuple(results,ResultRecord);_unique(tasks);_unique(results)
    source_map=_source_map(sources,meeting.business)
    confirmed={a.id for a in meeting.agreements if a.confirmed_decision}
    conflict_keys={c.fact_key for c in review_meeting(meeting).contradictions}
    task_map={task.id:task for task in tasks}
    for task in tasks:
        if task.business!=meeting.business or task.agreement_id not in confirmed:raise ReviewError('unlinked_or_foreign_task')
        _validate_spans(task.spans,source_map)
        if any(not meeting.on<=source_map[span.source_id].on<end for span in task.spans):
            raise ReviewError('task_provenance_outside_review')
    for result in results:
        if result.business!=meeting.business or result.task_id not in task_map or result.on<meeting.on:raise ReviewError('unlinked_or_foreign_result')
        _validate_spans(result.spans,source_map,result.on)
    output=[]
    for agreement in meeting.agreements:
        if not agreement.confirmed_decision:continue
        progress=[]
        for task in sorted((task for task in tasks if task.agreement_id==agreement.id),key=lambda task:task.id):
            relevant=[r for r in results if r.task_id==task.id and start<=r.on<end]
            latest=max((r.on for r in relevant),default=None)
            selected=sorted((r for r in relevant if r.on==latest),key=lambda r:r.id)
            outcomes={r.outcome for r in selected}
            state=('missing' if not selected else next(iter(outcomes)) if len(outcomes)==1 else 'conflict')
            spans=tuple(dict.fromkeys(task.spans+tuple(span for r in selected for span in r.spans)))
            progress.append(TaskProgress(task.id,task.recorded_status,state,tuple(r.id for r in selected),spans))
        states={p.result_state for p in progress}
        state=('contradiction' if agreement.fact_key in conflict_keys else 'unlinked' if not progress else 'conflict' if 'conflict' in states else 'missing_results' if 'missing' in states
               else 'completed' if states=={'completed'} else 'blocked' if 'blocked' in states else 'in_progress')
        output.append(AgreementProgress(agreement.id,state,tuple(progress)))
    return tuple(output)
