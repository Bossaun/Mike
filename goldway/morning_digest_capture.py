"""Default-disabled durable local draft capture; no schedule, outbox or dispatch."""
from dataclasses import dataclass,field,asdict
from datetime import datetime,timezone,date
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo
from .engine import Engine
from .store import Store
from .intelligence import Intelligence,intelligence_enabled
from .knowledge import KnowledgeIndex
from .intake import validate_team
from . import operational_agenda_adapter as adapter
from .operational_reports import AgendaPreview,AgendaFact,DigestPreview

MAX_HEAD_BYTES=65536
MAX_PAYLOAD_BYTES=32768
MAX_BYTES=1048576
MAX_QUERIES=32
MAX_ROWS=64


class CaptureUnavailable(ValueError):
    def __init__(self):super().__init__('morning_digest_capture_unavailable')


def _deny():raise CaptureUnavailable()

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

def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:_deny()
    return value

def _time(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=2**53-1:_deny()
    return value

def _id(value):
    if type(value) is not str or not 0<len(value)<=512 or any(ord(c)<32 for c in value):_deny()
    return value

def _date(value):
    if type(value) is not str or date.fromisoformat(value).isoformat()!=value:_deny()
    return value


@dataclass(frozen=True)
class CaptureReceipt:
    day:str
    captured_at:float|None
    payload_hash:str|None
    already_captured:bool
    due:bool
    payload_verified:bool=field(default=False,init=False)
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    capture_only:bool=field(default=True,init=False)


@dataclass(frozen=True)
class CapturedDigest:
    day:str
    captured_at:float
    draft:adapter.OperationalAgendaDraft
    payload_hash:str
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    capture_only:bool=field(default=True,init=False)


class _Reader:
    def __init__(self,store):self.store=store;self.queries=0;self.rows=0;self.bytes=0
    def read(self,key,cap,*,metadata=False):
        self.queries+=1
        if self.queries>MAX_QUERIES:_deny()
        if metadata:
            expression="""CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN
                CASE WHEN json_valid(value) THEN CASE WHEN json_type(value)='object' AND (SELECT count(*) FROM json_each(runtime.value))=6 AND (SELECT count(DISTINCT key) FROM json_each(runtime.value))=6 AND NOT EXISTS(SELECT 1 FROM json_each(runtime.value) WHERE key NOT IN ('version','day','captured_at','policy','snapshot','draft')) AND json_type(value,'$.draft')='object' AND json_type(value,'$.snapshot')='text' AND length(json_extract(value,'$.snapshot'))=64 AND json_type(value,'$.day')='text' AND length(json_extract(value,'$.day'))=10
                    AND json_type(value,'$.policy')='text' AND length(json_extract(value,'$.policy'))=64
                    AND json_type(value,'$.captured_at') IN ('integer','real') AND json_type(value,'$.version')='integer'
                    THEN json_array(length(CAST(value AS BLOB)),json_extract(value,'$.day'),json_extract(value,'$.captured_at'),json_extract(value,'$.policy'),json_extract(value,'$.version'),json_extract(value,'$.snapshot')) END END END"""
        else:expression="CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN value END"
        rows=self.store.execute('SELECT '+expression+' FROM runtime WHERE key=? LIMIT 2',(cap,key)).fetchall();self.rows+=len(rows)
        if len(rows)>1 or self.rows>MAX_ROWS:_deny()
        if not rows:return None
        value=rows[0][0]
        if type(value) is not str:_deny()
        self.bytes+=len(value.encode())
        if self.bytes>MAX_BYTES:_deny()
        if metadata:
            values=json.loads(value)
            if type(values) is not list or len(values)!=6:_deny()
            size,day,captured,policy,version,snapshot=values
            if type(size) is not int or not 0<size<=cap or type(version) is not int or version!=1:_deny()
            _date(day);_time(captured);_digest(policy);_digest(snapshot)
            return dict(size=size,day=day,captured_at=captured,policy=policy,version=version,snapshot=snapshot)
        return value



class MorningDigestCapture:
    def __init__(self,engine,*,enabled=False):
        self._engine=engine;self._enabled=enabled is True
        intel=getattr(engine,'intelligence',None);store=getattr(engine,'s',None)
        self._host=(engine,store,getattr(store,'db',None),getattr(engine,'c',None),getattr(engine,'b',None),intel,getattr(intel,'index',None),getattr(engine,'now',None),getattr(engine,'tz',None))
        self._last_clock=None

    def _policy(self):
        e,s,db,c,b,intel,index,clock,tz=self._host
        if self._engine is not e or type(e) is not Engine or e.s is not s or type(s) is not Store or s.db is not db or e.c is not c or type(c) is not dict or e.b!=b or e.intelligence is not intel or type(intel) is not Intelligence or intel.s is not s or intel.c is not c or intel.b!=b or intel.index is not index or type(index) is not KnowledgeIndex or index.store is not s or e.now is not clock or e.tz is not tz or type(tz) is not ZoneInfo or tz.key!=c.get('timezone'):_deny()
        validate_team(c)
        if not intelligence_enabled(c):_deny()
        if type(c.get('admins')) is not list or len(c['admins'])!=1 or c.get('business')!=b:_deny()
        admin=c['admins'][0]
        fields=('business','admins','employees','timezone','team_intake','team_roster_verified','business_account_user_id','allowed_live_recipients','live_send','recipients_verified','review_mode','sheets_write','daily_summary','allow_onboarding_replies','allow_registered_replies','allowed_group_ids','group_destinations','meeting_recipient','bot_user_id_verified','bot_user_id','intelligence')
        policy=_json({name:c.get(name) for name in fields})
        if len(policy.encode())>65536:_deny()
        return _hash(policy),admin,'morning-digest-capture:v1:'+_hash([b,admin])

    def _check(self,operation=None):
        policy,admin,key=self._policy();now=_time(self._host[7]());self._policy()
        if self._last_clock is not None and now<self._last_clock:_deny()
        self._last_clock=now;local=datetime.fromtimestamp(now,timezone.utc).astimezone(self._host[8]);day=local.date().isoformat()
        if operation is not None and (operation['policy']!=policy or operation['day']!=day or operation['admin']!=admin or now<operation['now']):_deny()
        return dict(policy=policy,admin=admin,key=key,now=now,day=day,local=local)

    def _head(self,raw,op):
        if raw is None:return dict(version=1,scope=_hash([self._host[4],op['admin']]),revision=0,last_write_at=None,days=[])
        head=json.loads(raw,object_pairs_hook=_pairs);_closed(head,{'version','scope','revision','last_write_at','days'})
        if type(head['version']) is not int or head['version']!=1 or head['scope']!=_hash([self._host[4],op['admin']]) or type(head['revision']) is not int or not 1<=head['revision']<2**53 or type(head['days']) is not list or not 1<=len(head['days'])<=32:_deny()
        last=_time(head['last_write_at']);previous=None
        for entry in head['days']:
            _closed(entry,{'day','captured_at','payload_hash','policy','snapshot','refs'})
            day=_date(entry['day']);captured=_time(entry['captured_at'])
            if (previous is not None and day<=previous) or captured>last or datetime.fromtimestamp(captured,timezone.utc).astimezone(self._host[8]).date().isoformat()!=day:_deny()
            for name in ('payload_hash','policy','snapshot'):_digest(entry[name])
            self._refs(entry['refs']);previous=day
        if head['revision']!=len(head['days']) or head['days'][-1]['captured_at']!=last or op['now']<last or head['days'][-1]['day']>op['day']:_deny()
        return head

    def _refs(self,values):
        if type(values) is not list or len(values)>16 or len(set(values))!=len(values):_deny()
        for value in values:_id(value)

    def _validate(self,draft,op):
        if type(draft) is not adapter.OperationalAgendaDraft or draft.business!=self._host[4] or draft.scope!='current_authorized_task_records' or draft.limited is not True or draft.metrics_status!='not_supplied' or draft.limitation!=adapter.LIMITATION or draft.requires_review is not True or draft.live_send_authorized is not False:_deny()
        _digest(draft.snapshot_digest)
        agenda=draft.agenda;digest=draft.digest
        if type(agenda) is not AgendaPreview or type(digest) is not DigestPreview or type(agenda.facts) is not tuple or len(agenda.facts)>5:_deny()
        agenda.__post_init__()
        if type(agenda.as_of) is not datetime or agenda.as_of!=op['local'] or digest.generated_at!=agenda.as_of:_deny()
        slot=digest.scheduled_for
        if type(slot) is not datetime or getattr(slot.tzinfo,'key',None)!=self._host[8].key or slot.date().isoformat()!=op['day'] or (slot.hour,slot.minute,slot.second,slot.microsecond)!=(9,0,0,0):_deny()
        if type(digest.due_today) is not bool or digest.due_today!=(op['local']>=slot) or type(digest.lines) is not tuple or not 1<=len(digest.lines)<=6:_deny()
        for line in digest.lines:
            if type(line) is not str or not line or '\n' in line or '\r' in line:_deny()
        if len('\n'.join(digest.lines).encode('utf-16-le'))//2>3500:_deny()
        if type(digest.sources) is not tuple:_deny()
        self._refs(list(digest.sources))
        for fact in agenda.facts:
            if type(fact) is not AgendaFact:_deny()
            fact.__post_init__()
        return list(digest.sources)

    def _pack(self,draft):
        return dict(business=draft.business,scope=draft.scope,limited=draft.limited,metrics_status=draft.metrics_status,limitation=draft.limitation,snapshot_digest=draft.snapshot_digest,
            agenda=dict(as_of=draft.agenda.as_of.isoformat(),omitted=draft.agenda.omitted,facts=[dict(id=f.id,text=f.text,kind=f.kind,status=f.status,priority=f.priority,sources=list(f.sources)) for f in draft.agenda.facts]),
            digest=dict(scheduled_for=draft.digest.scheduled_for.isoformat(),generated_at=draft.digest.generated_at.isoformat(),due_today=draft.digest.due_today,lines=list(draft.digest.lines),sources=list(draft.digest.sources)))

    def _content_hash(self,draft):
        packed=self._pack(draft);del packed['agenda']['as_of'];del packed['digest']['generated_at']
        return _hash(packed)

    def _snapshot(self,op):
        self._check(op)
        draft=adapter.build_agenda_preview(self._host[1],self._host[3],op['admin'],self._host[6],as_of=op['local'],max_items=5)
        refs=self._validate(draft,op);self._check(op)
        return draft,self._content_hash(draft),refs

    def _payload(self,draft,op):
        return dict(version=1,day=op['day'],captured_at=op['now'],policy=op['policy'],snapshot=self._content_hash(draft),draft=self._pack(draft))

    def _restore(self,payload,entry,op):
        _closed(payload,{'version','day','captured_at','policy','snapshot','draft'})
        if type(payload['version']) is not int or payload['version']!=1 or payload['day']!=entry['day'] or payload['captured_at']!=entry['captured_at'] or payload['policy']!=entry['policy'] or payload['snapshot']!=entry['snapshot']:_deny()
        data=_closed(payload['draft'],{'business','scope','limited','metrics_status','limitation','snapshot_digest','agenda','digest'})
        a=_closed(data['agenda'],{'as_of','omitted','facts'});d=_closed(data['digest'],{'scheduled_for','generated_at','due_today','lines','sources'})
        if type(a['facts']) is not list or len(a['facts'])>5 or type(d['lines']) is not list or type(d['sources']) is not list:_deny()
        facts=[]
        for item in a['facts']:
            _closed(item,{'id','text','kind','status','priority','sources'})
            if type(item['sources']) is not list:_deny()
            facts.append(AgendaFact(item['id'],item['text'],item['kind'],item['status'],item['priority'],tuple(item['sources'])))
        def instant(value):
            if type(value) is not str:_deny()
            result=datetime.fromisoformat(value)
            if result.utcoffset() is None:_deny()
            return result.astimezone(self._host[8])
        agenda=AgendaPreview(instant(a['as_of']),tuple(facts),a['omitted'])
        digest=DigestPreview(instant(d['scheduled_for']),instant(d['generated_at']),d['due_today'],tuple(d['lines']),tuple(d['sources']))
        draft=adapter.OperationalAgendaDraft(data['business'],agenda,digest,data['scope'],data['limited'],data['metrics_status'],data['limitation'],data['snapshot_digest'])
        original=dict(op,now=entry['captured_at'],local=datetime.fromtimestamp(entry['captured_at'],timezone.utc).astimezone(self._host[8]))
        if self._validate(draft,original)!=entry['refs'] or self._content_hash(draft)!=entry['snapshot'] or self._pack(draft)!=data:_deny()
        return draft

    def capture_due(self):
        if not self._enabled:_deny()
        try:
            op=self._check();s=self._host[1]
            if s.db.in_transaction:_deny()
            reader=_Reader(s)
            with s.transaction():
                raw=reader.read(op['key'],MAX_HEAD_BYTES);head=self._head(raw,op);daykey=op['key']+':'+op['day'];size=reader.read(daykey,MAX_PAYLOAD_BYTES,metadata=True)
                old=next((entry for entry in head['days'] if entry['day']==op['day']),None)
                if old is not None:
                    if size is None or size['day']!=old['day'] or size['captured_at']!=old['captured_at'] or size['policy']!=old['policy'] or size['snapshot']!=old['snapshot'] or old['policy']!=op['policy']:_deny()
                    self._check(op)
                    return CaptureReceipt(op['day'],old['captured_at'],old['payload_hash'],True,True)
                if size is not None or len(head['days'])>=32:_deny()
                draft,snapshot,refs=self._snapshot(op)
                if not draft.digest.due_today:
                    self._check(op);return CaptureReceipt(op['day'],None,None,False,False)
                payload=self._payload(draft,op);encoded=_json(payload)
                if len(encoded.encode())>MAX_PAYLOAD_BYTES:_deny()
                entry=dict(day=op['day'],captured_at=op['now'],payload_hash=_hash(payload),policy=op['policy'],snapshot=snapshot,refs=refs)
                updated=dict(head,revision=head['revision']+1,last_write_at=op['now'],days=head['days']+[entry]);newraw=_json(updated)
                if len(newraw.encode())>MAX_HEAD_BYTES:_deny()
                self._check(op)
                if s.execute('INSERT INTO runtime(key,value) SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM runtime WHERE key=?)',(daykey,encoded,daykey)).rowcount!=1:_deny()
                if raw is None:count=s.execute('INSERT INTO runtime(key,value) SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM runtime WHERE key=?)',(op['key'],newraw,op['key'])).rowcount
                else:count=s.execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(newraw,op['key'],raw)).rowcount
                if count!=1:_deny()
                fresh=self._check(op);_,fresh_snapshot,fresh_refs=self._snapshot(fresh)
                if fresh_snapshot!=snapshot or fresh_refs!=refs or reader.read(op['key'],MAX_HEAD_BYTES)!=newraw or reader.read(daykey,MAX_PAYLOAD_BYTES)!=encoded:_deny()
                self._check(fresh)
            return CaptureReceipt(op['day'],op['now'],entry['payload_hash'],False,True)
        except CaptureUnavailable:raise
        except Exception:raise CaptureUnavailable() from None

    def read_current(self):
        if not self._enabled:_deny()
        try:
            op=self._check();s=self._host[1];reader=_Reader(s);raw=reader.read(op['key'],MAX_HEAD_BYTES);head=self._head(raw,op)
            entry=next((entry for entry in head['days'] if entry['day']==op['day']),None)
            if entry is None or entry['policy']!=op['policy']:_deny()
            current,snapshot,refs=self._snapshot(op)
            if snapshot!=entry['snapshot'] or refs!=entry['refs']:_deny()
            self._check(op)
            encoded=reader.read(op['key']+':'+op['day'],MAX_PAYLOAD_BYTES)
            if encoded is None:_deny()
            payload=json.loads(encoded,object_pairs_hook=_pairs)
            if _hash(payload)!=entry['payload_hash']:_deny()
            draft=self._restore(payload,entry,op)
            fresh=self._check(op);_,new_snapshot,new_refs=self._snapshot(fresh)
            if new_snapshot!=snapshot or new_refs!=refs or reader.read(op['key'],MAX_HEAD_BYTES)!=raw or reader.read(op['key']+':'+op['day'],MAX_PAYLOAD_BYTES)!=encoded:_deny()
            self._check(fresh)
            return CapturedDigest(op['day'],entry['captured_at'],draft,entry['payload_hash'])
        except CaptureUnavailable:raise
        except Exception:raise CaptureUnavailable() from None
