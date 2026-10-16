"""Private short agreement binds to one prospective delivered host prompt.

No old-turn fallback, source-ID authority, model consent inference or I14 phase
requirement. All new reads use indexed point/range queries; outbox window 128.
"""
from dataclasses import dataclass
import json
import math
import re

from .answer_citations import _hash, _scope
from .monthly_task_history import (_Reader, _bounded_columns, _text, _integer, _numeric,
                                   _current, _documents, MonthlyHistoryUnavailable)
from .work_context import WORK_PREFIX, _readable, work_id
from .task_card_command import _intelligence_matches

NOTICE = 'Уточните, пожалуйста, что именно подтвердить. Назовите задачу и действие; прежнее предложение не использовано.'
_SHORT = re.compile(r'\s*(?:да|давай|давайте|можно|хорошо|ок(?:ей)?)\s*[.!]?\s*', re.I)
_MAX_WINDOW = 128


class ConsentAmbiguous(ValueError):
    def __init__(self):
        super().__init__('short_consent_ambiguous')


def guarded(engine, payload):
    return (type(payload) is dict and type(payload.get('text')) is str
        and len(payload['text']) <= 128 and _SHORT.fullmatch(payload['text']) is not None
        and 'team_intake' in engine.c and engine._closed_team()
        and payload.get('channel') in ('bot', 'business')
        and payload.get('sender') in engine.c['employees'])


def make_memory_anchor(store, business, scope, event_id, state, own, now):
    """Bind host-generated outbox identities, not model-provided metadata."""
    if not 1 <= len(own) <= 16:
        return None
    if (state.get('pending_offer') and state['pending_offer'].get('kind')=='confirm_task'
        and (not state.get('task_id') or any(row['task_id']!=state['task_id'] for row in own))):
        return None
    revision = None
    if state.get('task_id'):
        row = store.execute('SELECT revision FROM tasks WHERE business=? AND id=?',
                            (business, state['task_id'])).fetchone()
        if row and type(row[0]) is int and 0 < row[0] < 2**53:
            revision = row[0]
    if state.get('pending_offer') and state['pending_offer'].get('kind')=='confirm_task':
        if revision is None or any(row['dedupe']!=f"action:{state['task_id']}:{revision}:{row['recipient']}" for row in own):
            return None
    return dict(version=1, scope_key=_hash(scope), event_key=_hash(event_id), at=now,
        outbox=[row['id'] for row in own],
        task_revision=revision)


@dataclass(frozen=True)
class ConsentAnchor:
    kind: str
    task_id: str | None
    fingerprint: str


class _ConsentReader(_Reader):
    def query(self, sql, params=(), *, maximum=1):
        try:
            return super().query(sql, params, maximum=maximum)
        except Exception:
            raise ConsentAmbiguous() from None


class _Access:
    """Current metadata/grants only, bounded before existing role fixed point."""
    def __init__(self, reader, engine, sender, now):
        self.r,self.e,self.sender,self.now=reader,engine,sender,now
        self.records=None
        self.docs={}

    def allowed(self, refs):
        if (type(refs) is not set or len(refs)>256 or
            any(type(ref) is not str or not 0<len(ref)<=160 for ref in refs)):
            raise ConsentAmbiguous()
        roots={ref for ref in refs if ref.startswith(WORK_PREFIX)}
        if roots and self.records is None:
            ids=self.r.query('SELECT '+_bounded_columns({'id':_text('id',160)})+
                ' FROM tasks WHERE business=?',(self.e.b,),maximum=128)
            self.records,self.acl=_current(self.r,self.e.b,tuple(row['id'] for row in ids))
        pending=list(roots);seen=set();docs=refs-roots
        while pending:
            ref=pending.pop()
            if ref in seen:continue
            seen.add(ref)
            if len(seen)>128:raise ConsentAmbiguous()
            for dependency in self.acl.get(ref) or ():
                if dependency.startswith(WORK_PREFIX):pending.append(dependency)
                else:docs.add(dependency)
        if len(docs)>256:raise ConsentAmbiguous()
        from .intelligence import reader_key
        for ref in docs:
            if ref not in self.docs:
                try:
                    _documents(self.r,{ref},reader_key(self.e.b,self.sender),self.now)
                    self.docs[ref]=True
                except MonthlyHistoryUnavailable:
                    self.docs[ref]=False
        grants={ref for ref in docs if self.docs[ref]}
        role='admin' if self.sender in self.e.c['admins'] else 'employee'
        work=_readable(self.records or {},getattr(self,'acl',{}),role,self.sender,grants)
        return refs & (grants|work)


def resolve(engine, event_id, payload):
    try:
        if not guarded(engine, payload) or not _intelligence_matches(engine, engine.intelligence):
            raise ConsentAmbiguous()
        scope, parts = _scope(engine.c, payload)
        now = engine.now()
        message_at = payload.get('message_at')
        if type(message_at) not in (int,float) or not math.isfinite(message_at) or not 0 < message_at <= now:
            raise ConsentAmbiguous()
        if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < 2**53:
            raise ConsentAmbiguous()
        # This scope is private and the current configured identity must agree.
        if parts[3] != str(engine.c['employees'][payload['sender']]['telegram_user_id']):
            raise ConsentAmbiguous()
        reader = _ConsentReader(engine.s)
        access = _Access(reader,engine,payload['sender'],now)
        needed = engine.intelligence.memory.candidate_evidence_ids(scope)
        readable = access.allowed(needed)
        memory = engine.intelligence.memory.get(scope, readable_ids=readable, now=now,
                                               include_provenance=True)
        offer, provenance = memory.get('pending_offer'), memory.get('_state_provenance')
        if (type(offer) is not dict or offer.get('kind') not in ('confirm_task','explain','draft','clarify')
            or type(provenance) is not dict or set(provenance) !=
                {'version','scope_key','event_key','at','outbox','task_revision'}
            or type(provenance['version']) is not int or provenance['version'] != 1 or provenance['scope_key'] != _hash(scope)
            or type(provenance['at']) not in (int,float) or not 0 <= provenance['at'] <= now):
            raise ConsentAmbiguous()
        active = reader.query('SELECT '+_bounded_columns({'kind':_text('kind',16),'state':_text('state',16)})+
            ' FROM events WHERE business=? AND id=?', (engine.b,event_id))
        if not active or active[0] != {'kind':'reply','state':'pending'}:
            raise ConsentAmbiguous()
        current = reader.query('SELECT '+_bounded_columns({'id':_integer('id'), 'at':_numeric('at')})+
            ' FROM answer_citation_turns WHERE business=? AND scope_key=? AND event_key=?',
            (engine.b, _hash(scope), _hash(event_id)))
        if not current or provenance['at'] > current[0]['at']:
            raise ConsentAmbiguous()
        latest = reader.query('SELECT MAX(id) AS id FROM answer_citation_turns WHERE business=? AND scope_key=?',
                              (engine.b, _hash(scope)))
        if not latest or latest[0]['id'] != current[0]['id']:
            raise ConsentAmbiguous()
        preceding = reader.query('SELECT '+_bounded_columns({'event_key':_text('event_key',64)})+
            ' FROM answer_citation_turns WHERE id=(SELECT id FROM answer_citation_turns '
            'WHERE business=? AND scope_key=? AND id<? ORDER BY id DESC LIMIT 1)',
            (engine.b, _hash(scope), current[0]['id']))
        # I14 phase unavailable is legitimate for an action card. Identity/order,
        # actual delivery chronology and current ACL decide consent instead.
        if not preceding or preceding[0]['event_key'] != provenance['event_key']:
            raise ConsentAmbiguous()
        receipts = provenance['outbox']
        if (type(receipts) is not list or not 1 <= len(receipts) <= 16
            or any(type(item) is not int or not 0 < item < 2**53 for item in receipts)
            or len(set(receipts)) != len(receipts)):
            raise ConsentAmbiguous()
        top = reader.query('SELECT COALESCE(MAX(id),0) AS id FROM outbox')[0]['id']
        floor = max(1, top-_MAX_WINDOW+1)
        if min(receipts) < floor:
            raise ConsentAmbiguous()
        fields = {'id':_integer('id'), 'business':_text('business',128), 'recipient':_text('recipient',128),
            'task_id':_text('task_id',160,True), 'dedupe':_text('dedupe',512), 'state':_text('state',16), 'transport':_text('transport',16,True),
            'preferred_transport':_text('preferred_transport',16,True),
            'destination_chat':_text('destination_chat',128,True),
            'destination_connection':_text('destination_connection',512,True),
            'destination_thread':_integer('destination_thread',True), 'created':_numeric('created')}
        rows = reader.query('SELECT '+_bounded_columns(fields)+
            ' FROM outbox WHERE id>=? AND business=? AND recipient=? ORDER BY id',
            (floor,engine.b,payload['sender']), maximum=_MAX_WINDOW)
        def same_route(row):
            if row['transport'] is not None:
                return (row['transport']==parts[2] and row['destination_chat']==parts[3]
                    and (row['destination_connection'] or None)==parts[4]
                    and (row['destination_thread'] or None)==parts[5])
            # Unresolved routing can never authorize old consent. A queue with a
            # known different preferred transport does not cross private scopes.
            return row['preferred_transport'] in (None, parts[2])
        relevant = [row for row in rows if same_route(row)]
        if not relevant or relevant[-1]['id'] != max(receipts):
            raise ConsentAmbiguous()
        by_id = {row['id']:row for row in relevant}
        seals = []
        for item in receipts:
            row = by_id.get(item)
            if (not row or row['state'] != 'sent' or row['transport'] != parts[2]
                or row['created'] > now):
                raise ConsentAmbiguous()
            protected = reader.query('SELECT '+_bounded_columns({'evidence_ids':_text('evidence_ids',8192)})+
                ' FROM intelligence_outbox_acl WHERE outbox_id=? AND business=?',(item,engine.b))
            refs=set()
            if protected:
                values=json.loads(protected[0]['evidence_ids'])
                if type(values) is not list or len(values)>256 or any(type(v) is not str for v in values):
                    raise ConsentAmbiguous()
                refs.update(values)
            if row['task_id']:
                refs.add(work_id(engine.b,row['task_id']))
            if not refs<=access.allowed(refs):raise ConsentAmbiguous()
            deliveries = reader.query('SELECT '+_bounded_columns({'at':_numeric('at')})+
                " FROM deliveries WHERE outbox_id=? AND state='sent'", (item,))
            if (len(deliveries)!=1 or deliveries[0]['at'] > now
                or math.floor(deliveries[0]['at']) > math.floor(message_at)):
                raise ConsentAmbiguous()
            row = dict(row, delivered_at=deliveries[0]['at'])
            seals.append(row)
        task_id = memory.get('task_id')
        if offer['kind']=='confirm_task':
            if payload['text'].strip().casefold().strip(' .!')!='да':
                raise ConsentAmbiguous()
            if payload['sender'] not in engine.c['admins'] or type(task_id) is not str or not 0<len(task_id)<=160:
                raise ConsentAmbiguous()
            if any(row['task_id']!=task_id or row['dedupe']!=f"action:{task_id}:{provenance['task_revision']}:{payload['sender']}" for row in seals):
                raise ConsentAmbiguous()
            task = reader.query('SELECT '+_bounded_columns({'revision':_integer('revision'),'status':_text('status',32)})+
                ' FROM tasks WHERE business=? AND id=?',(engine.b,task_id))
            if (not task or type(provenance['task_revision']) is not int or task[0]['status']!='proposed' or task[0]['revision']!=provenance['task_revision']
                or work_id(engine.b,task_id) not in access.allowed({work_id(engine.b,task_id)})):
                raise ConsentAmbiguous()
        return ConsentAnchor(offer['kind'], task_id if offer['kind']=='confirm_task' else None,
                             _hash([provenance,offer,seals]))
    except ConsentAmbiguous:
        raise
    except Exception:
        raise ConsentAmbiguous() from None


def clarify(engine, event_id, payload):
    # Called inside the existing transaction, with ordinary sender transport.
    engine.s.queue(engine.b,payload['sender'],NOTICE,'short-consent:'+event_id,engine.now(),None,
                   preferred_transport=payload['channel'])
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))


def permit_action(engine, payload, action):
    """Final gate: short assent authorizes only its exact current draft status."""
    if not guarded(engine,payload):
        return
    if type(action) is not dict:
        raise ConsentAmbiguous()
    anchor = resolve(engine,payload['link'],payload)
    action = dict(action)
    # Provider schema requires these fields even in a status-only update. They
    # cannot carry any edit/value or unsupported evidence through short assent.
    for field in ('title','owner','deadline','next_step','criterion'):
        if field in action:
            if action[field] is not None:raise ConsentAmbiguous()
            action.pop(field)
    for field in ('owner_lines','deadline_lines'):
        if field in action:
            if action[field] != []:raise ConsentAmbiguous()
            action.pop(field)
    if (anchor.kind!='confirm_task' or action.get('op')!='update' or action.get('task_id')!=anchor.task_id
        or action.get('status')!='confirmed' or
        set(action)-{'op','task_id','status','evidence','evidence_lines','set_fields'}
        or ('set_fields' in action and action['set_fields'] != ['status'])):
        raise ConsentAmbiguous()
