"""Default-disabled exact final question -> actual current private task card.

The explicit reader is a host audience assertion, not an authenticated speaker
or permission to disclose to a room. No events, task writes, model or outputs.
Currentness is optimistically sampled, not a sandbox/global transaction claim.
"""
from dataclasses import dataclass,field
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import math

from .engine import Engine
from .store import Store
from .intelligence import Intelligence,intelligence_enabled
from .knowledge import KnowledgeIndex
from .intake import validate_team
from . import task_card_command as cards
from . import voice_meeting_bridge as meeting


class VoiceTaskQuestionUnavailable(ValueError):
    def __init__(self):super().__init__('voice_task_question_unavailable')


def _deny():raise VoiceTaskQuestionUnavailable()


def _callable(value):return getattr(value,'__self__',None),getattr(value,'__func__',value)


def _reference(value):
    if type(value) is not meeting.FinalSegmentRef or type(value.key) is not meeting.voice.SessionKey:_deny()
    for identifier in (value.key.business,value.key.source_id,value.key.session_id,value.segment_id):
        if type(identifier) is not str or not 0<len(identifier)<=200 or identifier!=identifier.strip() or any(ord(c)<32 or ord(c)==127 for c in identifier):_deny()
        identifier.encode('utf-8')
    if type(value.epoch) is not int or not 0<=value.epoch<=1000000 or type(value.revision) is not int or not 1<=value.revision<=1000000:_deny()
    return value.key.business,value.key.source_id,value.key.session_id,value.epoch,value.segment_id,value.revision


@dataclass(frozen=True)
class VoiceTaskAnswer:
    text:str
    intended_reader:str
    question:meeting.SelectedFinalSnapshot
    task_id:str
    task_evidence_ids:tuple[str,...]
    task_snapshot_digest:str
    review_required:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    delivery_authorized:bool=field(default=False,init=False)
    capture_authorized:bool=field(default=False,init=False)
    broadcast_authorized:bool=field(default=False,init=False)


class VoiceTaskQuestion:
    def __init__(self,*,engine,bridge,sender,enabled=False):
        self._engine=engine;self._bridge=bridge;self._sender=sender;self._enabled=enabled is True
        store=getattr(engine,'s',None);intel=getattr(engine,'intelligence',None)
        self._host=(engine,store,getattr(store,'db',None),getattr(engine,'c',None),getattr(engine,'b',None),
            intel,getattr(intel,'index',None),_callable(getattr(engine,'now',None)),getattr(engine,'tz',None),
            bridge,_callable(getattr(bridge,'selected_final',None)),sender,cards.build_task_card,cards.build_task_brief_source)

    def _policy(self):
        e,s,db,c,b,intel,index,clock,tz,bridge,selected,sender,build,metadata=self._host
        if (not self._enabled or self._engine is not e or type(e) is not Engine or type(s) is not Store
                or e.s is not s or s.db is not db or e.c is not c or type(c) is not dict or e.b!=b
                or e.intelligence is not intel or type(intel) is not Intelligence or intel.s is not s or intel.c is not c
                or intel.b!=b or intel.index is not index or type(index) is not KnowledgeIndex or index.store is not s
                or e.tz is not tz or type(tz) is not ZoneInfo or tz.key!=c.get('timezone')
                or self._bridge is not bridge or type(bridge) is not meeting.VoiceMeetingBridge
                or self._sender!=sender or type(sender) is not str or not 0<len(sender)<=128
                or not callable(e.now) or any(a is not b for a,b in zip(clock,_callable(e.now)))
                or any(a is not b for a,b in zip(selected,_callable(bridge.selected_final)))
                or cards.build_task_card is not build or cards.build_task_brief_source is not metadata):_deny()
        validate_team(c)
        if not intelligence_enabled(c) or c.get('business')!=b or type(c.get('admins')) is not list or c['admins']!=[sender]:_deny()
        keys=('business','admins','employees','timezone','team_intake','team_roster_verified','business_account_user_id',
            'allowed_live_recipients','live_send','recipients_verified','review_mode','sheets_write','daily_summary',
            'allow_onboarding_replies','allow_registered_replies','allowed_group_ids','group_destinations','meeting_recipient',
            'bot_user_id_verified','bot_user_id','intelligence')
        policy=json.dumps({key:c.get(key) for key in keys},ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
        if len(policy)>65536:_deny()
        return hashlib.sha256(policy).hexdigest()

    def read_question(self,reference,number):
        if not self._enabled:_deny()
        try:
            if type(number) is not int or not 1<=number<=999999999:_deny()
            shape=_reference(reference);policy=self._policy();stamps=[]
            def check():
                if self._policy()!=policy or _reference(reference)!=shape:_deny()
            def clock():
                check();value=self._engine.now();check()
                if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=2**53-1 or stamps and value<stamps[-1]:_deny()
                stamps.append(value);return value
            def selected():
                now=datetime.fromtimestamp(clock(),timezone.utc)
                result=self._bridge.selected_final(reference,now=now);check()
                if (type(result) is not meeting.SelectedFinalSnapshot or result.reference!=reference or result.key!=reference.key
                        or result.key.business!=self._engine.b or result.reviewer!=self._sender
                        or type(result.token) is not meeting.voice.OutputToken or result.token.key!=result.key or result.token.epoch!=reference.epoch
                        or type(result.text) is not str or not 0<len(result.text)<=128
                        or ' '.join(result.text.casefold().split())!='покажи задачу №'+str(number)):_deny()
                return result
            initial=selected()
            e,s,_,c,_,intel=self._host[:6]
            first=cards.build_task_card(s,c,self._sender,intel.index,clock=clock,number=number)
            check()
            second=cards.build_task_card(s,c,self._sender,intel.index,clock=clock,number=number)
            check()
            if type(first) is not cards.TaskCardPreview or type(second) is not cards.TaskCardPreview or first!=second:_deny()
            if (type(first.text) is not str or len(first.text.encode('utf-16-le'))//2>3500 or len(first.text.encode())>8000
                    or type(first.task_id) is not str or not 0<len(first.task_id)<=160
                    or type(first.evidence_ids) is not tuple or not 1<=len(first.evidence_ids)<=96
                    or any(type(ref) is not str or not 0<len(ref)<=160 for ref in first.evidence_ids)
                    or type(first.snapshot_digest) is not str or len(first.snapshot_digest)!=64):_deny()
            if selected()!=initial:_deny()
            check()
            # The last selected_final invokes external permission callbacks.
            # A same-valued voice permission cannot seal task/ACL mutations
            # made by those callbacks. Re-read actual bounded task metadata
            # after them, without rendering a third card (12 MiB total).
            final_task=cards.build_task_brief_source(s,c,self._sender,intel.index,clock=clock,number=number)
            check()
            if (type(final_task) is not cards.TaskBriefSource or final_task.task_id!=first.task_id
                    or final_task.public_number!=number or final_task.evidence_ids!=first.evidence_ids
                    or final_task.snapshot_digest!=first.snapshot_digest):_deny()
            return VoiceTaskAnswer(first.text,self._sender,initial,first.task_id,first.evidence_ids,first.snapshot_digest)
        except VoiceTaskQuestionUnavailable:raise
        except Exception:raise VoiceTaskQuestionUnavailable() from None
