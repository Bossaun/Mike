"""Disabled durable synthetic touch metadata; no reminders, sends or owner mapping."""
from dataclasses import dataclass,field
from datetime import datetime,timezone,timedelta
import hashlib
import json
import re

from . import customer_rules as core
from .customer_ingest import CustomerIngest,SourceRegistration,SourceProof,TouchEvaluation,REVIEW_DRAFT,registration_fingerprint
from .store import Store

MAX_STATE_BYTES=65536
MAX_BYTES=1048576
MAX_QUERIES=32
MAX_ROWS=16


class TouchRegisterUnavailable(ValueError):
    def __init__(self):super().__init__('customer_touch_register_unavailable')


def _deny():raise TouchRegisterUnavailable()


def _id(value):
    if type(value) is not str or not 0<len(value)<=128 or value!=value.strip() or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value


def _utc(value):
    if type(value) is not datetime or value.utcoffset() is None:_deny()
    return value.astimezone(timezone.utc)


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _hash(value):return hashlib.sha256(_json(value).encode()).hexdigest()


def _closed(value,keys):
    if type(value) is not dict or set(value)!=set(keys):_deny()
    return value


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:_deny()
        result[key]=value
    return result


def _date(value):
    if type(value) is not str:_deny()
    result=_utc(datetime.fromisoformat(value))
    if result.isoformat()!=value:_deny()
    return result


def _proof_data(proof):
    if type(proof) is not SourceProof or type(proof.state_present) is not bool:_deny()
    for value in (proof.source_id,proof.business,proof.channel):_id(value)
    for value in (proof.registration_fingerprint,proof.state_digest):
        if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:_deny()
    return dict(source_id=proof.source_id,business=proof.business,channel=proof.channel,registration_fingerprint=proof.registration_fingerprint,state_digest=proof.state_digest,state_present=proof.state_present)


def _window(value):
    if type(value) is not core.Window:_deny()
    start=_utc(value.start);end=_utc(value.end)
    if not timedelta(0)<end-start<=timedelta(days=366):_deny()
    return [start.isoformat(),end.isoformat()]


def _from_window(value):
    if type(value) is not list or len(value)!=2:_deny()
    result=core.Window(_date(value[0]),_date(value[1]))
    if _window(result)!=value:_deny()
    return result


def _data(row,conversation,kind):
    classes={'plan':core.TouchPlan,'permission':core.ContactPermission,'receipt':core.TouchReceipt}
    if type(row) is not classes[kind] or row.conversation!=conversation:_deny()
    row.__post_init__();_id(row.id);_id(row.event_id)
    data=dict(id=row.id,event_id=row.event_id)
    if kind=='plan':
        _id(row.owner_id);_id(row.permission_id)
        if row.draft!=REVIEW_DRAFT:_deny()
        data.update(owner_ref=row.owner_id,permission_id=row.permission_id,readiness=row.readiness,window=_window(row.window))
    elif kind=='permission':data.update(granted=row.granted,opt_out=row.opt_out,window=None if row.window is None else _window(row.window))
    else:_id(row.plan_id);data.update(plan_id=row.plan_id,confirmed=row.confirmed)
    return data


def _record_from(data,conversation,kind):
    if kind=='plan':
        _closed(data,{'id','event_id','owner_ref','permission_id','readiness','window'})
        result=core.TouchPlan(data['id'],conversation,data['event_id'],data['owner_ref'],data['permission_id'],data['readiness'],_from_window(data['window']),REVIEW_DRAFT)
    elif kind=='permission':
        _closed(data,{'id','event_id','granted','opt_out','window'})
        result=core.ContactPermission(data['id'],conversation,data['event_id'],data['granted'],data['opt_out'],None if data['window'] is None else _from_window(data['window']))
    else:
        _closed(data,{'id','event_id','plan_id','confirmed'})
        result=core.TouchReceipt(data['id'],conversation,data['plan_id'],data['event_id'],data['confirmed'])
    if _data(result,conversation,kind)!=data:_deny()
    return result


@dataclass(frozen=True)
class RegistrationReceipt:
    record_id:str
    kind:str
    registered_at:datetime
    local_revision:int
    already_recorded:bool
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


class _Reader:
    def __init__(self,store):self.store=store;self.queries=0;self.rows=0;self.bytes=0
    def read(self,key):
        self.queries+=1
        if self.queries>MAX_QUERIES:_deny()
        rows=self.store.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN value END FROM runtime WHERE key=? LIMIT 2",(MAX_STATE_BYTES,key)).fetchall()
        self.rows+=len(rows)
        if len(rows)>1 or self.rows>MAX_ROWS:_deny()
        if not rows:return None
        value=rows[0][0]
        if type(value) is not str:_deny()
        self.bytes+=len(value.encode())
        if self.bytes>MAX_BYTES:_deny()
        return value


class CustomerTouchRegister:
    def __init__(self,*,store,ingest,source,conversation,enabled=False):
        self._store=store;self._ingest=ingest;self._source=source;self._conversation=conversation;self._enabled=enabled is True
        self._host=(store,getattr(store,'db',None),ingest,source,conversation)
        self._methods=tuple((getattr(getattr(ingest,name,None),'__self__',None),getattr(getattr(ingest,name,None),'__func__',None)) for name in ('source_proof','evaluate_touches'))

    def _scope(self):
        store,db,ingest,source,conversation=self._host
        if self._store is not store or self._store.db is not db or type(store) is not Store or self._ingest is not ingest or type(ingest) is not CustomerIngest or self._source is not source or type(source) is not SourceRegistration or self._conversation is not conversation or type(conversation) is not core.Conversation:_deny()
        for name,expected in zip(('source_proof','evaluate_touches'),self._methods):
            method=getattr(ingest,name,None)
            if getattr(method,'__self__',None) is not expected[0] or getattr(method,'__func__',None) is not expected[1]:_deny()
        fingerprint=registration_fingerprint(source)
        for value in (conversation.customer_id,conversation.channel,conversation.thread_id,conversation.source_id):_id(value)
        if conversation.source_id!=source.source_id or conversation.channel!=source.channel:_deny()
        fields=dict(customer_id=conversation.customer_id,channel=conversation.channel,thread_id=conversation.thread_id,source_id=conversation.source_id)
        scope=_hash(['customer-touches-scope:v1',source.identity,fields])
        return scope,'customer-touches:v1:'+scope,fingerprint,fields

    def _proof(self,now,fingerprint):
        self._scope();proof=self._ingest.source_proof(now=now,expected_store=self._store)
        self._check_proof(proof,fingerprint);self._scope();return proof

    def _check_proof(self,proof,fingerprint):
        _proof_data(proof)
        if proof.registration_fingerprint!=fingerprint or proof.source_id!=self._source.source_id or proof.business!=self._source.business or proof.channel!=self._source.channel:_deny()

    def _decode(self,raw,scope,fingerprint,conversation):
        if raw is None:return dict(version=1,scope_fingerprint=scope,registration_fingerprint=fingerprint,conversation=conversation,revision=0,last_write_at=None,plans=[],permissions=[],receipts=[],anchors={}),dict(plan=(),permission=(),receipt=())
        state=json.loads(raw,object_pairs_hook=_pairs)
        _closed(state,{'version','scope_fingerprint','registration_fingerprint','conversation','revision','last_write_at','plans','permissions','receipts','anchors'})
        if type(state['version']) is not int or state['version']!=1 or state['scope_fingerprint']!=scope or state['registration_fingerprint']!=fingerprint or state['conversation']!=conversation or type(state['revision']) is not int or not 1<=state['revision']<2**53:_deny()
        _closed(state['conversation'],conversation)
        last=_date(state['last_write_at']);records={};allids=set();expected=set()
        for kind,key,cap in (('plan','plans',8),('permission','permissions',16),('receipt','receipts',16)):
            entries=state[key]
            if type(entries) is not list or len(entries)>cap:_deny()
            rows=[]
            for entry in entries:
                _closed(entry,{'payload','registered_at','source_proof'})
                if _date(entry['registered_at'])>last:_deny()
                proof=_closed(entry['source_proof'],{'source_id','business','channel','registration_fingerprint','state_digest','state_present'})
                original=SourceProof(**proof);self._check_proof(original,fingerprint)
                if original.state_present is not True:_deny()
                row=_record_from(entry['payload'],self._conversation,kind)
                if row.id in allids:_deny()
                allids.add(row.id);expected.add(row.event_id);rows.append(row)
            records[kind]=tuple(rows)
        if any(row.plan_id not in {p.id for p in records['plan']} for row in records['receipt']):_deny()
        if type(state['anchors']) is not dict or len(state['anchors'])>40 or set(state['anchors'])!=expected:_deny()
        for key,seal in state['anchors'].items():
            _id(key)
            if type(seal) is not str or re.fullmatch('[0-9a-f]{64}',seal) is None:_deny()
        return state,records

    def _evaluate(self,records,now,proof,anchors,reporting_window=None):
        evaluation=self._ingest.evaluate_touches(self._conversation,now=now,expected_store=self._store,plans=records['plan'],permissions=records['permission'],receipts=records['receipt'],reporting_window=reporting_window)
        if type(evaluation) is not TouchEvaluation or evaluation.requires_review is not True or evaluation.live_authorized is not False or evaluation.source_proof!=proof or type(evaluation.anchor_seals) is not tuple or len(evaluation.anchor_seals)>40:_deny()
        current={}
        for item in evaluation.anchor_seals:
            if type(item) is not tuple or len(item)!=2:_deny()
            identifier,seal=item;_id(identifier)
            if identifier in current or type(seal) is not str or re.fullmatch('[0-9a-f]{64}',seal) is None:_deny()
            current[identifier]=seal
        if set(current)!={row.event_id for rows in records.values() for row in rows} or any(current.get(key)!=value for key,value in anchors.items()):_deny()
        self._scope();return evaluation,current

    def _record(self,record,now,kind):
        if not self._enabled:_deny()
        try:
            now=_utc(now);scope,key,fingerprint,conversation=self._scope();data=_data(record,self._conversation,kind)
            if self._store.db.in_transaction:_deny()
            reader=_Reader(self._store);keyname={'plan':'plans','permission':'permissions','receipt':'receipts'}[kind]
            with self._store.transaction():
                proof=self._proof(now,fingerprint);raw=reader.read(key);state,records=self._decode(raw,scope,fingerprint,conversation)
                if state['last_write_at'] is not None and now<_date(state['last_write_at']):_deny()
                same=next((entry for entry in state[keyname] if entry['payload']['id']==record.id),None)
                if same is not None:
                    if same['payload']!=data:_deny()
                    self._evaluate(records,now,proof,state['anchors'])
                    if self._proof(now,fingerprint)!=proof or reader.read(key)!=raw:_deny()
                    self._scope();return RegistrationReceipt(record.id,kind,_date(same['registered_at']),state['revision'],True)
                if record.id in {row.id for rows in records.values() for row in rows} or len(records[kind])>=dict(plan=8,permission=16,receipt=16)[kind]:_deny()
                proposed=dict(records);proposed[kind]+= (record,)
                evaluation,anchors=self._evaluate(proposed,now,proof,state['anchors'])
                if self._proof(now,fingerprint)!=proof:_deny()
                updated=dict(state,revision=state['revision']+1,last_write_at=now.isoformat(),anchors=anchors)
                updated[keyname]=list(state[keyname])+[dict(payload=data,registered_at=now.isoformat(),source_proof=_proof_data(proof))]
                if not 1<=updated['revision']<2**53:_deny()
                encoded=_json(updated)
                if len(encoded.encode())>MAX_STATE_BYTES:_deny()
                if raw is None:count=self._store.execute('INSERT INTO runtime(key,value) SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM runtime WHERE key=?)',(key,encoded,key)).rowcount
                else:count=self._store.execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(encoded,key,raw)).rowcount
                if count!=1 or self._proof(now,fingerprint)!=proof or reader.read(key)!=encoded:_deny()
                self._scope()
            return RegistrationReceipt(record.id,kind,now,updated['revision'],False)
        except TouchRegisterUnavailable:raise
        except Exception:raise TouchRegisterUnavailable() from None

    def record_permission(self,permission,*,now):return self._record(permission,now,'permission')
    def record_plan(self,plan,*,now):return self._record(plan,now,'plan')
    def record_receipt(self,receipt,*,now):return self._record(receipt,now,'receipt')

    def preview(self,*,now,reporting_window=None):
        if not self._enabled:_deny()
        try:
            now=_utc(now)
            if reporting_window is not None:_window(reporting_window)
            scope,key,fingerprint,conversation=self._scope();reader=_Reader(self._store)
            proof=self._proof(now,fingerprint);raw=reader.read(key);state,records=self._decode(raw,scope,fingerprint,conversation)
            if state['last_write_at'] is not None and now<_date(state['last_write_at']):_deny()
            evaluation,anchors=self._evaluate(records,now,proof,state['anchors'],reporting_window)
            if self._proof(now,fingerprint)!=proof or reader.read(key)!=raw:_deny()
            self._scope();return evaluation
        except TouchRegisterUnavailable:raise
        except Exception:raise TouchRegisterUnavailable() from None
