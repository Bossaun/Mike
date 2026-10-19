"""Disabled synthetic WAV input port, not an STT/capture implementation.

Only local fake callbacks are in scope. No audio is retained by the adapter;
an arbitrary callback can retain its argument or block. Checks sample current
state optimistically, not atomically against arbitrary host code or wall time.
An ambiguous accept outcome is UNKNOWN, never a claim of rollback/unadmission.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
from io import BytesIO
import json
import wave

from . import voice_contract as voice
from . import voice_meeting_bridge as meeting

MAX_WAV=512*1024
MAX_FRAMES=160000
MAX_ATTEMPTS=128


class SyntheticWavUnavailable(ValueError):
    def __init__(self):super().__init__('synthetic_wav_unavailable')


def _deny():raise SyntheticWavUnavailable()


def _utc(value):
    if type(value) is not datetime or value.utcoffset() is None:_deny()
    return value.astimezone(timezone.utc)


def _id(value):
    if type(value) is not str or not 0<len(value)<=200 or value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):_deny()
    value.encode('utf-8')


def _callable(value):return getattr(value,'__self__',None),getattr(value,'__func__',value)


@dataclass(frozen=True)
class WavSegmentEnvelope:
    id:str
    key:voice.SessionKey
    sequence:int
    epoch:int
    at:datetime
    evidence_id:str
    segment_id:str
    revision:int
    start:datetime
    end:datetime
    speaker_label:str|None


@dataclass(frozen=True)
class PcmRequest:
    pcm:bytes
    channels:int
    sample_width:int
    sample_rate:int
    frame_count:int
    clip_hash:str


@dataclass(frozen=True)
class TranscriptionResult:
    text:str
    final:bool


@dataclass(frozen=True)
class _Attempt:
    fingerprint:str
    outcome:str
    reference:meeting.FinalSegmentRef|None=None


def _envelope(value):
    if type(value) is not WavSegmentEnvelope or type(value.key) is not voice.SessionKey:_deny()
    for text in (value.id,value.key.business,value.key.source_id,value.key.session_id,value.evidence_id,value.segment_id):_id(text)
    for integer,minimum in ((value.sequence,1),(value.epoch,0),(value.revision,1)):
        if type(integer) is not int or not minimum<=integer<=1000000:_deny()
    if value.speaker_label is not None:_id(value.speaker_label)
    if not _utc(value.start)<_utc(value.end)<=_utc(value.at):_deny()
    return (value.id,value.key.business,value.key.source_id,value.key.session_id,value.sequence,value.epoch,
        _utc(value.at).isoformat(),value.evidence_id,value.segment_id,value.revision,
        _utc(value.start).isoformat(),_utc(value.end).isoformat(),value.speaker_label)


def _decode(data):
    if type(data) is not bytes or not 44<=len(data)<=MAX_WAV:_deny()
    # Container size is exact; no silent trailing bytes outside the RIFF file.
    if data[:4]!=b'RIFF' or data[8:12]!=b'WAVE' or int.from_bytes(data[4:8],'little')+8!=len(data):_deny()
    with wave.open(BytesIO(data),'rb') as stream:
        count=stream.getnframes()
        if (stream.getnchannels(),stream.getsampwidth(),stream.getframerate(),stream.getcomptype())!=(1,2,16000,'NONE') or not 1<=count<=MAX_FRAMES:_deny()
        pcm=stream.readframes(count)
        if len(pcm)!=count*2 or stream.readframes(1)!=b'':_deny()
        # wave truncates an odd data-chunk byte when calculating nframes.
        # A bounded chunk walk checks the actual data size, not only nframes.
        position=12;data_sizes=[]
        while position<len(data):
            if position+8>len(data):_deny()
            size=int.from_bytes(data[position+4:position+8],'little');kind=data[position:position+4]
            position+=8
            if position+size>len(data):_deny()
            if kind==b'data':data_sizes.append(size)
            position+=size+(size%2)
        if position!=len(data) or data_sizes!=[count*2]:_deny()
        return pcm,count


def _pcm_shape(value):
    if type(value) is not PcmRequest or type(value.pcm) is not bytes:_deny()
    if any(type(x) is not int for x in (value.channels,value.sample_width,value.sample_rate,value.frame_count)):_deny()
    return value.channels,value.sample_width,value.sample_rate,value.frame_count,value.clip_hash,hashlib.sha256(value.pcm).hexdigest()


class SyntheticWavBridge:
    def __init__(self,*,bridge,transcribe,clock,enabled=False):
        self._bridge=bridge;self._transcribe=transcribe;self._clock=clock;self._enabled=enabled is True
        self._attempts={};self._busy=False;self._last_now=None
        self._host=(bridge,_callable(getattr(bridge,'admission_proof',None)),_callable(getattr(bridge,'accept',None)),
            _callable(transcribe),_callable(clock),self._attempts,voice.Segment)

    def _check(self):
        bridge,proof,accept,transcribe,clock,attempts,segment=self._host
        if (not self._enabled or type(bridge) is not meeting.VoiceMeetingBridge or self._bridge is not bridge
                or self._attempts is not attempts or voice.Segment is not segment
                or not callable(self._transcribe) or not callable(self._clock)):_deny()
        for saved,current in ((proof,_callable(bridge.admission_proof)),(accept,_callable(bridge.accept)),
                (transcribe,_callable(self._transcribe)),(clock,_callable(self._clock))):
            if any(a is not b for a,b in zip(saved,current)):_deny()

    def _now(self):
        self._check();now=_utc(self._clock());self._check()
        if self._last_now is not None and now<self._last_now:_deny()
        self._last_now=now
        return now

    def _proof(self,now):
        self._check();proof=self._bridge.admission_proof(now=now);self._check()
        if (type(proof) is not meeting.VoiceAdmissionProof or type(proof.token) is not voice.OutputToken
                or proof.token.key!=proof.key or type(proof.record_count) is not int
                or not 0<=proof.record_count<=128):_deny()
        return proof

    def _new(self,envelope,proof,now):
        if (envelope.key!=proof.key or envelope.epoch!=proof.token.epoch or envelope.sequence!=proof.token.sequence+1
                or not _utc(proof.started_at)<=_utc(envelope.start) or not _utc(proof.accept_after)<=_utc(envelope.start)
                or not _utc(proof.last_at)<=_utc(envelope.at)<=now or not _utc(envelope.at)<_utc(proof.expires_at)):_deny()

    def accept_wav(self,envelope,wav_bytes):
        """One local attempt; an UNKNOWN result never retries or asserts undo.

        Successful replay returns reference metadata only. Actual I64 selection
        is still required to prove that reference is current/reviewable.
        """
        request=pcm=result=None
        reserved=None;attempts=None
        try:
            self._check()
            if self._busy:_deny()
            shape=_envelope(envelope);pcm,count=_decode(wav_bytes)
            duration=_utc(envelope.end)-_utc(envelope.start)
            micros=(duration.days*86400+duration.seconds)*1000000+duration.microseconds
            if micros*16000!=count*1000000:_deny()
            fingerprint=hashlib.sha256(json.dumps([shape,hashlib.sha256(wav_bytes).hexdigest()],separators=(',',':')).encode()).hexdigest()
            before_now=self._now();before=self._proof(before_now)
            if _envelope(envelope)!=shape or envelope.key!=before.key:_deny()
            attempts=self._host[5];existing=attempts.get(envelope.id)
            if existing is not None:
                if type(existing) is not _Attempt or existing.fingerprint!=fingerprint or existing.outcome!='success':_deny()
                return existing.reference
            self._new(envelope,before,before_now)
            if len(attempts)>=MAX_ATTEMPTS:_deny()
            request=PcmRequest(pcm,1,2,16000,count,fingerprint);request_shape=_pcm_shape(request)
            reserved=envelope.id;attempts[reserved]=_Attempt(fingerprint,'in_flight');self._busy=True
            result=self._transcribe(request)
            self._check()
            if _envelope(envelope)!=shape or _pcm_shape(request)!=request_shape or attempts.get(reserved)!=_Attempt(fingerprint,'in_flight'):_deny()
            after_now=self._now();after=self._proof(after_now)
            if before!=after:_deny()
            self._new(envelope,after,after_now)
            if (type(result) is not TranscriptionResult or result.final is not True or type(result.text) is not str
                    or not result.text.strip() or len(result.text)>2000 or '\r' in result.text
                    or any(ord(c)<32 and c!='\n' or ord(c)==127 for c in result.text)):_deny()
            result.text.encode('utf-8')
            frame=voice.Segment(envelope.id,envelope.key,envelope.sequence,envelope.epoch,envelope.at,
                envelope.evidence_id,envelope.segment_id,envelope.revision,envelope.start,envelope.end,True,result.text,envelope.speaker_label)
            self._check()
            self._bridge.accept(frame)
            # Anything uncertain after actual accept remains UNKNOWN. There is
            # no rollback of an admitted frame, controls or host side effects.
            final=self._proof(self._now())
            if (final.key!=before.key or final.token.epoch!=envelope.epoch or final.token.sequence!=envelope.sequence
                    or final.permission_fingerprint!=before.permission_fingerprint or final.record_count!=before.record_count+1
                    or _utc(final.last_at)!=_utc(envelope.at) or final.accept_after!=before.accept_after
                    or final.started_at!=before.started_at or final.expires_at!=before.expires_at
                    or _envelope(envelope)!=shape or _pcm_shape(request)!=request_shape):_deny()
            reference=meeting.FinalSegmentRef(envelope.key,envelope.epoch,envelope.segment_id,envelope.revision)
            attempts[reserved]=_Attempt(fingerprint,'success',reference)
            return reference
        except BaseException as exc:
            if reserved is not None and attempts is not None:
                attempts[reserved]=_Attempt(fingerprint,'unknown')
            if not isinstance(exc,Exception):raise
            raise SyntheticWavUnavailable() from None
        finally:
            if reserved is not None:self._busy=False
            # Do not retain binary/text payloads in adapter fields or receipts.
            pcm=request=result=wav_bytes=None
