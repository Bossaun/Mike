import json
import sqlite3
from contextlib import contextmanager

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS events(
 business TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', error TEXT, PRIMARY KEY(business,id));
CREATE TABLE IF NOT EXISTS sources(
 business TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, link TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(business,id));
CREATE TABLE IF NOT EXISTS tasks(
 business TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL, owner TEXT, deadline TEXT,
 next_step TEXT NOT NULL, criterion TEXT NOT NULL, status TEXT NOT NULL,
 source_id TEXT NOT NULL, evidence TEXT, revision INTEGER NOT NULL DEFAULT 1,
 last_reply REAL, last_reminder REAL, created REAL NOT NULL,
 PRIMARY KEY(business,id));
CREATE TABLE IF NOT EXISTS task_sources(
 business TEXT NOT NULL, task_id TEXT NOT NULL, source_id TEXT NOT NULL,
 PRIMARY KEY(business,task_id,source_id));
CREATE TABLE IF NOT EXISTS outbox(
 id INTEGER PRIMARY KEY, business TEXT NOT NULL, recipient TEXT NOT NULL, task_id TEXT,
 text TEXT NOT NULL, dedupe TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
 transport TEXT, message_id TEXT, destination_chat TEXT, destination_connection TEXT, destination_thread INTEGER, parse_mode TEXT, preferred_transport TEXT, error TEXT, created REAL NOT NULL,
 UNIQUE(business,dedupe));
CREATE TABLE IF NOT EXISTS deliveries(
 id INTEGER PRIMARY KEY, outbox_id INTEGER NOT NULL, at REAL NOT NULL,
 state TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS connections(
 business TEXT NOT NULL, id TEXT NOT NULL, enabled INTEGER NOT NULL,
 can_reply INTEGER NOT NULL, bot_user TEXT, PRIMARY KEY(business,id));
CREATE TABLE IF NOT EXISTS chats(
 business TEXT NOT NULL, recipient TEXT NOT NULL, business_chat TEXT, connection TEXT,
 last_inbound REAL, bot_chat TEXT, group_chat TEXT, group_thread_id INTEGER,
 PRIMARY KEY(business,recipient));
CREATE TABLE IF NOT EXISTS usage(
 id INTEGER PRIMARY KEY, business TEXT NOT NULL, at REAL NOT NULL, provider TEXT NOT NULL,
 input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, reserved_cost REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sheet_snapshots(
 business TEXT NOT NULL, task_id TEXT NOT NULL, row_json TEXT NOT NULL,
 PRIMARY KEY(business,task_id));
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY, business TEXT NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL,
 detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_history_observation(
 business TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
 started_at REAL NOT NULL, timezone_name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_status_history(
 business TEXT NOT NULL, task_ref TEXT NOT NULL, task_number INTEGER NOT NULL,
 revision INTEGER NOT NULL, observed_at REAL NOT NULL, task_origin TEXT NOT NULL, history_quality TEXT NOT NULL,
 source_kind TEXT NOT NULL, is_completion_transition INTEGER NOT NULL,
 before_revision INTEGER, before_status TEXT, status TEXT NOT NULL,
 before_owner_state TEXT, before_owner_ref TEXT, owner_state TEXT NOT NULL, owner_ref TEXT,
 before_deadline_state TEXT, before_deadline TEXT, before_deadline_hash TEXT,
 deadline_state TEXT NOT NULL, deadline TEXT, deadline_hash TEXT,
 PRIMARY KEY(business,task_ref,revision));
CREATE INDEX IF NOT EXISTS task_status_history_period ON task_status_history(business,observed_at);
CREATE TRIGGER IF NOT EXISTS task_status_history_no_update BEFORE UPDATE ON task_status_history
 BEGIN SELECT RAISE(ABORT,'task_history_append_only'); END;
CREATE TRIGGER IF NOT EXISTS task_status_history_no_delete BEFORE DELETE ON task_status_history
 BEGIN SELECT RAISE(ABORT,'task_history_append_only'); END;
CREATE TRIGGER IF NOT EXISTS task_history_observation_no_update BEFORE UPDATE ON task_history_observation
 BEGIN SELECT RAISE(ABORT,'task_history_append_only'); END;
CREATE TRIGGER IF NOT EXISTS task_history_observation_no_delete BEFORE DELETE ON task_history_observation
 BEGIN SELECT RAISE(ABORT,'task_history_append_only'); END;
CREATE TABLE IF NOT EXISTS pending_members(
 business TEXT NOT NULL, user_id TEXT NOT NULL, label TEXT NOT NULL, first_seen REAL NOT NULL,
 PRIMARY KEY(business,user_id));
CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns={r[1] for r in self.execute('PRAGMA table_info(outbox)')}
        for column in ('destination_chat','destination_connection','destination_thread','parse_mode','preferred_transport'):
            if column not in columns:
                kind='INTEGER' if column=='destination_thread' else 'TEXT'
                self.execute(f'ALTER TABLE outbox ADD COLUMN {column} {kind}')

        if 'group_thread_id' not in {r[1] for r in self.execute('PRAGMA table_info(chats)')}:
            self.execute('ALTER TABLE chats ADD COLUMN group_thread_id INTEGER')
        if 'public_number' not in {r[1] for r in self.execute('PRAGMA table_info(tasks)')}:
            self.execute('ALTER TABLE tasks ADD COLUMN public_number INTEGER')
        with self.transaction():
            for row in self.rows('SELECT business,id FROM tasks WHERE public_number IS NULL ORDER BY created,rowid'):
                next_number=self.execute('SELECT COALESCE(MAX(public_number),0)+1 FROM tasks WHERE business=?',(row['business'],)).fetchone()[0]
                self.execute('UPDATE tasks SET public_number=? WHERE business=? AND id=?',(next_number,row['business'],row['id']))
            self.execute('CREATE UNIQUE INDEX IF NOT EXISTS task_public_number ON tasks(business,public_number)')
            self.execute('''CREATE TRIGGER IF NOT EXISTS assign_task_number AFTER INSERT ON tasks
                WHEN NEW.public_number IS NULL BEGIN
                UPDATE tasks SET public_number=(SELECT COALESCE(MAX(public_number),0)+1 FROM tasks WHERE business=NEW.business)
                WHERE rowid=NEW.rowid; END''')

    def execute(self, sql, args=()):
        return self.db.execute(sql, args)

    def rows(self, sql, args=()):
        return [dict(r) for r in self.execute(sql, args)]

    @contextmanager
    def transaction(self):
        self.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.execute('COMMIT')
        except BaseException:
            self.execute('ROLLBACK')
            raise

    def event(self, business, id, kind, payload):
        return self.execute(
            'INSERT OR IGNORE INTO events(business,id,kind,payload) VALUES(?,?,?,?)',
            (business, id, kind, json.dumps(payload, ensure_ascii=False))).rowcount == 1

    def queue(self, business, recipient, text, dedupe, now, task_id=None, parse_mode=None, preferred_transport=None):
        return self.execute(
            'INSERT OR IGNORE INTO outbox(business,recipient,text,dedupe,created,task_id,parse_mode,preferred_transport) VALUES(?,?,?,?,?,?,?,?)',
            (business, recipient, text, dedupe, now, task_id,parse_mode,preferred_transport)).rowcount == 1

    def audit(self, business, kind, detail, now):
        self.execute('INSERT INTO audit(business,kind,detail,at) VALUES(?,?,?,?)',
                     (business, kind, json.dumps(detail, ensure_ascii=False), now))
