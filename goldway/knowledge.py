"""Local lexical evidence index; provider text is data, never executable input.

Only explicit readers with a current permission observation can retrieve evidence.
The provider supplies Unix timestamps and controls ``confirmed``; adding structured
facts does not confirm a document. ``max_chars`` bounds the entire compact JSON
result, including provenance, facts and conflicts. No network client is used.
"""

import hashlib
import json
import math
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from urllib.parse import urlsplit


CHUNK_CHARS = 1200
MAX_RESULTS = 12
MAX_CONTEXT_CHARS = 24000
MAX_QUERY_CHARS = 2000
MAX_DOCUMENT_CHARS = 200000

_TABLES = (
    """CREATE TABLE IF NOT EXISTS knowledge_documents(
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL, document_id TEXT NOT NULL,
        title TEXT NOT NULL, url TEXT NOT NULL, updated_at TEXT, updated_epoch REAL,
        observed_at REAL NOT NULL, permission_expires_at REAL NOT NULL,
        confirmed INTEGER NOT NULL, priority INTEGER,
        UNIQUE(source_id,document_id))""",
    """CREATE TABLE IF NOT EXISTS knowledge_readers(
        document_key TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        reader TEXT NOT NULL, PRIMARY KEY(document_key,reader))""",
    """CREATE INDEX IF NOT EXISTS knowledge_reader_lookup ON knowledge_readers(reader)""",
    """CREATE TABLE IF NOT EXISTS knowledge_chunks(
        id TEXT PRIMARY KEY,
        document_key TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, text TEXT NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS knowledge_chunk_document ON knowledge_chunks(document_key)""",
    """CREATE TABLE IF NOT EXISTS knowledge_facts(
        document_key TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        evidence_id TEXT NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
        subject TEXT NOT NULL, predicate TEXT NOT NULL, value TEXT NOT NULL,
        quote TEXT NOT NULL, PRIMARY KEY(document_key,position))""",
    """CREATE TABLE IF NOT EXISTS knowledge_document_observations(
        document_key TEXT PRIMARY KEY, source_id TEXT NOT NULL,
        observed_at REAL NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS knowledge_observation_source
        ON knowledge_document_observations(source_id)""",
    """CREATE TABLE IF NOT EXISTS knowledge_source_revocations(
        source_id TEXT PRIMARY KEY, observed_at REAL NOT NULL,
        inclusive INTEGER NOT NULL)""",
)

_STOP = set('а и о об в во на у к ко с со для по из от до про как что кто где какие '
            'какой какая это этот эта ли же мне нам вы он она оно они расскажи '
            'расскажите покажи пожалуйста подскажи'.split())
_ENDINGS = ('иями', 'ами', 'ями', 'ого', 'ему', 'ыми', 'ими', 'ение', 'ения',
            'ов', 'ев', 'ам', 'ям', 'ах', 'ях', 'ой', 'ый', 'ий', 'ая', 'яя',
            'ое', 'ее', 'ую', 'юю', 'ом', 'ем', 'ы', 'и', 'а', 'я', 'у', 'ю', 'е', 'о')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _empty():
    return {'evidence': [], 'facts': [], 'conflicts': [], 'status': 'no_evidence'}


def _text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid ' + name)
    return value


def _time(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('Invalid ' + name)
    return float(value)


def _updated(value):
    if value is None:
        return None
    _text(value, 'updated_at', 64)
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.utcoffset() is None:
                raise ValueError('Missing UTC offset')
        return parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise ValueError('Invalid updated_at: use ISO date or aware datetime') from exc


def _normal(value):
    return ' '.join(value.casefold().replace('ё', 'е').split())


def _tokens(value):
    terms = []
    for token in re.findall(r'[a-zа-яё0-9]+', _normal(value)):
        if token in _STOP or len(token) < 2:
            continue
        if token.startswith(('ответствен', 'отвеч', 'owner')):
            token = 'owner'
        elif token.startswith(('товар', 'продукт', 'каталог', 'ассортимент', 'product', 'catalog')):
            token = 'catalog'
        elif len(token) > 4 and re.fullmatch(r'[а-я]+', token):
            for ending in _ENDINGS:
                if token.endswith(ending) and len(token) - len(ending) >= 4:
                    token = token[:-len(ending)]
                    break
        if token not in terms:
            terms.append(token)
    return terms


def _chunks(text):
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            boundary = max(text.rfind('\n', start, end), text.rfind(' ', start, end))
            if boundary >= start + CHUNK_CHARS // 2:
                end = boundary + 1
        part = text[start:end]
        if part.strip():
            chunks.append(part)
        start = end
    return chunks


class KnowledgeIndex:
    def __init__(self, store):
        self.store = store
        for statement in _TABLES:
            store.execute(statement)
        store.execute('''INSERT OR IGNORE INTO knowledge_document_observations
            SELECT id,source_id,observed_at FROM knowledge_documents''')

    @contextmanager
    def _atomic(self):
        self.store.execute('SAVEPOINT knowledge_write')
        try:
            yield
        except BaseException:
            self.store.execute('ROLLBACK TO knowledge_write')
            self.store.execute('RELEASE knowledge_write')
            raise
        else:
            self.store.execute('RELEASE knowledge_write')

    def upsert(self, doc: dict):
        if not isinstance(doc, dict):
            raise ValueError('Document must be an object')
        required = {'source_id', 'document_id', 'title', 'url', 'text', 'readers',
                    'observed_at', 'permission_expires_at'}
        if not required <= doc.keys():
            raise ValueError('Missing document fields')
        source = _text(doc['source_id'], 'source_id', 256)
        document = _text(doc['document_id'], 'document_id', 256)
        title = _text(doc['title'], 'title', 300)
        url = _text(doc['url'], 'url', 2048)
        try:
            parsed_url = urlsplit(url)
            valid_url = (parsed_url.scheme in ('http', 'https') and parsed_url.hostname
                         and parsed_url.username is None and parsed_url.password is None
                         and not any(character.isspace() or ord(character) < 32 for character in url))
            parsed_url.port  # Validate an explicitly supplied port too.
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError('Invalid document url')
        text = _text(doc['text'], 'text', MAX_DOCUMENT_CHARS)
        readers = doc['readers']
        if not isinstance(readers, (list, tuple)) or not 1 <= len(readers) <= 200:
            raise ValueError('Explicit nonempty readers are required')
        if any(not isinstance(reader, str) or not reader or reader.strip() != reader
               or len(reader) > 128 or reader == '*' for reader in readers):
            raise ValueError('Invalid reader ID')
        readers = sorted(set(readers))
        observed = _time(doc['observed_at'], 'observed_at')
        expires = _time(doc['permission_expires_at'], 'permission_expires_at')
        if expires <= observed:
            raise ValueError('Permission expiry must follow its observation')
        updated = doc.get('updated_at')
        updated_epoch = _updated(updated)
        confirmed = doc.get('confirmed', False)
        priority = doc.get('priority')
        if type(confirmed) is not bool or (priority is not None and
                (type(priority) is not int or not -(2**63) <= priority < 2**63)):
            raise ValueError('Invalid confirmation or priority')
        facts = doc.get('facts', [])
        if not isinstance(facts, list) or len(facts) > 40:
            raise ValueError('Invalid facts')
        validated = []
        for fact in facts:
            if not isinstance(fact, dict) or set(fact) != {'subject', 'predicate', 'value', 'quote'}:
                raise ValueError('Invalid fact fields')
            fact = {key: _text(fact[key], key, 160 if key in ('subject', 'predicate')
                              else CHUNK_CHARS) for key in fact}
            if fact['quote'] not in text or fact['value'] not in fact['quote']:
                raise ValueError('Fact value and quote must be literal source evidence')
            if fact not in validated:
                validated.append(fact)
        content = [source, document, title, url, updated, confirmed, priority, text, validated]
        version = hashlib.sha256(_json(content).encode()).hexdigest()
        document_key = hashlib.sha256(_json([source, document]).encode()).hexdigest()
        chunks = _chunks(text)
        for fact in validated:
            if not any(fact['quote'] in chunk for chunk in chunks):
                chunks.append(fact['quote'])
        ids = ['knowledge:' + hashlib.sha256(_json([version, position, chunk]).encode()).hexdigest()[:32]
               for position, chunk in enumerate(chunks)]
        with self._atomic():
            previous = self.store.execute('''SELECT observed_at FROM knowledge_document_observations
                WHERE document_key=?''', (document_key,)).fetchone()
            if previous and observed < previous['observed_at']:
                raise ValueError('Stale document permission observation')
            revoked = self.store.execute('''SELECT observed_at,inclusive FROM knowledge_source_revocations
                WHERE source_id=?''', (source,)).fetchone()
            if revoked and (observed < revoked['observed_at'] or
                            (revoked['inclusive'] and observed == revoked['observed_at'])):
                raise ValueError('Document permission observation predates source revocation')
            self.store.execute('DELETE FROM knowledge_documents WHERE id=?', (document_key,))
            self.store.execute('INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (document_key, source, document, title, url, updated, updated_epoch,
                                observed, expires, int(confirmed), priority))
            for reader in readers:
                self.store.execute('INSERT INTO knowledge_readers VALUES(?,?)', (document_key, reader))
            for position, (id, chunk) in enumerate(zip(ids, chunks)):
                self.store.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?)',
                                   (id, document_key, position, chunk))
            for position, fact in enumerate(validated):
                evidence_id = next(id for id, chunk in zip(ids, chunks) if fact['quote'] in chunk)
                self.store.execute('INSERT INTO knowledge_facts VALUES(?,?,?,?,?,?,?)',
                                   (document_key, position, evidence_id, fact['subject'],
                                    fact['predicate'], fact['value'], fact['quote']))
            self.store.execute('''INSERT INTO knowledge_document_observations VALUES(?,?,?)
                ON CONFLICT(document_key) DO UPDATE SET observed_at=excluded.observed_at''',
                               (document_key, source, observed))

    def allowed_ids(self, sender, *, now) -> set[str]:
        now = _time(now, 'now')
        if not isinstance(sender, str) or not sender:
            return set()
        # This first read intentionally selects identifiers only, never titles/text.
        rows = self.store.rows('''SELECT c.id FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id=c.document_key
            JOIN knowledge_readers r ON r.document_key=d.id
            WHERE r.reader=? AND d.observed_at<=? AND d.permission_expires_at>?''',
                               (sender, now, now))
        return {row['id'] for row in rows}

    def revoke_source(self, source_id, *, observed_at=None):
        """Remove grants without allowing a cached snapshot to restore them.

        A trusted provider timestamp permits a replacement batch observed at that
        same time. Without one, revocation covers the latest known observation
        inclusively; restoration then requires a newer provider observation.
        """
        source_id = _text(source_id, 'source_id', 256)
        observed = None if observed_at is None else _time(observed_at, 'observed_at')
        with self._atomic():
            latest = self.store.execute('''SELECT MAX(observed_at) FROM knowledge_document_observations
                WHERE source_id=?''', (source_id,)).fetchone()[0]
            previous = self.store.execute('''SELECT observed_at FROM knowledge_source_revocations
                WHERE source_id=?''', (source_id,)).fetchone()
            watermark = max(latest if latest is not None else 0,
                            previous['observed_at'] if previous else 0)
            if observed is not None and observed < watermark:
                raise ValueError('Stale source permission observation')
            self.store.execute('''INSERT INTO knowledge_source_revocations VALUES(?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET observed_at=excluded.observed_at,
                inclusive=excluded.inclusive''',
                               (source_id, watermark if observed is None else observed,
                                int(observed is None)))
            self.store.execute('DELETE FROM knowledge_documents WHERE source_id=?', (source_id,))

    def _read(self, sender, now, ids):
        chunks, facts = [], []
        identifiers = sorted(ids)
        for start in range(0, len(identifiers), 300):
            batch = identifiers[start:start + 300]
            placeholders = ','.join('?' for _ in batch)
            args = [sender, now, now, *batch]
            acl = '''JOIN knowledge_documents d ON d.id=c.document_key
                JOIN knowledge_readers r ON r.document_key=d.id
                WHERE r.reader=? AND d.observed_at<=? AND d.permission_expires_at>?
                AND c.id IN (''' + placeholders + ')'
            # Recheck grants in the text read too, including a revocation between reads.
            chunks.extend(self.store.rows('SELECT c.*,d.source_id,d.document_id,d.title,d.url,'
                'd.updated_at,d.updated_epoch,d.observed_at,d.confirmed,d.priority '
                'FROM knowledge_chunks c ' + acl, args))
            facts.extend(self.store.rows('SELECT f.* FROM knowledge_facts f '
                'JOIN knowledge_chunks c ON c.id=f.evidence_id ' + acl, args))
        return chunks, facts

    @staticmethod
    def _evidence(row):
        keys = ('id', 'text', 'title', 'url', 'updated_at', 'observed_at',
                'source_id', 'document_id', 'priority')
        item = {key: row[key] for key in keys}
        item.update(confirmed=bool(row['confirmed']),
                    status='confirmed' if row['confirmed'] else 'assumption', untrusted=True)
        return item

    @staticmethod
    def _resolution(facts):
        priorities = [fact['priority'] for fact in facts]
        candidates = facts
        if all(priority is not None for priority in priorities):
            highest = max(priorities)
            candidates = [fact for fact in facts if fact['priority'] == highest]
            values = {fact['value'] for fact in candidates}
            if len(set(priorities)) > 1 and len(values) == 1:
                return 'priority', next(iter(values))
        elif any(priority is not None for priority in priorities):
            return 'unresolved', None
        dates = [_updated(fact['updated_at']) for fact in candidates]
        if all(value is not None for value in dates):
            newest = max(dates)
            values = {fact['value'] for fact, value in zip(candidates, dates) if value == newest}
            if len(values) == 1:
                return 'updated_at', next(iter(values))
        return 'unresolved', None

    def search(self, query, sender, *, now, limit=6, max_chars=6000, subjects=()) -> dict:
        now = _time(now, 'now')
        if not isinstance(query, str):
            raise ValueError('Query must be text')
        if type(limit) is not int or limit < 0:
            raise ValueError('Invalid result limit')
        if type(max_chars) is not int or max_chars < len(_json(_empty())):
            raise ValueError('Character budget is smaller than the empty result')
        if not isinstance(subjects, (list, tuple, set)) or any(not isinstance(s, str) for s in subjects):
            raise ValueError('Subjects must be explicit text identifiers')
        limit, max_chars = min(limit, MAX_RESULTS), min(max_chars, MAX_CONTEXT_CHARS)
        query_terms = set(_tokens(query[:MAX_QUERY_CHARS])[:24])
        subject_terms = [_normal(s[:160]) for s in list(subjects)[:12] if s.strip()]
        if not limit or (not query_terms and not subject_terms):
            return _empty()
        ids = self.allowed_ids(sender, now=now)
        if not ids:
            return _empty()
        chunks, facts = self._read(sender, now, ids)
        by_id = {row['id']: row for row in chunks}
        grouped, document_groups, document_subjects = {}, {}, {}
        for fact in facts:
            row = by_id.get(fact['evidence_id'])
            if row is None:
                continue
            document_subjects.setdefault(row['document_key'], set()).add(_normal(fact['subject']))
            if not row['confirmed']:
                continue
            key = (_normal(fact['subject']), _normal(fact['predicate']))
            item = {name: fact[name] for name in ('subject', 'predicate', 'value', 'quote', 'evidence_id')}
            item.update({name: row[name] for name in ('title', 'url', 'updated_at', 'observed_at',
                                                     'source_id', 'document_id', 'priority')})
            item.update(confirmed=True, status='confirmed')
            grouped.setdefault(key, []).append(item)
            document_groups.setdefault(row['document_key'], set()).add(key)
        candidates = []
        for row in chunks:
            title_terms, body_terms = set(_tokens(row['title'])), set(_tokens(row['text']))
            subject_match = any(subject in document_subjects.get(row['document_key'], set())
                                or subject in _normal(row['title']) or subject in _normal(row['text'])
                                for subject in subject_terms)
            if subject_terms and not subject_match:
                continue
            score = 3 * len(query_terms & title_terms) + len(query_terms & body_terms) + 8 * subject_match
            if score:
                candidates.append((score, row))
        candidates.sort(key=lambda pair: (-pair[0], -pair[1]['confirmed'],
                        -(pair[1]['priority'] if pair[1]['priority'] is not None else -math.inf),
                        -(pair[1]['updated_epoch'] if pair[1]['updated_epoch'] is not None else -math.inf),
                        pair[1]['id']))
        result, included = _empty(), set()
        for _, row in candidates:
            if len(result['evidence']) >= limit:
                break
            keys = included | document_groups.get(row['document_key'], set())
            trial = {'evidence': [*result['evidence'], self._evidence(row)],
                     'facts': [], 'conflicts': [], 'status': 'ok'}
            for key in sorted(keys):
                group = sorted(grouped[key], key=lambda fact: (fact['source_id'], fact['document_id'], fact['value']))
                trial['facts'].extend(group)
                if len({fact['value'] for fact in group}) > 1:
                    resolution, selected = self._resolution(group)
                    trial['conflicts'].append({'subject': group[0]['subject'], 'predicate': group[0]['predicate'],
                        'values': group, 'resolution': resolution, 'selected_value': selected})
            # Never omit a known conflicting alternative to make a result fit.
            # If its complete provenance exceeds the budget, skip that evidence.
            if len(_json(trial)) > max_chars:
                text = trial['evidence'][-1]['text']
                trial['evidence'][-1]['truncated'] = True
                low, high = 0, len(text)
                while low < high:
                    middle = (low + high + 1) // 2
                    trial['evidence'][-1]['text'] = text[:middle]
                    if len(_json(trial)) <= max_chars:
                        low = middle
                    else:
                        high = middle - 1
                trial['evidence'][-1]['text'] = text[:low]
            if trial['evidence'][-1]['text'].strip() and len(_json(trial)) <= max_chars:
                result, included = trial, keys
        referenced = {item['id'] for item in result['evidence']}
        referenced.update(fact['evidence_id'] for fact in result['facts'])
        if referenced and not referenced <= self.allowed_ids(sender, now=now):
            return _empty()
        return result
