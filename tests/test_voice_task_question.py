"""Admitted synthetic question -> actual private current task-card read only."""
from dataclasses import replace,fields,FrozenInstanceError
from datetime import datetime,timedelta,timezone,date
from io import BytesIO
from types import SimpleNamespace
import copy,json,sqlite3,wave
import pytest

from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
from goldway.intelligence import reader_key
from goldway.work_context import work_id
from goldway import task_card_command as cards
from goldway import voice_meeting_bridge as meeting
from goldway import synthetic_wav_bridge as wav_port
from goldway import voice_task_question as m

voice=meeting.voice
BASE=datetime(2026,10,20,12,tzinfo=timezone.utc)
NOW=(BASE+timedelta(seconds=10)).timestamp()
KEY=voice.SessionKey('GOLDWAY','synthetic-source','synthetic-session')
SPEC=voice.SessionSpec(KEY,BASE,BASE+timedelta(hours=1))
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']


class NoExternal:
    def propose(self,*a,**kw):pytest.fail('model forbidden')
    def call(self,*a,**kw):pytest.fail('provider forbidden')


def config():
    return dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name='Synthetic '+x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=False,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[]))


def frame(sequence=1,**changes):
    values=dict(id='frame-'+str(sequence),key=KEY,sequence=sequence,epoch=0,at=BASE+timedelta(seconds=sequence+1),evidence_id='question-evidence-'+str(sequence),segment_id='question',revision=1,start=BASE,end=BASE+timedelta(seconds=1),final=True,text='Покажи задачу №1',speaker_label=None)
    values.update(changes);return voice.Segment(**values)


def ref(**changes):
    values=dict(key=KEY,epoch=0,segment_id='question',revision=1);values.update(changes)
    return meeting.FinalSegmentRef(**values)


@pytest.fixture
def c(monkeypatch):
    state={'now':NOW,'permission':meeting.TextReviewPermission(KEY,'test_operator',1,BASE,BASE+timedelta(hours=1),True)}
    store=Store(':memory:');engine=Engine(store,config(),NoExternal(),lambda:state['now']);bundles=[]
    def permission():return state['permission']
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=bundles.append,enabled=True)
    def forbidden(*args,**kwargs):pytest.fail('unexpected constructor/network/file/context/refresh/queue')
    engine._context=forbidden;engine.intelligence.sources.refresh=forbidden;store.queue=forbidden
    import builtins,socket,subprocess
    for cls in (Store,Engine,KnowledgeIndex):monkeypatch.setattr(cls,'__init__',forbidden)
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    value=SimpleNamespace(e=engine,s=store,state=state,bridge=bridge,bundles=bundles,permission=permission)
    yield value
    store.db.set_authorizer(None);store.db.close()


def seed(c,**changes):
    values=dict(business='GOLDWAY',id='opaque-task',title='Canonical task title',owner='alen',deadline='2026-10-21',next_step='Recorded next step',criterion='Recorded criterion',status='confirmed',source_id='opaque-source',evidence='DO NOT READ EVIDENCE',created=NOW)
    values.update(changes)
    c.s.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,0)',(values['business'],values['source_id'],'DO NOT READ BODY','DO NOT READ URL'))
    c.s.execute('INSERT INTO tasks('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
    return c.s.execute('SELECT public_number FROM tasks WHERE business=? AND id=?',(values['business'],values['id'])).fetchone()[0]


def document(c):
    c.e.intelligence.index.upsert(dict(source_id='doc-source',document_id='doc',title='DO NOT READ TITLE',url='https://example.invalid/no',text='DO NOT READ BODY',readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW-5,permission_expires_at=NOW+100,confirmed=True))
    chunk=c.s.execute('SELECT id FROM knowledge_chunks').fetchone()[0]
    c.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','opaque-task',json.dumps([chunk])))
    return chunk


def driver(c,**changes):
    values=dict(engine=c.e,bridge=c.bridge,sender='test_operator',enabled=True);values.update(changes)
    return m.VoiceTaskQuestion(**values)


def read(c,reader=None,reference=None,number=1):
    return (reader or driver(c)).read_question(reference or ref(),number)


def readonly(c):
    reads=[]
    def authorizer(action,table,column,*unused):
        if action==sqlite3.SQLITE_READ:
            reads.append((table,column))
            if (table=='tasks' and column=='evidence') or (table=='sources' and column in ('body','link')) or (table in ('knowledge_chunks','knowledge_documents') and column in ('text','title','url')):return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    c.s.db.set_authorizer(authorizer);return reads


def test_actual_synthetic_wav_question_then_two_real_task_cards_private_no_writes(c,monkeypatch):
    seed(c);chunk=document(c);calls=[];actual=cards.build_task_card
    def build(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(result);return result
    monkeypatch.setattr(cards,'build_task_card',build)
    buffer=BytesIO()
    with wave.open(buffer,'wb') as stream:
        stream.setparams((1,2,16000,16000,'NONE','not compressed'));stream.writeframes(b'\0'*32000)
    transcription=[]
    def callback(request):transcription.append(request);return wav_port.TranscriptionResult('Покажи задачу №1',True)
    port=wav_port.SyntheticWavBridge(bridge=c.bridge,transcribe=callback,clock=lambda:datetime.fromtimestamp(c.state['now'],timezone.utc),enabled=True)
    request=wav_port.WavSegmentEnvelope('wav-question',KEY,1,0,BASE+timedelta(seconds=2),'question-evidence','question',1,BASE,BASE+timedelta(seconds=1),None)
    reference=port.accept_wav(request,buffer.getvalue())
    before=tuple(c.s.db.iterdump());changes=c.s.db.total_changes;records=c.bridge._state;reads=readonly(c)
    answer=read(c,reference=reference)
    assert c.s.db.total_changes==changes and c.bridge._state is records and c.bundles==[]
    assert len(transcription)==1 and len(calls)==2 and calls[0]==calls[1] and answer.text==calls[0].text
    assert answer.intended_reader=='test_operator' and answer.task_id=='opaque-task'
    assert answer.task_evidence_ids==tuple(sorted((work_id('GOLDWAY','opaque-task'),chunk)))
    assert answer.task_snapshot_digest==calls[0].snapshot_digest
    assert answer.question.reference==reference and answer.question.evidence_ids==('question-evidence',)
    assert answer.review_required and not answer.live_authorized and not answer.broadcast_authorized and not answer.delivery_authorized and not answer.capture_authorized
    assert 'Canonical task title' in answer.text and 'состояние записи' in answer.text
    assert all(value not in answer.text for value in ('opaque-task','opaque-source',KEY.session_id,KEY.source_id,chunk))
    assert ('tasks','title') in reads
    with pytest.raises(FrozenInstanceError):answer.text='different'
    c.s.db.set_authorizer(None)
    assert tuple(c.s.db.iterdump())==before


@pytest.mark.parametrize('text',['Покажи задачу №1','  пОкАЖи  ЗАДАЧУ  №1  ','Покажи\nзадачу №1'])
def test_direct_literal_question_case_whitespace_normalization_only(c,text):
    seed(c);c.bridge.accept(frame(text=text));assert 'Canonical task title' in read(c).text


@pytest.mark.parametrize('text',['Покажи задачу №2','Покажи задачу №1?','Покажи задачу','Покажи задачу номер 1',
    'Измени задачу №1','Покажи задачу №1 и подтверди её','Кто отвечает за задачу №1','x'*129])
def test_unsupported_or_number_mismatch_before_task_display_or_builder(c,monkeypatch,text):
    seed(c);c.bridge.accept(frame(text=text))
    def fail(*a,**kw):pytest.fail('unsupported question reached task builder')
    monkeypatch.setattr(cards,'build_task_card',fail);reads=readonly(c)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert ('tasks','title') not in reads


@pytest.mark.parametrize('number',[None,True,0,-1,1000000000,'1',1.0])
def test_explicit_number_is_exact_integer_before_source_or_task(c,monkeypatch,number):
    def fail(*a,**kw):pytest.fail('invalid number reached source')
    monkeypatch.setattr(c.bridge,'selected_final',fail)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c,number=number)


@pytest.mark.parametrize('enabled',[False,None,0,1,'true'])
def test_default_disabled_consumer_no_source_clock_or_builder(c,monkeypatch,enabled):
    def fail(*a,**kw):pytest.fail('disabled consumer called host')
    monkeypatch.setattr(c.bridge,'selected_final',fail);c.e.now=fail
    value=m.VoiceTaskQuestion(engine=c.e,bridge=c.bridge,sender='test_operator',enabled=enabled)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):value.read_question(ref(),1)
    assert c.bridge._state is None


def test_selected_final_no_lazy_state_or_pending_mutation_no_full_transcript(c):
    with pytest.raises(meeting.VoiceBridgeUnavailable):c.bridge.selected_final(ref(),now=BASE+timedelta(seconds=10))
    assert c.bridge._state is None and c.bridge._pending is None
    c.bridge.accept(frame(text='Unselected private synthetic final',segment_id='other'))
    c.bridge.accept(frame(2,segment_id='question',text='Покажи задачу №1'))
    selected=c.bridge.selected_final(ref(),now=BASE+timedelta(seconds=10));state=c.bridge._state
    ticket=c.bridge.prepare_review(meeting.ReviewSelection((ref(),),()),on=date(2026,10,20),now=BASE+timedelta(seconds=10))
    pending=c.bridge._pending
    again=c.bridge.selected_final(ref(),now=BASE+timedelta(seconds=10))
    assert again==selected and c.bridge._state is state and c.bridge._pending is pending
    assert selected.text=='Покажи задачу №1' and 'Unselected' not in repr(selected)
    assert selected.evidence_ids==('question-evidence-2',) and selected.reviewer=='test_operator'
    assert not any(f.name in ('records','transcript','permission') for f in fields(selected))
    with pytest.raises(FrozenInstanceError):selected.text='changed'
    c.bridge.deliver_review(ticket,now=BASE+timedelta(seconds=10));assert len(c.bundles)==1


@pytest.mark.parametrize('kind',['partial','revised','epoch','cross_session','cross_source','cross_business','missing','badtype','expired','disabled','revoked'])
def test_source_selection_boundaries_deny_before_task_read(c,kind):
    seed(c);reference=ref()
    c.bridge.accept(frame(final=kind!='partial'))
    if kind=='revised':c.bridge.accept(frame(2,revision=2,text='Покажи задачу №1'))
    elif kind=='epoch':
        c.bridge.accept(voice.Control('interrupt',KEY,2,0,BASE+timedelta(seconds=3),'interrupt-evidence','interrupt',True))
        c.bridge.accept(voice.Control('resume',KEY,3,1,BASE+timedelta(seconds=4),'resume-evidence','resume',True))
    elif kind=='cross_session':reference=ref(key=replace(KEY,session_id='other'))
    elif kind=='cross_source':reference=ref(key=replace(KEY,source_id='other'))
    elif kind=='cross_business':reference=ref(key=replace(KEY,business='OTHER'))
    elif kind=='missing':reference=ref(segment_id='missing')
    elif kind=='badtype':reference=ref(epoch=True)
    elif kind=='expired':c.state['now']=(BASE+timedelta(hours=1)).timestamp()
    elif kind=='disabled':c.bridge._enabled=False
    elif kind=='revoked':c.state['permission']=replace(c.state['permission'],revoked=True)
    reads=readonly(c)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c,reference=reference)
    assert ('tasks','title') not in reads and c.bundles==[]


@pytest.mark.parametrize('change',['reviewer','employee','no_intel','admins','team','business','wrong_session_business'])
def test_current_soleadmin_audience_is_explicit_not_speaker_role(c,change):
    seed(c);c.bridge.accept(frame(speaker_label='test_operator'));sender='test_operator'
    if change=='reviewer':c.state['permission']=replace(c.state['permission'],reviewer='alen')
    elif change=='employee':sender='alen';c.state['permission']=replace(c.state['permission'],reviewer='alen')
    elif change=='no_intel':c.e.intelligence=None
    elif change=='admins':c.e.c['admins']=['test_operator','alen']
    elif change=='team':del c.e.c['team_intake']
    elif change=='business':c.e.b='OTHER'
    elif change=='wrong_session_business':c.e.c['business']='OTHER';c.e.b='OTHER'
    reads=readonly(c)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c,reader=driver(c,sender=sender))
    assert ('tasks','title') not in reads


def test_normal_task_refs_separate_from_question_speaker_provenance(c):
    seed(c);c.bridge.accept(voice.Speaker('speaker',KEY,1,0,BASE+timedelta(seconds=1),'speaker-evidence','A','synthetic-participant',True))
    c.bridge.accept(frame(2,speaker_label='A'));answer=read(c)
    assert answer.question.evidence_ids==('question-evidence-2','speaker-evidence')
    assert answer.task_evidence_ids==(work_id('GOLDWAY','opaque-task'),)
    assert answer.intended_reader=='test_operator' and 'synthetic-participant' not in answer.text


@pytest.mark.parametrize('kind',['source_deleted','doc_revoke','doc_expiry','dependency_missing','dependency_doc_denied','missing_task'])
def test_actual_task_acl_and_source_boundary_before_display(c,kind):
    seed(c);chunk=document(c);c.bridge.accept(frame())
    if kind=='source_deleted':c.s.execute("UPDATE sources SET deleted=1 WHERE business='GOLDWAY'")
    elif kind=='doc_revoke':c.s.execute('DELETE FROM knowledge_readers')
    elif kind=='doc_expiry':c.s.execute('UPDATE knowledge_documents SET permission_expires_at=?',(NOW,))
    elif kind=='dependency_missing':c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps([work_id('GOLDWAY','missing')]),))
    elif kind=='dependency_doc_denied':
        seed(c,id='dependency',source_id='dependency-source',title='Hidden dependency')
        c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE task_id=?',(json.dumps([work_id('GOLDWAY','dependency')]),'opaque-task'))
        c.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','dependency',json.dumps([chunk])))
        c.s.execute('DELETE FROM knowledge_readers')
    elif kind=='missing_task':c.s.execute("DELETE FROM tasks WHERE business='GOLDWAY'")
    reads=readonly(c)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert ('tasks','title') not in reads


@pytest.mark.parametrize('change',['source_deleted','revision','title','deadline','criterion','next_step','acl','dependency'])
def test_change_between_actual_two_card_builders_refuses_whole_answer(c,monkeypatch,change):
    seed(c);document(c);c.bridge.accept(frame());actual=cards.build_task_card;calls=[]
    def build(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(result)
        if len(calls)==1:
            if change=='source_deleted':c.s.execute("UPDATE sources SET deleted=1 WHERE business='GOLDWAY'")
            elif change=='revision':c.s.execute("UPDATE tasks SET revision=revision+1 WHERE id='opaque-task'")
            elif change in ('title','deadline','criterion','next_step'):c.s.execute('UPDATE tasks SET '+change+'=? WHERE id=?',('changed','opaque-task'))
            elif change=='acl':c.s.execute('DELETE FROM knowledge_readers')
            elif change=='dependency':c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps([work_id('GOLDWAY','missing')]),))
        return result
    monkeypatch.setattr(cards,'build_task_card',build)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert c.bundles==[]


@pytest.mark.parametrize('change',['interrupt','permission','reviewer','revision','state_advance'])
def test_source_change_during_task_read_rejected_by_final_public_selection(c,monkeypatch,change):
    seed(c);c.bridge.accept(frame());actual=cards.build_task_card;calls=[]
    def build(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(1)
        if len(calls)==1:
            if change=='interrupt':c.bridge.accept(voice.Control('interrupt',KEY,2,0,BASE+timedelta(seconds=3),'interrupt-evidence','interrupt',True))
            elif change=='permission':c.state['permission']=replace(c.state['permission'],revoked=True)
            elif change=='reviewer':c.state['permission']=replace(c.state['permission'],reviewer='alen')
            elif change=='revision':c.bridge.accept(frame(2,revision=2))
            elif change=='state_advance':c.bridge.accept(voice.Speaker('speaker',KEY,2,0,BASE+timedelta(seconds=3),'speaker-evidence','B',None,False))
        return result
    monkeypatch.setattr(cards,'build_task_card',build)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert c.bundles==[]


@pytest.mark.parametrize('change',['config','index','intel','store','connection','clock','business','admins','sender','bridge','method','builder','policy'])
def test_captured_host_or_policy_drift_after_actual_first_build(c,monkeypatch,change):
    seed(c);c.bridge.accept(frame());actual=cards.build_task_card;calls=[];original_db=c.s.db
    def build(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(1)
        if len(calls)==1:
            if change=='config':c.e.c=copy.deepcopy(c.e.c)
            elif change=='index':c.e.intelligence.index=object()
            elif change=='intel':c.e.intelligence=object()
            elif change=='store':c.e.s=object()
            elif change=='connection':c.s.db=object()
            elif change=='clock':c.e.now=lambda:NOW
            elif change=='business':c.e.b='OTHER'
            elif change=='admins':c.e.c['admins']=['alen']
            elif change=='sender':value._sender='alen'
            elif change=='bridge':value._bridge=object()
            elif change=='method':c.bridge.selected_final=lambda *a,**k:None
            elif change=='builder':monkeypatch.setattr(cards,'build_task_card',lambda *a,**k:None)
            elif change=='policy':c.e.c['employees']['alen']['name']='changed'
        return result
    monkeypatch.setattr(cards,'build_task_card',build);value=driver(c)
    try:
        with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c,reader=value)
    finally:c.s.db=original_db
    assert c.bundles==[]


@pytest.mark.parametrize('value',[float('nan'),float('inf'),True,-1,2**60,None])
def test_invalid_clock_before_task_transfer(c,value):
    seed(c);c.bridge.accept(frame());c.state['now']=value;reads=readonly(c)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert ('tasks','title') not in reads


def test_backward_clock_and_whole_text_asof_change_are_not_silently_ignored(c,monkeypatch):
    seed(c);c.bridge.accept(frame());actual=cards.build_task_card;calls=[]
    def build(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(1)
        if len(calls)==1:c.state['now']-=1
        return result
    monkeypatch.setattr(cards,'build_task_card',build)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)


def test_changed_rendered_timestamp_is_compared_not_only_revision(c,monkeypatch):
    seed(c);c.bridge.accept(frame());actual=cards.build_task_card;calls=[]
    def build(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(1)
        if len(calls)==1:c.state['now']+=60
        return result
    monkeypatch.setattr(cards,'build_task_card',build)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert len(calls)==2


@pytest.mark.parametrize('status',['proposed','confirmed','in_progress','blocked','completed'])
def test_actual_card_preserves_all_admin_visible_record_states_no_result_claim(c,status):
    seed(c,status=status,deadline=None);c.bridge.accept(frame());answer=read(c)
    assert 'Canonical task title' in answer.text and 'результат независимо не проверен' in answer.text
    assert c.s.execute('SELECT status FROM tasks').fetchone()[0]==status


@pytest.mark.parametrize('change',['unicode_bytes','output_units','register_limit','fatal_reader'])
def test_whole_caps_and_reader_failure_not_partial_output(c,monkeypatch,change):
    seed(c);c.bridge.accept(frame())
    if change=='unicode_bytes':c.s.execute("UPDATE tasks SET title=?",('界'*2800,))
    elif change=='output_units':c.s.execute("UPDATE tasks SET title=?",('😀'*1800,))
    elif change=='register_limit':
        for i in range(128):seed(c,id='extra-'+str(i),source_id='extra-source-'+str(i))
    elif change=='fatal_reader':
        def fail(*a,**k):raise cards.CardUnavailable()
        monkeypatch.setattr(cards,'build_task_card',fail)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert c.bundles==[]


def test_selected_final_reentrant_permission_control_is_preserved_but_selection_denied(c):
    c.bridge.accept(frame());called=[]
    def permission():
        if not called:
            called.append(1)
            c.bridge.accept(voice.Control('interrupt',KEY,2,0,BASE+timedelta(seconds=3),'interrupt-evidence','interrupt',True))
        return c.state['permission']
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=c.bundles.append,enabled=True)
    # Adopt accepted source via actual accept before switching to the reentrant
    # callback; the host identity is captured for that exact callback at init.
    c.bridge=bridge
    called.append(1);bridge.accept(frame());called.clear()
    with pytest.raises(meeting.VoiceBridgeUnavailable):bridge.selected_final(ref(),now=BASE+timedelta(seconds=10))
    assert bridge._state.mode is voice.Mode.HALTED and bridge._state.records[-1].kind=='interrupt'


def test_selected_final_callback_pending_ticket_replacement_detected(c):
    called=[];state={'during':False};bridge=None
    def permission():
        if state['during'] and not called:
            called.append(1)
            bridge.prepare_review(meeting.ReviewSelection((ref(),),()),on=date(2026,10,20),now=BASE+timedelta(seconds=10))
        return c.state['permission']
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=c.bundles.append,enabled=True)
    bridge.accept(frame());state['during']=True
    with pytest.raises(meeting.VoiceBridgeUnavailable):bridge.selected_final(ref(),now=BASE+timedelta(seconds=10))
    assert bridge._pending is not None and c.bundles==[]


def test_no_question_or_task_mutation_even_on_repeated_private_reads(c):
    seed(c);c.bridge.accept(frame());before=tuple(c.s.db.iterdump());record=c.bridge._state
    first=read(c);second=read(c)
    assert first==second and tuple(c.s.db.iterdump())==before and c.bridge._state is record and c.bridge._pending is None


@pytest.mark.parametrize('change',['doc_acl','source_deleted','task_revision','task_title','task_acl','unchanged'])
def test_last_selected_permission_callback_task_mutation_requires_final_current_metadata(c,monkeypatch,change):
    seed(c);document(c);cards_built=[];fired=[];metadata=[]
    actual_card=cards.build_task_card;actual_metadata=cards.build_task_brief_source
    def build(*args,**kwargs):
        result=actual_card(*args,**kwargs);cards_built.append(result);return result
    def proof(*args,**kwargs):
        metadata.append('after-callback' if fired else 'before-callback')
        return actual_metadata(*args,**kwargs)
    def permission():
        if len(cards_built)==2 and not fired:
            fired.append(change)
            if change=='doc_acl':c.s.execute('DELETE FROM knowledge_readers')
            elif change=='source_deleted':c.s.execute("UPDATE sources SET deleted=1 WHERE id='opaque-source'")
            elif change=='task_revision':c.s.execute("UPDATE tasks SET revision=revision+1 WHERE id='opaque-task'")
            elif change=='task_title':c.s.execute("UPDATE tasks SET title='New title' WHERE id='opaque-task'")
            elif change=='task_acl':c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps([work_id('GOLDWAY','missing')]),))
        return c.state['permission']
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=c.bundles.append,enabled=True)
    bridge.accept(frame());c.bridge=bridge
    monkeypatch.setattr(cards,'build_task_card',build)
    monkeypatch.setattr(cards,'build_task_brief_source',proof)
    value=driver(c)
    if change=='unchanged':
        answer=read(c,reader=value)
        assert answer.text==cards_built[0].text
    else:
        with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c,reader=value)
    assert fired==[change] and len(cards_built)==2 and metadata==['after-callback'] and c.bundles==[]


def test_final_metadata_api_identity_drift_from_last_permission_callback_denies(c,monkeypatch):
    seed(c);cards_built=[];actual=cards.build_task_card
    def build(*args,**kwargs):
        result=actual(*args,**kwargs);cards_built.append(result);return result
    def permission():
        if len(cards_built)==2:
            monkeypatch.setattr(cards,'build_task_brief_source',lambda *args,**kwargs:pytest.fail('replacement metadata method used'))
        return c.state['permission']
    bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=c.bundles.append,enabled=True)
    bridge.accept(frame());c.bridge=bridge
    monkeypatch.setattr(cards,'build_task_card',build)
    with pytest.raises(m.VoiceTaskQuestionUnavailable):read(c)
    assert len(cards_built)==2
