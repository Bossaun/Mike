"""Prospective metadata for accepted task mutations, never reconstructed history.

Every helper requires its caller's existing write transaction and never commits.
Only fresh canonical status/owner/deadline metadata is read. Observed completion
means a registered status transition, not verified real-world completion time.
"""
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math

from .work_context import work_id

SCHEMA_VERSION = 1
_STATUSES = {'confirmed', 'proposed', 'in_progress', 'blocked', 'completed'}
_SOURCES = {'reply_bot', 'reply_business', 'reply_group', 'reply_other',
            'transcript', 'sheet', 'import', 'recurring'}


class HistoryError(ValueError):
    pass


def _error():
    raise HistoryError('task_history_unavailable')


def _transaction(store):
    if not store.db.in_transaction:
        _error()


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2**53-1:
        _error()
    return float(value)


def _id(value, maximum=160):
    if (type(value) is not str or not 0 < len(value) <= maximum
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        _error()
    return value


def _hash(business, value):
    return hashlib.sha256(json.dumps([business, value]).encode()).hexdigest()


def start_observing(store, business, *, at, timezone_name):
    """Immutable first observer startup; deliberately no baseline/backfill reads."""
    _transaction(store)
    _id(business, 128); _id(timezone_name, 128)
    at = _time(at)
    existing = store.execute('SELECT schema_version,timezone_name FROM task_history_observation WHERE business=?',
                             (business,)).fetchone()
    if existing:
        if existing['schema_version'] != SCHEMA_VERSION or existing['timezone_name'] != timezone_name:
            _error()
        return
    store.execute('INSERT INTO task_history_observation VALUES(?,?,?,?)',
                  (business, SCHEMA_VERSION, at, timezone_name))


@dataclass(frozen=True)
class TaskSnapshot:
    task_ref: str
    task_number: int
    revision: int
    status: str
    owner_state: str
    owner_ref: str | None
    deadline_state: str
    deadline: str | None
    deadline_hash: str | None


def snapshot(store, business, task_id):
    """Bounded primary-key read; never transfer arbitrary bad old deadlines."""
    _transaction(store)
    _id(business, 128); _id(task_id)
    row = store.execute('''SELECT public_number,revision,
        CASE WHEN typeof(status)='text' AND length(CAST(status AS BLOB))<=32 THEN status END AS status,
        owner IS NULL AS owner_missing,
        CASE WHEN typeof(owner)='text' AND length(CAST(owner AS BLOB))<=128 THEN owner END AS owner,
        deadline IS NULL AS deadline_missing,
        CASE WHEN typeof(deadline)='text' AND length(CAST(deadline AS BLOB))<=64 THEN deadline END AS deadline
        FROM tasks WHERE business=? AND id=?''', (business, task_id)).fetchone()
    if row is None:
        return None
    if (type(row['public_number']) is not int or row['public_number'] < 1
            or type(row['revision']) is not int or row['revision'] < 1):
        _error()
    status = row['status'] if row['status'] in _STATUSES else 'unknown'
    owner = row['owner']
    owner_state = 'missing' if row['owner_missing'] else 'known' if owner else 'unknown'
    owner_ref = 'reader:'+_hash(business, owner) if owner_state == 'known' else None
    value = row['deadline']
    deadline, state, fingerprint = None, 'missing' if row['deadline_missing'] else 'unknown', None
    if value is not None:
        # Only a canonical ISO calendar date may enter the plaintext ledger.
        fingerprint = hashlib.sha256(value.encode()).hexdigest()
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                deadline, state = value, 'known'
        except ValueError:
            pass
    return TaskSnapshot(work_id(business, task_id), row['public_number'], row['revision'],
                        status, owner_state, owner_ref, state, deadline, fingerprint)


def _metadata(value):
    if value is None:
        return None
    return (value.status, value.owner_state, value.owner_ref, value.deadline_state,
            value.deadline, value.deadline_hash)


def source_kind(kind, payload):
    if kind == 'transcript':
        return 'transcript'
    return {'bot': 'reply_bot', 'business': 'reply_business', 'group': 'reply_group'}.get(
        payload.get('channel'), 'reply_other')


def append_transition(store, business, task_id, before, *, at, source, creation=None):
    """Fresh post-mutation observation, atomically appended in caller transaction.

    creation is new/recurring/import only after a genuine INSERT. Imported
    completed states and preexisting completed states never become completions.
    Repeated unchanged metadata/replayed duplicate creates append no new row.
    """
    _transaction(store)
    at = _time(at)
    if source not in _SOURCES or creation not in (None, 'new', 'recurring', 'import'):
        _error()
    after = snapshot(store, business, task_id)
    if after is None or (before is not None and type(before) is not TaskSnapshot):
        _error()
    if before is not None:
        if (before.task_ref != after.task_ref or before.task_number != after.task_number
                or after.revision != before.revision+1 or creation is not None):
            _error()
        if _metadata(before) == _metadata(after):
            return False
    elif creation is None or after.revision != 1:
        _error()
    coverage = store.execute('SELECT schema_version,started_at FROM task_history_observation WHERE business=?',
                             (business,)).fetchone()
    if not coverage or coverage['schema_version'] != SCHEMA_VERSION or at < coverage['started_at']:
        _error()
    latest = store.execute('''SELECT * FROM task_status_history
        WHERE business=? AND task_ref=? ORDER BY revision DESC LIMIT 1''',
        (business, after.task_ref)).fetchone()
    origin = creation if creation is not None else latest['task_origin'] if latest else 'existing'
    if latest and (latest['revision'] > after.revision or at < latest['observed_at']):
        _error()
    quality = ('from_creation' if creation in ('new', 'recurring') else 'unknown_prior') if not latest else latest['history_quality']
    if before is not None and latest and latest['revision'] < after.revision:
        prior = (latest['status'], latest['owner_state'], latest['owner_ref'],
                 latest['deadline_state'], latest['deadline'], latest['deadline_hash'])
        if prior != _metadata(before):
            quality = 'gap'
    completion = int(before is not None and before.status in _STATUSES-{'completed'}
                     and after.status == 'completed' and source != 'import')
    row = dict(business=business, task_ref=after.task_ref, task_number=after.task_number,
               revision=after.revision, observed_at=at, task_origin=origin, history_quality=quality, source_kind=source,
               is_completion_transition=completion,
               before_revision=before.revision if before else None,
               before_status=before.status if before else None,
               status=after.status,
               before_owner_state=before.owner_state if before else None,
               before_owner_ref=before.owner_ref if before else None,
               owner_state=after.owner_state, owner_ref=after.owner_ref,
               before_deadline_state=before.deadline_state if before else None,
               before_deadline=before.deadline if before else None,
               before_deadline_hash=before.deadline_hash if before else None,
               deadline_state=after.deadline_state, deadline=after.deadline,
               deadline_hash=after.deadline_hash)
    found = store.execute('SELECT * FROM task_status_history WHERE business=? AND task_ref=? AND revision=?',
                           (business, after.task_ref, after.revision)).fetchone()
    if found:
        if dict(found) != row:
            _error()
        return False
    store.execute(f"INSERT INTO task_status_history({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                  tuple(row.values()))
    return True
