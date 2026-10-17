"""Negative ordinary update-target barrier; never supplies action permission.

A remembered focus/filtered singleton cannot override contradictory host order.
Only current literal evidence + existing explicit/singleton rules authorize edits.
"""
from dataclasses import dataclass
import json
import math

from .answer_citations import _hash
from .task_card_reference import _gate, _barrier, _resolve as card_reference
from .task_card_command import _snapshot
from .monthly_task_history import _Reader, _bounded_columns, _text, _integer, _numeric, _documents
from .intelligence import reader_key
from .work_context import WORK_PREFIX
from .consent_anchor import guarded

NOTICE='К какой задаче относится изменение или результат? Ответьте на её карточку или назовите задачу; старый фокус не использован.'
_FIELDS=('id','public_number','title','owner','deadline','next_step','criterion','status','revision')
_WINDOW=128


class MutationAmbiguous(ValueError):
    def __init__(self):super().__init__('ordinary_mutation_target_ambiguous')


def _fail():raise MutationAmbiguous()


def _active(engine,payload):
    return (type(payload) is dict and 'team_intake' in engine.c
        and payload.get('channel') in ('bot','business') and not guarded(engine,payload))


def _policy(engine):
    roster=[]
    for key,person in sorted(engine.c['employees'].items()):
        name=person.get('name')
        if name is not None and (type(name) is not str or len(name)>256):_fail()
        roster.append((key,person['telegram_user_id'],name))
    return _hash([engine.b,engine.c['admins'],roster,engine.c['timezone'],engine.c['team_intake'],
                  engine.c['allowed_live_recipients'],engine.c['business_account_user_id']])


def _acl(reader,business,task_id):
    rows=reader.query('SELECT '+_bounded_columns({'evidence_ids':_text('evidence_ids',8192)})+
        ' FROM intelligence_task_acl WHERE business=? AND task_id=?',(business,task_id))
    return _hash(rows)


def _chronology(engine,event_id,payload,intelligence,reader):
    """Bounded metadata and optional canonical card proof; denial only."""
    try:
        scope,parts=_gate(engine,payload,intelligence)
        now=engine.now()
        if type(now) not in (int,float) or not math.isfinite(now) or not 0<=now<2**53:_fail()
        current=_barrier(reader,engine.b,scope,event_id)
        prior=reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64)})+
            ' FROM answer_citation_turns WHERE id=(SELECT id FROM answer_citation_turns '
            'WHERE business=? AND scope_key=? AND id<? ORDER BY id DESC LIMIT 1)',(engine.b,_hash(scope),current))
        prior_key=prior[0]['event_key'] if prior else None
        top=reader.query('SELECT COALESCE(MAX(id),0) AS id FROM outbox')[0]['id'];floor=max(1,top-_WINDOW+1)
        fields={'id':_integer('id'),'task_id':_text('task_id',160,True),'dedupe':_text('dedupe',512),
            'state':_text('state',16),'transport':_text('transport',16,True),'preferred_transport':_text('preferred_transport',16,True),
            'destination_chat':_text('destination_chat',128,True),'destination_connection':_text('destination_connection',512,True),
            'destination_thread':_integer('destination_thread',True),'created':_numeric('created')}
        rows=reader.query('SELECT '+_bounded_columns(fields)+
            ' FROM outbox NOT INDEXED WHERE id BETWEEN ? AND ? AND business=? AND recipient=? ORDER BY id',
            (floor,top,engine.b,payload['sender']),maximum=_WINDOW)
        def same_route(row):
            if row['transport'] is None:return row['preferred_transport'] in (None,parts[2])
            return (row['transport']==parts[2] and row['destination_chat']==parts[3]
                and (row['destination_connection'] or None)==parts[4] and (row['destination_thread'] or None)==parts[5])
        relevant=[row for row in rows if same_route(row)]
        pending_card=reader.query('SELECT '+_bounded_columns({'outbox_id':_integer('outbox_id')})+
            ' FROM task_card_references WHERE business=? AND scope_key=? AND event_key=?',
            (engine.b,_hash(scope),prior_key)) if prior_key is not None else []
        if not relevant:
            if pending_card:_fail()  # Outside the finite window, never an archive fallback.
            return dict(blocked=False,target=None,seal=_hash([prior_key,[]]))
        row=relevant[-1];at=payload.get('message_at')
        if row['dedupe'].startswith('mutation-target:'):_fail()
        if (row['state']!='sent' or row['transport']!=parts[2] or row['created']>now
            or type(at) not in (int,float) or not math.isfinite(at) or not 0<at<=now):_fail()
        deliveries=reader.query('SELECT '+_bounded_columns({'at':_numeric('at')})+
            " FROM deliveries WHERE outbox_id=? AND state='sent'",(row['id'],))
        if len(deliveries)!=1 or deliveries[0]['at']>now or math.floor(deliveries[0]['at'])>math.floor(at):_fail()
        card_proof=None
        if row['dedupe'].startswith(('task-card:','task-reference:')) or pending_card:
            # A valid card can veto a different target. Matching it never adds
            # the task to candidates or bypasses literal/action/role validation.
            card_proof=card_reference(engine,event_id,payload,intelligence,reader)
            if card_proof[0]['outbox_id']!=row['id']:_fail()
        return dict(blocked=False,target=row['task_id'],seal=_hash([prior_key,row,deliveries,card_proof]))
    except Exception:
        return dict(blocked=True,target=None,seal=None)


@dataclass
class TargetGuard:
    intelligence:object
    store:object
    config:object
    policy:str|None
    originals:frozenset
    rows:dict
    acls:dict
    chronology:dict
    reader:object
    valid:bool=False


def capture(engine,event_id,payload,context,original_ids):
    if not _active(engine,payload) or context.get('kind')!='operator_chat':return None
    reader=_Reader(engine.s)
    token=TargetGuard(engine.intelligence,engine.s,engine.c,None,frozenset(),{}, {},dict(blocked=True),reader)
    try:
        _gate(engine,payload,token.intelligence)
        rows=context.get('tasks')
        if type(rows) is not list or len(rows)>12 or len(original_ids)>12:_fail()
        for row in rows:
            item={key:row[key] for key in _FIELDS}
            if type(item['id']) is not str or not 0<len(item['id'])<=160 or item['id'] in token.rows:_fail()
            # Context already exists; reject an excessive host projection before
            # freezing it, rather than adding any provider-visible metadata.
            if len(json.dumps(item,ensure_ascii=False).encode())>16384:_fail()
            token.rows[item['id']]=item
            token.acls[item['id']]=_acl(reader,engine.b,item['id'])
        token.originals=frozenset(original_ids);token.policy=_policy(engine)
        token.chronology=_chronology(engine,event_id,payload,token.intelligence,reader)
        token.valid=True
    except Exception:pass
    return token


def _explicit(engine,payload,row):
    if payload.get('task_id'):return payload['task_id']==row['id']
    refs=engine.reference_ids(payload['text'])
    if refs:return row['id'] in refs
    title=row.get('title')
    return type(title) is str and bool(title) and title.casefold() in payload['text'].casefold()


def _current_target(engine,payload,token,expected):
    """Point checks for ordinary rows; dependency graphs reuse existing card ACL.

    No unrelated register cap for ordinary doc-only or unrestricted tasks.
    Work dependency closure retains the existing bounded fixed-point contract.
    """
    reader=token.reader;task_id=expected['id']
    rows=reader.query('SELECT '+_bounded_columns({'evidence_ids':_text('evidence_ids',8192)})+
        ' FROM intelligence_task_acl WHERE business=? AND task_id=?',(engine.b,task_id))
    if _hash(rows)!=token.acls[task_id]:_fail()
    refs=json.loads(rows[0]['evidence_ids']) if rows else []
    if (type(refs) is not list or len(refs)>32 or len(set(refs))!=len(refs)
        or any(type(ref) is not str or not 0<len(ref)<=160 for ref in refs)):_fail()
    if any(ref.startswith(WORK_PREFIX) for ref in refs):
        current,_,_=_snapshot(reader,engine.s,engine.c,payload['sender'],token.intelligence.index,
            engine.now(),None,task_id)
        if current!=expected:_fail()
        return
    _documents(reader,set(refs),reader_key(engine.b,payload['sender']),engine.now())
    spec={'id':_text('id',160),'public_number':_integer('public_number'),'revision':_integer('revision'),
        'title':_text('title',4000),'owner':_text('owner',128,True),'deadline':_text('deadline',64,True),
        'next_step':_text('next_step',4000),'criterion':_text('criterion',4000),'status':_text('status',32),
        'source_id':_text('source_id',256)}
    rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM tasks WHERE business=? AND id=?',(engine.b,task_id))
    if len(rows)!=1:_fail()
    row=rows[0];source=row.pop('source_id')
    if row!=expected or row['status'] not in ('proposed','confirmed','in_progress','blocked','completed'):_fail()
    if payload['sender'] not in engine.c['admins'] and (row['owner']!=payload['sender'] or row['status']=='proposed'):_fail()
    links=reader.query('SELECT '+_bounded_columns({'source_id':_text('source_id',256)})+
        ' FROM task_sources WHERE business=? AND task_id=?',(engine.b,task_id),maximum=64)
    sources={source}|{r['source_id'] for r in links}
    if len(sources)>64:_fail()
    for source in sorted(sources):
        current=reader.query('SELECT '+_bounded_columns({'deleted':"typeof(deleted)='integer' AND deleted IN (0,1)"})+
            ' FROM sources WHERE business=? AND id=?',(engine.b,source))
        if current!=[dict(deleted=0)]:_fail()


def permit(engine,payload,context,action):
    if type(action) is not dict or action.get('op')!='update':return
    captured=context.get('_mutation_target_guard')
    if type(captured) is not TargetGuard and (not _active(engine,payload) or context.get('kind')!='operator_chat'):return
    try:
        token=context.get('_mutation_target_guard')
        if type(token) is not TargetGuard or not token.valid or token.store is not engine.s or token.config is not engine.c:_fail()
        scope,_=_gate(engine,payload,token.intelligence)
        if context.get('kind')!='operator_chat' or _policy(engine)!=token.policy:_fail()
        _barrier(token.reader,engine.b,scope,payload['link'])
        task_id=action.get('task_id');expected=token.rows.get(task_id)
        if expected is None or next((r for r in context.get('tasks',[]) if r['id']==task_id),None)!=expected:_fail()
        if not _explicit(engine,payload,expected):
            # A completed/otherwise filtered focus injected by enrich is never
            # an implicit writable singleton. Explicit references still work.
            if task_id not in token.originals:_fail()
            fresh=_chronology(engine,payload['link'],payload,token.intelligence,token.reader)
            if (token.chronology.get('blocked') or fresh!=token.chronology or fresh['blocked']
                or fresh['target'] not in (None,task_id)):_fail()
        # Current exact metadata and source/dependency ACL checks; this does not
        # create a grant. Shared Reader bounds all update checks in the event.
        _current_target(engine,payload,token,expected)
    except MutationAmbiguous:raise
    except Exception:raise MutationAmbiguous() from None


def may_record_response(engine,event_id,payload,context,task_id):
    """Suppress premature staff last_reply/progress writes under ambiguity."""
    if not _active(engine,payload):return True
    try:
        token=capture(engine,event_id,payload,dict(context,kind='operator_chat'),tuple(t['id'] for t in context['tasks']))
        context=dict(context,kind='operator_chat',_mutation_target_guard=token)
        permit(engine,payload,context,dict(op='update',task_id=task_id))
        return True
    except Exception:return False


def clarify(engine,event_id,payload):
    intelligence=engine.intelligence
    try:_gate(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    engine.s.queue(engine.b,payload['sender'],NOTICE,'mutation-target:'+event_id,engine.now(),preferred_transport=payload.get('channel'))
    _gate(engine,payload,intelligence)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
