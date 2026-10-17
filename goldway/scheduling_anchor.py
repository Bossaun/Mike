"""One prospective host reminder prompt; no dialogue/model task-focus authority.

Only metadata in a point-key runtime receipt, atomic with its original queue.
No source bodies, archive scan, new delivery or mutation permission.
"""
import json
import math
from .answer_citations import _hash
from .task_card_reference import _gate, _barrier
from .monthly_task_history import _Reader, _bounded_columns, _text, _integer, _numeric, MonthlyHistoryUnavailable
from .consent_anchor import _Access
from .work_context import work_id

_WINDOW=128


def _key(scope):return 'scheduling-prompt:v1:'+_hash(scope)


def capture(engine,event_id,payload,task_id,outbox_id):
    if not engine.s.db.in_transaction:raise ValueError('scheduling_anchor_transaction')
    intelligence=engine.intelligence
    # Legacy/nonclosed paths can use explicit targets; never grant implicit focus.
    try:scope,parts=_gate(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):return
    row=engine.s.execute('SELECT revision FROM tasks WHERE business=? AND id=?',(engine.b,task_id)).fetchone()
    if not row:return
    doc=dict(version=1,kind='dialogue',event_key=_hash(event_id),producer_key=_hash(event_id),task_id=task_id,revision=row[0],outbox_id=outbox_id)
    engine.s.execute('INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (_key(scope),json.dumps(doc,separators=(',',':'))))


def _due_receipts(scheduler,check,outbox_id):
    """Actual fired-check producer only, max two existing private route scopes.

    Captures latest accepted barrier AT QUEUE time, not a later inbound event.
    No configured route is invented; delivery must later prove the exact route.
    """
    from .answer_citations import _scope
    c=scheduler.c;recipient=check['owner'];reader=_Reader(scheduler.s)
    employee=c.get('employees',{}).get(recipient,{})
    uid=employee.get('telegram_user_id')
    if type(uid) is not int or uid<=0:return []
    chats=reader.query('SELECT '+_bounded_columns({'bot_chat':_text('bot_chat',128,True),
        'business_chat':_text('business_chat',128,True),'connection':_text('connection',512,True)})+
        ' FROM chats WHERE business=? AND recipient=?',(scheduler.b,recipient))
    task=reader.query('SELECT '+_bounded_columns({'revision':_integer('revision')})+
        ' FROM tasks WHERE business=? AND id=?',(scheduler.b,check['task_id']))
    if len(chats)!=1 or len(task)!=1:return []
    routes=[];chat=chats[0]
    if chat['bot_chat']==str(uid):routes.append(('bot',f'telegram:{uid}:1'))
    if chat['business_chat']==str(uid) and chat['connection']:
        routes.append(('business',f"business:{chat['connection']}:{uid}:1"))
    receipts=[]
    for channel,source in routes:
        try:scope,parts=_scope(c,dict(sender=recipient,channel=channel,source_id=source))
        except (ValueError,TypeError,KeyError,AttributeError):continue
        prior=reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64)})+
            ' FROM answer_citation_turns WHERE id=(SELECT id FROM answer_citation_turns '
            'WHERE business=? AND scope_key=? ORDER BY id DESC LIMIT 1)',
            (scheduler.b,_hash(scope)))
        doc=dict(version=1,kind='due',event_key=prior[0]['event_key'] if prior else None,
            producer_key=_hash(check['id']),task_id=check['task_id'],revision=task[0]['revision'],outbox_id=outbox_id)
        receipts.append((_key(scope),json.dumps(doc,separators=(',',':'))))
    return receipts



def capture_due(scheduler,check,outbox_id):
    """Optional proof bounds may omit receipts; actual storage failures propagate."""
    if not scheduler.s.db.in_transaction:raise ValueError('scheduling_anchor_transaction')
    try:receipts=_due_receipts(scheduler,check,outbox_id)
    except MonthlyHistoryUnavailable:return
    # Outside the read-validation catch: never suppress real DB write failures.
    for key,value in receipts:
        scheduler.s.execute('INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key,value))


def resolve(engine,event_id,payload):
    """Return (target,seal) or None. Every missing/legacy/drift boundary denies."""
    try:
        intelligence=engine.intelligence;scope,parts=_gate(engine,payload,intelligence)
        now=engine.now();message_at=payload.get('message_at')
        if (type(now) not in (int,float) or not math.isfinite(now) or not 0<=now<2**53
            or type(message_at) not in (int,float) or not math.isfinite(message_at) or not 0<message_at<=now):return None
        reader=_Reader(engine.s);current=_barrier(reader,engine.b,scope,event_id)
        active=reader.query('SELECT '+_bounded_columns({'kind':_text('kind',16),'state':_text('state',16)})+
            ' FROM events WHERE business=? AND id=?',(engine.b,event_id))
        if active!=[dict(kind='reply',state='pending')]:return None
        prior=reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64)})+
            ' FROM answer_citation_turns WHERE id=(SELECT id FROM answer_citation_turns '
            'WHERE business=? AND scope_key=? AND id<? ORDER BY id DESC LIMIT 1)',(engine.b,_hash(scope),current))
        saved=reader.query('SELECT '+_bounded_columns({'value':_text('value',2048)})+' FROM runtime WHERE key=?',(_key(scope),))
        if len(saved)!=1:return None
        doc=json.loads(saved[0]['value'])
        if (type(doc) is not dict or set(doc)!={'version','kind','event_key','producer_key','task_id','revision','outbox_id'}
            or type(doc['version']) is not int or doc['version']!=1 or doc['kind'] not in ('dialogue','due')
            or doc['event_key']!=(prior[0]['event_key'] if prior else None)
            or (doc['kind']=='dialogue' and not prior)
            or type(doc['producer_key']) is not str or len(doc['producer_key'])!=64
            or type(doc['task_id']) is not str or not 0<len(doc['task_id'])<=160
            or type(doc['revision']) is not int or not 0<doc['revision']<2**53
            or type(doc['outbox_id']) is not int or not 0<doc['outbox_id']<2**53):return None
        top=reader.query('SELECT COALESCE(MAX(id),0) AS id FROM outbox')[0]['id'];floor=max(1,top-_WINDOW+1)
        if doc['outbox_id']<floor:return None
        fields={'id':_integer('id'),'business':_text('business',128),'recipient':_text('recipient',128),
            'task_id':_text('task_id',160,True),'dedupe':_text('dedupe',512),'state':_text('state',16),
            'transport':_text('transport',16,True),'preferred_transport':_text('preferred_transport',16,True),
            'destination_chat':_text('destination_chat',128,True),'destination_connection':_text('destination_connection',512,True),
            'destination_thread':_integer('destination_thread',True),'created':_numeric('created')}
        rows=reader.query('SELECT '+_bounded_columns(fields)+' FROM outbox NOT INDEXED WHERE id BETWEEN ? AND ? '
            'AND business=? AND recipient=? ORDER BY id',(floor,top,engine.b,payload['sender']),maximum=_WINDOW)
        def same_route(row):
            if row['transport'] is None:return row['preferred_transport'] in (None,parts[2])
            return (row['transport']==parts[2] and row['destination_chat']==parts[3]
                and (row['destination_connection'] or None)==parts[4] and (row['destination_thread'] or None)==parts[5])
        relevant=[r for r in rows if same_route(r)]
        if not relevant or relevant[-1]['id']!=doc['outbox_id']:return None
        row=relevant[-1];prefix='reminder:c07-prompt:' if doc['kind']=='dialogue' else 'reminder:c07:'
        if (row['task_id']!=doc['task_id'] or row['state']!='sent' or row['transport']!=parts[2]
            or not row['dedupe'].startswith(prefix) or _hash(row['dedupe'][len(prefix):])!=doc['producer_key']
            or row['created']>now):return None
        if doc['kind']=='due':
            check=reader.query('SELECT '+_bounded_columns({'task_id':_text('task_id',160),'owner':_text('owner',128),
                'state':_text('state',16),'at':_numeric('at')})+
                ' FROM c07_checks WHERE business=? AND id=?',(engine.b,row['dedupe'][len(prefix):]))
            if (len(check)!=1 or check[0]['state']!='fired' or check[0]['task_id']!=doc['task_id']
                or check[0]['owner']!=payload['sender'] or check[0]['at']>row['created']):return None
        delivery=reader.query('SELECT '+_bounded_columns({'at':_numeric('at')})+
            " FROM deliveries WHERE outbox_id=? AND state='sent'",(doc['outbox_id'],))
        if (len(delivery)!=1 or delivery[0]['at']>now or now-delivery[0]['at']>86400
            or math.floor(delivery[0]['at'])>math.floor(message_at)):return None
        task=reader.query('SELECT '+_bounded_columns({'revision':_integer('revision'),'status':_text('status',32)})+
            ' FROM tasks WHERE business=? AND id=?',(engine.b,doc['task_id']))
        if len(task)!=1 or task[0]['revision']!=doc['revision'] or task[0]['status'] not in ('confirmed','in_progress','blocked'):return None
        # Current source existence/deletion is separate from knowledge ACL.
        sources=reader.query('SELECT '+_bounded_columns({'source_id':_text('source_id',256)})+
            ' FROM task_sources WHERE business=? AND task_id=?',(engine.b,doc['task_id']),maximum=32)
        root=reader.query('SELECT '+_bounded_columns({'source_id':_text('source_id',256)})+
            ' FROM tasks WHERE business=? AND id=?',(engine.b,doc['task_id']))
        source_ids={r['source_id'] for r in sources+root}
        if not source_ids:return None
        marks=','.join('?' for _ in source_ids)
        alive=reader.query('SELECT '+_bounded_columns({'id':_text('id',256),'deleted':"typeof(deleted)='integer' AND deleted IN (0,1)"})+
            ' FROM sources WHERE business=? AND id IN ('+marks+')',(engine.b,*sorted(source_ids)),maximum=33)
        if {r['id'] for r in alive}!=source_ids or any(r['deleted']!=0 for r in alive):return None
        protected=reader.query('SELECT '+_bounded_columns({'evidence_ids':_text('evidence_ids',8192)})+
            ' FROM intelligence_outbox_acl WHERE business=? AND outbox_id=?',(engine.b,doc['outbox_id']))
        if not protected and doc['kind']!='due':return None
        values=json.loads(protected[0]['evidence_ids']) if protected else []
        if type(values) is not list or len(values)>256 or any(type(v) is not str or not 0<len(v)<=160 for v in values):return None
        refs=set(values)|{work_id(engine.b,doc['task_id'])};access=_Access(reader,engine,payload['sender'],now)
        if not refs<=access.allowed(refs):return None
        _gate(engine,payload,intelligence)
        return doc['task_id'],_hash([doc,row,delivery,task,values])
    except Exception:
        return None
