"""Default-off selected knowledge pack for one explicit synthetic final question.

Private host audience is not speaker/room authentication. No refresh, provider,
model, event, source grant, write or delivery. Currentness is sampled, not a
global transaction/callback sandbox or guarantee after returning to the host.
"""
from dataclasses import dataclass,field
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import hashlib,json,math,re

from .engine import Engine
from .store import Store
from .intelligence import Intelligence,intelligence_enabled,reader_key
from .knowledge import KnowledgeIndex,SelectedDocument,SelectedKnowledgeSnapshot
from .knowledge_sources import ReadOnlySources
from .trusted_source_bridge import TrustedSourceBridge
from .trusted_provider import TrustedProviders,SourceSpec
from .intake import validate_team
from . import voice_meeting_bridge as meeting


class VoiceKnowledgeUnavailable(ValueError):
    def __init__(self):super().__init__('voice_knowledge_unavailable')


def _deny():raise VoiceKnowledgeUnavailable()


def _callable(value):return getattr(value,'__self__',None),getattr(value,'__func__',value)


def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _documents(values):
    if type(values) is not tuple or not 1<=len(values)<=3 or any(type(item) is not SelectedDocument for item in values):_deny()
    result=[]
    for item in values:
        if any(type(v) is not str or not 0<len(v)<=256 or len(v.encode())>1024 for v in (item.source_id,item.document_id,item.document_key)):_deny()
        result.append([item.source_id,item.document_id,item.document_key])
    if len({row[2] for row in result})!=len(result):_deny()
    return result


def _reference(value):
    if type(value) is not meeting.FinalSegmentRef or type(value.key) is not meeting.voice.SessionKey:_deny()
    values=(value.key.business,value.key.source_id,value.key.session_id,value.segment_id)
    if any(type(v) is not str or not 0<len(v)<=200 or v!=v.strip() or any(ord(c)<32 or ord(c)==127 for c in v) for v in values):_deny()
    if type(value.epoch) is not int or not 0<=value.epoch<=1000000 or type(value.revision) is not int or not 1<=value.revision<=1000000:_deny()
    return list(values)+[value.epoch,value.revision]


@dataclass(frozen=True)
class VoiceKnowledgeAnswer:
    intended_reader:str
    question:meeting.SelectedFinalSnapshot
    knowledge:SelectedKnowledgeSnapshot
    review_required:bool=field(default=True,init=False)
    delivery_authorized:bool=field(default=False,init=False)
    live_authorized:bool=field(default=False,init=False)
    capture_authorized:bool=field(default=False,init=False)
    broadcast_authorized:bool=field(default=False,init=False)


class VoiceKnowledgeQuestion:
    def __init__(self,*,engine,bridge,sender,documents,enabled=False):
        self._engine=engine;self._bridge=bridge;self._sender=sender;self._documents=documents;self._enabled=enabled is True
        store=getattr(engine,'s',None);intel=getattr(engine,'intelligence',None);index=getattr(intel,'index',None)
        sources=getattr(intel,'sources',None);trusted=getattr(sources,'trusted_bridge',None)
        self._host=(engine,store,getattr(store,'db',None),getattr(engine,'c',None),getattr(engine,'b',None),intel,index,
            sources,trusted,getattr(trusted,'registry',None),_callable(getattr(engine,'now',None)),
            _callable(getattr(index,'search_selected',None)),bridge,_callable(getattr(bridge,'selected_final',None)),
            _callable(getattr(trusted,'source_id',None)),sender)

    def _state(self):
        e,s,db,c,b,intel,index,sources,trusted,registry,clock,search,bridge,selected,source_method,sender=self._host
        if (not self._enabled or self._engine is not e or type(e) is not Engine or type(s) is not Store or e.s is not s
                or s.db is not db or e.c is not c or type(c) is not dict or e.b!=b or e.intelligence is not intel
                or type(intel) is not Intelligence or intel.s is not s or intel.c is not c or intel.b!=b
                or intel.index is not index or type(index) is not KnowledgeIndex or index.store is not s
                or intel.sources is not sources or type(sources) is not ReadOnlySources or sources.index is not index
                or sources.trusted_bridge is not trusted or self._bridge is not bridge or type(bridge) is not meeting.VoiceMeetingBridge
                or self._sender!=sender or type(sender) is not str or not 0<len(sender)<=128
                or type(e.tz) is not ZoneInfo or e.tz.key!=c.get('timezone')):_deny()
        for saved,current in ((clock,_callable(e.now)),(search,_callable(index.search_selected)),(selected,_callable(bridge.selected_final))):
            if any(a is not b for a,b in zip(saved,current)):_deny()
        validate_team(c)
        if c.get('business')!=b or not intelligence_enabled(c) or c.get('admins')!=[sender]:_deny()
        reader=reader_key(b,sender)
        if type(sources.members) is not set or not 1<=len(sources.members)<=50 or any(type(v) is not str or len(v)>128 for v in sources.members) or reader not in sources.members:_deny()
        if type(sources.specs) is not list or len(sources.specs)>16 or any(type(spec) is not dict for spec in sources.specs):_deny()
        ids=[spec.get('id') for spec in sources.specs]
        if any(type(v) is not str or not 0<len(v)<=256 for v in ids) or len(set(ids))!=len(ids):_deny()
        trusted_state=None
        if trusted is not None:
            if (type(trusted) is not TrustedSourceBridge or trusted.index is not index or trusted.business!=b
                    or trusted.registry is not registry or type(registry) is not TrustedProviders or registry.business!=b
                    or type(trusted.bindings) is not tuple or len(trusted.bindings)>16 or trusted.members!=sources.members
                    or any(a is not b for a,b in zip(source_method,_callable(trusted.source_id)))
                    or source_method[1] is not TrustedSourceBridge.source_id):_deny()
            specs=[]
            for spec in trusted.bindings:
                if (type(spec) is not SourceSpec or type(spec.audience) is not frozenset or not spec.audience<=sources.members
                        or any(type(v) is not str or re.fullmatch('[A-Za-z0-9._-]{1,64}',v) is None for v in (spec.provider_id,spec.source_id))
                        or type(spec.minimum_version) is not int or not 1<=spec.minimum_version<2**63
                        or type(spec.ttl_seconds) is not int or not 1<=spec.ttl_seconds<=86400 or type(spec.confirmed) is not bool):_deny()
                specs.append([spec.provider_id,spec.source_id,sorted(spec.audience),spec.minimum_version,spec.ttl_seconds,spec.confirmed])
            trusted_state=[trusted.business,registry.business,sorted(trusted.members),specs]
        keys=('business','admins','employees','timezone','team_intake','team_roster_verified','business_account_user_id',
            'allowed_live_recipients','live_send','recipients_verified','review_mode','sheets_write','daily_summary',
            'allow_onboarding_replies','allow_registered_replies','allowed_group_ids','group_destinations','meeting_recipient',
            'bot_user_id_verified','bot_user_id','intelligence')
        state=[{key:c.get(key) for key in keys},sources.specs,sorted(sources.members),trusted_state,_documents(self._documents)]
        encoded=json.dumps(state,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
        if len(encoded)>65536:_deny()
        return hashlib.sha256(encoded).hexdigest()

    def _ownership(self):
        # Call the existing public canonical source mapper before data reads;
        # final checks compare metadata/method identity without another call.
        _,_,_,_,b,_,_,sources,trusted,_,_,_,_,_,_,sender=self._host
        reader=reader_key(b,sender)
        for item in self._documents:
            local=[spec for spec in sources.specs if spec.get('id')==item.source_id
                and type(spec.get('readers')) is list and reader in spec['readers'] and set(spec['readers'])<=sources.members]
            remote=[] if trusted is None else [spec for spec in trusted.bindings
                if trusted.source_id(spec)==item.source_id and reader in spec.audience]
            if len(local)+len(remote)!=1:_deny()

    def read_question(self,reference,query):
        if not self._enabled:_deny()
        try:
            if type(query) is not str or not query.strip() or len(query)>256 or len(query.encode())>1024:_deny()
            initial_ref=_reference(reference);initial_documents=_documents(self._documents);normalized=' '.join(query.casefold().split())
            initial=self._state();self._ownership()
            if self._state()!=initial:_deny()
            stamps=[]
            def check():
                if self._state()!=initial or _reference(reference)!=initial_ref or _documents(self._documents)!=initial_documents:_deny()
            def sample():
                check();value=self._engine.now();check()
                if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=2**53-1 or stamps and value<stamps[-1]:_deny()
                stamps.append(value);return value
            def question():
                result=self._bridge.selected_final(reference,now=datetime.fromtimestamp(sample(),timezone.utc));check()
                if (type(result) is not meeting.SelectedFinalSnapshot or result.reference!=reference or result.key!=reference.key
                        or result.key.business!=self._engine.b or result.reviewer!=self._sender
                        or type(result.text) is not str or len(result.text)>2000
                        or ' '.join(result.text.casefold().split())!='найди в выбранных источниках: '+normalized):_deny()
                return result
            source=question();index=self._host[6];reader=reader_key(self._engine.b,self._sender)
            first=index.search_selected(query,reader,now=sample(),documents=self._documents)
            check()
            if question()!=source:_deny()
            # Last external clock sample is before the final SQL phase. There
            # are no later voice/source/provider/clock calls or automatic output.
            now=sample()
            final=index.search_selected(query,reader,now=now,documents=self._documents)
            check()
            if type(first) is not SelectedKnowledgeSnapshot or type(final) is not SelectedKnowledgeSnapshot or first!=final:_deny()
            return VoiceKnowledgeAnswer(self._sender,source,final)
        except VoiceKnowledgeUnavailable:raise
        except Exception:raise VoiceKnowledgeUnavailable() from None
