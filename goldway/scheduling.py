"""Optional, local scheduling state; construction is the only schema opt-in.

The caller supplies current task ACL checks and a transactional task-cloning
callback. No transport, model, calendar deadline, or background worker lives here.
"""

import hashlib
import json
import math
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone


_ACTIVE = {'confirmed', 'in_progress', 'blocked'}
_TEMPLATE_FIELDS = ('title', 'owner', 'deadline', 'next_step', 'criterion',
                    'source_id', 'evidence')
_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS c07_events(
        business TEXT NOT NULL, op TEXT NOT NULL, event_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL, result TEXT NOT NULL,
        PRIMARY KEY(business,op,event_id))''',
    '''CREATE TABLE IF NOT EXISTS c07_checks(
        business TEXT NOT NULL, id TEXT NOT NULL, task_id TEXT NOT NULL,
        actor TEXT NOT NULL, owner TEXT NOT NULL, source_id TEXT NOT NULL,
        at REAL NOT NULL, source_at REAL NOT NULL, created REAL NOT NULL,
        state TEXT NOT NULL, event_id TEXT NOT NULL,
        PRIMARY KEY(business,id))''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS c07_pending_check
        ON c07_checks(business,task_id) WHERE state='pending' ''',
    '''CREATE TABLE IF NOT EXISTS c07_series(
        business TEXT NOT NULL, id TEXT NOT NULL, task_id TEXT NOT NULL,
        actor TEXT NOT NULL, owner TEXT NOT NULL, rule TEXT NOT NULL,
        snapshot TEXT NOT NULL, next_at REAL NOT NULL, source_at REAL NOT NULL,
        created REAL NOT NULL, state TEXT NOT NULL, reason TEXT,
        PRIMARY KEY(business,id))''',
    '''CREATE TABLE IF NOT EXISTS c07_occurrences(
        business TEXT NOT NULL, series_id TEXT NOT NULL,
        occurrence_key TEXT NOT NULL, at REAL NOT NULL, task_id TEXT,
        created REAL NOT NULL,
        PRIMARY KEY(business,series_id,occurrence_key),
        UNIQUE(business,task_id))''',
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _timestamp(value):
    if type(value) not in (int, float):
        raise ValueError('Timestamp must be a finite number')
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError('Timestamp must be a finite number') from None
    if not math.isfinite(result):
        raise ValueError('Timestamp must be a finite number')
    return result


def _identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('A nonempty identifier is required')
    return value


class Scheduler:
    def __init__(self, store, config, now=time.time, *, permitted, spawn):
        if not callable(permitted) or not callable(spawn) or not callable(now):
            raise TypeError('Scheduling requires local permission and spawn callbacks')
        self.s, self.c, self.now = store, config, now
        self.b = config['business']
        self.permitted, self.spawn = permitted, spawn
        # executescript commits an existing transaction; use individual DDL here.
        with self._transaction():
            for statement in _SCHEMA:
                self.s.execute(statement)

    def _transaction(self):
        return nullcontext() if self.s.db.in_transaction else self.s.transaction()

    def _task(self, task_id):
        rows = self.s.rows('SELECT * FROM tasks WHERE business=? AND id=?',
                           (self.b, task_id))
        return rows[0] if rows else None

    def _source_ids(self, task):
        return sorted({task['source_id']} | {
            row['source_id'] for row in self.s.rows(
                'SELECT source_id FROM task_sources WHERE business=? AND task_id=?',
                (self.b, task['id']))})

    def _sources_live(self, task):
        for source_id in self._source_ids(task):
            source = self.s.execute(
                'SELECT deleted FROM sources WHERE business=? AND id=?',
                (self.b, source_id)).fetchone()
            if source is None or source['deleted']:
                return False
        return True

    def _member(self, sender):
        return isinstance(sender, str) and sender in self.c.get('employees', {})

    def _eligible(self, task, sender, now, statuses, *, owner=None, admin=False):
        if not self._member(sender):
            return False
        if admin and sender not in self.c.get('admins', ()):
            return False
        if not task or task['status'] not in statuses:
            return False
        recipient = task['owner']
        if (not self._member(recipient)
                or recipient not in self.c.get('allowed_live_recipients', ())
                or (owner is not None and recipient != owner)):
            return False
        if sender not in self.c.get('admins', ()) and sender != recipient:
            return False
        if not self._sources_live(task):
            return False
        # Evaluate both checks, including when actor and owner happen to match.
        actor_allowed = self.permitted(task['id'], sender, now)
        owner_allowed = self.permitted(task['id'], recipient, now)
        return bool(actor_allowed and owner_allowed)

    def _authorize(self, task_id, sender, now, statuses, *, admin=False):
        task = self._task(task_id)
        if not self._eligible(task, sender, now, statuses, admin=admin):
            raise ValueError('Task is unavailable for this scheduling action')
        return task

    def _replay(self, op, event_id, payload):
        _identifier(event_id)
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        row = self.s.execute(
            'SELECT payload_hash,result FROM c07_events WHERE business=? AND op=? AND event_id=?',
            (self.b, op, event_id)).fetchone()
        if row is not None and row['payload_hash'] != digest:
            raise ValueError('Scheduling event was reused with a different payload')
        return digest, json.loads(row['result']) if row is not None else None

    def _remember(self, op, event_id, digest, result):
        self.s.execute('INSERT INTO c07_events VALUES(?,?,?,?,?)',
                       (self.b, op, event_id, digest, _json(result)))
        return result

    def _id(self, kind, event_id):
        digest = hashlib.sha256(_json([self.b, kind, event_id]).encode()).hexdigest()
        return kind + ':c07:' + digest

    def _cancel_reminders(self, task_id, owner=None, through=None, dedupe=None,
                          *, exclude_c07=False):
        sql = """UPDATE outbox SET state='cancelled' WHERE business=? AND task_id=?
            AND state IN ('pending','retry','blocked') AND dedupe LIKE 'reminder:%'"""
        args = [self.b, task_id]
        if owner is not None:
            sql += ' AND recipient=?'
            args.append(owner)
        if through is not None:
            sql += ' AND created<=?'
            args.append(through)
        if dedupe is not None:
            sql += ' AND dedupe=?'
            args.append(dedupe)
        if exclude_c07:
            sql += " AND dedupe NOT LIKE 'reminder:c07:%'"
        self.s.execute(sql, args)

    @staticmethod
    def _dedupe(check):
        return 'reminder:c07:' + check['id']

    def _cancel(self, check):
        self.s.execute("UPDATE c07_checks SET state='cancelled' WHERE business=? AND id=?",
                       (self.b, check['id']))
        self._cancel_reminders(check['task_id'], check['owner'],
                               dedupe=self._dedupe(check))

    def schedule_check(self, task_id, sender, when_float, event_id, source_at_float):
        task_id, sender = _identifier(task_id), _identifier(sender)
        at, source_at = _timestamp(when_float), _timestamp(source_at_float)
        with self._transaction():
            digest, replay = self._replay('check', event_id,
                                         [task_id, sender, at, source_at])
            if replay is not None:
                return replay
            now = _timestamp(self.now())
            if not now < at <= now + 7 * 86400 or source_at > now:
                raise ValueError('Check must be future, within seven days, with existing evidence')
            task = self._authorize(task_id, sender, now, _ACTIVE)
            latest_source = self.s.execute(
                'SELECT MAX(source_at) FROM c07_checks WHERE business=? AND task_id=?',
                (self.b, task_id)).fetchone()[0]
            if latest_source is not None and source_at < latest_source:
                raise ValueError('An older scheduling source cannot replace a newer promise')
            self.s.execute("UPDATE c07_checks SET state='cancelled' WHERE business=? AND task_id=? AND state='pending'",
                           (self.b, task_id))
            self._cancel_reminders(task_id, through=now)
            self.s.execute('''INSERT INTO c07_checks
                (business,id,task_id,actor,owner,source_id,at,source_at,created,state,event_id)
                VALUES(?,?,?,?,?,?,?,?,?,'pending',?)''',
                (self.b, self._id('check', event_id), task_id, sender, task['owner'],
                 task['source_id'], at, source_at, now, event_id))
            return self._remember('check', event_id, digest,
                                  {'at': at, 'task_id': task_id})

    def cancel_check(self, task_id, sender, event_id):
        task_id, sender = _identifier(task_id), _identifier(sender)
        with self._transaction():
            digest, replay = self._replay('cancel', event_id, [task_id, sender])
            if replay is not None:
                return replay
            now = _timestamp(self.now())
            self._authorize(task_id, sender, now, _ACTIVE)
            checks = self.s.rows("SELECT * FROM c07_checks WHERE business=? AND task_id=? AND state='pending'",
                                 (self.b, task_id))
            for check in checks:
                self._cancel(check)
            self._cancel_reminders(task_id, through=now)
            return self._remember('cancel', event_id, digest, bool(checks))

    def pending(self, task_id):
        rows = self.s.rows("SELECT * FROM c07_checks WHERE business=? AND task_id=? AND state='pending'",
                           (self.b, task_id))
        return dict(rows[0]) if rows else None

    def _check_live(self, check, now):
        task = self._task(check['task_id'])
        return (task is not None and task['source_id'] == check['source_id']
                and self._eligible(task, check['actor'], now, _ACTIVE,
                                   owner=check['owner']))

    def suppress_generic(self, task_id):
        with self._transaction():
            check = self.pending(task_id)
            if check is None:
                return False
            now = _timestamp(self.now())
            if not self._check_live(check, now):
                self._cancel(check)
                return False
            return check['at'] > now

    def progress(self, task_id, sender, received_at, *, message_at=None):
        received_at = _timestamp(received_at)
        message_at = received_at if message_at is None else _timestamp(message_at)
        with self._transaction():
            now = _timestamp(self.now())
            if message_at > received_at or received_at > now:
                raise ValueError('Progress timestamps must precede receipt and current time')
            task = self._task(task_id)
            if (not task or task['owner'] != sender
                    or not self._eligible(task, sender, now, _ACTIVE | {'completed'})):
                return
            for check in self.s.rows("""SELECT * FROM c07_checks WHERE business=? AND task_id=?
                    AND owner=? AND state IN ('pending','fired')
                    AND created<=? AND source_at<=?""",
                    (self.b, task_id, sender, received_at, message_at)):
                if check['state'] == 'pending':
                    self.s.execute("UPDATE c07_checks SET state='cancelled' WHERE business=? AND id=?",
                                   (self.b, check['id']))
                self._cancel_reminders(task_id, sender, through=received_at,
                                       dedupe=self._dedupe(check))
            self._cancel_reminders(task_id, sender, through=received_at, exclude_c07=True)

    @staticmethod
    def _rule(rule):
        if (not isinstance(rule, dict)
                or set(rule) != {'frequency', 'weekdays', 'time', 'timezone'}
                or rule['frequency'] not in ('daily', 'weekly')
                or not isinstance(rule['weekdays'], list)
                or not rule['weekdays']
                or any(type(day) is not int or not 0 <= day <= 6 for day in rule['weekdays'])
                or len(set(rule['weekdays'])) != len(rule['weekdays'])
                or not isinstance(rule['time'], str)
                or not isinstance(rule['timezone'], str)):
            raise ValueError('Invalid recurring rule')
        result = dict(rule, weekdays=sorted(rule['weekdays']))
        if result['frequency'] == 'daily' and result['weekdays'] != list(range(7)):
            raise ValueError('Daily rules require every weekday')
        return result

    @staticmethod
    def _next(rule, after):
        from .reminder_times import next_occurrence
        slot = next_occurrence(rule, datetime.fromtimestamp(after, timezone.utc),
                               inclusive=False)
        if not isinstance(slot, datetime) or slot.utcoffset() is None:
            raise ValueError('Recurring rule did not return an aware datetime')
        result = _timestamp(slot.timestamp())
        if result <= after:
            raise ValueError('Recurring rule did not advance')
        return result

    def _snapshot(self, task):
        return dict({field: task[field] for field in _TEMPLATE_FIELDS},
                    source_ids=self._source_ids(task))

    def register_series(self, task_id, sender, rule, event_id, source_at):
        task_id, sender = _identifier(task_id), _identifier(sender)
        source_at, rule = _timestamp(source_at), self._rule(rule)
        with self._transaction():
            digest, replay = self._replay('series', event_id,
                                         [task_id, sender, rule, source_at])
            if replay is not None:
                return replay
            now = _timestamp(self.now())
            if source_at > now:
                raise ValueError('Recurring evidence must already exist')
            task = self._authorize(task_id, sender, now, {'confirmed'}, admin=True)
            next_at = self._next(rule, now)
            series_id = self._id('series', event_id)
            self.s.execute("UPDATE c07_series SET state='paused',reason='replaced' WHERE business=? AND task_id=? AND state='active'",
                           (self.b, task_id))
            self.s.execute('''INSERT INTO c07_series
                (business,id,task_id,actor,owner,rule,snapshot,next_at,source_at,created,state)
                VALUES(?,?,?,?,?,?,?,?,?,?,'active')''',
                (self.b, series_id, task_id, sender, task['owner'], _json(rule),
                 _json(self._snapshot(task)), next_at, source_at, now))
            return self._remember('series', event_id, digest,
                                  {'id': series_id, 'next_at': next_at})

    def pause_series(self, series_id, sender, event_id):
        series_id, sender = _identifier(series_id), _identifier(sender)
        with self._transaction():
            digest, replay = self._replay('pause', event_id, [series_id, sender])
            if replay is not None:
                return replay
            if not self._member(sender) or sender not in self.c.get('admins', ()):
                raise ValueError('Only a known administrator may pause recurring work')
            changed = self.s.execute("UPDATE c07_series SET state='paused',reason='requested' WHERE business=? AND id=? AND state='active'",
                                     (self.b, series_id)).rowcount
            return self._remember('pause', event_id, digest, bool(changed))

    def _series_live(self, series, now):
        task = self._task(series['task_id'])
        if (task is None or not self._eligible(
                task, series['actor'], now, _ACTIVE | {'completed'},
                owner=series['owner'], admin=True)):
            return False
        snapshot = json.loads(series['snapshot'])
        # Progress/completion replaces task evidence and appends source links.
        # Keep the original evidence frozen for cloning while requiring the
        # authorized instruction and every original source to remain present.
        return (all(task[field] == snapshot[field]
                    for field in _TEMPLATE_FIELDS if field != 'evidence')
                and set(snapshot['source_ids']).issubset(self._source_ids(task)))

    def _current_slot(self, rule, next_at, now):
        # Daily/weekly rules need at most eight days of lookback, even after a
        # long shutdown. Consume just the latest missed slot, then advance past now.
        candidate = self._next(rule, max(next_at - 1, now - 8 * 86400))
        current = next_at
        for _ in range(16):
            if candidate > now:
                return current, candidate
            current = candidate
            candidate = self._next(rule, candidate)
        raise ValueError('Recurring rule produced too many slots')

    @contextmanager
    def _occurrence_transaction(self):
        # A failed cloning callback must also roll back when the caller catches
        # its exception inside an already-open Store transaction.
        self.s.execute('SAVEPOINT c07_occurrence')
        try:
            yield
            self.s.execute('RELEASE SAVEPOINT c07_occurrence')
        except BaseException:
            self.s.execute('ROLLBACK TO SAVEPOINT c07_occurrence')
            self.s.execute('RELEASE SAVEPOINT c07_occurrence')
            raise

    def tick(self):
        effects = {'checks': [], 'occurrences': [], 'paused': [], 'cancelled': []}
        with self._transaction():
            now = _timestamp(self.now())
            checks = self.s.rows("""SELECT * FROM c07_checks WHERE business=? AND
                (state='pending' OR (state='fired' AND EXISTS(
                SELECT 1 FROM outbox WHERE business=c07_checks.business
                AND dedupe='reminder:c07:' || c07_checks.id
                AND state IN ('pending','retry','blocked')))) ORDER BY at,id""", (self.b,))
            for check in checks:
                if not self._check_live(check, now):
                    self._cancel(check)
                    effects['cancelled'].append(check['task_id'])
                    continue
                if check['state'] != 'pending' or check['at'] > now:
                    continue
                task = self._task(check['task_id'])
                text = (f'Какой результат по задаче «{task["title"]}»? '
                        'Что уже сделано и какой следующий шаг?')
                queued = self.s.queue(self.b, check['owner'], text, self._dedupe(check),
                                      now, check['task_id'])
                if queued:
                    self.s.execute('UPDATE tasks SET last_reminder=? WHERE business=? AND id=?',
                                   (now, self.b, check['task_id']))
                    effects['checks'].append({'task_id': check['task_id'], 'at': check['at']})
                self.s.execute("UPDATE c07_checks SET state='fired' WHERE business=? AND id=?",
                               (self.b, check['id']))
                if queued:
                    from .scheduling_anchor import capture_due
                    own = self.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',
                                         (self.b, self._dedupe(check))).fetchone()
                    if own:capture_due(self, check, own[0])
            for series in self.s.rows("SELECT * FROM c07_series WHERE business=? AND state='active' ORDER BY next_at,id", (self.b,)):
                if not self._series_live(series, now):
                    self.s.execute("UPDATE c07_series SET state='paused',reason='review_required' WHERE business=? AND id=?",
                                   (self.b, series['id']))
                    effects['paused'].append(series['id'])
                    continue
                if series['next_at'] > now:
                    continue
                try:
                    rule = self._rule(json.loads(series['rule']))
                    due_at, next_at = self._current_slot(rule, series['next_at'], now)
                except (ValueError, TypeError, KeyError, OverflowError):
                    self.s.execute("UPDATE c07_series SET state='paused',reason='invalid_rule' WHERE business=? AND id=?",
                                   (self.b, series['id']))
                    effects['paused'].append(series['id'])
                    continue
                key = series['id'] + ':' + due_at.hex()
                with self._occurrence_transaction():
                    added = self.s.execute('''INSERT OR IGNORE INTO c07_occurrences
                        (business,series_id,occurrence_key,at,created) VALUES(?,?,?,?,?)''',
                        (self.b, series['id'], key, due_at, now)).rowcount
                    if added:
                        task_id = _identifier(self.spawn(series['task_id'], key, due_at))
                        task = self._task(task_id)
                        if (task_id == series['task_id'] or not self._eligible(
                                task, series['actor'], now, _ACTIVE,
                                owner=series['owner'], admin=True)):
                            raise ValueError('Occurrence callback must create a distinct authorized task')
                        self.s.execute('UPDATE c07_occurrences SET task_id=? WHERE business=? AND series_id=? AND occurrence_key=?',
                                       (task_id, self.b, series['id'], key))
                        effects['occurrences'].append({'series_id': series['id'],
                                                      'task_id': task_id, 'at': due_at})
                    self.s.execute('UPDATE c07_series SET next_at=? WHERE business=? AND id=?',
                                   (next_at, self.b, series['id']))
        return effects
