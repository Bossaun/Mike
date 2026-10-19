"""Disabled durable synthetic promise metadata; no reminders, sends or owner mapping."""
from dataclasses import dataclass,field
from datetime import datetime,timezone
import hashlib
import json
import re

from . import customer_rules as core
from .customer_ingest import CustomerIngest,SourceRegistration,SourceProof,PromiseEvaluation,registration_fingerprint
from .store import Store

MAX_STATE_BYTES=65536
MAX_BYTES=1048576
MAX_QUERIES=32
MAX_ROWS=16


class PromiseRegisterUnavailable(ValueError):
    def __init__(self):super().__init__('customer_promise_register_unavailable')


def _deny():raise PromiseRegisterUnavailable()


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


def _promise_data(promise,conversation):
    if type(promise) is not core.Promise or promise.conversation!=conversation or type(promise.confirmed) is not bool:_deny()
    for value in (promise.id,promise.event_id,promise.owner_id):_id(value)
    return dict(id=promise.id,event_id=promise.event_id,owner_ref=promise.owner_id,due_at=None if promise.due_at is None else _utc(promise.due_at).isoformat(),confirmed=promise.confirmed)


def _result_data(result,conversation):
    if type(result) is not core.PromiseResult or result.conversation!=conversation or type(result.confirmed) is not bool:_deny()
    for value in (result.id,result.promise_id,result.event_id):_id(value)
    return dict(id=result.id,promise_id=result.promise_id,event_id=result.event_id,confirmed=result.confirmed)


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


class CustomerPromiseRegister:
    def __init__(self,*,store,ingest,source,conversation,enabled=False):
        self._store=store;self._ingest=ingest;self._source=source;self._conversation=conversation;self._enabled=enabled is True
        self._host=(store,getattr(store,'db',None),ingest,source,conversation)

    def _scope(self):
        store,db,ingest,source,conversation=self._host
        if self._store is not store or self._store.db is not db or type(store) is not Store or self._ingest is not ingest or type(ingest) is not CustomerIngest or self._source is not source or type(source) is not SourceRegistration or self._conversation is not conversation or type(conversation) is not core.Conversation:_deny()
        fingerprint=registration_fingerprint(source)
        for value in (conversation.customer_id,conversation.channel,conversation.thread_id,conversation.source_id):_id(value)
        if conversation.source_id!=source.source_id or conversation.channel!=source.channel:_deny()
        fields=dict(customer_id=conversation.customer_id,channel=conversation.channel,thread_id=conversation.thread_id,source_id=conversation.source_id)
        scope=_hash(['customer-promises-scope:v1',source.identity,fields])
        return scope,'customer-promises:v1:'+scope,fingerprint,fields

    def _proof(self,now,fingerprint):
        self._scope();proof=self._ingest.source_proof(now=now,expected_store=self._store)
        self._check_proof(proof,fingerprint);self._scope();return proof

    def _check_proof(self,proof,fingerprint):
        _proof_data(proof)
        if proof.registration_fingerprint!=fingerprint or proof.source_id!=self._source.source_id or proof.business!=self._source.business or proof.channel!=self._source.channel:_deny()

    def _decode(self,raw,scope,fingerprint,conversation):
        if raw is None:return dict(version=1,scope_fingerprint=scope,registration_fingerprint=fingerprint,conversation=conversation,revision=0,last_write_at=None,promises=[],results=[],anchors={}),(),()
        state=json.loads(raw,object_pairs_hook=_pairs)
        _closed(state,{'version','scope_fingerprint','registration_fingerprint','conversation','revision','last_write_at','promises','results','anchors'})
        if type(state['version']) is not int or state['version']!=1 or state['scope_fingerprint']!=scope or state['registration_fingerprint']!=fingerprint or state['conversation']!=conversation or type(state['revision']) is not int or not 1<=state['revision']<2**53:_deny()
        _closed(state['conversation'],conversation)
        for value in state['conversation'].values():_id(value)
        last=_date(state['last_write_at'])
        if type(state['promises']) is not list or len(state['promises'])>8 or type(state['results']) is not list or len(state['results'])>16 or type(state['anchors']) is not dict or len(state['anchors'])>24:_deny()
        promises=[];results=[];record_ids=set();declarations=set();result_events=set();expected=set()
        for kind,entries in (('promise',state['promises']),('result',state['results'])):
            for entry in entries:
                _closed(entry,{'payload','registered_at','source_proof'})
                if _date(entry['registered_at'])>last:_deny()
                proof=_closed(entry['source_proof'],{'source_id','business','channel','registration_fingerprint','state_digest','state_present'})
                original=SourceProof(**proof);self._check_proof(original,fingerprint)
                if original.state_present is not True:_deny()
                data=entry['payload']
                if kind=='promise':
                    _closed(data,{'id','event_id','owner_ref','due_at','confirmed'})
                    row=core.Promise(data['id'],self._conversation,data['event_id'],data['owner_ref'],None if data['due_at'] is None else _date(data['due_at']),data['confirmed'])
                    if _promise_data(row,self._conversation)!=data or row.event_id in declarations:_deny()
                    declarations.add(row.event_id);promises.append(row)
                else:
                    _closed(data,{'id','promise_id','event_id','confirmed'})
                    row=core.PromiseResult(data['id'],self._conversation,data['promise_id'],data['event_id'],data['confirmed'])
                    if _result_data(row,self._conversation)!=data or row.event_id in result_events or row.promise_id not in {p.id for p in promises}:_deny()
                    result_events.add(row.event_id);results.append(row)
                if row.id in record_ids:_deny()
                record_ids.add(row.id);expected.add(row.event_id)
        if declarations&result_events or set(state['anchors'])!=expected:_deny()
        for key,seal in state['anchors'].items():
            _id(key)
            if type(seal) is not str or re.fullmatch('[0-9a-f]{64}',seal) is None:_deny()
        return state,tuple(promises),tuple(results)

    def _evaluate(self,promises,results,now,proof,anchors):
        evaluation=self._ingest.preview_promises(self._conversation,now=now,expected_store=self._store,promises=promises,results=results)
        if type(evaluation) is not PromiseEvaluation or evaluation.requires_review is not True or evaluation.live_authorized is not False or evaluation.source_proof!=proof or type(evaluation.anchor_seals) is not tuple or len(evaluation.anchor_seals)>24:_deny()
        current={}
        for item in evaluation.anchor_seals:
            if type(item) is not tuple or len(item)!=2:_deny()
            identifier,seal=item;_id(identifier)
            if identifier in current or type(seal) is not str or re.fullmatch('[0-9a-f]{64}',seal) is None:_deny()
            current[identifier]=seal
        if set(current)!={p.event_id for p in promises}|{r.event_id for r in results} or any(current.get(key)!=value for key,value in anchors.items()):_deny()
        self._scope();return evaluation,current

    def _record(self,record,now,kind):
        if not self._enabled:_deny()
        try:
            now=_utc(now);scope,key,fingerprint,conversation=self._scope()
            data=_promise_data(record,self._conversation) if kind=='promise' else _result_data(record,self._conversation)
            if self._store.db.in_transaction:_deny()
            reader=_Reader(self._store)
            with self._store.transaction():
                proof=self._proof(now,fingerprint);raw=reader.read(key);state,promises,results=self._decode(raw,scope,fingerprint,conversation)
                if state['last_write_at'] is not None and now<_date(state['last_write_at']):_deny()
                same=next((entry for entry in state['promises' if kind=='promise' else 'results'] if entry['payload']['id']==record.id),None)
                if same is not None:
                    if same['payload']!=data:_deny()
                    self._evaluate(promises,results,now,proof,state['anchors'])
                    if self._proof(now,fingerprint)!=proof or reader.read(key)!=raw:_deny()
                    self._scope()
                    return RegistrationReceipt(record.id,kind,_date(same['registered_at']),state['revision'],True)
                if record.id in {row.id for row in promises+results}:_deny()
                if kind=='promise':
                    if len(promises)>=8 or record.event_id in {p.event_id for p in promises}|{r.event_id for r in results}:_deny()
                    proposed_promises=promises+(record,);proposed_results=results
                else:
                    if len(results)>=16 or record.promise_id not in {p.id for p in promises} or record.event_id in {p.event_id for p in promises}|{r.event_id for r in results}:_deny()
                    proposed_promises=promises;proposed_results=results+(record,)
                evaluation,anchors=self._evaluate(proposed_promises,proposed_results,now,proof,state['anchors'])
                if self._proof(now,fingerprint)!=proof:_deny()
                entry=dict(payload=data,registered_at=now.isoformat(),source_proof=_proof_data(proof))
                updated=dict(state,revision=state['revision']+1,last_write_at=now.isoformat(),anchors=anchors,promises=list(state['promises']),results=list(state['results']))
                updated['promises' if kind=='promise' else 'results'].append(entry)
                if not 1<=updated['revision']<2**53:_deny()
                encoded=_json(updated)
                if len(encoded.encode())>MAX_STATE_BYTES:_deny()
                if raw is None:count=self._store.execute('INSERT INTO runtime(key,value) SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM runtime WHERE key=?)',(key,encoded,key)).rowcount
                else:count=self._store.execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(encoded,key,raw)).rowcount
                if count!=1 or self._proof(now,fingerprint)!=proof or reader.read(key)!=encoded:_deny()
                self._scope()
            return RegistrationReceipt(record.id,kind,now,updated['revision'],False)
        except PromiseRegisterUnavailable:raise
        except Exception:raise PromiseRegisterUnavailable() from None

    def record_promise(self,promise,*,now):return self._record(promise,now,'promise')
    def record_result(self,result,*,now):return self._record(result,now,'result')

    def preview_due(self,*,now):
        if not self._enabled:_deny()
        try:
            now=_utc(now);scope,key,fingerprint,conversation=self._scope();reader=_Reader(self._store)
            proof=self._proof(now,fingerprint);raw=reader.read(key);state,promises,results=self._decode(raw,scope,fingerprint,conversation)
            if state['last_write_at'] is not None and now<_date(state['last_write_at']):_deny()
            evaluation,anchors=self._evaluate(promises,results,now,proof,state['anchors'])
            if self._proof(now,fingerprint)!=proof or reader.read(key)!=raw:_deny()
            self._scope();return evaluation
        except PromiseRegisterUnavailable:raise
        except Exception:raise PromiseRegisterUnavailable() from None
