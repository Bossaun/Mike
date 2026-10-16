"""Read-only Store boundary for explicitly bound weekly review drafts.

Dates, semantic links and outcomes are caller-confirmed assertions. Canonical
Store rows prove only current registration and exact live source text, never an
independent external outcome or a reconstructed result history.
"""

import json
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta

from .meeting_review import (
    AgreementProgress, Meeting, ResultRecord, ReviewError, Source, Span,
    TaskRecord, compare_week,
)
from .work_context import WORK_PREFIX, readable_work_ids, work_id


MAX_BINDINGS = 100
MAX_CURRENT_ROWS = 1000  # each business task/ACL table, including unrelated rows
MAX_SOURCES = 50
MAX_SOURCE_BYTES = 64000
MAX_DRAFT_CHARS = 16000
LIMITATION = (
    'Черновик недельного разбора по явно связанным зарегистрированным сообщениям. '
    'Даты и смысл связей подтверждены вызывающей стороной; '
    'результаты не проверены независимо. Поручения не создаются.'
)


def _id(value):
    if (type(value) is not str or not 0 < len(value) <= 128
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ReviewError('invalid_binding_identifier')


def _spans(value):
    if (type(value) is not tuple or not 1 <= len(value) <= 3
            or any(type(span) is not Span for span in value) or len(set(value)) != len(value)):
        raise ReviewError('invalid_binding_spans')
    for span in value:
        replace(span)


@dataclass(frozen=True)
class TaskBinding:
    business: str
    agreement_id: str
    task_id: str
    spans: tuple[Span, ...]
    confirmed: bool

    def __post_init__(self):
        for value in (self.business, self.agreement_id, self.task_id):
            _id(value)
        _spans(self.spans)
        if self.confirmed is not True:
            raise ReviewError('explicit_binding_confirmation_required')


@dataclass(frozen=True)
class ResultBinding:
    business: str
    id: str
    task_id: str
    on: date | None
    outcome: str | None
    spans: tuple[Span, ...]
    confirmed: bool

    def __post_init__(self):
        for value in (self.business, self.id, self.task_id):
            _id(value)
        _spans(self.spans)
        if self.confirmed is not True:
            raise ReviewError('explicit_binding_confirmation_required')
        if self.on is not None and type(self.on) is not date:
            raise ReviewError('explicit_result_date_required')
        if self.outcome is not None and (type(self.outcome) is not str or self.outcome not in ('completed', 'in_progress', 'blocked')):
            raise ReviewError('invalid_bound_result_outcome')


@dataclass(frozen=True)
class WeeklyDraft:
    business: str
    meeting_id: str
    start: date
    end: date
    progress: tuple[AgreementProgress, ...]
    as_of: float
    review_only: bool = True
    result_scope: str = 'caller_bound_registered_reports'
    limitation: str = LIMITATION


def _records(values, cls):
    if type(values) is not tuple or len(values) > MAX_BINDINGS or any(type(v) is not cls for v in values):
        raise ReviewError('invalid_bounded_bindings')
    return tuple(replace(value) for value in values)


def _live_source(store, business, source):
    row = store.execute('''SELECT deleted,
        CASE WHEN typeof(body)='text' AND length(CAST(body AS BLOB))<=?
             THEN body END FROM sources WHERE business=? AND id=?''',
        (MAX_SOURCE_BYTES, business, source.id)).fetchone()
    if not row or row[0] != 0 or row[1] != '\n'.join(source.lines):
        raise ReviewError('weekly_source_unavailable')


def _linked(store, business, task_id, spans):
    for span in spans:
        if not store.execute('''SELECT 1 FROM task_sources
            WHERE business=? AND task_id=? AND source_id=?''',
            (business, task_id, span.source_id)).fetchone():
            raise ReviewError('weekly_source_unavailable')


def _sources(meeting, sources):
    if type(sources) is not tuple or len(sources) > MAX_SOURCES or any(type(s) is not Source for s in sources):
        raise ReviewError('invalid_bounded_sources')
    result = {}
    for source in meeting.sources + sources:
        source = replace(source)
        if source.business != meeting.business or (source.id in result and result[source.id] != source):
            raise ReviewError('ambiguous_or_foreign_source')
        result[source.id] = source
    if len(result) > MAX_SOURCES:
        raise ReviewError('invalid_bounded_sources')
    return result


def _check_spans(spans, sources, on=None):
    for span in spans:
        source = sources.get(span.source_id)
        if (source is None or (on is not None and source.on != on)
                or '\n'.join(source.lines[span.first - 1:span.last]) != span.quote
                or span.last > len(source.lines)):
            raise ReviewError('weekly_source_span_mismatch')


def _document_ids(values):
    if (type(values) not in (set, frozenset, tuple) or len(values) > 256
            or any(type(value) is not str or not 0 < len(value) <= 512
                   or value.startswith(WORK_PREFIX) for value in values)):
        raise ReviewError('invalid_document_grants')
    return frozenset(values)


def _member(config, sender):
    return (type(sender) is str and bool(sender)
            and (sender in config.get('admins', ()) or sender in config.get('employees', {})))


def _check_acl_budget(store, business):
    # The shared ACL evaluator reads whole business task/ACL rows. Check their
    # cardinality with bounded constant-only reads before either evaluation.
    for table in ('tasks', 'intelligence_task_acl'):
        if table == 'intelligence_task_acl' and not store.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        rows = store.rows(f'SELECT 1 AS present FROM {table} WHERE business=? LIMIT ?',
                          (business, MAX_CURRENT_ROWS + 1))
        if len(rows) > MAX_CURRENT_ROWS:
            raise ReviewError('weekly_acl_row_budget_exceeded')


class _BoundedACLStore:
    """Cap the shared evaluator's rows too, if state grows after preflight."""

    def __init__(self, store):
        self.store = store

    def execute(self, sql, args=()):
        return self.store.execute(sql, args)

    def rows(self, sql, args=()):
        rows = self.store.rows(sql + ' LIMIT ?', tuple(args) + (MAX_CURRENT_ROWS + 1,))
        if len(rows) > MAX_CURRENT_ROWS:
            raise ReviewError('weekly_acl_row_budget_exceeded')
        return rows


def project_week(store, config, sender, *, meeting, start, end, task_bindings,
                 result_bindings, sources, document_ids, meeting_evidence_ids,
                 meeting_anchor_confirmed, now):
    """Return a bounded draft after current ACL and canonical-source checks.

    The trusted host MUST freshly compute ``document_ids`` for this reader and
    supply the confirmed, nonempty ACL refs belonging to THIS meeting in
    ``meeting_evidence_ids``. The adapter cannot establish that upstream mapping
    from the canonical schema; subset membership is not a source authorization
    discovery mechanism. No anchor is derived from a task, title or source ID.
    Missing/unconfirmed/revoked anchors return no meeting/progress contents.

    Task/result semantics and all dates are explicit caller confirmations. Each
    task/result span additionally needs a current allowed task_sources link.
    The draft must be regenerated before later use; it authorizes no delivery.
    """
    documents = _document_ids(document_ids)
    anchor = _document_ids(meeting_evidence_ids)
    if (not _member(config, sender) or meeting_anchor_confirmed is not True
            or not anchor or not anchor <= documents):
        raise ReviewError('weekly_meeting_access_unavailable')
    if type(meeting) is not Meeting or config.get('business') != meeting.business:
        raise ReviewError('weekly_meeting_access_unavailable')
    if type(now) not in (int, float) or not -1e15 <= now <= 1e15:
        raise ReviewError('explicit_observation_time_required')
    if (type(start) is not date or type(end) is not date
            or end - start != timedelta(days=7) or meeting.on >= end):
        raise ReviewError('invalid_week_window')
    meeting = replace(meeting)
    tasks = _records(task_bindings, TaskBinding)
    results = _records(result_bindings, ResultBinding)
    if len({binding.task_id for binding in tasks}) != len(tasks) or len({binding.id for binding in results}) != len(results):
        raise ReviewError('ambiguous_binding')
    business = meeting.business
    confirmed = {a.id for a in meeting.agreements if a.confirmed_decision}
    task_ids = {binding.task_id for binding in tasks}
    if (any(binding.business != business or binding.agreement_id not in confirmed for binding in tasks)
            or any(binding.business != business or binding.task_id not in task_ids for binding in results)):
        raise ReviewError('unlinked_or_foreign_binding')
    _check_acl_budget(store, business)
    allowed = readable_work_ids(_BoundedACLStore(store), config, sender, documents)
    if any(work_id(business, task_id) not in allowed for task_id in task_ids):
        raise ReviewError('weekly_task_access_unavailable')
    source_map = _sources(meeting, sources)
    used = set()
    for record in meeting.agreements + meeting.questions + meeting.claims:
        spans = record.spans
        for bound in (getattr(record, 'owner', None), getattr(record, 'deadline', None),
                      getattr(record, 'resolution', None)):
            if bound is not None:
                spans += (bound.span,)
        used.update(span.source_id for span in spans)
    task_records, result_records, snapshots = [], [], {}
    for binding in tasks:
        row = store.execute('SELECT status,owner,revision FROM tasks WHERE business=? AND id=?',
                            (business, binding.task_id)).fetchone()
        if row is None:
            raise ReviewError('weekly_task_access_unavailable')
        snapshots[binding.task_id] = tuple(row)
        _check_spans(binding.spans, source_map)
        _linked(store, business, binding.task_id, binding.spans)
        used.update(span.source_id for span in binding.spans)
        task_records.append(TaskRecord(binding.task_id, business, binding.agreement_id, row[0], binding.spans))
    for binding in results:
        _check_spans(binding.spans, source_map, binding.on)
        _linked(store, business, binding.task_id, binding.spans)
        used.update(span.source_id for span in binding.spans)
        # Missing date/outcome remains missing; never infer either from a task,
        # source timestamp or matching title. Current task evidence is no ledger.
        if binding.on is not None and binding.outcome is not None:
            result_records.append(ResultRecord(binding.id, business, binding.task_id,
                                               binding.on, binding.outcome, binding.spans))
    for source_id in sorted(used):
        _live_source(store, business, source_map[source_id])
    progress = compare_week(meeting, start=start, end=end, tasks=tuple(task_records),
                            results=tuple(result_records), sources=tuple(source_map.values()))
    draft = WeeklyDraft(business, meeting.id, start, end, progress, now)
    if len(json.dumps(asdict(draft), ensure_ascii=False, default=str)) > MAX_DRAFT_CHARS:
        raise ReviewError('weekly_draft_too_large')
    # Recheck after projection, before releasing any body or metadata. These are
    # reads only; Store.transaction would acquire an unnecessary writer lock.
    if config.get('business') != business or not _member(config, sender):
        raise ReviewError('weekly_task_access_unavailable')
    _check_acl_budget(store, business)
    fresh_allowed = readable_work_ids(_BoundedACLStore(store), config, sender, documents)
    if any(work_id(business, task_id) not in fresh_allowed for task_id in task_ids):
        raise ReviewError('weekly_task_access_unavailable')
    for task_id, snapshot in snapshots.items():
        row = store.execute('SELECT status,owner,revision FROM tasks WHERE business=? AND id=?',
                            (business, task_id)).fetchone()
        if row is None or tuple(row) != snapshot:
            raise ReviewError('weekly_task_changed')
    for binding in tasks + results:
        _linked(store, business, binding.task_id, binding.spans)
    for source_id in sorted(used):
        _live_source(store, business, source_map[source_id])
    return draft
