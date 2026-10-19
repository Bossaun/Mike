"""Default-disabled, one-page metadata ingestion for an explicit injected stream.

Host registrations and provider confirmation marks are assertions, not authenticated
customer permissions, delivery receipts or consent. No message text or send path.
"""
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
import hashlib
import json
import re

from . import customer_rules as core

MAX_STATE_BYTES=262144
MAX_BYTES=1048576
MAX_QUERIES=32
MAX_ROWS=256


class CustomerIngestUnavailable(ValueError):
    def __init__(self):super().__init__('customer_ingest_unavailable')


def _deny():raise CustomerIngestUnavailable()


def _id(value):
    if type(value) is not str or not 0<len(value)<=128 or value!=value.strip() or len(value.encode())>512 or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    return value


def _utc(value):
    if type(value) is not datetime or value.utcoffset() is None:_deny()
    return value.astimezone(timezone.utc)


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _hash(value):return hashlib.sha256(_json(value).encode()).hexdigest()


def _cursor(value):return None if value is None else _id(value)


def _closed(value,keys):
    if type(value) is not dict or set(value)!=set(keys):_deny()
    return value


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:_deny()
        result[key]=value
    return result


@dataclass(frozen=True)
class SourceRegistration:
    business:str
    provider_id:str
    channel:str
    account_id:str
    revision:int
    valid_from:datetime
    expires_at:datetime
    revoked:bool=False

    def __post_init__(self):
        for value in (self.business,self.provider_id,self.channel,self.account_id):_id(value)
        if type(self.revision) is not int or not 1<=self.revision<2**53 or type(self.revoked) is not bool or _utc(self.valid_from)>=_utc(self.expires_at):_deny()

    @property
    def identity(self):
        return dict(business=self.business,provider_id=self.provider_id,channel=self.channel,account_id=self.account_id,revision=self.revision)

    @property
    def stream_hash(self):return _hash([self.business,self.provider_id,self.channel,self.account_id])

    @property
    def source_id(self):return 'customer-stream:'+self.stream_hash

    @property
    def runtime_key(self):return 'customer-ingest:v1:'+self.stream_hash


@dataclass(frozen=True)
class IngestReceipt:
    source_id:str
    cursor:str|None
    retained_events:int
    page_fingerprint:str
    replayed:bool
    no_op:bool
    live_authorized:bool=field(default=False,init=False)


def registration_fingerprint(registration):
    """Canonical metadata fingerprint; host assertion, never provider authority."""
    if type(registration) is not SourceRegistration:_deny()
    registration.__post_init__()
    return _hash(['customer-ingest-registration:v1',registration.identity,
                  _utc(registration.valid_from).isoformat(),_utc(registration.expires_at).isoformat(),registration.revoked])


@dataclass(frozen=True)
class SourceProof:
    source_id:str
    business:str
    channel:str
    registration_fingerprint:str
    state_digest:str
    state_present:bool


class _Reader:
    def __init__(self,store):self._store=store;self.queries=0;self.rows=0;self.bytes=0

    def state(self,key):
        self.queries+=1
        if self.queries>MAX_QUERIES:_deny()
        rows=self._store.execute('''SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=?
            THEN value END AS value FROM runtime WHERE key=? LIMIT 2''',(MAX_STATE_BYTES,key)).fetchall()
        self.rows+=len(rows)
        if len(rows)>1 or self.rows>MAX_ROWS:_deny()
        if not rows:return None
        value=rows[0]['value']
        if type(value) is not str:_deny()
        self.bytes+=len(value.encode())
        if self.bytes>MAX_BYTES:_deny()
        return value


def _callable_identity(value):
    return getattr(value,'__self__',None),getattr(value,'__func__',value)


def _event(row,registration,*,stored=False):
    required={'event_id','customer_id','thread_id','at','direction','confirmed'}
    if type(row) is not dict or not required<=set(row) or set(row)-required-{'reply_to'}:_deny()
    event_id=_id(row['event_id']);customer=_id(row['customer_id']);thread=_id(row['thread_id'])
    at=row['at']
    if stored:
        if type(at) is not str:_deny()
        at=datetime.fromisoformat(at)
        if _utc(at).isoformat()!=row['at']:_deny()
    at=_utc(at)
    reply=row.get('reply_to')
    if reply is not None:_id(reply)
    if row['direction'] not in ('incoming','outgoing') or type(row['direction']) is not str or type(row['confirmed']) is not bool:_deny()
    conversation=core.Conversation(customer,registration.channel,thread,registration.source_id)
    evidence='customer-evidence:'+_hash([registration.source_id,registration.channel,customer,thread,event_id])
    return core.Event(event_id,conversation,at,evidence,row['direction'],row['confirmed'],reply)


def _serialized(event):
    return dict(event_id=event.id,customer_id=event.conversation.customer_id,thread_id=event.conversation.thread_id,
                at=_utc(event.at).isoformat(),direction=event.direction,confirmed=event.confirmed,reply_to=event.reply_to)


def _retention(events):
    if type(events) is not tuple or len(events)>128 or len({event.conversation for event in events})>4:_deny()


class CustomerIngest:
    def __init__(self,*,store,current_source,provider,enabled=False):
        self._store=store;self._current_source=current_source;self._provider=provider;self._enabled=enabled is True
        self._host=(store,getattr(store,'db',None),current_source,provider,_callable_identity(getattr(provider,'fetch',None)))
        self._bound=None

    def _host_check(self):
        s,db,callback,provider,fetch=self._host
        current_fetch=_callable_identity(getattr(self._provider,'fetch',None))
        if self._store is not s or self._store.db is not db or self._current_source is not callback or self._provider is not provider or any(a is not b for a,b in zip(fetch,current_fetch)):_deny()

    def _active(self,now):
        self._host_check();registration=self._current_source()
        if type(registration) is not SourceRegistration:_deny()
        registration.__post_init__()
        if registration.revoked or not _utc(registration.valid_from)<=now<_utc(registration.expires_at):_deny()
        identity=_json(registration.identity)
        if self._bound is None:self._bound=identity
        elif self._bound!=identity:_deny()
        self._host_check()
        return registration

    def _state(self,raw,registration):
        if raw is None:return None,()
        state=json.loads(raw,object_pairs_hook=_pairs)
        _closed(state,{'version','registration','cursor','events','last_receipt','committed_cursors'})
        stored_registration=_closed(state['registration'],{'business','provider_id','channel','account_id','revision'})
        for key in ('business','provider_id','channel','account_id'):_id(stored_registration[key])
        if type(stored_registration['revision']) is not int or not 1<=stored_registration['revision']<2**53:_deny()
        if type(state['version']) is not int or state['version']!=1 or stored_registration!=registration.identity:_deny()
        if _cursor(state['cursor']) is None:_deny()
        inventory=state['committed_cursors']
        if type(inventory) is not list or not 1<=len(inventory)<=128:_deny()
        for item in inventory:
            if _cursor(item) is None:_deny()
        if len(set(inventory))!=len(inventory) or inventory[-1]!=state['cursor']:_deny()
        rows=state['events']
        if type(rows) is not list or len(rows)>128:_deny()
        events=tuple(_event(row,registration,stored=True) for row in rows);_retention(events)
        identities=[(event.conversation.channel,event.conversation.source_id,event.conversation.thread_id,event.id) for event in events]
        if len(set(identities))!=len(identities):_deny()
        last=_closed(state['last_receipt'],{'fingerprint','cursor_before','cursor_after'})
        if type(last['fingerprint']) is not str or re.fullmatch('[0-9a-f]{64}',last['fingerprint']) is None:_deny()
        if _cursor(last['cursor_before'])!=(inventory[-2] if len(inventory)>1 else None):_deny()
        if _cursor(last['cursor_after'])!=state['cursor']:_deny()
        return state,events

    def ingest_once(self,*,now):
        if not self._enabled:_deny()
        try:
            now=_utc(now);registration=self._active(now)
            if self._store.db.in_transaction:_deny()
            reader=_Reader(self._store);initial=reader.state(registration.runtime_key);state,retained=self._state(initial,registration)
            cursor=state['cursor'] if state else None
            # Exactly one injected metadata fetch, outside the write transaction.
            page=self._provider.fetch(cursor,limit=32)
            self._host_check()
            _closed(page,{'mode','cursor_before','cursor_after','events'})
            if page['mode']!='incremental' or type(page['mode']) is not str:_deny()
            before=_cursor(page['cursor_before']);after=_cursor(page['cursor_after'])
            if type(page['events']) not in (tuple,list) or len(page['events'])>32:_deny()
            incoming=tuple(_event(row,registration) for row in page['events'])
            fingerprint=_hash(dict(mode='incremental',cursor_before=before,cursor_after=after,events=[_serialized(row) for row in incoming]))
            replay=bool(state and state['last_receipt']==dict(fingerprint=fingerprint,cursor_before=before,cursor_after=after))
            if before!=cursor and not replay:_deny()
            manifest=core.normalize_batch(retained+incoming,mode='incremental',source_id=registration.source_id,channel=registration.channel,cursor_before=before,cursor_after=after)
            if type(manifest) is not core.BatchManifest or manifest.mode!='incremental' or manifest.source_id!=registration.source_id or manifest.channel!=registration.channel or manifest.cursor_before!=before or manifest.cursor_after!=after or type(manifest.events) is not tuple or any(type(event) is not core.Event for event in manifest.events):_deny()
            _retention(manifest.events)
            # Output must retain all admitted metadata; no invented detached event.
            supplied=set(retained+incoming)
            if set(manifest.events)!=supplied:_deny()
            if replay and manifest.events!=retained:_deny()
            no_op=before==after==cursor and not incoming and manifest.events==retained
            inventory=list(state['committed_cursors']) if state else []
            if not replay and not no_op:
                if after is None or after==before or after in inventory or len(inventory)>=128:_deny()
                inventory.append(after)
            next_state=dict(version=1,registration=registration.identity,cursor=after,committed_cursors=inventory,events=[_serialized(event) for event in manifest.events],last_receipt=dict(fingerprint=fingerprint,cursor_before=before,cursor_after=after))
            encoded=_json(next_state)
            if len(encoded.encode())>MAX_STATE_BYTES:_deny()
            with self._store.transaction():
                if self._active(now)!=registration or reader.state(registration.runtime_key)!=initial:_deny()
                expected=initial
                if not replay and not no_op:
                    self._store.execute('INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(registration.runtime_key,encoded));expected=encoded
                if self._active(now)!=registration or reader.state(registration.runtime_key)!=expected:_deny()
                self._host_check()
            return IngestReceipt(registration.source_id,cursor if replay or no_op else after,len(manifest.events),fingerprint,replay,no_op)
        except CustomerIngestUnavailable:raise
        except Exception:raise CustomerIngestUnavailable() from None

    def source_proof(self,*,now,expected_store):
        """Read-only metadata proof for the exact captured Store; no raw state."""
        if not self._enabled:_deny()
        try:
            if expected_store is not self._host[0] or expected_store is not self._store:_deny()
            now=_utc(now);registration=self._active(now);reader=_Reader(self._store)
            initial=reader.state(registration.runtime_key);self._state(initial,registration)
            if self._active(now)!=registration:_deny()
            current=reader.state(registration.runtime_key);self._state(current,registration)
            if current!=initial:_deny()
            self._host_check()
            fingerprint=registration_fingerprint(registration)
            digest=_hash(['customer-ingest-state-proof:v1',fingerprint,initial])
            return SourceProof(registration.source_id,registration.business,registration.channel,fingerprint,digest,initial is not None)
        except CustomerIngestUnavailable:raise
        except Exception:raise CustomerIngestUnavailable() from None

    def preview_unanswered(self,conversation,*,now,window,stale_after,dispositions):
        if not self._enabled:_deny()
        try:
            now=_utc(now);registration=self._active(now);reader=_Reader(self._store)
            if type(conversation) is not core.Conversation or conversation.source_id!=registration.source_id or conversation.channel!=registration.channel:_deny()
            for value in (conversation.customer_id,conversation.thread_id):_id(value)
            if type(window) is not core.Window or type(stale_after) is not timedelta or type(dispositions) is not tuple or len(dispositions)>128:_deny()
            for row in dispositions:
                if type(row) is not core.Disposition or row.conversation!=conversation:_deny()
                for value in (row.id,row.candidate_id,row.evidence_id):_id(value)
                _utc(row.at)
            initial=reader.state(registration.runtime_key);state,retained=self._state(initial,registration)
            selected=tuple(row for row in retained if row.conversation==conversation)
            result=core.unanswered(selected,now=now,window=window,stale_after=stale_after,dispositions=dispositions)
            if type(result) is not core.Preview or type(result.candidates) is not tuple or type(result.blocked) is not tuple or any(type(row) is not core.Candidate or row.conversation!=conversation or row.draft_only is not True for row in result.candidates) or any(type(row) is not core.Blocked or row.conversation!=conversation for row in result.blocked):_deny()
            if self._active(now)!=registration or reader.state(registration.runtime_key)!=initial:_deny()
            self._host_check()
            return result
        except CustomerIngestUnavailable:raise
        except Exception:raise CustomerIngestUnavailable() from None
