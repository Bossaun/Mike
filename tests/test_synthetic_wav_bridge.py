"""Synthetic bytes -> actual I64 admission/review; no real STT or audio I/O."""
from dataclasses import replace,fields,FrozenInstanceError
from datetime import datetime,date,timedelta,timezone
from io import BytesIO
from types import SimpleNamespace
import struct
import wave
import pytest

from goldway import synthetic_wav_bridge as port
from goldway import voice_meeting_bridge as meeting

voice=meeting.voice
BASE=datetime(2026,10,20,12,tzinfo=timezone.utc)
KEY=voice.SessionKey('GOLDWAY','synthetic-source','synthetic-session')
SPEC=voice.SessionSpec(KEY,BASE,BASE+timedelta(hours=1))


def wav(*,count=16000,channels=1,width=2,rate=16000):
    buffer=BytesIO()
    with wave.open(buffer,'wb') as stream:
        stream.setparams((channels,width,rate,count,'NONE','not compressed'))
        stream.writeframes(b'\0'*(count*channels*width))
    return buffer.getvalue()


def envelope(sequence=1,**changes):
    offset=(sequence-1)*2
    values=dict(id='synthetic-frame-'+str(sequence),key=KEY,sequence=sequence,epoch=0,
        at=BASE+timedelta(seconds=offset+2),evidence_id='synthetic-evidence-'+str(sequence),
        segment_id='clip-'+str(sequence),revision=1,start=BASE+timedelta(seconds=offset),
        end=BASE+timedelta(seconds=offset+1),speaker_label=None)
    values.update(changes)
    return port.WavSegmentEnvelope(**values)


def control(sequence,kind,*,epoch=0,at=None):
    return voice.Control('control-'+str(sequence),KEY,sequence,epoch,at or BASE+timedelta(seconds=3),
        'control-evidence-'+str(sequence),kind,True)


@pytest.fixture
def c(monkeypatch):
    state={'now':BASE+timedelta(seconds=10),'permission':meeting.TextReviewPermission(
        KEY,'synthetic-reviewer',1,BASE,BASE+timedelta(hours=1),True)}
    calls=[];bundles=[]
    def permission():return state['permission']
    def clock():return state['now']
    def callback(request):
        calls.append(request)
        return port.TranscriptionResult('Проверить синтетический образец',True)
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=bundles.append,enabled=True)
    adapter=port.SyntheticWavBridge(bridge=bridge,transcribe=callback,clock=clock,enabled=True)
    def fail(*args,**kwargs):pytest.fail('unexpected filesystem/provider/network/process operation')
    import builtins,socket,subprocess
    monkeypatch.setattr(builtins,'open',fail)
    monkeypatch.setattr(socket,'socket',fail)
    monkeypatch.setattr(subprocess,'Popen',fail)
    return SimpleNamespace(state=state,calls=calls,bundles=bundles,permission=permission,clock=clock,
        callback=callback,bridge=bridge,adapter=adapter)


def with_callback(c,callback):
    return port.SyntheticWavBridge(bridge=c.bridge,transcribe=callback,clock=c.clock,enabled=True)


def admitted(c):
    return c.bridge._state.records if c.bridge._state is not None else ()


def chosen(reference,text='Проверить синтетический образец',*,decisions=False):
    span=meeting.SelectedSpan(reference,1,1,text)
    return meeting.ReviewSelection((reference,),
        (meeting.DecisionSelection(text,True,(span,)),) if decisions else ())


def test_real_binary_port_then_explicit_selection_actual_core_review_renderer_sink(c,monkeypatch):
    calls=[];actual=meeting.meeting.review_meeting
    def review(value):calls.append(value);return actual(value)
    monkeypatch.setattr(meeting.meeting,'review_meeting',review)
    reference=c.adapter.accept_wav(envelope(),wav())
    assert reference==meeting.FinalSegmentRef(KEY,0,'clip-1',1)
    assert len(c.calls)==1 and len(admitted(c))==1 and c.bundles==[] and calls==[]
    frame=admitted(c)[0]
    assert type(frame) is voice.Segment and frame.final is True and frame.text=='Проверить синтетический образец'
    assert frame.key==KEY and frame.id=='synthetic-frame-1' and frame.evidence_id=='synthetic-evidence-1'
    assert SPEC.capture_authorized is False
    ticket=c.bridge.prepare_review(chosen(reference),on=date(2026,10,20),now=c.state['now'])
    c.bridge.deliver_review(ticket,now=c.state['now'])
    assert len(c.bundles)==len(calls)==1
    bundle=c.bundles[0]
    assert bundle.review.proposed_tasks==() and bundle.review.meeting.agreements==()
    assert bundle.review.meeting.sources[0].lines==('Проверить синтетический образец',)
    assert bundle.pages and bundle.review_required and not bundle.capture_authorized and not bundle.live_authorized
    with pytest.raises(meeting.VoiceBridgeUnavailable):c.bridge.deliver_review(ticket,now=c.state['now'])


def test_callback_request_has_only_decoded_pcm_format_and_opaque_hash(c):
    c.adapter.accept_wav(envelope(),wav())
    request=c.calls[0]
    assert {f.name for f in fields(request)}=={'pcm','channels','sample_width','sample_rate','frame_count','clip_hash'}
    assert type(request.pcm) is bytes and len(request.pcm)==32000
    assert (request.channels,request.sample_width,request.sample_rate,request.frame_count)==(1,2,16000,16000)
    assert len(request.clip_hash)==64 and KEY.source_id not in repr(request) and 'synthetic-reviewer' not in repr(request)
    with pytest.raises(FrozenInstanceError):request.frame_count=1
    ledger=c.adapter._attempts
    assert len(ledger)==1 and all(not hasattr(row,'text') and not hasattr(row,'pcm') for row in ledger.values())
    assert not any(type(value) in (bytes,port.PcmRequest,port.TranscriptionResult) for value in vars(c.adapter).values())


def test_admission_proof_lazy_and_pending_are_readonly_metadata(c):
    proof=c.bridge.admission_proof(now=c.state['now'])
    assert c.bridge._state is None and c.bridge._pending is None
    assert type(proof) is meeting.VoiceAdmissionProof and proof.token==voice.OutputToken(KEY,0,0)
    assert proof.record_count==0 and proof.started_at==BASE and len(proof.permission_fingerprint)==64
    assert not any(f.name in ('records','text','transcript','reviewer','permission') for f in fields(proof))
    reference=c.adapter.accept_wav(envelope(),wav())
    ticket=c.bridge.prepare_review(chosen(reference),on=date(2026,10,20),now=c.state['now'])
    pending=c.bridge._pending;state=c.bridge._state
    assert c.bridge.admission_proof(now=c.state['now']).record_count==1
    assert c.bridge._pending is pending and c.bridge._state is state
    c.bridge.deliver_review(ticket,now=c.state['now'])
    assert len(c.bundles)==1


@pytest.mark.parametrize('enabled',[False,None,0,1,'true'])
def test_disabled_port_has_zero_callbacks_clock_proof_and_admission(c,enabled):
    def fail(*a,**kw):pytest.fail('disabled port called host')
    adapter=port.SyntheticWavBridge(bridge=c.bridge,transcribe=fail,clock=fail,enabled=enabled)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert c.bridge._state is None and adapter._attempts=={}


def test_enabled_port_does_not_enable_disabled_existing_bridge(c):
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=c.permission,sink=c.bundles.append)
    adapter=port.SyntheticWavBridge(bridge=bridge,transcribe=c.callback,clock=c.clock,enabled=True)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    with pytest.raises(meeting.VoiceBridgeUnavailable):bridge.admission_proof(now=c.state['now'])
    assert c.calls==[] and bridge._state is None


@pytest.mark.parametrize('data',[
    b'',b'not WAV',b'RIFF'+b'\0'*40,bytearray(b'\0'*44),'file.wav',BytesIO(b'not audio'),b'\0'*(512*1024+1),
])
def test_binary_type_header_and_container_caps_zero_callback(c,data):
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(envelope(),data)
    assert c.calls==[] and admitted(c)==() and c.adapter._attempts=={}


@pytest.mark.parametrize('changes',[{'channels':2},{'width':1},{'width':3},{'rate':8000},{'rate':48000},{'count':0},{'count':160001}])
def test_format_and_declared_frame_bounds_before_callback(c,changes):
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(envelope(),wav(**changes))
    assert c.calls==[] and admitted(c)==()


@pytest.mark.parametrize('kind',['truncated','extra','odd_data','duplicate_data','compressed','huge_declared','bad_chunk'])
def test_riff_chunk_malformations_refuse_whole(c,kind):
    data=bytearray(wav())
    if kind=='truncated':data=data[:-2]
    elif kind=='extra':data+=b'ZZ'
    elif kind=='odd_data':data[40:44]=struct.pack('<I',31999)
    elif kind=='duplicate_data':data+=b'data'+struct.pack('<I',2)+b'\0\0'
    elif kind=='compressed':data[20:22]=struct.pack('<H',3)
    elif kind=='huge_declared':data[40:44]=struct.pack('<I',2**32-2)
    elif kind=='bad_chunk':data+=b'JUNK'+struct.pack('<I',12)+b'x'
    data[4:8]=struct.pack('<I',len(data)-8)
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(envelope(),bytes(data))
    assert c.calls==[] and admitted(c)==()


def test_exact_maximum_duration_with_integer_sample_match(c):
    c.state['now']=BASE+timedelta(seconds=11)
    ref=c.adapter.accept_wav(envelope(end=BASE+timedelta(seconds=10),at=BASE+timedelta(seconds=10)),wav(count=160000))
    assert ref.revision==1 and len(c.calls[0].pcm)==320000


@pytest.mark.parametrize('changes',[
    {'end':BASE+timedelta(seconds=1,microseconds=1)},
    {'at':BASE}, {'start':BASE-timedelta(seconds=1),'end':BASE},
    {'at':BASE+timedelta(hours=1)}, {'sequence':2},{'epoch':1},
    {'key':voice.SessionKey('OTHER','synthetic-source','synthetic-session')},
    {'key':voice.SessionKey('GOLDWAY','other','synthetic-session')},
    {'key':voice.SessionKey('GOLDWAY','synthetic-source','other')},
    {'sequence':True},{'revision':0},{'id':' x'},{'evidence_id':'x\n'},
    {'speaker_label':'x\n'},{'start':BASE.replace(tzinfo=None)},
])
def test_explicit_envelope_duration_window_session_sequence_shape(c,changes):
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(envelope(**changes),wav())
    assert c.calls==[] and admitted(c)==() and c.adapter._attempts=={}


def test_subsample_duration_that_cannot_be_represented_is_not_rounded(c):
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(
        envelope(end=BASE+timedelta(microseconds=62),at=BASE+timedelta(seconds=1)),wav(count=1))
    assert c.calls==[]


@pytest.mark.parametrize('result',[
    'transcript',None,port.TranscriptionResult('text',False),port.TranscriptionResult('text',1),
    port.TranscriptionResult('',True),port.TranscriptionResult(' '*3,True),
    port.TranscriptionResult('x'*2001,True),port.TranscriptionResult(1,True),
    port.TranscriptionResult('x\r',True),port.TranscriptionResult('x\0',True),
    port.TranscriptionResult('\ud800',True),
])
def test_callback_result_is_closed_literal_final_and_unknown_not_retried(c,result):
    calls=[]
    def callback(request):calls.append(request.clip_hash);return result
    adapter=with_callback(c,callback)
    for _ in range(2):
        with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert len(calls)==1 and admitted(c)==()
    assert adapter._attempts['synthetic-frame-1'].outcome=='unknown'


@pytest.mark.parametrize('error',[RuntimeError('private callback detail'),KeyboardInterrupt(),SystemExit(5),GeneratorExit()])
def test_baseexception_attempt_is_consumed_and_process_interrupt_rethrown(c,error):
    calls=[]
    def callback(request):calls.append(1);raise error
    adapter=with_callback(c,callback)
    expected=port.SyntheticWavUnavailable if isinstance(error,Exception) else type(error)
    with pytest.raises(expected) as caught:adapter.accept_wav(envelope(),wav())
    assert str(caught.value)=='synthetic_wav_unavailable' if isinstance(error,Exception) else caught.value is error
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert len(calls)==1 and adapter._attempts['synthetic-frame-1'].outcome=='unknown' and not adapter._busy


def test_successful_exact_replay_is_metadata_only_and_current_selection_still_required(c):
    first=c.adapter.accept_wav(envelope(),wav())
    assert c.adapter.accept_wav(envelope(),wav()) is first and len(c.calls)==1 and len(admitted(c))==1
    c.bridge.accept(voice.Segment('correction',KEY,2,0,BASE+timedelta(seconds=4),'correction-evidence',
        'clip-1',2,BASE,BASE+timedelta(seconds=1),True,'Исправленный текст',None))
    assert c.adapter.accept_wav(envelope(),wav()) is first and len(c.calls)==1
    with pytest.raises(meeting.VoiceBridgeUnavailable):
        c.bridge.prepare_review(chosen(first),on=date(2026,10,20),now=c.state['now'])


@pytest.mark.parametrize('change',['metadata','bytes'])
def test_conflicting_replay_never_retranscribes(c,change):
    c.adapter.accept_wav(envelope(),wav())
    request=envelope(evidence_id='other') if change=='metadata' else envelope()
    data=wav() if change=='metadata' else wav()[:-2]+b'\1\0'
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(request,data)
    assert len(c.calls)==1 and len(admitted(c))==1


@pytest.mark.parametrize('kind',['halt','interrupt','stop'])
def test_callback_negative_control_prevents_delayed_final_then_explicit_resume_new_epoch(c,kind):
    def callback(request):
        c.bridge.accept(control(1,kind));return port.TranscriptionResult('delayed',True)
    adapter=with_callback(c,callback)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert all(type(row) is voice.Control for row in admitted(c))
    assert adapter._attempts['synthetic-frame-1'].outcome=='unknown'
    c.bridge.accept(control(2,'resume',epoch=1,at=BASE+timedelta(seconds=4)))
    ref=c.adapter.accept_wav(envelope(3,epoch=1,start=BASE+timedelta(seconds=4),end=BASE+timedelta(seconds=5)),wav())
    assert ref.epoch==1 and len([row for row in admitted(c) if type(row) is voice.Segment])==1


@pytest.mark.parametrize('change',['revoked','expired','revision','allow','clock_expired','state_advance'])
def test_callback_current_source_or_permission_change_denies_admission(c,change):
    def callback(request):
        if change=='revoked':c.state['permission']=replace(c.state['permission'],revoked=True)
        elif change=='expired':c.state['permission']=replace(c.state['permission'],expires_at=BASE+timedelta(seconds=5))
        elif change=='revision':c.state['permission']=replace(c.state['permission'],revision=2)
        elif change=='allow':c.state['permission']=replace(c.state['permission'],allow_text_review=False)
        elif change=='clock_expired':c.state['now']=BASE+timedelta(hours=1)
        elif change=='state_advance':c.bridge.accept(voice.Speaker('speaker',KEY,1,0,BASE+timedelta(seconds=2),'speaker-evidence','A',None,False))
        return port.TranscriptionResult('delayed',True)
    adapter=with_callback(c,callback)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert not any(type(row) is voice.Segment for row in admitted(c))
    assert adapter._attempts['synthetic-frame-1'].outcome=='unknown'


def test_current_permission_required_for_successful_metadata_replay(c):
    c.adapter.accept_wav(envelope(),wav());c.state['permission']=replace(c.state['permission'],revoked=True)
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(envelope(),wav())
    assert len(c.calls)==1


@pytest.mark.parametrize('change',['bridge','transcribe','clock','proof_method','accept_method','envelope','request'])
def test_callback_host_method_input_replacement_refuses(c,change):
    request=envelope()
    def callback(pcm):
        if change=='bridge':adapter._bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=c.permission,sink=c.bundles.append,enabled=True)
        elif change=='transcribe':adapter._transcribe=lambda value:port.TranscriptionResult('other',True)
        elif change=='clock':adapter._clock=lambda:c.state['now']
        elif change=='proof_method':c.bridge.admission_proof=lambda **kw:None
        elif change=='accept_method':c.bridge.accept=lambda frame:None
        elif change=='envelope':object.__setattr__(request,'evidence_id','changed')
        elif change=='request':object.__setattr__(pcm,'pcm',b'\1'*len(pcm.pcm))
        return port.TranscriptionResult('delayed',True)
    adapter=with_callback(c,callback)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(request,wav())
    assert admitted(c)==() and adapter._attempts['synthetic-frame-1'].outcome=='unknown'


def test_reentrant_port_request_does_not_reuse_inflight_attempt(c):
    calls=[]
    def callback(request):
        calls.append(1)
        with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
        return port.TranscriptionResult('valid',True)
    adapter=with_callback(c,callback)
    assert adapter.accept_wav(envelope(),wav()).revision==1 and len(calls)==1


def test_monotonic_current_clock_denies_backward_callback_result(c):
    def callback(request):c.state['now']-=timedelta(seconds=1);return port.TranscriptionResult('late',True)
    adapter=with_callback(c,callback)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert admitted(c)==()


def test_attempt_capacity_is_whole_refusal_no_eviction_and_no_audio_retention(c):
    count=[]
    def callback(request):count.append(request.clip_hash);raise RuntimeError()
    adapter=with_callback(c,callback)
    for i in range(128):
        with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(id='failed-'+str(i)),wav())
    original=dict(adapter._attempts)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(id='failed-129'),wav())
    assert adapter._attempts==original and len(count)==128 and admitted(c)==()


def test_actual_accept_ambiguous_failure_never_claims_rollback_or_retries(c,monkeypatch):
    actual=meeting.VoiceMeetingBridge.accept
    def accepts_then_raises(self,frame):actual(self,frame);raise RuntimeError('ambiguous after accept')
    monkeypatch.setattr(meeting.VoiceMeetingBridge,'accept',accepts_then_raises)
    adapter=with_callback(c,c.callback)
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert len(admitted(c))==1 and type(admitted(c)[0]) is voice.Segment
    assert adapter._attempts['synthetic-frame-1'].outcome=='unknown'
    with pytest.raises(port.SyntheticWavUnavailable):adapter.accept_wav(envelope(),wav())
    assert len(c.calls)==1 and len(admitted(c))==1 and c.bundles==[]


def test_actual_revision_guard_owns_gap_and_keeps_existing_final(c):
    c.adapter.accept_wav(envelope(),wav())
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(
        envelope(2,segment_id='clip-1',revision=3),wav())
    assert len(c.calls)==2 and len(admitted(c))==1 and admitted(c)[0].revision==1


@pytest.mark.parametrize('confirmed',[False,True])
def test_existing_explicit_speaker_mapping_not_inferred_or_promoted_to_task_owner(c,confirmed):
    c.bridge.accept(voice.Speaker('speaker',KEY,1,0,BASE+timedelta(seconds=1),'speaker-evidence','A',
        'synthetic-participant' if confirmed else None,confirmed))
    ref=c.adapter.accept_wav(envelope(2,speaker_label='A'),wav())
    frame=admitted(c)[1]
    assert type(frame) is voice.Segment and frame.speaker_label=='A' and len(admitted(c))==2
    ticket=c.bridge.prepare_review(chosen(ref,decisions=True),on=date(2026,10,20),now=c.state['now'])
    c.bridge.deliver_review(ticket,now=c.state['now'])
    assert c.bundles[0].review.proposed_tasks[0].owner is None
    assert c.bundles[0].review.proposed_tasks[0].deadline is None


def test_proof_permission_callback_state_change_is_detected_without_undo(c):
    invoked=[]
    def permission():
        if not invoked:
            invoked.append(1)
            c.bridge.accept(control(1,'interrupt'))
        return c.state['permission']
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=c.bundles.append,enabled=True)
    c.bridge=bridge
    with pytest.raises(meeting.VoiceBridgeUnavailable):bridge.admission_proof(now=c.state['now'])
    assert len(bridge._state.records)==1 and bridge._state.mode is voice.Mode.HALTED
    assert bridge._pending is None


def test_proof_permission_change_between_its_observations_denies(c):
    calls=[]
    def permission():
        calls.append(1)
        return replace(c.state['permission'],revision=len(calls))
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=c.bundles.append,enabled=True)
    with pytest.raises(meeting.VoiceBridgeUnavailable):bridge.admission_proof(now=c.state['now'])
    assert bridge._state is None and bridge._pending is None


def test_negative_control_after_revocation_invalidates_existing_ticket_and_new_port(c):
    ref=c.adapter.accept_wav(envelope(),wav())
    ticket=c.bridge.prepare_review(chosen(ref),on=date(2026,10,20),now=c.state['now'])
    c.state['permission']=replace(c.state['permission'],revoked=True)
    c.bridge.accept(control(2,'stop',at=BASE+timedelta(seconds=4)))
    with pytest.raises(meeting.VoiceBridgeUnavailable):c.bridge.deliver_review(ticket,now=c.state['now'])
    with pytest.raises(port.SyntheticWavUnavailable):c.adapter.accept_wav(envelope(3,epoch=1),wav())
    assert c.bundles==[] and len(c.calls)==1


def test_partial_existing_segment_can_be_explicitly_revised_to_final_via_wav(c):
    c.bridge.accept(voice.Segment('partial',KEY,1,0,BASE+timedelta(seconds=2),'partial-evidence','clip-1',1,
        BASE,BASE+timedelta(seconds=1),False,'Частично',None))
    ref=c.adapter.accept_wav(envelope(2,segment_id='clip-1',revision=2,start=BASE,end=BASE+timedelta(seconds=1)),wav())
    assert ref.revision==2 and admitted(c)[-1].final is True
    ticket=c.bridge.prepare_review(chosen(ref),on=date(2026,10,20),now=c.state['now'])
    c.bridge.deliver_review(ticket,now=c.state['now'])
    assert c.bundles[0].review.meeting.sources[0].lines==('Проверить синтетический образец',)
