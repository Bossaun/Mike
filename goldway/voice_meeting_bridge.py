"""Disabled-by-default synthetic text frames to explicit literal meeting reviews.

A host review permit is not real capture/participant consent. This bridge has no
provider, audio, storage, task creation or transport integration.
"""
from dataclasses import dataclass,field
from datetime import date,datetime,timezone
import hashlib
import json

from . import voice_contract as voice
from . import meeting_review as meeting
from . import meeting_review_render as renderer


class VoiceBridgeUnavailable(ValueError):
    def __init__(self):super().__init__('voice_meeting_bridge_unavailable')


def _deny():raise VoiceBridgeUnavailable()


def _utc(value):
    if type(value) is not datetime or value.utcoffset() is None:_deny()
    return value.astimezone(timezone.utc)


def _id(value):
    if type(value) is not str or not 0<len(value)<=200 or value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    value.encode('utf-8')


def _text(value):
    if type(value) is not str or '\r' in value or any(ord(c)<32 and c!='\n' or ord(c)==127 for c in value):_deny()
    value.encode('utf-8')


def _units(value):return len(value.encode('utf-16-le'))//2


def _callable(value):return getattr(value,'__self__',None),getattr(value,'__func__',value)


@dataclass(frozen=True)
class TextReviewPermission:
    key:voice.SessionKey
    reviewer:str
    revision:int
    issued_at:datetime
    expires_at:datetime
    allow_text_review:bool
    revoked:bool=False


@dataclass(frozen=True)
class FinalSegmentRef:
    key:voice.SessionKey
    epoch:int
    segment_id:str
    revision:int


@dataclass(frozen=True)
class SelectedSpan:
    source:FinalSegmentRef
    first:int
    last:int
    quote:str


@dataclass(frozen=True)
class DecisionSelection:
    value:str
    confirmed_decision:bool
    spans:tuple[SelectedSpan,...]


@dataclass(frozen=True)
class ReviewSelection:
    sources:tuple[FinalSegmentRef,...]
    agreements:tuple[DecisionSelection,...]


@dataclass(frozen=True,eq=False)
class ReviewTicket:
    """Identity-only capability for this one in-memory pending bundle; no body."""


@dataclass(frozen=True)
class VoiceReviewBundle:
    review:meeting.MeetingReview
    pages:tuple[str,...]
    on:date
    reviewer:str
    review_required:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)
    capture_authorized:bool=field(default=False,init=False)
    limitation:str=field(default='Только явно выбранные условные текстовые кадры. Метка разрешения хоста не подтверждает запись или согласие реальных участников.',init=False)


@dataclass(frozen=True)
class VoiceAdmissionProof:
    """Sampled current input metadata, not capture consent or a frame grant."""
    key:voice.SessionKey
    token:voice.OutputToken
    permission_fingerprint:str
    started_at:datetime
    expires_at:datetime
    last_at:datetime
    accept_after:datetime
    record_count:int


@dataclass(frozen=True)
class SelectedFinalSnapshot:
    """One selected literal source, not speaker or audience authentication."""
    key:voice.SessionKey
    reference:FinalSegmentRef
    text:str
    evidence_ids:tuple[str,...]
    token:voice.OutputToken
    permission_fingerprint:str
    reviewer:str


@dataclass(frozen=True)
class _Pending:
    ticket:ReviewTicket
    token:voice.OutputToken
    permission:TextReviewPermission
    selection:ReviewSelection
    on:date
    mapping:tuple
    bundle:VoiceReviewBundle
    state:voice.SessionState


class VoiceMeetingBridge:
    def __init__(self,spec,*,current_permission,sink,enabled=False):
        self._spec=spec;self._permission_callback=current_permission;self._sink=sink
        self._enabled=enabled is True;self._state=None;self._pending=None;self._exhausted=False
        self._host=(spec,_callable(current_permission),_callable(sink))

    def selected_final(self,reference,*,now):
        """Read exactly one current-epoch final; no record/ticket mutation."""
        if not self._enabled:_deny()
        try:
            self._check_host();now=_utc(now)
            previous,pending,spec=self._state,self._pending,self._spec
            if type(reference) is not FinalSegmentRef or type(reference.key) is not voice.SessionKey:_deny()
            for value in (reference.key.business,reference.key.source_id,reference.key.session_id,reference.segment_id):_id(value)
            if type(reference.epoch) is not int or not 0<=reference.epoch<=1000000 or type(reference.revision) is not int or not 1<=reference.revision<=1000000:_deny()
            ref_shape=(reference.key.business,reference.key.source_id,reference.key.session_id,reference.epoch,reference.segment_id,reference.revision)
            permission=self._permission(now)
            if self._state is not previous or self._pending is not pending or previous is None:_deny()
            token=voice.output_token(previous,now=now)
            if reference.key!=spec.key or reference.epoch!=token.epoch:_deny()
            preview=voice.meeting_preview(previous,token,now=now)
            if type(preview) is not voice.MeetingPreview or preview.key!=spec.key or preview.epoch!=token.epoch or preview.sequence!=token.sequence:_deny()
            selected=[line for line in preview.lines if (line.epoch,line.segment_id,line.revision)==(reference.epoch,reference.segment_id,reference.revision)]
            if len(selected)!=1 or type(selected[0]) is not voice.PreviewLine:_deny()
            line=selected[0];_text(line.text)
            if len(line.text)>2000 or type(line.evidence_ids) is not tuple or not 1<=len(line.evidence_ids)<=2:_deny()
            for identifier in line.evidence_ids:_id(identifier)
            self._check_host()
            if self._permission(now)!=permission or self._state is not previous or self._pending is not pending or self._spec is not spec:_deny()
            if (reference.key.business,reference.key.source_id,reference.key.session_id,reference.epoch,reference.segment_id,reference.revision)!=ref_shape:_deny()
            voice.validate_output(previous,token,now=now)
            value=[permission.key.business,permission.key.source_id,permission.key.session_id,
                permission.reviewer,permission.revision,_utc(permission.issued_at).isoformat(),
                _utc(permission.expires_at).isoformat(),permission.allow_text_review,permission.revoked]
            fingerprint=hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            return SelectedFinalSnapshot(spec.key,reference,line.text,line.evidence_ids,token,fingerprint,permission.reviewer)
        except VoiceBridgeUnavailable:raise
        except Exception:raise VoiceBridgeUnavailable() from None

    def admission_proof(self,*,now):
        """No record/pending mutation; callbacks are optimistically rechecked.

        A lazy empty session is projected without saving it. The proof carries
        neither transcript nor an authorization to capture real audio.
        """
        if not self._enabled:_deny()
        try:
            self._check_host();now=_utc(now)
            previous,pending,spec=self._state,self._pending,self._spec
            permission=self._permission(now)
            if self._state is not previous or self._pending is not pending:_deny()
            state=previous if previous is not None else voice.start_session(spec)
            token=voice.output_token(state,now=now)
            self._check_host()
            if self._permission(now)!=permission or self._state is not previous or self._pending is not pending or self._spec is not spec:_deny()
            voice.validate_output(state,token,now=now)
            value=[permission.key.business,permission.key.source_id,permission.key.session_id,
                permission.reviewer,permission.revision,_utc(permission.issued_at).isoformat(),
                _utc(permission.expires_at).isoformat(),permission.allow_text_review,permission.revoked]
            fingerprint=hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            return VoiceAdmissionProof(spec.key,token,fingerprint,spec.started_at,spec.expires_at,
                state.last_at,state.accept_after,len(state.records))
        except VoiceBridgeUnavailable:raise
        except Exception:raise VoiceBridgeUnavailable() from None

    def _check_host(self):
        spec,permission,sink=self._host
        if self._spec is not spec or any(a is not b for a,b in zip(permission,_callable(self._permission_callback))) or any(a is not b for a,b in zip(sink,_callable(self._sink))):_deny()
        if type(spec) is not voice.SessionSpec or type(spec.key) is not voice.SessionKey or spec.key.business!='GOLDWAY' or spec.capture_authorized is not False:_deny()
        for value in (spec.key.business,spec.key.source_id,spec.key.session_id):_id(value)
        if _utc(spec.started_at)>=_utc(spec.expires_at):_deny()
        if self._state is not None and (type(self._state) is not voice.SessionState or self._state.spec is not spec or self._state.spec.capture_authorized is not False):_deny()
        if self._exhausted:_deny()

    def _ensure(self):
        self._check_host()
        if self._state is None:self._state=voice.start_session(self._spec)
        self._check_host()

    def _permission(self,now):
        self._check_host();permission=self._permission_callback()
        if type(permission) is not TextReviewPermission or permission.key!=self._spec.key:_deny()
        _id(permission.reviewer)
        if type(permission.revision) is not int or not 1<=permission.revision<2**53 or type(permission.allow_text_review) is not bool or type(permission.revoked) is not bool:_deny()
        if permission.revoked or not permission.allow_text_review or not _utc(permission.issued_at)<=now<_utc(permission.expires_at):_deny()
        self._check_host()
        return permission

    def accept(self,record):
        if not self._enabled:_deny()
        try:
            self._ensure()
            if type(record) not in (voice.Control,voice.Segment,voice.Speaker):_deny()
            negative=type(record) is voice.Control and record.kind in ('halt','interrupt','stop')
            if negative:self._pending=None
            for value in (record.id,record.evidence_id):_id(value)
            now=_utc(record.at)
            permission=None if negative else self._permission(now)
            if type(record) is voice.Segment:
                _id(record.segment_id);_text(record.text)
                if record.speaker_label is not None:_id(record.speaker_label)
            elif type(record) is voice.Speaker:
                _id(record.label)
                if record.participant_id is not None:_id(record.participant_id)
            previous=self._state
            updated=voice.apply(previous,record)
            if type(updated) is not voice.SessionState or updated.spec is not self._spec:_deny()
            if len(updated.records)>128 or sum(len(row.text) for row in updated.records if type(row) is voice.Segment)>10000:
                self._pending=None;self._exhausted=True;_deny()
            self._check_host()
            if not negative and self._permission(now)!=permission:_deny()
            if self._state is not previous:_deny()
            if updated is not previous:self._pending=None
            self._state=updated
        except VoiceBridgeUnavailable:
            self._pending=None;raise
        except Exception:
            self._pending=None;raise VoiceBridgeUnavailable() from None

    def _selected(self,selection,preview,on):
        if type(selection) is not ReviewSelection or type(selection.sources) is not tuple or not 1<=len(selection.sources)<=5 or type(selection.agreements) is not tuple or len(selection.agreements)>3:_deny()
        if len(set(selection.sources))!=len(selection.sources):_deny()
        current={(row.epoch,row.segment_id,row.revision):row for row in preview.lines}
        sources=[];mapping=[];by_ref={}
        for ordinal,ref in enumerate(selection.sources,1):
            if type(ref) is not FinalSegmentRef or ref.key!=self._spec.key or type(ref.epoch) is not int or type(ref.revision) is not int or ref.epoch<0 or ref.revision<1:_deny()
            _id(ref.segment_id)
            line=current.get((ref.epoch,ref.segment_id,ref.revision))
            if line is None:_deny()
            _text(line.text)
            source=meeting.Source('Фрагмент'+str(ordinal),'GOLDWAY',on,tuple(line.text.split('\n')))
            sources.append(source);by_ref[ref]=source
            mapping.append((ref,line.evidence_ids,line.start,line.end))
        if sum(len('\n'.join(source.lines)) for source in sources)>10000:_deny()
        agreements=[]
        if len(set(selection.agreements))!=len(selection.agreements):_deny()
        for ordinal,decision in enumerate(selection.agreements,1):
            if type(decision) is not DecisionSelection or type(decision.confirmed_decision) is not bool or type(decision.spans) is not tuple or not 1<=len(decision.spans)<=3:_deny()
            _text(decision.value)
            if not decision.value.strip() or len(decision.value)>300:_deny()
            spans=[]
            for selected in decision.spans:
                if type(selected) is not SelectedSpan or type(selected.source) is not FinalSegmentRef or selected.source not in by_ref:_deny()
                source=by_ref[selected.source]
                if type(selected.first) is not int or type(selected.last) is not int or not 1<=selected.first<=selected.last<=len(source.lines):_deny()
                quote='\n'.join(source.lines[selected.first-1:selected.last])
                if type(selected.quote) is not str or selected.quote!=quote or decision.value!=quote:_deny()
                spans.append(meeting.Span(source.id,selected.first,selected.last,selected.quote))
            agreements.append(meeting.Agreement('Решение'+str(ordinal),'Решение'+str(ordinal),decision.value,tuple(spans),decision.confirmed_decision,None,None))
        return tuple(sources),tuple(agreements),tuple(mapping)

    def prepare_review(self,selection,*,on,now):
        if not self._enabled:_deny()
        self._pending=None
        try:
            self._ensure();now=_utc(now)
            if type(on) is not date:_deny()
            permission=self._permission(now);state=self._state
            token=voice.output_token(state,now=now)
            preview=voice.meeting_preview(state,token,now=now)
            if type(preview) is not voice.MeetingPreview or preview.key!=self._spec.key or preview.epoch!=token.epoch or preview.sequence!=token.sequence:_deny()
            sources,agreements,mapping=self._selected(selection,preview,on)
            identifier='voice-review:'+hashlib.sha256(json.dumps([self._spec.key.business,self._spec.key.source_id,self._spec.key.session_id,token.epoch,token.sequence,on.isoformat()],ensure_ascii=False).encode()).hexdigest()
            review=meeting.review_meeting(meeting.Meeting(identifier,'GOLDWAY',on,sources,agreements))
            pages=renderer.render_meeting_review(review,{})
            if type(pages) not in (list,tuple) or not 1<=len(pages)<=6 or any(type(page) is not str or _units(page)>3500 for page in pages) or sum(_units(page) for page in pages)>24000:_deny()
            if self._state is not state or self._permission(now)!=permission:_deny()
            voice.validate_output(self._state,token,now=now);self._check_host()
            ticket=ReviewTicket();bundle=VoiceReviewBundle(review,tuple(pages),on,permission.reviewer)
            self._pending=_Pending(ticket,token,permission,selection,on,mapping,bundle,state)
            return ticket
        except VoiceBridgeUnavailable:raise
        except Exception:raise VoiceBridgeUnavailable() from None

    def deliver_review(self,ticket,*,now):
        if not self._enabled:_deny()
        pending=self._pending
        owned=pending is not None and type(ticket) is ReviewTicket and ticket is pending.ticket
        try:
            if not owned:_deny()
            self._ensure();now=_utc(now)
            if self._permission(now)!=pending.permission or self._state is not pending.state or self._pending is not pending:_deny()
            voice.validate_output(self._state,pending.token,now=now)
            self._check_host()
            if self._pending is not pending or self._state is not pending.state:_deny()
            self._pending=None  # One use even if the local callback fails.
            self._sink(pending.bundle)
        except VoiceBridgeUnavailable:
            if owned and self._pending is pending:self._pending=None
            raise
        except Exception:
            if owned and self._pending is pending:self._pending=None
            raise VoiceBridgeUnavailable() from None
