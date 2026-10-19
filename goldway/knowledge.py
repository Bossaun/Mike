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
from dataclasses import dataclass
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


class SelectedKnowledgeUnavailable(ValueError):
    def __init__(self):super().__init__('selected_knowledge_unavailable')


def _selected_deny():raise SelectedKnowledgeUnavailable()


@dataclass(frozen=True)
class SelectedDocument:
    source_id:str
    document_id:str
    document_key:str


@dataclass(frozen=True)
class SelectedKnowledgeSnapshot:
    result_json:str
    documents:tuple[SelectedDocument,...]
    consulted_ids:tuple[str,...]
    evidence_ids:tuple[str,...]
    snapshot_digest:str
    scope:str='selected_documents_only'


def _selected_documents(documents):
    if type(documents) is not tuple or not 1<=len(documents)<=3:_selected_deny()
    values=[]
    for item in documents:
        if type(item) is not SelectedDocument:_selected_deny()
        for value in (item.source_id,item.document_id):
            if (type(value) is not str or not 0<len(value)<=256 or len(value.encode())>1024
                    or value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value)):_selected_deny()
        if type(item.document_key) is not str or re.fullmatch('[0-9a-f]{64}',item.document_key) is None:_selected_deny()
        if hashlib.sha256(_json([item.source_id,item.document_id]).encode()).hexdigest()!=item.document_key:_selected_deny()
        values.append((item.source_id,item.document_id,item.document_key))
    if len({item[2] for item in values})!=len(values):_selected_deny()
    return tuple(values)


class _SelectedBudget:
    """One cumulative budget for metadata, guarded bodies and final reread."""
    def __init__(self,store):
        self.store=store;self.db=store.db;self.queries=0;self.rows=0;self.bytes=0

    def query(self,sql,params,maximum):
        if self.store.db is not self.db or self.queries>=64:_selected_deny()
        self.queries+=1
        rows=self.store.execute(sql,params).fetchmany(maximum+1)
        if self.store.db is not self.db or len(rows)>maximum:_selected_deny()
        result=[dict(row) for row in rows]
        self.rows+=len(result)
        self.bytes+=sum(len(v.encode()) if type(v) is str else len(v) if type(v) is bytes else 16 for row in result for v in row.values())
        if self.rows>256 or self.bytes>1024*1024:_selected_deny()
        return result


def _stext(column,chars,byte_limit,nullable=False):
    check="typeof("+column+")='text' AND length("+column+") BETWEEN 1 AND "+str(chars)+" AND length(CAST("+column+" AS BLOB))<="+str(byte_limit)
    return '('+column+' IS NULL OR ('+check+'))' if nullable else '('+check+')'


def _snumber(column,nullable=False):
    check="typeof("+column+") IN ('integer','real') AND "+column+' BETWEEN 0 AND 9007199254740991'
    return '('+column+' IS NULL OR ('+check+'))' if nullable else '('+check+')'


def _selected_sql(values,reader,now):
    params={'reader':reader,'now':now,'chosen':len(values)}
    for i,(source,document,key) in enumerate(values):params.update({f'k{i}':key,f's{i}':source,f'd{i}':document})
    keys=','.join(':k'+str(i) for i in range(len(values)))
    identities=' OR '.join('(d.id=:k'+str(i)+' AND d.source_id=:s'+str(i)+' AND d.document_id=:d'+str(i)+')' for i in range(len(values)))
    doc_good=' AND '.join((_stext('d.id',64,64),_stext('d.source_id',256,1024),_stext('d.document_id',256,1024),
        _stext('d.title',300,1200),_stext('d.url',2048,8192),_stext('d.updated_at',64,256,True),
        _snumber('d.observed_at'),_snumber('d.permission_expires_at'),
        "(d.updated_epoch IS NULL OR (typeof(d.updated_epoch) IN ('integer','real') AND abs(d.updated_epoch)<=9007199254740991))",
        "typeof(d.confirmed)='integer' AND d.confirmed IN (0,1)","(d.priority IS NULL OR typeof(d.priority)='integer')",
        '('+identities+')','d.observed_at<=:now AND d.permission_expires_at>:now AND d.permission_expires_at>d.observed_at',
        'o.document_key=d.id AND o.source_id=d.source_id AND '+_snumber('o.observed_at')+' AND o.observed_at=d.observed_at',
        "(rv.source_id IS NULL OR ("+_snumber('rv.observed_at')+" AND typeof(rv.inclusive)='integer' AND rv.inclusive IN (0,1) AND (d.observed_at>rv.observed_at OR (d.observed_at=rv.observed_at AND rv.inclusive=0))))",
        'EXISTS(SELECT 1 FROM knowledge_readers r WHERE r.document_key=d.id AND r.reader=:reader)'))
    chunk_good=' AND '.join((_stext('c.id',128,512),_stext('c.document_key',64,64),
        "typeof(c.position)='integer' AND c.position BETWEEN 0 AND 1000000",_stext('c.text',1200,4800)))
    fact_good=' AND '.join((_stext('f.document_key',64,64),_stext('f.evidence_id',128,512),
        "typeof(f.position)='integer' AND f.position BETWEEN 0 AND 1000000",
        _stext('f.subject',160,640),_stext('f.predicate',160,640),_stext('f.value',1200,4800),_stext('f.quote',1200,4800),
        'EXISTS(SELECT 1 FROM knowledge_chunks c WHERE c.id=f.evidence_id AND c.document_key=f.document_key)'))
    def size(columns):return '+'.join('COALESCE(length(CAST('+c+' AS BLOB)),0)' for c in columns)
    ds_size=size(('d.id','d.source_id','d.document_id','d.title','d.url','d.updated_at','o.source_id','rv.source_id'))+'+length(CAST(:reader AS BLOB))'
    cs_size=size(('c.id','c.document_key','c.text'));fs_size=size(('f.document_key','f.evidence_id','f.subject','f.predicate','f.value','f.quote'))
    cte=f'''WITH ds AS (SELECT d.id,CASE WHEN {doc_good} THEN 1 ELSE 0 END good,{ds_size} nb
        FROM knowledge_documents d LEFT JOIN knowledge_document_observations o ON o.document_key=d.id
        LEFT JOIN knowledge_source_revocations rv ON rv.source_id=d.source_id WHERE d.id IN ({keys}) LIMIT 4),
        cs AS (SELECT c.document_key,CASE WHEN {chunk_good} THEN 1 ELSE 0 END good,{cs_size} nb
        FROM knowledge_chunks c WHERE c.document_key IN ({keys}) LIMIT 17),
        fs AS (SELECT CASE WHEN {fact_good} THEN 1 ELSE 0 END good,{fs_size} nb
        FROM knowledge_facts f WHERE f.document_key IN ({keys}) LIMIT 65),
        totals AS (SELECT (SELECT COUNT(*) FROM ds) nd,(SELECT COUNT(*) FROM cs) nc,(SELECT COUNT(*) FROM fs) nf,
        COALESCE((SELECT SUM(nb) FROM ds),0)+COALESCE((SELECT SUM(nb) FROM cs),0)+COALESCE((SELECT SUM(nb) FROM fs),0) nb),
        gate AS (SELECT CASE WHEN nd=:chosen AND nc BETWEEN :chosen AND 16 AND nf<=64 AND nb<=65536
        AND NOT EXISTS(SELECT 1 FROM ds WHERE good<>1) AND NOT EXISTS(SELECT 1 FROM cs WHERE good<>1)
        AND NOT EXISTS(SELECT 1 FROM fs WHERE good<>1)
        AND NOT EXISTS(SELECT 1 FROM ds WHERE NOT EXISTS(SELECT 1 FROM cs WHERE cs.document_key=ds.id))
        THEN 1 ELSE 0 END ok,nd,nc,nf,nb FROM totals) '''
    return cte,params,keys


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

    def _selected_snapshot(self,budget,values,reader,now):
        cte,params,keys=_selected_sql(values,reader,now)
        gate=budget.query(cte+'SELECT * FROM gate LIMIT 2',params,1)
        if len(gate)!=1 or gate[0]['ok']!=1:_selected_deny()
        def projection(columns):
            return ','.join('CASE WHEN gate.ok=1 THEN '+column+' END AS '+alias for alias,column in columns)
        def rows(sql,maximum):
            result=budget.query(cte+'SELECT gate.ok AS _ok,'+sql,params,maximum)
            for row in result:
                if row.pop('_ok')!=1:_selected_deny()
            return result
        documents=rows(projection((('id','d.id'),('source_id','d.source_id'),('document_id','d.document_id'),
            ('updated_at','d.updated_at'),('updated_epoch','d.updated_epoch'),('observed_at','d.observed_at'),
            ('permission_expires_at','d.permission_expires_at'),('confirmed','d.confirmed'),('priority','d.priority'),
            ('observation_source','o.source_id'),('observation_at','o.observed_at'),
            ('revoked_at','rv.observed_at'),('inclusive','rv.inclusive')))+f''',:reader AS reader FROM knowledge_documents d
            LEFT JOIN knowledge_document_observations o ON o.document_key=d.id
            LEFT JOIN knowledge_source_revocations rv ON rv.source_id=d.source_id CROSS JOIN gate
            WHERE d.id IN ({keys}) ORDER BY d.id LIMIT 4''',3)
        if len(documents)!=len(values):_selected_deny()
        chunks=rows(projection((('id','c.id'),('document_key','c.document_key'),('position','c.position'),('text','c.text'),
            ('source_id','d.source_id'),('document_id','d.document_id'),('title','d.title'),('url','d.url'),
            ('updated_at','d.updated_at'),('updated_epoch','d.updated_epoch'),('observed_at','d.observed_at'),
            ('confirmed','d.confirmed'),('priority','d.priority')))+f''' FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id=c.document_key CROSS JOIN gate
            WHERE c.document_key IN ({keys}) ORDER BY c.id LIMIT 17''',16)
        facts=rows(projection(tuple((name,'f.'+name) for name in ('document_key','position','evidence_id','subject','predicate','value','quote')))+f'''
            FROM knowledge_facts f CROSS JOIN gate WHERE f.document_key IN ({keys})
            ORDER BY f.document_key,f.position LIMIT 65''',64)
        if not chunks:_selected_deny()
        by_id={row['id']:row for row in chunks}
        if len(by_id)!=len(chunks):_selected_deny()
        for fact in facts:
            chunk=by_id.get(fact['evidence_id'])
            if chunk is None or fact['document_key']!=chunk['document_key'] or fact['quote'] not in chunk['text'] or fact['value'] not in fact['quote']:_selected_deny()
        return documents,chunks,facts

    def search_selected(self,query,reader,*,now,documents,limit=3,max_chars=6000):
        """Bounded explicit document scope; no evidence outside it is consulted.

        Legacy lexical/fact/conflict assembly is shared exactly. Index grants,
        not bindings, authorize reads. Two snapshots are optimistic observations.
        """
        try:
            now=_time(now,'now');values=_selected_documents(documents)
            if (type(query) is not str or not query.strip() or len(query)>256 or len(query.encode())>1024
                    or type(reader) is not str or not 0<len(reader)<=128 or reader!=reader.strip() or reader=='*'
                    or type(limit) is not int or not 1<=limit<=3 or type(max_chars) is not int
                    or not len(_json(_empty()))<=max_chars<=6000):_selected_deny()
            reader.encode();store=self.store;budget=_SelectedBudget(store)
            initial=self._selected_snapshot(budget,values,reader,now)
            query_terms=set(_tokens(query)[:24]);subject_terms=[]
            result=self._rank(initial[1],initial[2],query_terms,subject_terms,limit,max_chars)
            final=self._selected_snapshot(budget,values,reader,now)
            if self.store is not store or store.db is not budget.db or final!=initial or _selected_documents(documents)!=values:_selected_deny()
            encoded=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            if len(encoded)>max_chars or len(encoded.encode())>24000:_selected_deny()
            consulted=tuple(sorted(row['id'] for row in initial[1]))
            refs={item['id'] for item in result['evidence']}
            refs.update(fact['evidence_id'] for fact in result['facts'])
            if not refs<=set(consulted):_selected_deny()
            seal=json.dumps([reader,values,initial],ensure_ascii=False,sort_keys=True,separators=(',',':'))
            budget.bytes+=len(encoded.encode())+len(seal.encode())
            if budget.bytes>1024*1024:_selected_deny()
            return SelectedKnowledgeSnapshot(encoded,documents,consulted,tuple(sorted(refs)),hashlib.sha256(seal.encode()).hexdigest())
        except SelectedKnowledgeUnavailable:raise
        except Exception:raise SelectedKnowledgeUnavailable() from None

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

    def _rank(self, chunks, facts, query_terms, subject_terms, limit, max_chars):
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
        return result

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
        result = self._rank(chunks, facts, query_terms, subject_terms, limit, max_chars)
        referenced = {item['id'] for item in result['evidence']}
        referenced.update(fact['evidence_id'] for fact in result['facts'])
        if referenced and not referenced <= self.allowed_ids(sender, now=now):
            return _empty()
        return result
