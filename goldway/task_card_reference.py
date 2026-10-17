"""Read-only pronominal task reference to one prospective delivered I15 card.

Host metadata only; no model/memory/text inference, archive fallback or actions.
"""
import json
import math

from .answer_citations import _scope, _hash
from .monthly_task_history import _Reader, _bounded_columns, _text, _integer, _numeric
from .task_card_command import _intelligence_matches, _snapshot, build_task_card
from .intake import validate_team

_PHRASES=frozenset(('кто отвечает по этой задаче','какой срок у этой задачи','покажи эту задачу'))
NOTICE='Не могу однозначно связать вопрос с доставленной карточкой. Назовите номер задачи или ответьте на её карточку.'
_WINDOW=128


class ReferenceUnavailable(ValueError):
    def __init__(self):super().__init__('task_card_reference_unavailable')


def _fail():raise ReferenceUnavailable()


def is_task_reference(text):
    return (type(text) is str and len(text)<=128 and
        ' '.join(text.casefold().replace('ё','е').split()).rstrip('?!.') in _PHRASES)


def reference_request(payload):
    # Actual Telegram reply-task identity retains the existing route/priority.
    return type(payload) is dict and not payload.get('task_id') and is_task_reference(payload.get('text'))


def _gate(engine,payload,intelligence):
    if not _intelligence_matches(engine,intelligence):_fail()
    scope,parts=_scope(engine.c,payload)
    if parts[3]!=str(engine.c['employees'][payload['sender']]['telegram_user_id']):_fail()
    return scope,parts


def _barrier(reader,business,scope,event_id):
    current=reader.query('SELECT '+_bounded_columns({'id':_integer('id')})+
        ' FROM answer_citation_turns WHERE business=? AND scope_key=? AND event_key=?',
        (business,_hash(scope),_hash(event_id)))
    latest=reader.query('SELECT MAX(id) AS id FROM answer_citation_turns WHERE business=? AND scope_key=?',
        (business,_hash(scope)))
    if len(current)!=1 or latest[0]['id']!=current[0]['id']:_fail()
    return current[0]['id']


def _protected(reader,business,outbox_id,refs):
    rows=reader.query('SELECT '+_bounded_columns({'evidence_ids':_text('evidence_ids',16384)})+
        ' FROM intelligence_outbox_acl WHERE business=? AND outbox_id=?',(business,outbox_id))
    if len(rows)!=1:_fail()
    values=json.loads(rows[0]['evidence_ids'])
    if (type(values) is not list or len(values)>256 or any(type(v) is not str or not 0<len(v)<=160 for v in values)
        or len(set(values))!=len(values) or set(values)!=set(refs)):_fail()


def remember_card(engine,event_id,payload,draft,outbox_id,*,intelligence,reader=None):
    """Optional future I15 receipt, atomic with its queued canonical host card.

    Invalid/unsupported prior scope or bounded metadata may omit the receipt,
    preserving the existing card path. Host identity drift still aborts queue.
    """
    if not engine.s.db.in_transaction or not _intelligence_matches(engine,intelligence):_fail()
    try:validate_team(engine.c)
    except (ValueError,TypeError,KeyError):_fail()
    try:
        scope,_=_gate(engine,payload,intelligence)
        reader=reader if reader is not None else _Reader(engine.s)
        _barrier(reader,engine.b,scope,event_id)
        # Match trusted producer code, not arbitrary action/reminder/model text.
        from .task_card_command import is_task_card_command
        if is_task_card_command(payload['text']):prefix='task-card:'
        elif reference_request(payload):prefix='task-reference:'
        else:return False
        row=reader.query('SELECT '+_bounded_columns({'id':_integer('id'),'text':_text('text',16000),
            'business':_text('business',128),'recipient':_text('recipient',128),'task_id':_text('task_id',160),
            'dedupe':_text('dedupe',512),'state':_text('state',16),'parse_mode':_text('parse_mode',16),
            'preferred_transport':_text('preferred_transport',16)})+' FROM outbox WHERE id=?',(outbox_id,))
        if (len(row)!=1 or row[0]['business']!=engine.b or row[0]['recipient']!=payload['sender']
            or row[0]['task_id']!=draft.task_id or row[0]['text']!=draft.text or row[0]['state']!='pending'
            or row[0]['dedupe']!=prefix+event_id or row[0]['parse_mode']!='HTML'
            or row[0]['preferred_transport']!=payload['channel']):_fail()
        task,refs,fingerprint=_snapshot(reader,engine.s,engine.c,payload['sender'],intelligence.index,
            engine.now(),None,draft.task_id)
        if fingerprint!=draft.snapshot_digest or tuple(refs)!=tuple(draft.evidence_ids):_fail()
        _protected(reader,engine.b,outbox_id,refs)
        if not _intelligence_matches(engine,intelligence):_fail()
        _gate(engine,payload,intelligence)
        engine.s.execute('''INSERT INTO task_card_references
            (business,scope_key,event_key,outbox_id,body_hash,task_id,task_revision,snapshot_digest)
            VALUES(?,?,?,?,?,?,?,?)''',
            (engine.b,_hash(scope),_hash(event_id),outbox_id,_hash(draft.text),draft.task_id,task['revision'],fingerprint))
        return True
    except Exception:
        if not _intelligence_matches(engine,intelligence):_fail()
        try:validate_team(engine.c)
        except (ValueError,TypeError,KeyError):_fail()
        return False


def _resolve(engine,event_id,payload,intelligence,reader):
    scope,parts=_gate(engine,payload,intelligence)
    now=engine.now();message_at=payload.get('message_at')
    if (type(now) not in (int,float) or not math.isfinite(now) or not 0<=now<2**53
        or type(message_at) not in (int,float) or not math.isfinite(message_at) or not 0<message_at<=now):_fail()
    current=_barrier(reader,engine.b,scope,event_id)
    event=reader.query('SELECT '+_bounded_columns({'kind':_text('kind',16),'state':_text('state',16)})+
        ' FROM events WHERE business=? AND id=?',(engine.b,event_id))
    if event!=[dict(kind='reply',state='pending')]:_fail()
    prior=reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64)})+
        ' FROM answer_citation_turns WHERE id=(SELECT id FROM answer_citation_turns '
        'WHERE business=? AND scope_key=? AND id<? ORDER BY id DESC LIMIT 1)',(engine.b,_hash(scope),current))
    if len(prior)!=1:_fail()
    receipt=reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64),'outbox_id':_integer('outbox_id'),
        'body_hash':_text('body_hash',64),'task_id':_text('task_id',160),'task_revision':_integer('task_revision'),
        'snapshot_digest':_text('snapshot_digest',64)})+
        ' FROM task_card_references WHERE business=? AND scope_key=? AND event_key=?',
        (engine.b,_hash(scope),prior[0]['event_key']))
    if len(receipt)!=1:_fail()
    receipt=receipt[0];outbox_id=receipt['outbox_id']
    top=reader.query('SELECT COALESCE(MAX(id),0) AS id FROM outbox')[0]['id'];floor=max(1,top-_WINDOW+1)
    if outbox_id<floor:_fail()
    spec={'id':_integer('id'),'business':_text('business',128),'recipient':_text('recipient',128),
        'task_id':_text('task_id',160,True),'dedupe':_text('dedupe',512),'state':_text('state',16),'transport':_text('transport',16,True),
        'preferred_transport':_text('preferred_transport',16,True),'destination_chat':_text('destination_chat',128,True),
        'destination_connection':_text('destination_connection',512,True),'destination_thread':_integer('destination_thread',True),
        'created':_numeric('created')}
    # Explicit primary-key range prevents archive scans before route filtering.
    rows=reader.query('SELECT '+_bounded_columns(spec)+
        ' FROM outbox NOT INDEXED WHERE id BETWEEN ? AND ? AND business=? AND recipient=? ORDER BY id',
        (floor,top,engine.b,payload['sender']),maximum=_WINDOW)
    def same_route(row):
        if row['transport'] is None:return row['preferred_transport'] in (None,parts[2])
        return (row['transport']==parts[2] and row['destination_chat']==parts[3]
            and (row['destination_connection'] or None)==parts[4] and (row['destination_thread'] or None)==parts[5])
    relevant=[row for row in rows if same_route(row)]
    if not relevant or relevant[-1]['id']!=outbox_id:_fail()
    row=relevant[-1]
    if row['task_id']!=receipt['task_id'] or row['state']!='sent' or row['transport']!=parts[2] or row['created']>now:_fail()
    producer=next((prefix for prefix in ('task-card:','task-reference:') if row['dedupe'].startswith(prefix)),None)
    if producer is None or _hash(row['dedupe'][len(producer):])!=receipt['event_key']:_fail()
    task,refs,fingerprint=_snapshot(reader,engine.s,engine.c,payload['sender'],intelligence.index,now,None,receipt['task_id'])
    if task['revision']!=receipt['task_revision'] or fingerprint!=receipt['snapshot_digest']:_fail()
    _protected(reader,engine.b,outbox_id,refs)
    body=reader.query('SELECT '+_bounded_columns({'text':_text('text',16000),'parse_mode':_text('parse_mode',16)})+
        ' FROM outbox WHERE id=?',(outbox_id,))
    if len(body)!=1 or body[0]['parse_mode']!='HTML' or _hash(body[0]['text'])!=receipt['body_hash']:_fail()
    deliveries=reader.query('SELECT '+_bounded_columns({'at':_numeric('at')})+
        " FROM deliveries WHERE outbox_id=? AND state='sent'",(outbox_id,))
    if len(deliveries)!=1 or deliveries[0]['at']>now or math.floor(deliveries[0]['at'])>math.floor(message_at):_fail()
    return receipt,_hash([receipt,row,body,deliveries,fingerprint])


def _clarify(engine,event_id,payload,intelligence):
    _gate(engine,payload,intelligence)
    engine.s.queue(engine.b,payload['sender'],NOTICE,'task-reference:'+event_id,engine.now(),preferred_transport=payload['channel'])
    _gate(engine,payload,intelligence)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))


def handle_task_reference(engine,event_id,payload):
    if not reference_request(payload) or not engine.s.db.in_transaction:_fail()
    intelligence=engine.intelligence
    try:_gate(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    reader=_Reader(engine.s)
    try:
        initial=_resolve(engine,event_id,payload,intelligence,reader)
        draft=build_task_card(engine.s,engine.c,payload['sender'],intelligence.index,
            clock=engine.now,trusted_task_id=initial[0]['task_id'],reader=reader)
        final=_resolve(engine,event_id,payload,intelligence,reader)
        if initial!=final or draft.snapshot_digest!=initial[0]['snapshot_digest']:_fail()
    except Exception:
        _clarify(engine,event_id,payload,intelligence);return
    _gate(engine,payload,intelligence)
    key='task-reference:'+event_id
    engine.s.queue(engine.b,payload['sender'],draft.text,key,engine.now(),draft.task_id,
        parse_mode='HTML',preferred_transport=payload['channel'])
    _gate(engine,payload,intelligence)
    row=reader.query('SELECT '+_bounded_columns({'id':_integer('id')})+
        ' FROM outbox WHERE business=? AND dedupe=?',(engine.b,key))
    intelligence.protect_outbox(row[0]['id'],draft.evidence_ids)
    _gate(engine,payload,intelligence)
    if not remember_card(engine,event_id,payload,draft,row[0]['id'],intelligence=intelligence,reader=reader):_fail()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
