"""Bounded, read-only current Store tasks -> existing operational draft core.

No request matching, runtime hook, source refresh, history reconstruction or send.
The supplied KnowledgeIndex must already exist on the same Store. Read budgets
fail closed: an oversized business register is unavailable, never a full report.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import hashlib
from zoneinfo import ZoneInfo

from .intelligence import reader_key
from .knowledge import KnowledgeIndex
from .operational_reports import AgendaFact, AgendaPreview, DigestPreview, agenda_preview, digest_preview
from .work_context import collect_work_context, readable_work_ids, validate_work_snapshot

MAX_ROWS = 128
MAX_DOCUMENT_GRANTS = 256
MAX_QUERIES = 64
MAX_READ_ROWS = 4096
MAX_READ_BYTES = 4 * 1024 * 1024
LIMITATION = 'Только выборка текущих доступных задач; цифры и история не предоставлены.'
_FIELDS = {'id': 160, 'public_number': 32, 'title': 2048, 'owner': 128,
           'deadline': 64, 'next_step': 2048, 'criterion': 2048, 'status': 32, 'revision': 32}


class AgendaUnavailable(ValueError):
    """Fixed code only; never echo a row or arbitrary exception string."""


@dataclass(frozen=True)
class OperationalAgendaDraft:
    business: str
    agenda: AgendaPreview
    digest: DigestPreview
    scope: str = 'current_authorized_task_records'
    limited: bool = True
    metrics_status: str = 'not_supplied'
    limitation: str = LIMITATION
    snapshot_digest: str = ''

    @property
    def requires_review(self):
        return True

    @property
    def live_send_authorized(self):
        return False


def _fail():
    raise AgendaUnavailable('operational_agenda_unavailable')


def _member(config, sender):
    return (type(sender) is str and 0 < len(sender) <= 128
            and (sender in config.get('admins', ()) or sender in config.get('employees', {})))


class _ReadView:
    """Allowlist precisely the existing work projection's reads, with hard caps.

    Guard text in SQL before returning it to Python. Never select evidence,
    source bodies, messages or knowledge text. Cap the whole business register
    before role filtering so finding a rare owner cannot scan an unbounded archive.
    """
    def __init__(self, store, config, sender):
        self.store, self.business, self.sender = store, config['business'], sender
        self.queries = self.read_rows = self.read_bytes = 0
        self.proof = hashlib.sha256(b'operational-agenda-metadata:v1')

    def _read(self, sql, args, limit):
        self.queries += 1
        if self.queries > MAX_QUERIES:
            _fail()
        rows = self.store.rows(sql, args)
        self.read_rows += len(rows)
        # Field guards ensure each transferred row is itself bounded.
        self.read_bytes += len(json.dumps(rows, ensure_ascii=False, default=str).encode())
        if (len(rows) > limit or self.read_rows > MAX_READ_ROWS
                or self.read_bytes > MAX_READ_BYTES or any(row.get('_invalid') for row in rows)):
            _fail()
        for row in rows:
            row.pop('_invalid', None)
        # Seal only the already bounded metadata projection, never bodies or clock args.
        canonical = sorted(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False) for row in rows)
        self.proof.update(json.dumps([' '.join(sql.split()),canonical],ensure_ascii=False,separators=(',',':')).encode())
        return rows

    def rows(self, sql, args=()):
        normalized = ' '.join(sql.split())
        if args != (self.business,):
            _fail()
        if normalized == 'SELECT '+','.join(_FIELDS)+' FROM tasks WHERE business=?':
            condition = ' AND '.join(
                f'({field} IS NULL OR length(CAST({field} AS BLOB))<={limit})'
                for field, limit in _FIELDS.items())
            guarded = ','.join(f'CASE WHEN {condition} THEN {field} END AS {field}' for field in _FIELDS)
            where, parameters = 'business=?', [self.business]
            return self._read(f'SELECT {guarded},NOT ({condition}) AS _invalid FROM tasks '
                              f'WHERE {where} LIMIT {MAX_ROWS + 1}', tuple(parameters), MAX_ROWS)
        if normalized == 'SELECT task_id,evidence_ids FROM intelligence_task_acl WHERE business=?':
            condition = "typeof(task_id)='text' AND length(CAST(task_id AS BLOB))<=160 AND typeof(evidence_ids)='text' AND length(CAST(evidence_ids AS BLOB))<=8192"
            # Only ACL identifiers; the existing helper still handles malformed JSON as denial.
            return self._read(f'SELECT CASE WHEN {condition} THEN task_id END AS task_id,'
                              f'CASE WHEN {condition} THEN evidence_ids END AS evidence_ids,'
                              f'NOT ({condition}) AS _invalid FROM intelligence_task_acl '
                              f'WHERE business=? LIMIT {MAX_ROWS + 1}', args, MAX_ROWS)
        _fail()

    def execute(self, sql, args=()):
        if (' '.join(sql.split()) != "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intelligence_task_acl'"
                or args):
            _fail()
        self.queries += 1
        if self.queries > MAX_QUERIES:
            _fail()
        return self.store.execute(sql)

    def grants(self, index, sender, now):
        if index.store is not self.store:
            _fail()
        # Bound reader associations before freshness filtering; many expired
        # associations must not create an unbounded scan for a few live grants.
        condition = "typeof(document_key)='text' AND length(CAST(document_key AS BLOB))<=512"
        self._read(f"SELECT CASE WHEN {condition} THEN document_key END AS id,"
                   f"NOT ({condition}) AS _invalid FROM knowledge_readers WHERE reader=? "
                   f"LIMIT {MAX_DOCUMENT_GRANTS + 1}",
                   (reader_key(self.business, sender),), MAX_DOCUMENT_GRANTS)
        condition = "typeof(c.id)='text' AND length(CAST(c.id AS BLOB))<=512"
        rows = self._read(f'''SELECT CASE WHEN {condition} THEN c.id END AS id,
            NOT ({condition}) AS _invalid FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id=c.document_key
            JOIN knowledge_readers r ON r.document_key=d.id
            WHERE r.reader=? AND d.observed_at<=? AND d.permission_expires_at>?
            LIMIT {MAX_DOCUMENT_GRANTS + 1}''', (reader_key(self.business, sender), now, now), MAX_DOCUMENT_GRANTS)
        documents = {row['id'] for row in rows}
        return documents | readable_work_ids(self, self.config, sender, documents)


def _fact(item):
    signals = set(item['signals'])
    labels = []
    if 'blocked' in signals:
        labels.append('статус «blocked»')
    if 'overdue' in signals:
        labels.append('срок прошёл')
    if item['proposal']:
        labels.append('предложение, ещё не подтверждено')
    if 'missing_owner' in signals:
        labels.append('нет подтверждённого исполнителя')
    if 'missing_deadline' in signals:
        labels.append('нет распознанного срока')
    if not labels:
        labels.append('статус «'+item['status']+'»')
    number = '№'+str(item['public_number']) if item['public_number'] else 'без номера'
    text = 'В записи задачи '+number+' «'+item['title'][:80]+'»: '+', '.join(labels)+'.'
    # Display order of explicit register signals, not company KPI priorities.
    priority = 0 if 'blocked' in signals else 10 if 'overdue' in signals else 20 if item['proposal'] else 30
    return AgendaFact(item['id'], text, 'overdue' if 'overdue' in signals else 'flag',
                      'confirmed', priority, (item['id'],))


def build_agenda_preview(store, config, sender, existing_index, *, as_of, max_items=5):
    """Return a review draft; regenerate before any future use/delivery.

    Bounds:128 business register rows,128 business ACL rows,
    256 current document grants,64 SQL queries/4096 returned rows/4MiB per call.
    Larger or malformed inputs return one fixed unavailable code. No archive
    coverage, historical completion, business metrics or omitted hidden totals
    are inferred. 09:00 in the digest is existing preview metadata only.
    """
    try:
        if (type(config) is not dict or not _member(config, sender)
                or type(config.get('business')) is not str or not 0 < len(config['business']) <= 128
                or type(existing_index) is not KnowledgeIndex or existing_index.store is not store
                or type(as_of) is not datetime or as_of.utcoffset() is None
                or type(max_items) is not int or not 1 <= max_items <= 5):
            _fail()
        # Prevent impossible wall times and naive/date-only clock inputs.
        restored = as_of.astimezone(timezone.utc).astimezone(as_of.tzinfo)
        if restored.replace(tzinfo=None) != as_of.replace(tzinfo=None):
            _fail()
        now, business = as_of.timestamp(), config['business']
        view = _ReadView(store, config, sender)
        view.config = config
        allowed = view.grants(existing_index, sender, now)
        work = collect_work_context(view, config, sender, allowed, now, 'обзор задач')
        items = work['evidence']
        agenda = agenda_preview(facts=tuple(_fact(item) for item in items), as_of=as_of, max_items=max_items)
        # Reserve a physical line for the honest scope limitation. The core's
        # footer counts only supplied sample facts, never inaccessible tasks.
        digest_agenda = replace(agenda, facts=agenda.facts[:3],
                                omitted=agenda.omitted+max(0, len(agenda.facts)-3))
        digest = digest_preview(agenda=digest_agenda, day=as_of.astimezone(
            ZoneInfo(config.get('timezone', 'UTC'))).date(),
            timezone_name=config.get('timezone', 'UTC'))
        digest = replace(digest, lines=digest.lines+(LIMITATION,),
                         sources=tuple(item['id'] for item in items))
        # Both policy and every projected row must still match before returning
        # content; do not initialize an index, refresh providers or acquire locks.
        if config.get('business') != business or not _member(config, sender):
            _fail()
        fresh = view.grants(existing_index, sender, now)
        for item in items:
            if not validate_work_snapshot(view, config, sender, fresh, item):
                _fail()
        return OperationalAgendaDraft(business, agenda, digest,snapshot_digest=view.proof.hexdigest())
    except AgendaUnavailable:
        raise
    except Exception:
        raise AgendaUnavailable('operational_agenda_unavailable') from None
