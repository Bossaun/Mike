"""Read-only, bounded projections of currently authorized task records.

The records establish that tasks exist, not that a business diagnosis is true.
Callers supply fresh document grants; this module never reads message bodies,
source bodies, external documents, or historical task snapshots.
"""

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WORK_PREFIX = 'work-task:'
MAX_EVIDENCE = 5
MAX_EVIDENCE_CHARS = 6000
_FIELDS = ('id', 'public_number', 'title', 'owner', 'deadline', 'next_step',
           'criterion', 'status', 'revision')
_CLOSED = {'completed', 'cancelled', 'canceled', 'deleted'}


def work_id(business, task_id):
    """A stable, business-scoped reference; never a fabricated source URL."""
    packed = json.dumps([business, task_id], ensure_ascii=False, separators=(',', ':'))
    return WORK_PREFIX + hashlib.sha256(packed.encode()).hexdigest()


def _role(config, sender):
    if not isinstance(sender, str) or not sender:
        return None
    if sender in config.get('admins', ()):
        return 'admin'
    if sender in config.get('employees', {}):
        return 'employee'
    return None


def _ids(values):
    if not isinstance(values, (set, frozenset, list, tuple)):
        return set()
    return {value for value in values if isinstance(value, str) and value}


def _records(store, business):
    # Deliberately omit source_id, evidence, sources, and every message table.
    fields = ','.join(_FIELDS)
    rows = store.rows(f'SELECT {fields} FROM tasks WHERE business=?', (business,))
    records = {work_id(business, row['id']): row for row in rows}
    acl = {}
    exists = store.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='intelligence_task_acl'").fetchone()
    if exists:
        for row in store.rows('SELECT task_id,evidence_ids FROM intelligence_task_acl WHERE business=?',
                              (business,)):
            try:
                refs = json.loads(row['evidence_ids'])
                valid = isinstance(refs, list) and all(isinstance(ref, str) and ref for ref in refs)
            except (TypeError, ValueError):
                valid = False
            # A malformed ACL is a denial, never an ordinary unprotected task.
            acl[work_id(business, row['task_id'])] = set(refs) if valid else None
    return records, acl


def _readable(records, acl, role, sender, document_ids):
    if role is None:
        return set()
    documents = {ref for ref in _ids(document_ids) if not ref.startswith(WORK_PREFIX)}
    eligible = {ref for ref, row in records.items()
                if role == 'admin' or (row['owner'] == sender and row['status'] != 'proposed')}
    # Start with role-eligible nodes, then remove denied/missing dependencies to
    # a fixed point. Self references and fully eligible cycles remain readable.
    while True:
        denied = {ref for ref in eligible
                  if acl.get(ref, set()) is None
                  or not acl.get(ref, set()) <= (documents | eligible)}
        if not denied:
            return eligible
        eligible -= denied


def readable_work_ids(store, config, sender, document_ids):
    """Return current task grants, propagating document/task ACL revocations.

    Supplied work references cannot manufacture grants: only current rows and
    configured membership seed the fixed point. This function performs no writes.
    """
    role = _role(config, sender)
    if role is None:
        return set()
    records, acl = _records(store, config['business'])
    return _readable(records, acl, role, sender, document_ids)


def _bounded(value, limit):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value[:limit * 4])
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def _owner(config, key):
    people = config.get('employees', {})
    if not isinstance(key, str) or not key or len(key) > 128 or key not in people:
        return None
    person = people[key]
    name = person.get('name') if isinstance(person, dict) else person
    return {'key': key, 'name': _bounded(name, 80) or key}


def _zone(config):
    try:
        return ZoneInfo(config.get('timezone', 'UTC'))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return timezone.utc


def _deadline(value, config, now):
    """Date-only deadlines are due for their entire configured local day."""
    if not isinstance(value, str) or not value or len(value) > 64:
        return None, False, float('inf')
    zone = _zone(config)
    current = datetime.fromtimestamp(now, zone)
    try:
        day = date.fromisoformat(value)
        return day.isoformat(), day < current.date(), datetime.combine(day, datetime.min.time(), zone).timestamp()
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=zone)
        return moment.isoformat(), moment < current, moment.timestamp()
    except (ValueError, OverflowError):
        return None, False, float('inf')


def _digest(ref, records, acl, config, sender, role):
    # Include dependency task state as well as the root row. A source permission
    # is checked by _readable against the caller's fresh document grants.
    pending, seen, state = [ref], set(), []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        row = records[current]
        refs = acl.get(current, set())
        state.append([current, [row[field] for field in _FIELDS], sorted(refs),
                      _owner(config, row['owner'])])
        pending.extend(dep for dep in refs if dep in records and dep not in seen)
    packed = json.dumps([config['business'], sender, role, sorted(state)],
                        ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha256(packed.encode()).hexdigest()


def _project(ref, row, records, acl, config, sender, role, now, *, snapshot=True):
    if not isinstance(row['id'], str) or not 0 < len(row['id']) <= 160:
        return None, None
    status = _bounded(row['status'], 32)
    if status.casefold() in _CLOSED:
        return None, None
    owner = _owner(config, row['owner'])
    deadline, overdue, deadline_order = _deadline(row['deadline'], config, now)
    flags = [('blocked', status.casefold() == 'blocked'), ('overdue', overdue),
             ('missing_owner', owner is None), ('missing_deadline', deadline is None)]
    number = row['public_number']
    if type(number) is not int or number < 1:
        number = None
    item = {
        'id': ref, 'source_kind': 'task', 'task_id': row['id'],
        'public_number': number, 'title': _bounded(row['title'], 180),
        'status': status, 'deadline': deadline, 'owner': owner,
        'next_step': _bounded(row['next_step'], 240), 'criterion': _bounded(row['criterion'], 180),
        'proposal': status.casefold() == 'proposed',
        'signals': [name for name, present in flags if present],
        'confirmed': True, 'confirmation_scope': 'record_existence_only',
        'observed_at': now, 'updated_at': None,
        'snapshot_digest': _digest(ref, records, acl, config, sender, role) if snapshot else '0' * 64,
    }
    rank = tuple(-int(present) for _, present in flags) + (deadline_order, number or float('inf'), ref)
    return item, rank


def collect_work_context(store, config, sender, readable_ids, now, query):
    """Project at most five open task records, with at most 6000 JSON characters.

    The caller selects operational requests using is_operational_query, or a
    delivered work-context follow-up. Thus a short follow-up such as ``Давай``
    can use this collector too. ``readable_ids`` must contain fresh document and
    work grants. No task counts or metadata about inaccessible rows are emitted.
    """
    role = _role(config, sender)
    result = {'evidence': [], 'status': 'no_evidence', 'scope': 'current_authorized_task_records',
              'audience_scope': {'admin': 'admin_visible', 'employee': 'own_tasks_only'}.get(role, 'unregistered'),
              'as_of': now, 'limited': True,
              'limitation': 'Current accessible task records only; not a complete business assessment.'}
    if role is None:
        return result
    records, acl = _records(store, config['business'])
    permitted = _readable(records, acl, role, sender, readable_ids) & _ids(readable_ids)
    ranked = []
    for ref in permitted:
        item, rank = _project(ref, records[ref], records, acl, config, sender, role, now, snapshot=False)
        if item:
            ranked.append((rank, item))
    for _, item in sorted(ranked):
        candidate = result['evidence'] + [item]
        if len(json.dumps(candidate, ensure_ascii=False)) > MAX_EVIDENCE_CHARS:
            break
        item['snapshot_digest'] = _digest(item['id'], records, acl, config, sender, role)
        result['evidence'] = candidate
        if len(candidate) == MAX_EVIDENCE:
            break
    if result['evidence']:
        result['status'] = 'grounded'
    return result


def validate_work_snapshot(store, config, sender, readable_ids, item):
    """Check a complete projection against current role, grants, and task state.

    ``readable_ids`` must be recomputed at validation time. Observation time is
    when the task was read; it is never presented as the task's update time.
    """
    if not isinstance(item, dict) or not isinstance(item.get('task_id'), str):
        return False
    now = item.get('observed_at')
    if type(now) not in (int, float) or not math.isfinite(now):
        return False
    role = _role(config, sender)
    if role is None:
        return False
    records, acl = _records(store, config['business'])
    ref = work_id(config['business'], item['task_id'])
    permitted = _readable(records, acl, role, sender, readable_ids) & _ids(readable_ids)
    if ref not in permitted:
        return False
    current, _ = _project(ref, records[ref], records, acl, config, sender, role, now)
    return current is not None and current == item


def is_operational_query(query):
    """Recognize explicit work/problem questions, without authorizing actions."""
    if not isinstance(query, str) or not query.strip():
        return False
    text = re.sub(r'\s+', ' ', query.casefold().replace('ё', 'е')).strip()
    # Drafting and mutations must continue through their ordinary intent gates.
    if re.search(r'\b(?:созда(?:й|йте|ть|дим)|постав(?:ь|ьте|ить|им)|'
                 r'назнач(?:ь|ьте|ить|им)|перен(?:еси|есите|ести|есем)|'
                 r'отлож(?:и|ите|ить|им)|закр(?:ой|ойте|ыть|оем)|'
                 r'подтверд(?:и|ите|ить|им)|отмет(?:ь|ьте|ить|им)|'
                 r'измен(?:и|ите|ить|им)|обнов(?:и|ите|ить|им)|'
                 r'добав(?:ь|ьте|ить|им)|запиш(?:и|ите|ем)|записать|'
                 r'напиш(?:и|ите|ем)|написать|состав(?:ь|ьте|ить|им)|'
                 r'сформулир(?:уй|уйте|овать|уем)|придум(?:ай|айте|ать|аем)|'
                 r'перепиш(?:и|ите|ем)|переписать|подготов(?:ь|ьте|ить|им)|'
                 r'описани\w*|реклам\w*|пост(?:а|е|ом|ы|ов)?|скрипт\w*)\b', text):
        return False
    # Product, brand, and customer research are distinct from current task state.
    if re.search(r'\b(?:бренд\w*|продукт\w*|товар\w*|крем\w*|косметик\w*|'
                 r'шампун\w*|покупател\w*|клиент\w*)\b', text):
        return False
    question = bool('?' in text or re.search(
        r'\b(?:каки\w*|что|где|почему|есть ли|покажи\w*|расскажи\w*|'
        r'перечисли\w*|обзор|оцени\w*|проверь\w*|как)\b', text))
    if not question:
        return False
    work = bool(re.search(r'\b(?:бизнес\w*|работ\w*|задач\w*|поручени\w*|'
                          r'команд\w*|проект\w*|процесс\w*|операцион\w*)\b', text))
    problem = bool(re.search(r'\b(?:проблем\w*|риск\w*|блокер\w*|застр[яе]\w*|'
                             r'просроч\w*|тормоз\w*|узки\w* мест\w*|меша\w*|'
                             r'заблокирован\w*|слаб\w* мест\w*)\b', text))
    if work and problem:
        return True
    # These status questions conventionally refer to the caller's current work.
    if re.search(r'\b(?:блокер\w*|застр[яе]\w*|просроч\w*|заблокирован\w*)\b', text):
        return True
    if re.fullmatch(r'(?:какие(?: есть| сейчас)? риски|есть(?: ли)? риски|'
                    r'где(?: сейчас)? узкие места)[?!. ]*', text):
        return True
    if re.search(r'\b(?:у нас|наши|нашей|нашем|наших|мои|моей|моим|моих)\b', text) and problem:
        return True
    if re.search(r'\bчто\s+(?:сейчас\s+)?мешает\b', text) and not re.search(r'\b(?:мне|тебе|ему|ей)\b', text):
        return True
    return work and bool(re.search(r'\b(?:как (?:идут|продвигаются)|статус\w*|состояни\w*|обзор)\b', text))
