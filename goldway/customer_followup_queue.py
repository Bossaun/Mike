"""Disabled durable LOCAL processing observations for one admitted conversation.

No employee/customer reply, delivery, consent, contact send or real queue activation.
"""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
import hashlib
import json
import re

from . import customer_rules as core
from .customer_ingest import CustomerIngest,SourceRegistration,SourceProof,registration_fingerprint
from .store import Store

MAX_STATE_BYTES=65536
MAX_BYTES=1048576
MAX_QUERIES=32
MAX_ROWS=16


class FollowupUnavailable(ValueError):
    def __init__(self):super().__init__('customer_followup_unavailable')


def _deny():raise FollowupUnavailable()


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
    parsed=_utc(datetime.fromisoformat(value))
    if parsed.isoformat()!=value:_deny()
    return parsed


def _duration(value):
    if type(value) is not timedelta or not timedelta(0)<value<=timedelta(days=366):_deny()
    return value


def _window(value):
    if type(value) is not core.Window:_deny()
    start,end=_utc(value.start),_utc(value.end)
    if not start<end or end-start>timedelta(days=366):_deny()
    return core.Window(start,end)


def _micros(value):return (value.days*86400+value.seconds)*1000000+value.microseconds


def _query_data(window,stale):
    window=_window(window);stale=_duration(stale)
    return dict(start=window.start.isoformat(),end=window.end.isoformat(),stale_microseconds=_micros(stale))


def _check_query(value):
    _closed(value,{'start','end','stale_microseconds'})
    if type(value['stale_microseconds']) is not int or not 1<=value['stale_microseconds']<=366*86400*1000000:_deny()
    query=_query_data(core.Window(_date(value['start']),_date(value['end'])),timedelta(microseconds=value['stale_microseconds']))
    if query!=value:_deny()
    return query


def _proof_data(value):
    if type(value) is not SourceProof or type(value.state_present) is not bool:_deny()
    for text in (value.source_id,value.business,value.channel):_id(text)
    for digest in (value.registration_fingerprint,value.state_digest):
        if type(digest) is not str or re.fullmatch('[0-9a-f]{64}',digest) is None:_deny()
    return dict(source_id=value.source_id,business=value.business,channel=value.channel,registration_fingerprint=value.registration_fingerprint,state_digest=value.state_digest,state_present=value.state_present)


@dataclass(frozen=True)
class CandidateReceipt:
    scope_fingerprint:str
    source_proof:SourceProof
    candidate_id:str
    kind:str
    target_id:str
    due_at:datetime
    evidence_ids:tuple[str,...]
    window:core.Window
    stale_after:timedelta
    prepared_at:datetime
    local_revision:int
    local_processing_only:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


@dataclass(frozen=True)
class QueueView:
    preview:core.Preview
    receipt:CandidateReceipt|None
    local_processing_only:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


@dataclass(frozen=True)
class ObservationReceipt:
    observation_id:str
    candidate_id:str
    state:str
    at:datetime
    already_recorded:bool
    local_processing_only:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


def _receipt_data(receipt):
    if type(receipt) is not CandidateReceipt or receipt.local_processing_only is not True or receipt.live_authorized is not False:_deny()
    if type(receipt.scope_fingerprint) is not str or re.fullmatch('[0-9a-f]{64}',receipt.scope_fingerprint) is None:_deny()
    for value in (receipt.candidate_id,receipt.target_id):_id(value)
    if receipt.kind!='unanswered' or type(receipt.kind) is not str or type(receipt.local_revision) is not int or not 1<=receipt.local_revision<2**53:_deny()
    if type(receipt.evidence_ids) is not tuple or not 1<=len(receipt.evidence_ids)<=4:_deny()
    for value in receipt.evidence_ids:_id(value)
    if len(set(receipt.evidence_ids))!=len(receipt.evidence_ids):_deny()
    window=_window(receipt.window);stale=_duration(receipt.stale_after)
    return dict(scope_fingerprint=receipt.scope_fingerprint,source_proof=_proof_data(receipt.source_proof),candidate_id=receipt.candidate_id,kind=receipt.kind,target_id=receipt.target_id,due_at=_utc(receipt.due_at).isoformat(),evidence_ids=list(receipt.evidence_ids),window=dict(start=window.start.isoformat(),end=window.end.isoformat()),stale_microseconds=_micros(stale),prepared_at=_utc(receipt.prepared_at).isoformat(),local_revision=receipt.local_revision)


def _decode_receipt(value):
    _closed(value,{'scope_fingerprint','source_proof','candidate_id','kind','target_id','due_at','evidence_ids','window','stale_microseconds','prepared_at','local_revision'})
    proof=_closed(value['source_proof'],{'source_id','business','channel','registration_fingerprint','state_digest','state_present'})
    window=_closed(value['window'],{'start','end'})
    if type(value['stale_microseconds']) is not int or not 1<=value['stale_microseconds']<=366*86400*1000000 or type(value['evidence_ids']) is not list:_deny()
    result=CandidateReceipt(value['scope_fingerprint'],SourceProof(**proof),value['candidate_id'],value['kind'],value['target_id'],_date(value['due_at']),tuple(value['evidence_ids']),core.Window(_date(window['start']),_date(window['end'])),timedelta(microseconds=value['stale_microseconds']),_date(value['prepared_at']),value['local_revision'])
    if _receipt_data(result)!=value:_deny()
    return result


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


class CustomerFollowupQueue:
    def __init__(self,*,store,ingest,source,conversation,enabled=False):
        self._store=store;self._ingest=ingest;self._source=source;self._conversation=conversation;self._enabled=enabled is True
        self._host=(store,getattr(store,'db',None),ingest,source,conversation)

    def _scope(self):
        s,db,ingest,source,conversation=self._host
        if self._store is not s or self._store.db is not db or type(s) is not Store or self._ingest is not ingest or type(ingest) is not CustomerIngest or self._source is not source or self._conversation is not conversation:_deny()
        if type(source) is not SourceRegistration or type(conversation) is not core.Conversation:_deny()
        fingerprint=registration_fingerprint(source)
        for value in (conversation.customer_id,conversation.channel,conversation.thread_id,conversation.source_id):_id(value)
        if conversation.source_id!=source.source_id or conversation.channel!=source.channel:_deny()
        scope=dict(business=source.business,provider_id=source.provider_id,channel=source.channel,account_id=source.account_id,revision=source.revision,conversation=dict(customer_id=conversation.customer_id,channel=conversation.channel,thread_id=conversation.thread_id,source_id=conversation.source_id))
        return scope,'customer-followup:v1:'+_hash(scope),fingerprint

    def _proof(self,now,fingerprint):
        self._scope()
        proof=self._ingest.source_proof(now=now,expected_store=self._store)
        _proof_data(proof)
        if proof.registration_fingerprint!=fingerprint or proof.source_id!=self._source.source_id or proof.business!=self._source.business or proof.channel!=self._source.channel:_deny()
        self._scope()
        return proof

    def _decode(self,raw,scope,fingerprint):
        if raw is None:return dict(version=1,scope=scope,revision=0,last_now=None,query=None,current=None,records=[]),None,[]
        state=json.loads(raw,object_pairs_hook=_pairs);_closed(state,{'version','scope','revision','last_now','query','current','records'})
        if type(state['version']) is not int or state['version']!=1 or state['scope']!=scope or type(state['revision']) is not int or not 1<=state['revision']<2**53:_deny()
        # Validate exact closed scope, including nested field sets and builtin types.
        _closed(state['scope'],scope);_closed(state['scope']['conversation'],scope['conversation'])
        if type(state['scope']['revision']) is not int:_deny()
        for key in ('business','provider_id','channel','account_id'):_id(state['scope'][key])
        for value in state['scope']['conversation'].values():_id(value)
        query=_check_query(state['query'])
        last=_date(state['last_now']);current=None if state['current'] is None else _decode_receipt(state['current'])
        if type(state['records']) is not list or len(state['records'])>64:_deny()
        records=[];ids=set();observations=set();dispositions=[]
        for record in state['records']:
            _closed(record,{'receipt','observations'});receipt=_decode_receipt(record['receipt'])
            if receipt.candidate_id in ids:_deny()
            ids.add(receipt.candidate_id)
            if type(record['observations']) is not list or not 1<=len(record['observations'])<=2:_deny()
            sequence=[];previous_at=None
            for observation in record['observations']:
                _closed(observation,{'id','state','evidence_id','at'});identifier=_id(observation['id']);_id(observation['evidence_id']);at=_date(observation['at'])
                if identifier in observations or not receipt.prepared_at<=at<=last:_deny()
                if previous_at is not None and at<previous_at:_deny()
                previous_at=at
                observations.add(identifier);sequence.append(observation['state'])
                dispositions.append(core.Disposition(identifier,self._conversation,receipt.candidate_id,at,observation['state'],observation['evidence_id']))
            if sequence not in (['acknowledged'],['resolved'],['acknowledged','resolved']):_deny()
            if len(observations)>128:_deny()
            records.append(receipt)
        for receipt in ([current] if current else [])+records:
            if receipt.scope_fingerprint!=_hash(scope) or receipt.source_proof.registration_fingerprint!=fingerprint or receipt.source_proof.source_id!=self._source.source_id or receipt.source_proof.business!=self._source.business or receipt.source_proof.channel!=self._source.channel or receipt.local_revision>state['revision'] or receipt.prepared_at>last:_deny()
        if current is not None and (current.candidate_id in ids or _query_data(current.window,current.stale_after)!=query):_deny()
        return state,current,dispositions

    def _preview(self,now,window,stale,dispositions):
        result=self._ingest.preview_unanswered(self._conversation,now=now,window=window,stale_after=stale,dispositions=tuple(dispositions))
        if type(result) is not core.Preview or type(result.candidates) is not tuple or type(result.blocked) is not tuple or len(result.candidates)>1 or len(result.blocked)>1 or result.candidates and result.blocked:_deny()
        for candidate in result.candidates:
            if type(candidate) is not core.Candidate or candidate.conversation!=self._conversation or candidate.kind!='unanswered' or candidate.owner_id is not None or candidate.draft_only is not True:_deny()
            for value in (candidate.id,candidate.target_id):_id(value)
            if type(candidate.evidence_ids) is not tuple or not 1<=len(candidate.evidence_ids)<=4:_deny()
            for value in candidate.evidence_ids:_id(value)
            _utc(candidate.due_at)
        for blocked in result.blocked:
            if type(blocked) is not core.Blocked or blocked.conversation!=self._conversation:_deny()
            _id(blocked.target_id);_id(blocked.reason)
        return result

    def _matches(self,candidate,receipt):
        return (candidate.id,candidate.kind,candidate.target_id,_utc(candidate.due_at),candidate.evidence_ids)==(receipt.candidate_id,receipt.kind,receipt.target_id,receipt.due_at,receipt.evidence_ids)

    def _write(self,reader,key,raw,state):
        if not 1<=state['revision']<2**53:_deny()
        encoded=_json(state)
        if len(encoded.encode())>MAX_STATE_BYTES:_deny()
        if raw is None:count=self._store.execute('INSERT INTO runtime(key,value) SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM runtime WHERE key=?)',(key,encoded,key)).rowcount
        else:count=self._store.execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(encoded,key,raw)).rowcount
        if count!=1:_deny()
        return encoded

    def refresh_unanswered(self,*,now,window,stale_after):
        if not self._enabled:_deny()
        try:
            now=_utc(now);window=_window(window);stale=_duration(stale_after);scope,key,fingerprint=self._scope()
            if self._store.db.in_transaction:_deny()
            reader=_Reader(self._store)
            with self._store.transaction():
                proof=self._proof(now,fingerprint);raw=reader.read(key);state,current,dispositions=self._decode(raw,scope,fingerprint)
                if state['last_now'] is not None and now<_date(state['last_now']):_deny()
                result=self._preview(now,window,stale,dispositions)
                if self._proof(now,fingerprint)!=proof:_deny()
                receipt=None
                if result.candidates:
                    candidate=result.candidates[0]
                    if current and current.source_proof==proof and current.window==window and current.stale_after==stale and self._matches(candidate,current):receipt=current
                    else:receipt=CandidateReceipt(_hash(scope),proof,candidate.id,candidate.kind,candidate.target_id,_utc(candidate.due_at),candidate.evidence_ids,window,stale,now,state['revision']+1)
                    if len(state['records'])>=64 and candidate.id not in {r['receipt']['candidate_id'] for r in state['records']}:_deny()
                payload=None if receipt is None else _receipt_data(receipt)
                query=_query_data(window,stale)
                expected=raw
                if raw is None or state['current']!=payload or state['last_now']!=now.isoformat() or state['query']!=query:
                    state=dict(state,revision=state['revision']+1,last_now=now.isoformat(),query=query,current=payload)
                    expected=self._write(reader,key,raw,state)
                if self._proof(now,fingerprint)!=proof or reader.read(key)!=expected:_deny()
                self._scope()
            return QueueView(result,receipt)
        except FollowupUnavailable:raise
        except Exception:raise FollowupUnavailable() from None

    def record_disposition(self,receipt,*,state,observation_id,evidence_id,at,now):
        if not self._enabled:_deny()
        try:
            now=_utc(now);at=_utc(at);_id(observation_id);_id(evidence_id);payload=_receipt_data(receipt)
            if type(state) is not str or state not in ('acknowledged','resolved') or not receipt.prepared_at<=at<=now:_deny()
            scope,key,fingerprint=self._scope()
            if self._store.db.in_transaction:_deny()
            reader=_Reader(self._store);observation=dict(id=observation_id,state=state,evidence_id=evidence_id,at=at.isoformat())
            with self._store.transaction():
                proof=self._proof(now,fingerprint);raw=reader.read(key);saved,current,dispositions=self._decode(raw,scope,fingerprint)
                if raw is None or now<_date(saved['last_now']) or receipt.source_proof!=proof:_deny()
                recorded=None
                for record in saved['records']:
                    for previous in record['observations']:
                        if previous['id']==observation_id:
                            if previous!=observation or record['receipt']!=payload:_deny()
                            recorded=previous
                if recorded is not None:
                    if self._proof(now,fingerprint)!=proof or reader.read(key)!=raw:_deny()
                    self._scope()
                    return ObservationReceipt(observation_id,receipt.candidate_id,state,at,True)
                if saved['query']!=_query_data(receipt.window,receipt.stale_after):_deny()
                existing=next((r for r in saved['records'] if r['receipt']['candidate_id']==receipt.candidate_id),None)
                transition=existing is not None
                if transition:
                    if current is not None:_deny()
                    if existing['receipt']!=payload or [o['state'] for o in existing['observations']]!=['acknowledged'] or state!='resolved' or at<_date(existing['observations'][-1]['at']):_deny()
                    selected=tuple(d for d in dispositions if d.candidate_id!=receipt.candidate_id)
                else:
                    if current is None or _receipt_data(current)!=payload or len(saved['records'])>=64:_deny()
                    selected=tuple(dispositions)
                if len(dispositions)>=128:_deny()
                before=self._preview(now,receipt.window,receipt.stale_after,selected)
                if len(before.candidates)!=1 or not self._matches(before.candidates[0],receipt):_deny()
                if self._proof(now,fingerprint)!=proof:_deny()
                actual=core.Disposition(observation_id,self._conversation,receipt.candidate_id,at,state,evidence_id)
                after=self._preview(now,receipt.window,receipt.stale_after,tuple(dispositions)+(actual,))
                if after.candidates or after.blocked or self._proof(now,fingerprint)!=proof:_deny()
                records=[dict(receipt=r['receipt'],observations=list(r['observations'])) for r in saved['records']]
                if transition:next(r for r in records if r['receipt']['candidate_id']==receipt.candidate_id)['observations'].append(observation)
                else:records.append(dict(receipt=payload,observations=[observation]))
                updated=dict(saved,revision=saved['revision']+1,last_now=now.isoformat(),current=None,records=records)
                expected=self._write(reader,key,raw,updated)
                if self._proof(now,fingerprint)!=proof or reader.read(key)!=expected:_deny()
                self._scope()
            return ObservationReceipt(observation_id,receipt.candidate_id,state,at,False)
        except FollowupUnavailable:raise
        except Exception:raise FollowupUnavailable() from None
