"""Default-disabled explicit local seventh-calendar-day meeting capture; no send."""
from dataclasses import dataclass,field,asdict,replace
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import json,hashlib,re,math
from .engine import Engine
from .store import Store
from .intelligence import Intelligence
from .knowledge import KnowledgeIndex
from . import weekly_review_command as weekly
MAX_RECORD_BYTES=196608
MAX_BYTES=1048576
BASE_KEYS=('version','code','scope','policy','profile','registration','meeting_on','due_on','start','end','armed_at','last_now','capture')
CAPTURE_KEYS=('captured_at','proof','report_proof','refs','body_hash','payload')
class WeeklyCaptureUnavailable(ValueError):
    def __init__(self):super().__init__('weekly_meeting_capture_unavailable')
def _deny():raise WeeklyCaptureUnavailable()
def _json(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _hash(v):return hashlib.sha256(_json(v).encode()).hexdigest()
def _pairs(pairs):
    d={}
    for k,v in pairs:
        if k in d:_deny()
        d[k]=v
    return d
def _digest(v):
    if type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None:_deny()
    return v
def _time(v):
    if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<2**53:_deny()
    return v
def _day(v):
    if type(v) is not str or len(v)!=10 or date.fromisoformat(v).isoformat()!=v:_deny()
    return date.fromisoformat(v)
def _refs(v):
    if type(v) is not list or len(v)>256 or v!=sorted(set(v)) or any(type(x) is not str or not 0<len(x)<=160 or x.startswith('meeting-access:') for x in v):_deny()
    return v
@dataclass(frozen=True)
class WatchReceipt:
    code:str;due_on:date;registration_seal:str;already_armed:bool
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
@dataclass(frozen=True)
class CaptureReceipt:
    code:str;due_on:date;due:bool;missed:bool;already_captured:bool;captured_at:int|float|None;capture_id:str|None
    payload_verified:bool=field(default=False,init=False)
    capture_only:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
@dataclass(frozen=True)
class CurrentCapturedReview:
    snapshot:weekly.RegisteredWeekSnapshot
    captured_at:int|float
    capture_id:str
    requires_review:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    capture_only:bool=field(default=True,init=False)
class _Reader:
    def __init__(self,store):self.s=store;self.queries=0;self.rows=0;self.bytes=0
    def _get(self,sql,args):
        self.queries+=1
        if self.queries>32:_deny()
        rows=self.s.execute(sql,args).fetchall();self.rows+=len(rows)
        if len(rows)>1 or self.rows>64:_deny()
        if not rows:return None
        value=rows[0][0]
        if type(value) is not str:_deny()
        self.bytes+=len(value.encode())
        if self.bytes>MAX_BYTES:_deny()
        return value
    def metadata(self,key):
        capture="json_object("+','.join("'"+k+"',json_extract(value,'$.capture."+k+"')" for k in CAPTURE_KEYS[:-1])+",'payload_type',json_type(value,'$.capture.payload'))"
        members=','.join("'"+k+"',json_extract(value,'$."+k+"')" for k in BASE_KEYS[:-1])
        basecount="(SELECT count(*) FROM json_each(runtime.value))="+str(len(BASE_KEYS))
        baseclosed="NOT EXISTS(SELECT 1 FROM json_each(runtime.value) WHERE key NOT IN ("+','.join("'"+k+"'" for k in BASE_KEYS)+"))"
        capclosed="(SELECT count(*) FROM json_each(runtime.value,'$.capture'))="+str(len(CAPTURE_KEYS))+" AND NOT EXISTS(SELECT 1 FROM json_each(runtime.value,'$.capture') WHERE key NOT IN ("+','.join("'"+k+"'" for k in CAPTURE_KEYS)+"))"
        condition="json_type(value)='object' AND "+basecount+' AND '+baseclosed+" AND (json_type(value,'$.capture')='null' OR (json_type(value,'$.capture')='object' AND "+capclosed+"))"
        expr="CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN CASE WHEN json_valid(value) THEN CASE WHEN "+condition+" THEN json_object("+members+",'capture',CASE WHEN json_type(value,'$.capture')='object' THEN "+capture+" ELSE NULL END,'size',length(CAST(value AS BLOB))) END END END"
        raw=self._get('SELECT '+expr+' FROM runtime WHERE key=? LIMIT 2',(MAX_RECORD_BYTES,key))
        return None if raw is None else json.loads(raw,object_pairs_hook=_pairs)
    def payload(self,key):
        raw=self._get("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN value END FROM runtime WHERE key=? LIMIT 2",(MAX_RECORD_BYTES,key))
        if raw is None:_deny()
        return raw
class WeeklyMeetingCapture:
    def __init__(self,engine,*,enabled=False):
        self._engine=engine;self._enabled=enabled is True;s=getattr(engine,'s',None);intel=getattr(engine,'intelligence',None)
        self._host=(engine,s,getattr(s,'db',None),getattr(engine,'c',None),getattr(engine,'b',None),intel,getattr(intel,'index',None),getattr(engine,'now',None),getattr(engine,'tz',None),weekly.registered_week_identity,weekly.build_registered_weekly_review)
        self._last_now=None
    def _scope(self,code,payload):
        if not self._enabled:_deny()
        e,s,db,c,b,intel,index,clock,tz,identity,build=self._host
        if (self._engine is not e or type(e) is not Engine or e.s is not s or type(s) is not Store or s.db is not db or e.c is not c or e.b!=b or e.intelligence is not intel or type(intel) is not Intelligence or intel.s is not s or intel.c is not c or intel.b!=b or intel.index is not index or type(index) is not KnowledgeIndex or index.store is not s or e.now is not clock or e.tz is not tz or type(tz) is not ZoneInfo or tz.key!=c.get('timezone') or weekly.registered_week_identity is not identity or weekly.build_registered_weekly_review is not build):_deny()
        host,request,scope,policy,connection,currentclock=weekly._public_scope(e,code,payload)
        return request,scope,policy,'weekly-meeting-capture:v1:'+_hash([b,scope,code])
    def _check(self,code,payload,initial=None):
        request,scope,policy,key=self._scope(code,payload);now=_time(self._host[7]());day=datetime.fromtimestamp(now,timezone.utc).astimezone(self._host[8]).date()
        self._scope(code,payload)
        if self._last_now is not None and now<self._last_now:_deny()
        self._last_now=now
        profile=_json(self._host[3])
        if len(profile.encode())>65536 or self._host[3].get('daily_summary') is not False:_deny()
        current=dict(request=request,scope=_hash(scope),policy=policy,profile=_hash(profile),key=key,now=now,day=day)
        if initial and (any(current[k]!=initial[k] for k in ('request','scope','policy','profile','key','day')) or now<initial['now']):_deny()
        return current
    def _identity(self,code,op):
        value=weekly.registered_week_identity(self._engine,code,dict(op['request']))
        if type(value) is not weekly.RegisteredWeekIdentity or value.code!=code or value.scope_hash!=op['scope'] or value.policy_seal!=op['policy'] or type(value.meeting_on) is not date:_deny()
        _time(value.expires_at);_digest(value.registration_seal);return value
    def _valid(self,doc,code,op,identity):
        if type(doc) is not dict or set(doc)!=set(BASE_KEYS)|{'size'} or type(doc['version']) is not int or doc['version']!=1 or doc['code']!=code or doc['scope']!=op['scope'] or doc['policy']!=op['policy'] or doc['profile']!=op['profile'] or doc['registration']!=identity.registration_seal:_deny()
        meeting=_day(doc['meeting_on']);due=_day(doc['due_on'])
        if meeting!=identity.meeting_on or due!=meeting+timedelta(days=7) or _day(doc['start'])!=meeting or _day(doc['end'])!=due or not 0<doc['size']<=MAX_RECORD_BYTES:_deny()
        if _time(doc['armed_at'])>_time(doc['last_now']) or op['now']<doc['last_now']:_deny()
        capture=doc['capture']
        if capture is not None:
            if type(capture) is not dict or set(capture)!=set(CAPTURE_KEYS[:-1])|{'payload_type'} or capture['payload_type']!='object' or _time(capture['captured_at'])<doc['armed_at'] or capture['captured_at']!=doc['last_now'] or datetime.fromtimestamp(capture['captured_at'],timezone.utc).astimezone(self._host[8]).date()!=due:_deny()
            for k in ('proof','report_proof','body_hash'):_digest(capture[k])
            _refs(capture['refs'])
        return doc
    def _snapshot(self,code,op,identity=None,*,start=None):
        on=identity.meeting_on if identity is not None else start
        value=weekly.build_registered_weekly_review(self._engine,code,dict(op['request']),start=on,end=on+timedelta(days=7))
        if identity is None:identity=value.identity
        if identity.code!=code or identity.scope_hash!=op['scope'] or identity.policy_seal!=op['policy']:_deny()
        if type(value) is not weekly.RegisteredWeekSnapshot or value.identity!=identity or value.start!=identity.meeting_on or value.end!=identity.meeting_on+timedelta(days=7) or type(value.pages) is not tuple or not 1<=len(value.pages)<=8 or value.requires_review is not True or value.live_authorized is not False:_deny()
        _digest(value.proof);_digest(value.report_proof);_refs(list(value.refs))
        if sum(weekly.render._units(p) for p in value.pages)>24000 or any(weekly.render._units(p)>3500 for p in value.pages):_deny()
        return value
    def _same(self,a,b):
        if (a.identity,a.start,a.end,a.proof,a.report_proof,a.refs)!=(b.identity,b.start,b.end,b.proof,b.report_proof,b.refs):_deny()
    def _receipt(self,doc,op,*,already=False):
        cap=doc['capture'];due=_day(doc['due_on']);identifier=None if cap is None else _hash([op['key'],cap['body_hash'],cap['captured_at']])
        return CaptureReceipt(doc['code'],due,op['day']==due,cap is None and op['day']>due,already,None if cap is None else cap['captured_at'],identifier)
    def arm(self,code,*,scope_payload):
        try:
            op=self._check(code,scope_payload);identity=self._identity(code,op);self._check(code,scope_payload,op);due=identity.meeting_on+timedelta(days=7)
            if not identity.meeting_on<=op['day']<due or identity.expires_at<=datetime(due.year,due.month,due.day,tzinfo=self._host[8]).timestamp():_deny()
            reader=_Reader(self._host[1]);initial=reader.metadata(op['key'])
            if initial is not None:
                self._valid(initial,code,op,identity);return WatchReceipt(code,due,identity.registration_seal,True)
            doc=dict(version=1,code=code,scope=op['scope'],policy=op['policy'],profile=op['profile'],registration=identity.registration_seal,meeting_on=identity.meeting_on.isoformat(),due_on=due.isoformat(),start=identity.meeting_on.isoformat(),end=due.isoformat(),armed_at=op['now'],last_now=op['now'],capture=None);encoded=_json(doc)
            with self._host[1].transaction():
                self._check(code,scope_payload,op)
                if reader.metadata(op['key']) is not None:_deny()
                if not self._host[1].execute('INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING',(op['key'],encoded)).rowcount:_deny()
                if reader.payload(op['key'])!=encoded or self._identity(code,op)!=identity:_deny()
                self._check(code,scope_payload,op)
            return WatchReceipt(code,due,identity.registration_seal,False)
        except WeeklyCaptureUnavailable:raise
        except Exception:raise WeeklyCaptureUnavailable() from None
    def capture_due(self,code,*,scope_payload):
        try:
            op=self._check(code,scope_payload);reader=_Reader(self._host[1]);doc=reader.metadata(op['key'])
            if type(doc) is not dict:_deny()
            if doc['capture'] is not None or op['day']!=_day(doc['due_on']):
                identity=self._identity(code,op);self._check(code,scope_payload,op);self._valid(doc,code,op,identity)
                return self._receipt(doc,op,already=doc['capture'] is not None)
            first=self._snapshot(code,op,start=_day(doc['start']));identity=first.identity;self._check(code,scope_payload,op);self._valid(doc,code,op,identity)
            payload=dict(identity=asdict(identity),start=first.start.isoformat(),end=first.end.isoformat(),draft=weekly._typed(first.draft),pages=list(first.pages),refs=list(first.refs),proof=first.proof,report_proof=first.report_proof,as_of=first.as_of)
            # Dates in identity are encoded canonically, no raw registration/source bodies added.
            payload['identity']['meeting_on']=identity.meeting_on.isoformat()
            value={k:doc[k] for k in BASE_KEYS};value['last_now']=op['now'];value['capture']=dict(captured_at=op['now'],proof=first.proof,report_proof=first.report_proof,refs=list(first.refs),body_hash=_hash(payload),payload=payload);encoded=_json(value)
            if len(encoded.encode())>MAX_RECORD_BYTES:_deny()
            before=_json({k:doc[k] for k in BASE_KEYS})
            with self._host[1].transaction():
                self._check(code,scope_payload,op)
                if reader.metadata(op['key'])!=doc:_deny()
                if not self._host[1].execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(encoded,op['key'],before)).rowcount:_deny()
                if reader.payload(op['key'])!=encoded:_deny()
                fresh=self._snapshot(code,op,identity);self._same(first,fresh);self._check(code,scope_payload,op)
            saved=dict(value,size=len(encoded.encode()));saved['capture']=dict(value['capture']);saved['capture'].pop('payload');saved['capture']['payload_type']='object'
            return self._receipt(saved,op)
        except WeeklyCaptureUnavailable:raise
        except Exception:raise WeeklyCaptureUnavailable() from None
    def read_current(self,code,*,scope_payload):
        try:
            op=self._check(code,scope_payload);reader=_Reader(self._host[1]);doc=reader.metadata(op['key'])
            if type(doc) is not dict or doc['capture'] is None:_deny()
            first=self._snapshot(code,op,start=_day(doc['start']));identity=first.identity;self._check(code,scope_payload,op);self._valid(doc,code,op,identity);cap=doc['capture']
            if (first.proof,first.report_proof,list(first.refs))!=(cap['proof'],cap['report_proof'],cap['refs']):_deny()
            raw=reader.payload(op['key']);saved=json.loads(raw,object_pairs_hook=_pairs)
            if set(saved)!=set(BASE_KEYS) or type(saved['capture']) is not dict or set(saved['capture'])!=set(CAPTURE_KEYS) or len(raw.encode())!=doc['size']:_deny()
            payload=saved['capture']['payload']
            if type(payload) is not dict or set(payload)!={'identity','start','end','draft','pages','refs','proof','report_proof','as_of'} or _hash(payload)!=cap['body_hash'] or saved['capture']['body_hash']!=cap['body_hash']:_deny()
            if payload['identity']!=dict(asdict(identity),meeting_on=identity.meeting_on.isoformat()) or payload['start']!=doc['start'] or payload['end']!=doc['end'] or payload['refs']!=cap['refs'] or payload['proof']!=cap['proof'] or payload['report_proof']!=cap['report_proof'] or type(payload['draft']) is not dict or type(payload['pages']) is not list or not 1<=len(payload['pages'])<=8 or any(type(p) is not str or weekly.render._units(p)>3500 for p in payload['pages']) or sum(weekly.render._units(p) for p in payload['pages'])>24000:_deny()
            observed=_time(payload['as_of'])
            if observed<cap['captured_at'] or observed>op['now']:_deny()
            expected_draft=replace(first.draft,as_of=observed)
            if payload['draft']!=weekly._typed(expected_draft):_deny()
            expected_pages=weekly.render.render_weekly_draft(expected_draft)
            if first.pages[0].startswith(weekly.ATTRIBUTION+'\n\n'):expected_pages=[weekly.ATTRIBUTION+'\n\n'+p for p in expected_pages]
            if payload['pages']!=expected_pages:_deny()
            if reader.metadata(op['key'])!=doc:_deny()
            fresh=self._snapshot(code,op,identity);self._same(first,fresh);self._check(code,scope_payload,op)
            return CurrentCapturedReview(fresh,cap['captured_at'],_hash([op['key'],cap['body_hash'],cap['captured_at']]))
        except WeeklyCaptureUnavailable:raise
        except Exception:raise WeeklyCaptureUnavailable() from None
