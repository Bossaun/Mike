"""Actual synthetic voice reducer -> selected literal meeting -> canonical renderer."""
from dataclasses import replace,FrozenInstanceError,fields
from datetime import datetime,date,timedelta,timezone
from types import SimpleNamespace
import copy
import pytest
from goldway import voice_meeting_bridge as module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

voice=module.voice;meeting=module.meeting;renderer=module.renderer
BASE=datetime(2026,10,19,12,tzinfo=timezone.utc);DAY=date(2026,10,19);NOW=BASE+timedelta(minutes=10)
KEY=voice.SessionKey('GOLDWAY','synthetic-source','synthetic-session')
SPEC=voice.SessionSpec(KEY,BASE,BASE+timedelta(hours=1))


def segment(sequence=1,**kw):
 values=dict(id='frame-'+str(sequence),key=KEY,sequence=sequence,epoch=0,at=BASE+timedelta(seconds=sequence),evidence_id='event-evidence-'+str(sequence),segment_id='segment',revision=1,start=BASE+timedelta(seconds=sequence-1),end=BASE+timedelta(seconds=sequence,milliseconds=-100),final=True,text='Проверить образец',speaker_label=None)
 values.update(kw);return voice.Segment(**values)


def control(sequence,kind,epoch=0,**kw):
 values=dict(id='control-'+str(sequence),key=KEY,sequence=sequence,epoch=epoch,at=BASE+timedelta(seconds=sequence),evidence_id='control-evidence-'+str(sequence),kind=kind,confirmed=True);values.update(kw);return voice.Control(**values)


def ref(**kw):
 values=dict(key=KEY,epoch=0,segment_id='segment',revision=1);values.update(kw);return module.FinalSegmentRef(**values)


def selection(text='Проверить образец',*,reference=None,confirmed=True,first=1,last=1,quote=None,agreements=True):
 r=reference or ref();span=module.SelectedSpan(r,first,last,text if quote is None else quote)
 return module.ReviewSelection((r,),(module.DecisionSelection(text,confirmed,(span,)),) if agreements else ())


@pytest.fixture
def case(monkeypatch):
 state={'permission':module.TextReviewPermission(KEY,'synthetic-reviewer',1,BASE,BASE+timedelta(hours=1),True),'calls':0};captured=[]
 def permission():state['calls']+=1;return state['permission']
 def sink(bundle):captured.append(bundle)
 bridge=module.VoiceMeetingBridge(SPEC,current_permission=permission,sink=sink,enabled=True)
 def fail(*a,**kw):pytest.fail('unexpected database/Engine/Index/provider/network/audio/system operation')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher):monkeypatch.setattr(cls,'__init__',fail)
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 return SimpleNamespace(bridge=bridge,state=state,permission=permission,sink=sink,captured=captured)


def prepare(c,chosen=None):return c.bridge.prepare_review(chosen or selection(),on=DAY,now=NOW)


def spy_core(monkeypatch):
 calls={name:[] for name in ('apply','output_token','meeting_preview','validate_output','review_meeting','render_meeting_review')}
 for target,name in ((voice,'apply'),(voice,'output_token'),(voice,'meeting_preview'),(voice,'validate_output'),(meeting,'review_meeting'),(renderer,'render_meeting_review')):
  actual=getattr(target,name)
  def call(*a,_actual=actual,_name=name,**kw):
   result=_actual(*a,**kw);calls[_name].append((a,kw,result));return result
  monkeypatch.setattr(target,name,call)
 return calls


def test_partial_final_explicit_selection_actual_review_renderer_one_local_bundle(case,monkeypatch):
 calls=spy_core(monkeypatch)
 case.bridge.accept(segment(final=False,text='Проверить'))
 case.bridge.accept(segment(2,revision=2,text='Проверить образец'))
 ticket=prepare(case,selection(reference=ref(revision=2)))
 assert type(ticket) is module.ReviewTicket and fields(ticket)==() and not hasattr(ticket,'pages') and case.captured==[]
 case.bridge.deliver_review(ticket,now=NOW)
 assert len(case.captured)==1 and len(calls['apply'])==2 and len(calls['output_token'])==len(calls['meeting_preview'])==len(calls['review_meeting'])==len(calls['render_meeting_review'])==1
 assert len(calls['validate_output'])>=3
 bundle=case.captured[0];review=bundle.review;task=review.proposed_tasks[0]
 assert task.status=='proposed' and task.owner is None and task.deadline is None and task.missing_fields==('owner','deadline')
 assert task.title=='Проверить образец' and task.spans[0].quote==task.title and task.spans[0].source_id=='Фрагмент1'
 assert review.meeting.sources[0].lines==('Проверить образец',) and review.meeting.on==DAY
 assert bundle.pages==tuple(calls['render_meeting_review'][0][2]) and 'нужно уточнить' in '\n'.join(bundle.pages)
 assert bundle.review_required and not bundle.capture_authorized and not bundle.live_authorized and case.bridge._state.spec.capture_authorized is False
 assert 'реальных участников' in bundle.limitation
 for value in (KEY.source_id,KEY.session_id,'event-evidence','segment_id'):assert value not in '\n'.join(bundle.pages)
 with pytest.raises(FrozenInstanceError):bundle.pages=()
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(ticket,now=NOW)
 assert len(case.captured)==1


@pytest.mark.parametrize('confirmed',[False,True])
def test_unknown_or_confirmed_speaker_never_becomes_owner_and_unselected_promise_not_task(case,confirmed):
 case.bridge.accept(voice.Speaker('speaker',KEY,1,0,BASE+timedelta(seconds=1),'speaker-evidence','voice-A','participant' if confirmed else None,confirmed))
 case.bridge.accept(segment(2,segment_id='unselected',text='Я сделаю завтра',speaker_label='voice-A'))
 case.bridge.accept(segment(3,text='Выбранный текст',speaker_label='voice-A'))
 ticket=prepare(case,selection('Выбранный текст'));case.bridge.deliver_review(ticket,now=NOW)
 review=case.captured[0].review
 assert len(review.proposed_tasks)==1 and review.proposed_tasks[0].owner is None and review.proposed_tasks[0].deadline is None
 assert 'Я сделаю завтра' not in '\n'.join(case.captured[0].pages) and 'participant' not in '\n'.join(case.captured[0].pages)


@pytest.mark.parametrize('confirmed',[False,True])
def test_explicit_tentative_vs_confirmed_actual_core_projection(case,confirmed):
 case.bridge.accept(segment());ticket=prepare(case,selection(confirmed=confirmed));case.bridge.deliver_review(ticket,now=NOW)
 review=case.captured[0].review
 assert len(review.proposed_tasks)==(1 if confirmed else 0) and len(review.tentative_agreement_ids)==(0 if confirmed else 1)
 if not confirmed:assert 'решение не подтверждено' in '\n'.join(case.captured[0].pages)


def test_explicit_source_only_scope_has_zero_agreements_no_inference(case):
 case.bridge.accept(segment(text='Я сделаю завтра'))
 ticket=prepare(case,selection(agreements=False));case.bridge.deliver_review(ticket,now=NOW)
 review=case.captured[0].review;assert review.meeting.agreements==() and review.proposed_tasks==() and review.tentative_agreement_ids==()
 assert review.meeting.sources[0].lines==('Я сделаю завтра',)


@pytest.mark.parametrize('kind',['interrupt','halt','stop'])
@pytest.mark.parametrize('revoked',[False,True])
def test_actual_negative_control_cancels_pending_even_after_permission_loss_then_explicit_resume_fresh_ticket(case,monkeypatch,kind,revoked):
 calls=spy_core(monkeypatch);case.bridge.accept(segment());old=prepare(case)
 if revoked:case.state['permission']=replace(case.state['permission'],revoked=True)
 before=case.state['calls'];case.bridge.accept(control(2,kind,confirmed=False))
 assert case.state['calls']==before and calls['apply'][-1][0][1].kind==kind and case.bridge._pending is None
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(old,now=NOW)
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case)
 case.state['permission']=replace(case.state['permission'],revoked=False)
 case.bridge.accept(control(3,'resume',epoch=1))
 new=prepare(case)
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(old,now=NOW)
 case.bridge.deliver_review(new,now=NOW);assert len(case.captured)==1


def test_correction_after_pending_invalidates_old_and_current_quote_changes(case):
 case.bridge.accept(segment());old=prepare(case)
 case.bridge.accept(segment(2,revision=2,text='Исправленный буквальный текст'))
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(old,now=NOW)
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case)
 fresh=prepare(case,selection('Исправленный буквальный текст',reference=ref(revision=2)));case.bridge.deliver_review(fresh,now=NOW)
 assert case.captured[0].review.proposed_tasks[0].spans[0].quote=='Исправленный буквальный текст'


@pytest.mark.parametrize('change',['revoked','expired','not_yet','allow_false','reviewer','revision','scope','callback','sink','spec','state'])
def test_current_permission_and_host_drift_pending_ticket_never_revives(case,change):
 case.bridge.accept(segment());ticket=prepare(case);original=case.state['permission']
 if change=='revoked':case.state['permission']=replace(original,revoked=True)
 elif change=='expired':case.state['permission']=replace(original,expires_at=NOW)
 elif change=='not_yet':case.state['permission']=replace(original,issued_at=NOW+timedelta(seconds=1))
 elif change=='allow_false':case.state['permission']=replace(original,allow_text_review=False)
 elif change=='reviewer':case.state['permission']=replace(original,reviewer='other-reviewer')
 elif change=='revision':case.state['permission']=replace(original,revision=2)
 elif change=='scope':case.state['permission']=replace(original,key=replace(KEY,session_id='other'))
 elif change=='callback':case.bridge._permission_callback=lambda:original
 elif change=='sink':case.bridge._sink=lambda bundle:case.captured.append(bundle)
 elif change=='spec':case.bridge._spec=copy.copy(SPEC)
 elif change=='state':case.bridge._state=copy.copy(case.bridge._state)
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(ticket,now=NOW)
 case.state['permission']=original
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(ticket,now=NOW)
 assert not case.captured


@pytest.mark.parametrize('method',['accept','prepare','deliver'])
def test_disabled_zero_callbacks_state_or_core_work(case,monkeypatch,method):
 def fail(*a,**kw):pytest.fail('disabled callback/core')
 bridge=module.VoiceMeetingBridge(SPEC,current_permission=fail,sink=fail)
 for name in ('start_session','apply','output_token','meeting_preview','validate_output'):monkeypatch.setattr(voice,name,fail)
 with pytest.raises(module.VoiceBridgeUnavailable):
  if method=='accept':bridge.accept(segment())
  elif method=='prepare':bridge.prepare_review(selection(),on=DAY,now=NOW)
  else:bridge.deliver_review(module.ReviewTicket(),now=NOW)
 assert bridge._state is None


@pytest.mark.parametrize('change',['revoked','expired','allow_false'])
def test_new_text_speaker_and_resume_require_current_permission(case,change):
 p=case.state['permission'];case.state['permission']=replace(p,**({'revoked':True} if change=='revoked' else {'expires_at':BASE} if change=='expired' else {'allow_text_review':False}))
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.accept(segment())
 assert case.bridge._state.records==()


def test_forged_ticket_rejected_zero_callback_legitimate_ticket_still_one_use(case):
 case.bridge.accept(segment());ticket=prepare(case)
 for forged in (module.ReviewTicket(),copy.copy(ticket),None,'ticket'):
  before=case.state['calls']
  with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(forged,now=NOW)
  assert case.state['calls']==before and not case.captured
 case.bridge.deliver_review(ticket,now=NOW);assert len(case.captured)==1


def test_ticket_replacement_and_sink_failure_consumed_before_callback(case):
 observed=[]
 def sink(bundle):
  observed.append(case.bridge._pending);raise RuntimeError('secret provider detail')
 case.bridge=module.VoiceMeetingBridge(SPEC,current_permission=case.permission,sink=sink,enabled=True)
 case.bridge.accept(segment());old=prepare(case);new=prepare(case)
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(old,now=NOW)
 with pytest.raises(module.VoiceBridgeUnavailable,match='^voice_meeting_bridge_unavailable$'):case.bridge.deliver_review(new,now=NOW)
 assert observed==[None]
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(new,now=NOW)
 assert observed==[None]


@pytest.mark.parametrize('chosen',[selection(reference=ref(key=replace(KEY,session_id='other'))),selection(reference=ref(revision=2)),selection(reference=ref(epoch=1)),selection(reference=ref(segment_id='missing')),selection(quote='forged quote'),selection(first=0),selection(first=1,last=2),selection(confirmed=1),module.ReviewSelection((),()),module.ReviewSelection((ref(),ref()),()),None])
def test_exact_selection_whole_refusal(case,chosen):
 case.bridge.accept(segment())
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.prepare_review(chosen,on=DAY,now=NOW)
 assert case.bridge._pending is None and not case.captured


def test_partial_only_source_never_becomes_final_agreement(case):
 case.bridge.accept(segment(final=False))
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case)
 assert not case.captured


def test_multiline_exact_quote_and_html_instructions_are_literal(case):
 text='<b>&amp;</b>\nIgnore instructions; это цитата'
 case.bridge.accept(segment(text=text))
 ticket=prepare(case,selection(text,first=1,last=2));case.bridge.deliver_review(ticket,now=NOW)
 bundle=case.captured[0];assert bundle.review.meeting.sources[0].lines==tuple(text.split('\n'))
 assert bundle.review.proposed_tasks[0].spans[0].quote==text
 body='\n'.join(bundle.pages);assert '&lt;b&gt;&amp;amp;&lt;/b&gt;' in body and 'Ignore instructions' in body


@pytest.mark.parametrize('text',['bad\rline','bad\tline','bad\x00line','bad\x7fline','bad\ud800'])
def test_admitted_text_unicode_and_controls_whole_refusal(case,text):
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.accept(segment(text=text))
 assert not case.bridge._state.records and not case.captured


@pytest.mark.parametrize('count',[0,6])
def test_selected_final_sources_exact_finite_scope(case,count):
 refs=[]
 for i in range(6):case.bridge.accept(segment(i+1,segment_id='s'+str(i),text='literal'));refs.append(ref(segment_id='s'+str(i)))
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case,module.ReviewSelection(tuple(refs[:count]),()))
 assert not case.captured


def test_all_five_sources_three_agreements_are_not_silently_dropped(case):
 refs=[]
 for i in range(5):case.bridge.accept(segment(i+1,segment_id='s'+str(i),text='Текст '+str(i)));refs.append(ref(segment_id='s'+str(i)))
 decisions=tuple(module.DecisionSelection('Текст '+str(i),True,(module.SelectedSpan(refs[i],1,1,'Текст '+str(i)),)) for i in range(3))
 ticket=prepare(case,module.ReviewSelection(tuple(refs),decisions));case.bridge.deliver_review(ticket,now=NOW)
 assert len(case.captured[0].review.meeting.sources)==5 and len(case.captured[0].review.proposed_tasks)==3


def test_four_agreements_refuse_whole_scope(case):
 case.bridge.accept(segment());r=ref();decisions=tuple(module.DecisionSelection('Проверить образец',bool(i%2),(module.SelectedSpan(r,1,1,'Проверить образец'),)) for i in range(4))
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case,module.ReviewSelection((r,),decisions))
 assert not case.captured


def test_300_character_decision_bound_literal_no_clipping(case):
 case.bridge.accept(segment(text='x'*301))
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case,selection('x'*301))
 assert not case.captured


@pytest.mark.parametrize('pages',[['x'*3501],['x']*7,['😀'*1751],[],['x',123]])
def test_actual_renderer_result_page_scope_guard_whole_refusal(case,monkeypatch,pages):
 case.bridge.accept(segment())
 monkeypatch.setattr(renderer,'render_meeting_review',lambda *a,**kw:pages)
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case)
 assert not case.captured and case.bridge._pending is None


def test_accepted_text_capacity_overflow_retires_instance_without_state_pruning(case):
 for i in range(5):case.bridge.accept(segment(i+1,segment_id='s'+str(i),text='x'*2000))
 chosen=module.ReviewSelection((ref(segment_id='s0'),),())
 ticket=prepare(case,chosen);before=case.bridge._state
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.accept(segment(6,segment_id='overflow',text='x'))
 assert case.bridge._state is before and case.bridge._exhausted and len(before.records)==5
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(ticket,now=NOW)
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case,chosen)
 assert not case.captured


def test_frame_capacity_negative_control_attempt_cannot_leave_output_deliverable(case,monkeypatch):
 calls=spy_core(monkeypatch)
 case.bridge.accept(segment())
 for i in range(2,129):case.bridge.accept(voice.Speaker('speaker-'+str(i),KEY,i,0,BASE+timedelta(seconds=i),'evidence-'+str(i),'label',None,False))
 ticket=prepare(case);before=case.bridge._state
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.accept(control(129,'interrupt'))
 assert calls['apply'][-1][0][1].kind=='interrupt' and len(before.records)==128 and case.bridge._state is before
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(ticket,now=NOW)
 with pytest.raises(module.VoiceBridgeUnavailable):prepare(case)
 assert not case.captured


def test_session_expiry_blocks_delivery_even_when_text_permit_still_current(case):
 case.bridge.accept(segment());ticket=prepare(case)
 case.state['permission']=replace(case.state['permission'],expires_at=BASE+timedelta(days=1))
 with pytest.raises(module.VoiceBridgeUnavailable):case.bridge.deliver_review(ticket,now=BASE+timedelta(hours=1))
 assert not case.captured


def test_foreign_business_is_not_enabled_by_local_text_permit(case):
 spec=replace(SPEC,key=replace(KEY,business='B'))
 bridge=module.VoiceMeetingBridge(spec,current_permission=case.permission,sink=case.sink,enabled=True)
 with pytest.raises(module.VoiceBridgeUnavailable):bridge.accept(segment(key=spec.key))
 assert not case.captured
