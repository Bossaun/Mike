"""Bounded read-only counts of prospectively registered task transitions.

The selected cohort is never the whole business. Observation timestamps are
registration times, not actual work completion dates. No titles, message/source
bodies, constructors, migrations, refreshes, writes or historical ACL grants.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
import sqlite3
from zoneinfo import ZoneInfo

from .intake import validate_team
from .knowledge import KnowledgeIndex
from . import operational_reports as reports
from .task_transition_history import SCHEMA_VERSION, _hash, _SOURCES, _STATUSES
from .work_context import WORK_PREFIX, _readable, work_id

MAX_TASKS = 64
MAX_HISTORY = 1024
MAX_TASK_HISTORY = 64
MAX_DOCUMENTS = 256
MAX_QUERIES = 2048
MAX_ROWS = 8192
MAX_BYTES = 4 * 1024 * 1024
_HASH = re.compile(r'[0-9a-f]{64}')


class MonthlyHistoryUnavailable(ValueError):
    def __init__(self):
        super().__init__('monthly_history_unavailable')


def _deny():
    raise MonthlyHistoryUnavailable()


def _identifier(value, maximum=160):
    if (type(value) is not str or not 0 < len(value) <= maximum
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        _deny()
    return value


def _selection(value):
    if type(value) is not tuple or len(value) > MAX_TASKS:
        _deny()
    for item in value:
        _identifier(item)
    if len(set(value)) != len(value):
        _deny()
    return value


def _timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2**53-1:
        _deny()
    return float(value)


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class MonthlyHistoryReport:
    lines: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    snapshot_digest: str
    period: reports.Period
    as_of: datetime
    observation_started_at: float
    selected_tasks: int
    tracked_tasks: int
    unknown_prior_tasks: int
    gap_tasks: int
    registered_creations: int | None
    registered_changes: int | None
    completion_transitions: int | None
    distinct_tasks_with_completion_transition: int | None
    core_observed_tasks: int
    unattributed_tasks: int
    core_status: str = 'provisional'
    scope: str = 'explicit_selected_post_cutoff_cohort'
    coverage_complete: bool = False
    actual_completed_tasks: None = None
    overdue_tasks: None = None


class _Reader:
    def __init__(self, store):
        self.store, self.queries, self.rows, self.bytes = store, 0, 0, 0

    def query(self, sql, params=(), *, maximum=1):
        self.queries += 1
        if self.queries > MAX_QUERIES:
            _deny()
        rows = self.store.execute(sql + ' LIMIT ?', (*params, maximum + 1)).fetchall()
        self.rows += len(rows)
        if len(rows) > maximum or self.rows > MAX_ROWS:
            _deny()
        result = []
        for row in rows:
            values = dict(row)
            self.bytes += sum(len(v.encode()) if isinstance(v, str) else 16 for v in values.values())
            if self.bytes > MAX_BYTES or values.pop('_bad', 0):
                _deny()
            result.append(values)
        return result


def _text(column, maximum, nullable=False):
    check = f"(typeof({column})='text' AND length(CAST({column} AS BLOB))<={maximum})"
    return f'({column} IS NULL OR {check})' if nullable else check


def _integer(column, nullable=False):
    check = f"(typeof({column})='integer' AND {column} BETWEEN 1 AND 9007199254740991)"
    return f'({column} IS NULL OR {check})' if nullable else check


def _numeric(column):
    return f"(typeof({column}) IN ('integer','real') AND {column} BETWEEN 0 AND 9007199254740991)"


def _bounded_columns(spec):
    # Suppress malformed/oversize cell content inside SQLite before transfer.
    fields = [f'CASE WHEN {guard} THEN {name} END AS {name}' for name, guard in spec.items()]
    fields.append('CASE WHEN ' + ' AND '.join(spec.values()) + ' THEN 0 ELSE 1 END AS _bad')
    return ','.join(fields)


_HISTORY_SPEC = {
    'task_ref': _text('task_ref', 74), 'task_number': _integer('task_number'),
    'revision': _integer('revision'),
    'observed_at': _numeric('observed_at'),
    'task_origin': _text('task_origin', 16), 'history_quality': _text('history_quality', 16),
    'source_kind': _text('source_kind', 32),
    'is_completion_transition': "(typeof(is_completion_transition)='integer' AND is_completion_transition IN (0,1))",
    'before_revision': _integer('before_revision', True),
    'before_status': _text('before_status', 32, True), 'status': _text('status', 32),
}
for _prefix in ('before_', ''):
    for _name, _maximum in (('owner_state', 16), ('owner_ref', 71),
                           ('deadline_state', 16), ('deadline', 10), ('deadline_hash', 64)):
        _HISTORY_SPEC[_prefix + _name] = _text(_prefix + _name, _maximum,
                                            _prefix == 'before_' or _name in ('owner_ref', 'deadline', 'deadline_hash'))
_HISTORY_COLUMNS = _bounded_columns(_HISTORY_SPEC)


def _gate(config, sender, index, store, period, as_of):
    validate_team(config)
    if sender not in config['admins'] or type(index) is not KnowledgeIndex or index.store is not store:
        _deny()
    _identifier(config['business'], 128)
    zone = ZoneInfo(config['timezone'])
    if type(as_of) is not datetime or as_of.utcoffset() is None:
        _deny()
    if as_of.astimezone(timezone.utc).astimezone(as_of.tzinfo).replace(tzinfo=None) != as_of.replace(tzinfo=None):
        _deny()
    now = _timestamp(as_of.timestamp())
    if type(period) is not reports.Period or type(period.start) is not date or type(period.end) is not date:
        _deny()
    following = date(period.start.year + 1, 1, 1) if period.start.month == 12 else date(period.start.year, period.start.month + 1, 1)
    if period.start.day != 1 or period.end != following:
        _deny()
    # Permission-bearing config only; deliberately never serialize credentials.
    permission = [config['business'], sender, config['timezone'], config['admins'],
                  [[key, person['telegram_user_id']] for key, person in sorted(config['employees'].items())],
                  config['business_account_user_id'], config['team_intake']]
    return zone, now, permission


def _current(reader, business, ids):
    rows, acl = {}, {}
    for task_id in ids:
        found = reader.query('SELECT id,' + _bounded_columns({
            'public_number': _integer('public_number'), 'revision': _integer('revision')}) + ''',
            CASE WHEN typeof(status)='text' AND length(CAST(status AS BLOB))<=32 THEN status END AS status,
            owner IS NULL AS owner_missing,
            CASE WHEN typeof(owner)='text' AND length(CAST(owner AS BLOB))<=128 THEN owner END AS owner,
            deadline IS NULL AS deadline_missing,
            CASE WHEN typeof(deadline)='text' AND length(CAST(deadline AS BLOB))<=64 THEN deadline END AS deadline
            FROM tasks WHERE business=? AND id=?''', (business, task_id))
        if len(found) != 1:
            _deny()
        row = found[0]
        if any(type(row[field]) is not int or not 1 <= row[field] <= 2**53-1 for field in ('public_number', 'revision')):
            _deny()
        ref = work_id(business, task_id)
        if row['id'] != task_id:
            _deny()
        rows[ref] = row
    exists = reader.query("SELECT 1 AS present FROM sqlite_master WHERE type='table' AND name='intelligence_task_acl'")
    if exists:
        for task_id in ids:
            entry = reader.query('SELECT ' + _bounded_columns({'evidence_ids': _text('evidence_ids', 8192)}) +
                                 ' FROM intelligence_task_acl WHERE business=? AND task_id=?', (business, task_id))
            if entry:
                refs = json.loads(entry[0]['evidence_ids'])
                if type(refs) is not list or len(refs) > 32 or len(set(refs)) != len(refs):
                    _deny()
                for ref in refs:
                    _identifier(ref, 256)
                acl[work_id(business, task_id)] = set(refs)
    return rows, acl


def _documents(reader, references, reader_key, now):
    if len(references) > MAX_DOCUMENTS:
        _deny()
    metadata = []
    for ref in sorted(references):
        chunks = reader.query('SELECT ' + _bounded_columns({'document_key': _text('document_key', 512)}) +
                              ' FROM knowledge_chunks WHERE id=?', (ref,))
        if not chunks:
            _deny()
        key = chunks[0]['document_key']
        docs = reader.query('SELECT ' + _bounded_columns({
            'observed_at': _numeric('observed_at'), 'permission_expires_at': _numeric('permission_expires_at')}) +
            ' FROM knowledge_documents WHERE id=?', (key,))
        grants = reader.query('SELECT 1 AS permitted FROM knowledge_readers WHERE document_key=? AND reader=?',
                              (key, reader_key))
        if (not docs or not grants or _timestamp(docs[0]['observed_at']) > now
                or _timestamp(docs[0]['permission_expires_at']) <= now):
            _deny()
        metadata.append((ref, key, docs[0]['observed_at'], docs[0]['permission_expires_at']))
    return metadata


def _read_snapshot(reader, store, config, sender, index, ids, selected, period, as_of):
    zone, now, permission = _gate(config, sender, index, store, period, as_of)
    business = config['business']
    markers = reader.query('SELECT ' + _bounded_columns({
        'schema_version': _integer('schema_version'), 'started_at': _numeric('started_at'),
        'timezone_name': _text('timezone_name', 128)}) + ' ' +
        'FROM task_history_observation WHERE business=?', (business,))
    if (not markers or markers[0]['schema_version'] != SCHEMA_VERSION
            or type(markers[0]['schema_version']) is not int or markers[0]['timezone_name'] != config['timezone']
            or _timestamp(markers[0]['started_at']) > now):
        _deny()
    cutoff = float(markers[0]['started_at'])
    records, acl = _current(reader, business, ids)
    documents = set().union(*(refs for refs in acl.values())) if acl else set()
    documents = {ref for ref in documents if not ref.startswith(WORK_PREFIX)}
    doc_meta = _documents(reader, documents, 'reader:' + _hash(business, sender), now)
    if _readable(records, acl, 'admin', sender, documents) != set(records):
        _deny()
    histories = {}
    total = 0
    for task_id in selected:
        ref = work_id(business, task_id)
        # Constant-only indexed preflight never transfers archive row payloads.
        reader.query('SELECT 1 AS present FROM task_status_history WHERE business=? AND task_ref=?',
                     (business, ref), maximum=MAX_TASK_HISTORY)
        rows = reader.query('SELECT ' + _HISTORY_COLUMNS +
                            ' FROM task_status_history WHERE business=? AND task_ref=? ORDER BY revision',
                            (business, ref), maximum=MAX_TASK_HISTORY)
        total += len(rows)
        if total > MAX_HISTORY:
            _deny()
        histories[ref] = rows
    state = [permission, markers, records,
             {ref: sorted(refs) for ref, refs in acl.items()}, doc_meta, histories, selected,
             period.start.isoformat(), period.end.isoformat(), as_of.isoformat()]
    evidence = tuple(sorted(set(records) | documents))
    return _fingerprint(state), evidence, records, histories, cutoff, zone


def _metadata(row, prefix=''):
    return tuple(row[prefix + key] for key in ('status', 'owner_state', 'owner_ref',
                                              'deadline_state', 'deadline', 'deadline_hash'))


def _state_valid(row, prefix=''):
    owner, owner_ref = row[prefix+'owner_state'], row[prefix+'owner_ref']
    state, day, digest = (row[prefix+key] for key in ('deadline_state', 'deadline', 'deadline_hash'))
    if row[prefix+'status'] not in _STATUSES | {'unknown'} or owner not in ('known', 'missing', 'unknown'):
        _deny()
    if (owner == 'known' and (not isinstance(owner_ref, str) or not owner_ref.startswith('reader:')
                             or _HASH.fullmatch(owner_ref[7:]) is None)) or (owner != 'known' and owner_ref is not None):
        _deny()
    if state not in ('known', 'missing', 'unknown') or (digest is not None and _HASH.fullmatch(digest) is None):
        _deny()
    if state == 'known':
        if (not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or digest != hashlib.sha256(day.encode()).hexdigest()):
            _deny()
    elif day is not None or (state == 'missing' and digest is not None):
        _deny()


def _current_metadata(row, business):
    owner = row['owner']
    owner_state = 'missing' if row['owner_missing'] else 'known' if owner else 'unknown'
    owner_ref = 'reader:'+_hash(business, owner) if owner_state == 'known' else None
    raw = row['deadline']
    state, day = ('missing' if row['deadline_missing'] else 'unknown'), None
    digest = hashlib.sha256(raw.encode()).hexdigest() if raw is not None else None
    if raw is not None:
        try:
            if date.fromisoformat(raw).isoformat() == raw:
                state, day = 'known', raw
        except ValueError:
            pass
    return (row['status'] if row['status'] in _STATUSES else 'unknown', owner_state,
            owner_ref, state, day, digest)


def _classify(rows, current, business, cutoff, now):
    if not rows:
        return 'unknown_prior'
    prior, quality = None, None
    for row in rows:
        _state_valid(row)
        if (row['task_origin'] not in ('new', 'recurring', 'import', 'existing')
                or row['history_quality'] not in ('from_creation', 'unknown_prior', 'gap')
                or row['source_kind'] not in _SOURCES or not cutoff <= _timestamp(row['observed_at']) <= now
                or row['task_number'] != current['public_number']):
            _deny()
        if row['before_revision'] is None:
            if (prior is not None or row['revision'] != 1 or row['task_origin'] == 'existing'
                    or any(row['before_'+key] is not None for key in ('status','owner_state','owner_ref','deadline_state','deadline','deadline_hash'))):
                _deny()
            completion = 0
        else:
            _state_valid(row, 'before_')
            if row['before_revision'] != row['revision'] - 1:
                _deny()
            completion = int(row['before_status'] in _STATUSES-{'completed'}
                             and row['status'] == 'completed' and row['source_kind'] != 'import')
        if completion != row['is_completion_transition']:
            _deny()
        if prior:
            if (row['observed_at'] < prior['observed_at'] or row['task_origin'] != prior['task_origin']
                    or row['revision'] <= prior['revision']):
                _deny()
            if _metadata(prior) != _metadata(row, 'before_') or (quality == 'gap'):
                quality = 'gap'
            elif quality == 'unknown_prior':
                quality = 'unknown_prior'
            if row['history_quality'] != prior['history_quality'] and row['history_quality'] != 'gap':
                _deny()
        quality = 'gap' if row['history_quality'] == 'gap' else quality or row['history_quality']
        prior = row
    first, last = rows[0], rows[-1]
    if (quality == 'gap' or current['revision'] < last['revision']
            or _current_metadata(current, business) != _metadata(last)):
        return 'gap'
    if (quality != 'from_creation' or first['before_revision'] is not None
            or first['task_origin'] not in ('new', 'recurring')):
        return 'unknown_prior'
    return 'tracked'


def build_monthly_task_history(store, config, sender, existing_index, *, task_ids,
                               dependency_task_ids=(), period, as_of):
    """Use an existing Store/index; caller supplies the exact limited task scope.

    Every consulted task/document reference is returned for normal downstream
    provenance checks. This adapter itself creates no delivery authorization.
    All metadata, ledger and permissions are checked again after projection.
    """
    try:
        selected = _selection(task_ids)
        dependencies = _selection(dependency_task_ids)
        ids = tuple(sorted(set(selected) | set(dependencies)))
        reader = _Reader(store)
        initial = _read_snapshot(reader, store, config, sender, existing_index, ids, selected, period, as_of)
        digest, evidence, current, histories, cutoff, zone = initial
        now = as_of.timestamp()
        classifications = {ref: _classify(rows, current[ref], config['business'], cutoff, now)
                           for ref, rows in histories.items()}
        tracked = [ref for ref, kind in classifications.items() if kind == 'tracked']
        unknown = sum(kind == 'unknown_prior' for kind in classifications.values())
        gaps = sum(kind == 'gap' for kind in classifications.values())
        in_month = lambda row: period.start <= datetime.fromtimestamp(row['observed_at'], zone).date() < period.end
        month_rows = [(ref, row) for ref in tracked for row in histories[ref] if in_month(row)]
        counts = (sum(row['before_revision'] is None for _, row in month_rows),
                  sum(row['before_revision'] is not None for _, row in month_rows),
                  sum(row['is_completion_transition'] for _, row in month_rows),
                  len({ref for ref, row in month_rows if row['is_completion_transition']})) if tracked else (None,)*4
        owners = tuple('reader:'+_hash(config['business'], key) for key in sorted(config['employees']))
        outcomes = []
        for ref in tracked:
            rows = histories[ref]
            owner = rows[0]['owner_ref']
            if owner not in owners or any(row['owner_ref'] != owner for row in rows):
                continue
            deadlines = {(row['deadline_state'], row['deadline'], row['deadline_hash']) for row in rows}
            due = date.fromisoformat(rows[0]['deadline']) if len(deadlines) == 1 and rows[0]['deadline_state'] == 'known' else None
            outcomes.append(reports.TaskOutcome(ref, owner, datetime.fromtimestamp(rows[0]['observed_at'], zone).date(),
                due, 'unknown', None, as_of.astimezone(zone).date(), 'registered:'+digest))
        core = reports.monthly_snapshot(owners=owners, records=tuple(outcomes),
            coverage=reports.Coverage(period, False, as_of.astimezone(zone).date(), 'registered:'+digest))
        if any(line.status != 'provisional' or line.completed_in_period is not None for line in core):
            _deny()
        lines = [f'Зарегистрированные изменения задач · {period.start:%Y-%m}',
                 f'Ограниченная выборка: выбрано {len(selected)}; с создания отслеживаются {len(tracked)}.',
                 f'Наблюдение с {datetime.fromtimestamp(cutoff, zone):%d.%m.%Y %H:%M}. Данные частичные.']
        if tracked:
            lines += [f'За месяц в этой когорте: созданий {counts[0]}; изменений {counts[1]}; переходов в «выполнено» {counts[2]}.',
                      f'Задач с такими переходами {counts[3]}. Переходы не означают фактическое выполнение.']
        else:
            lines.append('В выбранной выборке нет задач с известной историей с создания; месячные счётчики неизвестны.')
        lines += [f'Прежняя история неизвестна для {unknown}; разрыв истории — {gaps}.',
                  'Фактические даты выполнения, проценты и показатели просрочки неизвестны.',
                  f'Срез регистрации: {as_of.astimezone(zone):%d.%m.%Y %H:%M}.']
        if len('\n'.join(lines).encode('utf-16-le')) // 2 > 3500:
            _deny()
        final = _read_snapshot(reader, store, config, sender, existing_index, ids, selected, period, as_of)
        if final[0:2] != initial[0:2]:
            _deny()
        return MonthlyHistoryReport(tuple(lines), evidence, digest, period, as_of, cutoff, len(selected),
            len(tracked), unknown, gaps, *counts, sum(line.observed_tasks for line in core), len(tracked)-len(outcomes))
    except MonthlyHistoryUnavailable:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, sqlite3.Error):
        raise MonthlyHistoryUnavailable() from None
