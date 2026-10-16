"""Opt-in calendar checks. No model, transport, chosen live hours or worker."""
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

PREFIX = 'deadline-policy:'
DIGEST_PREFIX = PREFIX + 'digest:'
ACTIVE = {'confirmed', 'in_progress', 'blocked'}
UNSENT = {'pending', 'retry', 'blocked'}


def enabled(config):
    value = config.get('deadline_policy')
    return isinstance(value, dict) and value.get('enabled') is True


def validate_config(config):
    if 'deadline_policy' not in config:
        return
    value = config['deadline_policy']
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('Deadline policy requires an explicit boolean enabled flag')
    keys = {'enabled', 'owner_hour', 'admin_hour'} if value['enabled'] else {'enabled'}
    optional = {'admin_mode'} if value['enabled'] else set()
    if not keys <= set(value) <= keys | optional:
        raise ValueError('Enabled deadline policy requires explicit owner_hour and admin_hour')
    if value['enabled']:
        if value.get('admin_mode', 'per_task') not in ('per_task', 'digest'):
            raise ValueError('Deadline policy admin_mode must be per_task or digest')
        if any(type(value[k]) is not int or not 0 <= value[k] <= 23 for k in ('owner_hour', 'admin_hour')):
            raise ValueError('Deadline policy hours must be integers from 0 through 23')
        ZoneInfo(config.get('timezone', 'Europe/Moscow'))


def _table(store, name):
    return bool(store.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def current_acl(store, config, task_id, recipient, now):
    """Same task-role/provenance contract as Engine._scheduling_permitted."""
    task = store.execute('SELECT owner FROM tasks WHERE business=? AND id=?', (config['business'], task_id)).fetchone()
    if (not task or recipient not in config.get('employees', {})
            or (recipient not in config.get('admins', ()) and task['owner'] != recipient)):
        return False
    if not _table(store, 'intelligence_task_acl'):
        return True
    row = store.execute('SELECT evidence_ids FROM intelligence_task_acl WHERE business=? AND task_id=?',
                        (config['business'], task_id)).fetchone()
    if not row:
        return True
    try:
        refs = json.loads(row[0])
        if not isinstance(refs, list) or any(not isinstance(v, str) for v in refs):
            return False
        if not refs:
            return True
        from .intelligence import readable_evidence_ids
        return set(refs) <= readable_evidence_ids(store, config, recipient, now)
    except (ValueError, TypeError):
        return False


class DeadlinePolicy:
    def __init__(self, store, config, now, *, permitted=None, create_schema=True):
        validate_config(config)
        self.s, self.c, self.now, self.b = store, config, now, config['business']
        self.zone = ZoneInfo(config.get('timezone', 'Europe/Moscow'))
        self.permitted = permitted or (lambda task, recipient, at: current_acl(store, config, task, recipient, at))
        if enabled(config) and create_schema:
            store.execute('''CREATE TABLE IF NOT EXISTS deadline_policy_slots(
                business TEXT NOT NULL,id TEXT NOT NULL,task_id TEXT NOT NULL,
                deadline TEXT NOT NULL,owner TEXT NOT NULL,kind TEXT NOT NULL,
                recipient TEXT NOT NULL,day TEXT NOT NULL,opens REAL NOT NULL,
                expires REAL NOT NULL,state TEXT NOT NULL,outbox_id INTEGER,
                PRIMARY KEY(business,id))''')
            if self._digest_mode():
                store.execute('''CREATE TABLE IF NOT EXISTS deadline_policy_digests(
                    business TEXT NOT NULL,id TEXT NOT NULL,recipient TEXT NOT NULL,
                    day TEXT NOT NULL,opens REAL NOT NULL,expires REAL NOT NULL,
                    items TEXT NOT NULL,state TEXT NOT NULL,outbox_id INTEGER,
                    PRIMARY KEY(business,id),UNIQUE(business,recipient,day))''')

    def _transaction(self):
        return nullcontext() if self.s.db.in_transaction else self.s.transaction()

    def _task(self, task_id):
        row = self.s.execute('SELECT * FROM tasks WHERE business=? AND id=?', (self.b, task_id)).fetchone()
        return dict(row) if row else None

    def _admin(self):
        admins = self.c.get('admins', [])
        return admins[0] if admins else None

    def _digest_mode(self):
        return enabled(self.c) and self.c['deadline_policy'].get('admin_mode', 'per_task') == 'digest'

    def _window(self, deadline, kind, now):
        try:
            due = date.fromisoformat(deadline)
            if due.isoformat() != deadline:
                return None
            day = due - timedelta(days=kind == 'owner')
            hour = self.c['deadline_policy'][kind + '_hour']
            opening = datetime.combine(day, time(hour), self.zone)
            # Fold zero is a single slot; nonexistent local hours are skipped.
            if opening.astimezone(timezone.utc).astimezone(self.zone).replace(tzinfo=None) != opening.replace(tzinfo=None):
                return None
            end = datetime.combine(day + timedelta(days=1), time(), self.zone)
            if datetime.fromtimestamp(now, self.zone).date() != day:
                return None
            return day.isoformat(), opening.timestamp(), end.timestamp()
        except (ValueError, TypeError, OverflowError):
            return None

    def _eligible(self, task, kind, recipient, now):
        if (not enabled(self.c) or not task or task['status'] not in ACTIVE
                or kind not in ('owner', 'admin') or task['owner'] not in self.c.get('employees', {})
                or task['owner'] not in self.c.get('allowed_live_recipients', ())
                or recipient not in self.c.get('employees', {})
                or recipient not in self.c.get('allowed_live_recipients', ())
                or recipient != (task['owner'] if kind == 'owner' else self._admin())):
            return False
        if _table(self.s, 'c07_series') and self.s.execute(
                "SELECT 1 FROM c07_series WHERE business=? AND task_id=? AND state='active'",
                (self.b, task['id'])).fetchone():
            return False
        source_ids = {task['source_id']} | {r[0] for r in self.s.execute(
            'SELECT source_id FROM task_sources WHERE business=? AND task_id=?', (self.b, task['id']))}
        for source in source_ids:
            row = self.s.execute('SELECT deleted FROM sources WHERE business=? AND id=?', (self.b, source)).fetchone()
            if not row or row['deleted']:
                return False
        return bool(self.permitted(task['id'], task['owner'], now)
                    and self.permitted(task['id'], recipient, now))

    def _c07_covers(self, task, now):
        if (not isinstance(self.c.get('scheduling'), dict) or self.c['scheduling'].get('enabled') is not True
                or not _table(self.s, 'c07_checks')):
            return False
        today = datetime.fromtimestamp(now, self.zone).date()
        for check in self.s.rows("SELECT * FROM c07_checks WHERE business=? AND task_id=? AND owner=? AND state IN ('pending','fired')",
                                 (self.b, task['id'], task['owner'])):
            if (check['source_id'] != task['source_id'] or check['actor'] not in self.c.get('employees', {})
                    or not self.permitted(task['id'], check['actor'], now)):
                continue
            if check['state'] == 'pending' and check['at'] >= now:
                return True
            row = self.s.execute('SELECT id,state FROM outbox WHERE business=? AND dedupe=?',
                                 (self.b, 'reminder:c07:' + check['id'])).fetchone()
            if row and row['state'] in UNSENT | {'sending', 'unknown'}:
                return True
            if row and row['state'] == 'sent' and any(datetime.fromtimestamp(r[0], self.zone).date() == today
                    for r in self.s.execute("SELECT at FROM deliveries WHERE outbox_id=? AND state='sent'", (row['id'],))):
                return True
        return False

    def _valid_slot(self, slot, now):
        task = self._task(slot['task_id'])
        return (not (slot['kind'] == 'admin' and self._digest_mode())
                and self._eligible(task, slot['kind'], slot['recipient'], now)
                and task['owner'] == slot['owner'] and task['deadline'] == slot['deadline']
                and self._window(task['deadline'], slot['kind'], now) == (slot['day'], slot['opens'], slot['expires'])
                and slot['opens'] <= now < slot['expires'])

    def _cancel(self, slot, state):
        if slot['outbox_id'] is not None:
            self.s.execute("UPDATE outbox SET state='cancelled' WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",
                           (self.b, slot['outbox_id']))
        self.s.execute('UPDATE deadline_policy_slots SET state=? WHERE business=? AND id=?', (state, self.b, slot['id']))

    def reconcile(self, now):
        for slot in self.s.rows('SELECT * FROM deadline_policy_slots WHERE business=? AND outbox_id IS NOT NULL', (self.b,)):
            row = self.s.execute('SELECT state FROM outbox WHERE business=? AND id=?', (self.b, slot['outbox_id'])).fetchone()
            if not row:
                self._cancel(slot, 'cancelled')
            elif row['state'] in UNSENT and not self._valid_slot(slot, now):
                self._cancel(slot, 'expired' if now >= slot['expires'] else 'cancelled')
            else:
                state = 'queued' if row['state'] == 'pending' else row['state']
                self.s.execute('UPDATE deadline_policy_slots SET state=? WHERE business=? AND id=?', (state, self.b, slot['id']))

    def _digest_committed(self, day):
        return _table(self.s, 'deadline_policy_digests') and self.s.execute('''SELECT 1
            FROM deadline_policy_digests d JOIN outbox o ON o.business=d.business AND o.id=d.outbox_id
            WHERE d.business=? AND d.day=? AND o.state IN ('sending','unknown','sent')''',
            (self.b, day)).fetchone() is not None

    def _digest_tasks(self, slot, now):
        if (not self._digest_mode() or slot['recipient'] != self._admin()
                or self._window(slot['day'], 'admin', now) != (slot['day'], slot['opens'], slot['expires'])
                or not slot['opens'] <= now < slot['expires']):
            return []
        try:
            items = json.loads(slot['items'])
            if not isinstance(items, list):
                return []
            tasks = []
            for item in items:
                if not isinstance(item, dict) or set(item) != {'task_id', 'owner', 'deadline'}:
                    return []
                task = self._task(item['task_id'])
                if (task and task['owner'] == item['owner'] and task['deadline'] == item['deadline'] == slot['day']
                        and self._eligible(task, 'admin', slot['recipient'], now)):
                    tasks.append(task)
            return tasks
        except (ValueError, TypeError):
            return []

    def _digest_text(self, day, tasks):
        def short(value, limit):
            value = ' '.join(str(value).split())
            return value if len(value) <= limit else value[:limit - 1] + '…'
        def units(value):
            return len(value.encode('utf-16-le')) // 2
        text = f'Срок сегодня ({day}), результат ещё не зарегистрирован:\n'
        for index, task in enumerate(tasks):
            owner = self.c['employees'][task['owner']].get('name', task['owner'])
            line = f'\n№{task["public_number"]} · {short(task["title"], 80)} · {" ".join(str(owner).split())}'
            if units(text + line) > 3900:
                text += f'\nЕщё {len(tasks) - index} доступных задач.'
                break
            text += line
        return text

    def _refresh_digest(self, slot, row, now):
        if row['state'] not in UNSENT:
            return False
        tasks = self._digest_tasks(slot, now)
        valid = (row['recipient'] == slot['recipient'] and row['task_id'] is None
                 and row['dedupe'] == DIGEST_PREFIX + slot['id'] and tasks)
        if not valid:
            self.s.execute("UPDATE outbox SET state='cancelled' WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",
                           (self.b, row['id']))
            state = 'expired' if now >= slot['expires'] else 'cancelled'
            self.s.execute('UPDATE deadline_policy_digests SET state=? WHERE business=? AND id=?', (state, self.b, slot['id']))
            return False
        # Per-item task ACL is checked above. Do not attach a stale union ACL to this body.
        self.s.execute("UPDATE outbox SET text=?,parse_mode=NULL WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",
                       (self._digest_text(slot['day'], tasks), self.b, row['id']))
        return True

    def _tick_digests(self, now):
        if _table(self.s, 'deadline_policy_digests'):
            for slot in self.s.rows('SELECT * FROM deadline_policy_digests WHERE business=?', (self.b,)):
                row = self.s.execute('SELECT * FROM outbox WHERE business=? AND id=?', (self.b, slot['outbox_id'])).fetchone()
                if row and row['state'] in UNSENT:
                    self._refresh_digest(slot, row, now)
                else:
                    self.s.execute('UPDATE deadline_policy_digests SET state=? WHERE business=? AND id=?',
                                   (row['state'] if row else 'cancelled', self.b, slot['id']))
        if not self._digest_mode():
            return
        day = datetime.fromtimestamp(now, self.zone).date().isoformat()
        window = self._window(day, 'admin', now)
        if not window or now < window[1] or self._digest_committed(day):
            return
        # A mode change cannot add a digest after a possibly delivered per-task notice.
        if self.s.execute('''SELECT 1 FROM deadline_policy_slots p JOIN outbox o ON o.business=p.business
            AND o.id=p.outbox_id WHERE p.business=? AND p.kind='admin' AND p.day=?
            AND o.state IN ('sending','unknown','sent')''', (self.b, day)).fetchone():
            return
        recipient = self._admin()
        tasks = [task for task in self.s.rows('SELECT * FROM tasks WHERE business=? ORDER BY public_number', (self.b,))
                 if task['deadline'] == day and self._eligible(task, 'admin', recipient, now)]
        if not tasks:
            return
        slot_id = hashlib.sha256(json.dumps([self.b, recipient, day, 'digest']).encode()).hexdigest()
        items = json.dumps([{'task_id': t['id'], 'owner': t['owner'], 'deadline': t['deadline']} for t in tasks])
        added = self.s.execute('''INSERT OR IGNORE INTO deadline_policy_digests
            (business,id,recipient,day,opens,expires,items,state) VALUES(?,?,?,?,?,?,?,'queued')''',
            (self.b, slot_id, recipient, *window, items)).rowcount
        if added:
            self.s.queue(self.b, recipient, self._digest_text(day, tasks), DIGEST_PREFIX + slot_id, now)
            outbox_id = self.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?', (self.b, DIGEST_PREFIX + slot_id)).fetchone()[0]
            self.s.execute('UPDATE deadline_policy_digests SET outbox_id=? WHERE business=? AND id=?', (outbox_id, self.b, slot_id))

    def tick(self):
        if not enabled(self.c):
            return
        now = self.now()
        with self._transaction():
            self.reconcile(now)
            for task in self.s.rows('SELECT * FROM tasks WHERE business=?', (self.b,)):
                for kind, recipient in (('owner', task['owner']), ('admin', self._admin())):
                    if kind == 'admin' and (self._digest_mode() or self._digest_committed(task['deadline'])):
                        continue
                    if not self._eligible(task, kind, recipient, now):
                        continue
                    window = self._window(task['deadline'], kind, now)
                    if not window or now < window[1]:
                        continue
                    # Never replace a possibly delivered policy message by a new semantic slot.
                    if self.s.execute("""SELECT 1 FROM deadline_policy_slots p JOIN outbox o ON o.id=p.outbox_id
                        AND o.business=p.business WHERE p.business=? AND p.task_id=? AND p.kind=?
                        AND o.state IN ('sending','unknown')""", (self.b, task['id'], kind)).fetchone():
                        continue
                    identity = json.dumps([self.b, task['id'], task['deadline'], task['owner'], kind, recipient])
                    slot_id = hashlib.sha256(identity.encode()).hexdigest()
                    covered = kind == 'owner' and self._c07_covers(task, now)
                    added = self.s.execute('''INSERT OR IGNORE INTO deadline_policy_slots
                        (business,id,task_id,deadline,owner,kind,recipient,day,opens,expires,state)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (self.b, slot_id, task['id'], task['deadline'], task['owner'], kind, recipient,
                         *window, 'covered_by_agreed_check' if covered else 'queued')).rowcount
                    if not added or covered:
                        continue
                    number = int(task['public_number'])
                    text = (f'По задаче №{number} срок завтра ({task["deadline"]}). Сообщите результат или препятствие.'
                            if kind == 'owner' else
                            f'По задаче №{number} срок сегодня ({task["deadline"]}); результат ещё не зарегистрирован.')
                    self.s.queue(self.b, recipient, text, PREFIX + slot_id, now, task['id'])
                    outbox_id = self.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?', (self.b, PREFIX + slot_id)).fetchone()[0]
                    self.s.execute('UPDATE deadline_policy_slots SET outbox_id=? WHERE business=? AND id=?', (outbox_id, self.b, slot_id))
                    if kind == 'owner':
                        # Replace only definitely-unsent generic checks for this owner/task.
                        generic_prefix = 'reminder:' + task['id'] + ':'
                        self.s.execute("""UPDATE outbox SET state='cancelled' WHERE business=? AND task_id=?
                            AND recipient=? AND state IN ('pending','retry','blocked') AND created<=?
                            AND substr(dedupe,1,?)=? AND dedupe NOT LIKE 'reminder:c07:%'
                            AND dedupe NOT LIKE 'reminder:c07-prompt:%'""",
                            (self.b, task['id'], recipient, now, len(generic_prefix), generic_prefix))
                        self.s.execute('UPDATE tasks SET last_reminder=? WHERE business=? AND id=?', (now, self.b, task['id']))
            self._tick_digests(now)


def permit_delivery(store, config, row, now):
    """Pre-claim guard: never creates policy schema, only cancels unsent rows."""
    if not row['dedupe'].startswith(PREFIX):
        return True
    if row['dedupe'].startswith(DIGEST_PREFIX):
        if not _table(store, 'deadline_policy_digests'):
            store.execute("UPDATE outbox SET state='cancelled' WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",
                          (config['business'], row['id']))
            return False
        slot = store.execute('SELECT * FROM deadline_policy_digests WHERE business=? AND outbox_id=?',
                             (config['business'], row['id'])).fetchone()
        if slot:
            return DeadlinePolicy(store, config, lambda: now, create_schema=False)._refresh_digest(slot, row, now)
        store.execute("UPDATE outbox SET state='cancelled' WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",
                      (config['business'], row['id']))
        return False
    slot = None
    if _table(store, 'deadline_policy_slots'):
        found = store.execute('SELECT * FROM deadline_policy_slots WHERE business=? AND outbox_id=?',
                               (config['business'], row['id'])).fetchone()
        slot = dict(found) if found else None
    policy = DeadlinePolicy(store, config, lambda: now, create_schema=False)
    if (slot and row['task_id'] == slot['task_id'] and row['recipient'] == slot['recipient']
            and row['dedupe'] == PREFIX + slot['id'] and policy._valid_slot(slot, now)):
        if slot['kind'] != 'owner' or not policy._c07_covers(policy._task(slot['task_id']), now):
            return True
    if row['state'] in UNSENT:
        store.execute("UPDATE outbox SET state='cancelled' WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",
                      (config['business'], row['id']))
        if slot:
            state = 'expired' if now >= slot['expires'] else 'cancelled'
            store.execute('UPDATE deadline_policy_slots SET state=? WHERE business=? AND id=?',
                          (state, config['business'], slot['id']))
    return False
