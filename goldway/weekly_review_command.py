"""Exact own-meeting weekly review: read only, no dated-result inference.

Fixed build passes: two I35 readers, one shared selected/report-proof reader,
one allowlisted I04 reader and TWO actual I37 loads (five readers each):
14 readers <=56 MiB. Each guard has one I35/proof pass and ONE I37 load:
seven readers <=28 MiB. Each Reader <=4 MiB/8192 rows/2048 queries.
Only the initialized host may inject the guard; no constructors or refreshes.
"""
from dataclasses import asdict,dataclass,field
from datetime import date, timedelta,datetime,timezone
import json
import re

from . import meeting_binding as binding
from . import dated_result as reports
from . import meeting_week_adapter as adapter
from . import weekly_draft_render as render
from .answer_citations import _hash, _scope
from .monthly_task_history import _Reader, _bounded_columns, _text, _integer
from .task_card_command import _snapshot
from .task_card_reference import _barrier, _protected

PREFIX = 'weekly-review:'
CANCEL = 'Weekly review authorization changed; request a fresh review'
ATTRIBUTION = 'Результаты — по вашим зарегистрированным отчётам; независимой проверки нет.'
NOTICE = 'Не удалось сверить планёрку. Укажите её код MB и семь дат периода; проверьте текущую регистрацию и доступ.'
_REQUEST = re.compile(r'Сверь планёрку (MB-[0-9a-f]{16}) за ([0-9]{4}-[0-9]{2}-[0-9]{2}) — ([0-9]{4}-[0-9]{2}-[0-9]{2})')


def _deny():
    raise ValueError('weekly_review_unavailable')


def is_weekly_review_command(text):
    return type(text) is str and text.startswith('Сверь планёрку')


def _request(text):
    if type(text) is not str or len(text) > 160:
        _deny()
    match = _REQUEST.fullmatch(text)
    if not match:
        _deny()
    code, first, last = match.groups()
    start, inclusive = date.fromisoformat(first), date.fromisoformat(last)
    if start.isoformat() != first or inclusive.isoformat() != last or inclusive-start != timedelta(days=6):
        _deny()
    return code, start, inclusive+timedelta(days=1)


class _Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if type(key) is int else super().__getitem__(key)

    def __iter__(self):
        return iter(self.values())


class _Cursor:
    def __init__(self, rows):
        self.rows = [_Row(row) for row in rows]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _ReadView:
    """Only the current I04 query vocabulary, SQL cell checks BEFORE transfer.

    Shared work_context asks for display columns even for ACL evaluation. Keep
    that API, bound each field and every whole-business register at 128 rows.
    The caller cannot forward arbitrary reads, writes, or added SQL clauses.
    """
    def __init__(self, store):
        self.reader = _Reader(store)

    def execute(self, sql, args=()):
        query = ' '.join(sql.split())
        args = tuple(args)
        if query.endswith(' LIMIT ?'):
            query = query[:-8]
            if not args or type(args[-1]) is not int or not 1 <= args[-1] <= 1001:
                _deny()
            args = args[:-1]
        spec = None
        maximum = 1
        if query in ("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                     "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intelligence_task_acl'"):
            if args not in ((), ('intelligence_task_acl',)):
                _deny()
            spec = {'1': '1'}
            tail = " FROM sqlite_master WHERE type='table' AND name='intelligence_task_acl'"
            args = ()
        elif query in ('SELECT 1 AS present FROM tasks WHERE business=?',
                       'SELECT 1 AS present FROM intelligence_task_acl WHERE business=?'):
            spec = {'1': '1'}
            tail = ' FROM '+query.split(' FROM ', 1)[1]
            maximum = 128
        elif query == 'SELECT id,public_number,title,owner,deadline,next_step,criterion,status,revision FROM tasks WHERE business=?':
            spec = {'id': _text('id',160), 'public_number': _integer('public_number'),
                    'title': _text('title',4000), 'owner': _text('owner',128,True),
                    'deadline': _text('deadline',64,True), 'next_step': _text('next_step',4000),
                    'criterion': _text('criterion',4000), 'status': _text('status',32),
                    'revision': _integer('revision')}
            tail = ' FROM tasks WHERE business=?'
            maximum = 128
        elif query == 'SELECT task_id,evidence_ids FROM intelligence_task_acl WHERE business=?':
            spec = {'task_id': _text('task_id',160), 'evidence_ids': _text('evidence_ids',16384)}
            tail = ' FROM intelligence_task_acl WHERE business=?'
            maximum = 128
        elif query == 'SELECT status,owner,revision FROM tasks WHERE business=? AND id=?':
            spec = {'status': _text('status',32), 'owner': _text('owner',128,True), 'revision': _integer('revision')}
            tail = ' FROM tasks WHERE business=? AND id=?'
        elif query == 'SELECT 1 FROM task_sources WHERE business=? AND task_id=? AND source_id=?':
            spec = {'1': '1'}
            tail = ' FROM task_sources WHERE business=? AND task_id=? AND source_id=?'
        elif query == "SELECT deleted, CASE WHEN typeof(body)='text' AND length(CAST(body AS BLOB))<=? THEN body END FROM sources WHERE business=? AND id=?":
            if args[0] != 64000:
                _deny()
            args = args[1:]
            spec = {'deleted': "typeof(deleted)='integer' AND deleted IN (0,1)", 'body': _text('body',64000)}
            tail = ' FROM sources WHERE business=? AND id=?'
        if spec is None:
            _deny()
        # Constant expression cannot use the normal alias helper ('AS 1').
        if spec == {'1':'1'}:
            selected = '1 AS present'
        else:
            selected = _bounded_columns(spec)
        return _Cursor(self.reader.query('SELECT '+selected+tail, args, maximum=maximum))

    def rows(self, sql, args=()):
        return self.execute(sql, args).fetchall()


def _typed(value):
    return json.loads(json.dumps(asdict(value), default=str, sort_keys=True))


def _proof(engine, code, payload, inputs, reader, reported, registration_out=None):
    scope, policy = binding._gate(engine, payload, engine.intelligence)
    registration = binding._load(reader, scope, code)
    if registration_out is not None:registration_out.append((scope,policy,registration))
    refs, snapshots = set(), []
    if not 1 <= len(inputs.task_bindings) <= 5:
        _deny()
    for link in inputs.task_bindings:
        task, current_refs, fingerprint = _snapshot(reader, engine.s, engine.c, payload['sender'],
            engine.intelligence.index, engine.now(), None, link.task_id)
        refs.update(current_refs)
        snapshots.append([task['id'], fingerprint])
    if any(ref.startswith('meeting-access:') for ref in refs):
        _deny()
    # Full registration metadata includes expiry/source/policy and explicit links;
    # elapsed observation time is deliberately absent from the fingerprint.
    report_digest = _report_proof(engine,code,payload,reported,reader)
    digest = _hash([report_digest, scope, policy, registration, [_typed(inputs.meeting), [_typed(link) for link in inputs.task_bindings], sorted(inputs.document_ids), sorted(inputs.meeting_evidence_ids)], snapshots, sorted(refs)])
    return digest, tuple(sorted(refs)), report_digest


def _report_proof(engine,code,payload,reported,reader):
    """Seal ALL exact current report metadata, including withdrawn index entries.

    Reuse the selected-proof Reader; no extra allowance, broad scans or bodies.
    Actual I37 loader remains the authority for current active source/link proof.
    """
    if type(reported) is not reports.ReportedInputs:
        _deny()
    if (type(reported.sources) is not tuple or type(reported.result_bindings) is not tuple or
        len(reported.sources)!=len(reported.result_bindings) or len(reported.sources)>32 or
        len(reported.sources)>reports.MAX_REPORTS):
        _deny()
    scope,_=binding._gate(engine,payload,engine.intelligence)
    index=reports._read(reader,reports._key(scope,code),True)
    ids=reports._ids(reader,scope,code)
    if len(ids)>32:
        _deny()
    records=[reports._read(reader,reports._key(scope,code,rid)) for rid in ids]
    return _hash([index,ids,records,_typed(reported)])


def _receipt_key(business, identifier):
    return PREFIX+'v1:'+_hash([business, identifier])


def _outbox(reader, business, identifier):
    spec = {'id':_integer('id'), 'business':_text('business',128), 'recipient':_text('recipient',128),
        'text':_text('text',16000), 'dedupe':_text('dedupe',512), 'state':_text('state',16),
        'task_id':_text('task_id',160,True), 'parse_mode':_text('parse_mode',16,True),
        'preferred_transport':_text('preferred_transport',16,True)}
    rows = reader.query('SELECT '+_bounded_columns(spec)+' FROM outbox WHERE business=? AND id=?', (business,identifier))
    if len(rows) != 1:
        _deny()
    return rows[0]


def _seal(row):
    return _hash([row[key] for key in ('id','business','recipient','text','dedupe','task_id','parse_mode','preferred_transport')])


def _host(engine):
    intelligence=engine.intelligence
    return (engine.s,engine.c,engine.b,intelligence,getattr(intelligence,'index',None))


def _same_host(engine,host):
    store,config,business,intelligence,index=host
    if (intelligence is None or index is None or engine.s is not store or engine.c is not config or engine.b!=business or
        engine.intelligence is not intelligence or intelligence.index is not index or
        intelligence.s is not store or intelligence.c is not config or intelligence.b!=business or
        index.store is not store):
        _deny()




@dataclass(frozen=True)
class RegisteredWeekIdentity:
    code:str
    meeting_on:date
    expires_at:int|float
    scope_hash:str
    policy_seal:str
    registration_seal:str
@dataclass(frozen=True)
class RegisteredWeekSnapshot:
    identity:RegisteredWeekIdentity
    start:date
    end:date
    draft:adapter.WeeklyDraft
    pages:tuple[str,...]
    refs:tuple[str,...]
    proof:str
    report_proof:str
    as_of:int|float
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)

def _identity(code,on,scope,policy,registration):
    if registration.get('code')!=code or registration.get('on')!=on.isoformat():_deny()
    return RegisteredWeekIdentity(code,on,registration['expiry'],_hash(scope),policy,_hash(['registered-week-identity:v1',scope,policy,registration]))

def _public_scope(engine,code,payload):
    from .engine import Engine
    from .store import Store
    from .intelligence import Intelligence
    from .knowledge import KnowledgeIndex
    if (type(engine) is not Engine or type(engine.s) is not Store or type(engine.intelligence) is not Intelligence
        or type(engine.intelligence.index) is not KnowledgeIndex or type(payload) is not dict
        or set(payload)!={'sender','channel','source_id'} or type(code) is not str or re.fullmatch('MB-[0-9a-f]{16}',code) is None):_deny()
    request=dict(payload)
    if any(type(v) is not str or not v or len(v)>1024 for v in request.values()) or len(json.dumps(request).encode())>2048:_deny()
    host=_host(engine);_same_host(engine,host);scope,policy=binding._gate(engine,request,host[3])
    return host,request,scope,policy,engine.s.db,engine.now

def _public_check(engine,host,request,payload,scope,policy,connection,clock):
    _same_host(engine,host)
    if engine.s.db is not connection or engine.now is not clock or payload!=request or binding._gate(engine,request,host[3])!=(scope,policy):_deny()

def registered_week_identity(engine,code,payload):
    host,request,scope,policy,connection,clock=_public_scope(engine,code,payload)
    reader=_Reader(host[0]);inputs=binding.registered_inputs(engine,code,request)
    registration=binding._load(reader,scope,code);identity=_identity(code,inputs.meeting.on,scope,policy,registration)
    _public_check(engine,host,request,payload,scope,policy,connection,clock)
    current=binding._load(reader,scope,code)
    fresh=binding.registered_inputs(engine,code,request)
    _public_check(engine,host,request,payload,scope,policy,connection,clock)
    if fresh!=inputs or _identity(code,fresh.meeting.on,scope,policy,current)!=identity:_deny()
    return identity

def build_registered_weekly_review(engine,code,payload,*,start,end):
    if type(start) is not date or type(end) is not date or end-start!=timedelta(days=7):_deny()
    host,request,scope,policy,connection,clock=_public_scope(engine,code,payload)
    reader=_Reader(host[0]);inputs,reported,initial,refs,report_digest,draft,pages,identity=_build_pass(engine,code,request,start,end,host,reader)
    _public_check(engine,host,request,payload,scope,policy,connection,clock)
    fresh=binding.registered_inputs(engine,code,request);fresh_reported=reports.reported_inputs(engine,code,request)
    if fresh!=inputs or fresh_reported!=reported or _proof(engine,code,request,fresh,reader,fresh_reported)!=(initial,refs,report_digest):_deny()
    _public_check(engine,host,request,payload,scope,policy,connection,clock)
    return RegisteredWeekSnapshot(identity,start,end,draft,tuple(pages),refs,initial,report_digest,draft.as_of)

def _build_pass(engine,code,payload,start,end,host,reader):
    store=host[0]
    inputs = binding.registered_inputs(engine,code,payload)
    reported = reports.reported_inputs(engine,code,payload)
    _same_host(engine,host)
    registration=[]
    initial, refs, report_digest = _proof(engine,code,payload,inputs,reader,reported,registration)
    draft = adapter.project_week(_ReadView(store),engine.c,payload['sender'],meeting=inputs.meeting,
        start=start,end=end,task_bindings=inputs.task_bindings,result_bindings=reported.result_bindings,sources=reported.sources,
        document_ids=inputs.document_ids,meeting_evidence_ids=inputs.meeting_evidence_ids,
        meeting_anchor_confirmed=True,now=engine.now())
    pages = render.render_weekly_draft(draft)
    if reported.result_bindings:
        pages = [ATTRIBUTION+'\n\n'+page for page in pages]
    if not 1 <= len(pages) <= 8 or sum(render._units(p) for p in pages)>24000 or any(render._units(p)>3500 for p in pages):
        _deny()
    identity=_identity(code,inputs.meeting.on,*registration[0])
    return inputs,reported,initial,refs,report_digest,draft,pages,identity


def handle(engine, event_id, payload):
    host=_host(engine)
    store=host[0]
    if not store.db.in_transaction:
        _deny()
    # Explicit savepoint keeps the accepted-turn barrier and fixed clarification,
    # but removes ALL queued pages, protection refs and receipts on any race.
    store.execute('SAVEPOINT weekly_review_build')
    try:
        _same_host(engine,host)
        code, start, end = _request(payload['text'])
        scope, _ = binding._gate(engine,payload,engine.intelligence)
        reader = _Reader(store)
        _barrier(reader,engine.b,scope,event_id)
        inputs,reported,initial,refs,report_digest,draft,pages,_identity_unused = _build_pass(engine,code,payload,start,end,host,reader)
        # Persist minimal actual request metadata; no text/transcript duplicates.
        if type(event_id) is not str or not 0<len(event_id.encode())<=1024:
            _deny()
        request = {key:payload[key] for key in ('sender','channel','source_id')}
        if len(json.dumps(request).encode())>2048:
            _deny()
        _same_host(engine,host)
        for ordinal, body in enumerate(pages,1):
            _same_host(engine,host)
            dedupe = PREFIX+_hash_from_event(_hash(event_id),scope)+':'+str(ordinal)
            if not store.queue(engine.b,payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):
                _deny()
            _same_host(engine,host)
            identifier = store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,dedupe)).fetchone()[0]
            host[3].protect_outbox(identifier,refs)
            _same_host(engine,host)
            row = _outbox(reader,engine.b,identifier)
            _protected(reader,engine.b,identifier,refs)
            receipt = dict(version=2,business=engine.b,outbox=identifier,code=code,start=start.isoformat(),
                end=(end-timedelta(days=1)).isoformat(),request=request,event=_hash(event_id),event_id=event_id,scope=_hash(scope),
                proof=initial,report_proof=report_digest,refs=list(refs),ordinal=ordinal,pages=len(pages),seal=_seal(row))
            value = json.dumps(receipt,ensure_ascii=False,separators=(',',':'))
            if len(value.encode())>16384:
                _deny()
            store.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(_receipt_key(engine.b,identifier),value))
        _same_host(engine,host)
        fresh = binding.registered_inputs(engine,code,payload)
        fresh_reported = reports.reported_inputs(engine,code,payload)
        _same_host(engine,host)
        if fresh != inputs or fresh_reported!=reported or _proof(engine,code,payload,fresh,reader,fresh_reported) != (initial,refs,report_digest):
            _deny()
        # Catch mutations in queue/protect callbacks, including earlier pages.
        for ordinal in range(1,len(pages)+1):
            dedupe = PREFIX+_hash_from_event(_hash(event_id),scope)+':'+str(ordinal)
            identifier = store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,dedupe)).fetchone()[0]
            row = _outbox(reader,engine.b,identifier)
            if row['text']!=pages[ordinal-1] or row['state']!='pending' or row['parse_mode']!='HTML' or row['preferred_transport']!=payload['channel'] or row['recipient']!=payload['sender']:
                _deny()
            _protected(reader,engine.b,identifier,refs)
            saved = _load_receipt(reader,engine.b,identifier)
            if saved['seal']!=_seal(row) or saved['proof']!=initial or saved['report_proof']!=report_digest:
                _deny()
    except Exception:
        store.execute('ROLLBACK TO weekly_review_build')
        store.execute('RELEASE weekly_review_build')
        # Scope failures have no authority even to queue a private clarification.
        try:
            _same_host(engine,host)
            binding._gate(engine,payload,host[3])
        except Exception:
            pass
        else:
            store.queue(engine.b,payload['sender'],NOTICE,'weekly-review-notice:'+_hash(event_id),engine.now(),preferred_transport=payload['channel'])
    else:
        _same_host(engine,host)
        store.execute('RELEASE weekly_review_build')
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event_id))


def _load_receipt(reader,business,identifier):
    rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',16384)})+' FROM runtime WHERE key=?',(_receipt_key(business,identifier),))
    if len(rows)!=1:
        _deny()
    receipt=json.loads(rows[0]['value'])
    if type(receipt) is not dict or set(receipt)!=set(('version','business','outbox','code','start','end','request','event','event_id','scope','proof','report_proof','refs','ordinal','pages','seal')):
        _deny()
    if type(receipt['event_id']) is not str or not 0<len(receipt['event_id'].encode())<=1024 or _hash(receipt['event_id'])!=receipt['event']:
        _deny()
    if type(receipt['version']) is not int or receipt['version']!=2 or receipt['business']!=business or receipt['outbox']!=identifier:
        _deny()
    if type(receipt['ordinal']) is not int or type(receipt['pages']) is not int or not 1<=receipt['ordinal']<=receipt['pages']<=8:
        _deny()
    if type(receipt['request']) is not dict or set(receipt['request'])!={'sender','channel','source_id'}:
        _deny()
    if type(receipt['refs']) is not list or len(receipt['refs'])>256 or any(type(r) is not str or not 0<len(r)<=160 or r.startswith('meeting-access:') for r in receipt['refs']):
        _deny()
    if receipt['refs']!=sorted(set(receipt['refs'])):
        _deny()
    for key in ('event','scope','proof','report_proof','seal'):
        if type(receipt[key]) is not str or not re.fullmatch('[0-9a-f]{64}',receipt[key]):
            _deny()
    return receipt


def cancel(store,business,identifier):
    store.execute("UPDATE outbox SET state='cancelled',error=? WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",(CANCEL,business,identifier))


def make_delivery_guard(engine):
    """Fixed namespace-only callback bound to the existing initialized Engine."""
    host=_host(engine)
    def guard(row, route=None, transport=None):
        if not row['dedupe'].startswith(PREFIX):
            return False
        try:
            _same_host(engine,host)
            reader=_Reader(engine.s)
            current=_outbox(reader,engine.b,row['id'])
            receipt=_load_receipt(reader,engine.b,row['id'])
            if current['state'] not in ('pending','retry','blocked') or any(current[k]!=row[k] for k in current):
                _deny()
            if receipt['seal']!=_seal(current) or current['parse_mode']!='HTML' or current['task_id'] is not None or render._units(current['text'])>3500:
                _deny()
            payload=receipt['request']
            scope,parts=_scope(engine.c,payload)
            if receipt['scope']!=_hash(scope) or payload['sender']!=current['recipient'] or payload['channel']!=current['preferred_transport']:
                _deny()
            code,start,end=_request('Сверь планёрку '+receipt['code']+' за '+receipt['start']+' — '+receipt['end'])
            expected=PREFIX+_hash_from_event(receipt['event'],scope)+':'+str(receipt['ordinal'])
            # event is already hashed; dedupe uses that same public metadata seal.
            if current['dedupe']!=expected:
                _deny()
            turns=reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64)})+' FROM answer_citation_turns WHERE business=? AND scope_key=? AND event_key=?',(engine.b,_hash(scope),receipt['event']))
            if len(turns)!=1:
                _deny()
            accepted=_accepted(reader,engine.b,receipt['event_id'])
            expected_text='Сверь планёрку '+code+' за '+receipt['start']+' — '+receipt['end']
            if accepted != dict(payload,text=expected_text):
                _deny()
            inputs=binding.registered_inputs(engine,code,payload)
            reported=reports.reported_inputs(engine,code,payload)
            _same_host(engine,host)
            if _proof(engine,code,payload,inputs,reader,reported)!=(receipt['proof'],tuple(receipt['refs']),receipt['report_proof']):
                _deny()
            _protected(reader,engine.b,current['id'],receipt['refs'])
            _same_host(engine,host)
            if route is not None:
                if transport!=parts[2] or str(route.get('chat_id'))!=parts[3] or (route.get('business_connection_id') or None)!=parts[4] or (route.get('message_thread_id') or None)!=parts[5]:
                    _deny()
            return True
        except Exception:
            cancel(host[0],host[2],row['id'])
            return False
    return guard


def _hash_from_event(event_hash,scope):
    return _hash([event_hash,scope])


def _accepted(reader,business,event_id):
    # Exact accepted command only, bounded fields extracted within SQL. No
    # archive/history lookup, transcript transfer or copied body in receipts.
    fields={'sender':_text('sender',128),'channel':_text('channel',16),
            'source_id':_text('source_id',1024),'text':_text('text',160)}
    extracted=','.join("json_extract(checked,'$."+key+"') AS "+key for key in fields)
    sql=('SELECT '+_bounded_columns(fields)+' FROM (SELECT '+extracted+
        " FROM (SELECT CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=16384 AND json_valid(payload) THEN payload END AS checked FROM events WHERE business=? AND id=? AND kind='reply'))")
    rows=reader.query(sql,(business,event_id))
    if len(rows)!=1:_deny()
    return rows[0]
