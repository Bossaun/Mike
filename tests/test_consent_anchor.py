"""Prospective actual Engine/Updates/synthetic Dispatcher consent boundary."""
import copy
import json
import math
import sqlite3
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher, Updates
from goldway.intelligence import reader_key
from goldway.dialogue import scope_from_payload
import goldway.consent_anchor as consent

MEMBERS=['test_operator','frost','alen','nastya','dasha_it']


def result(actions=(), *, offer=None, task=None):
    return dict(intent='task_action' if actions else 'conversation',actions=list(actions),
        summary='Synthetic result',reply_text='Синтетический ответ; подтвердите предложение.',
        knowledge_mode='not_needed',evidence_ids=[],
        dialogue_state=dict(topic='Synthetic',entities=[],pending_offer=offer,task_id=task))


class Model:
    def __init__(self, proposal, callback=None):
        self.proposal=proposal;self.calls=[];self.callback=callback
    def propose(self, context):
        self.calls.append(copy.deepcopy(context))
        if self.callback:self.callback()
        return copy.deepcopy(self.proposal)


@pytest.fixture
def case(monkeypatch):
    config=dict(business='GOLDWAY',timezone='Europe/Moscow',
        employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},
        admins=['test_operator'],business_account_user_id=99,
        team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],
        team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,
        sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,
        allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,
        intelligence=dict(enabled=True,sources=[]))
    clock=[1792177200.25];store=Store(':memory:')
    engine=Engine(store,config,None,lambda:clock[0]);engine.intelligence.sources.refresh=lambda *a,**k:{}
    class API:
        def __init__(self):self.calls=[]
        def call(self,method,**params):
            self.calls.append((method,params))
            if method=='getBusinessConnection':return dict(user=dict(id=99),is_enabled=True,rights=dict(can_reply=True))
            assert method=='sendMessage'
            return dict(message_id=100+len(self.calls))
    api=API();dispatcher=Dispatcher(store,config,api,lambda:clock[0])
    for key,person in config['employees'].items():
        store.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',
            ('GOLDWAY',key,str(person['telegram_user_id']),str(person['telegram_user_id']),'conn',clock[0]))
    import builtins,socket,httpx
    def fail(*a,**k):pytest.fail('external IO')
    monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
    c=SimpleNamespace(e=engine,s=store,c=config,clock=clock,d=dispatcher,api=api)
    yield c
    store.db.close()


def inbound(c, text, number, *, channel='bot', sender='test_operator', at=None, connection='conn'):
    uid=c.c['employees'][sender]['telegram_user_id']
    message=dict(message_id=number,date=int(c.clock[0]) if at is None else at,
        chat=dict(id=uid,type='private'),**{'from':dict(id=uid,is_bot=False)},text=text)
    if channel=='business':message['business_connection_id']=connection
    Updates(c.e,c.api,100).handle(dict(update_id=number,**{'business_message' if channel=='business' else 'message':message}))
    return 'update:'+str(number)


def prompt(c, *, channel='bot', send=True, kind='confirm_task'):
    actions=[dict(op='create',title='Synthetic task',next_step='Synthetic next',criterion='Synthetic result',status='proposed',evidence_lines=[1])]
    c.e.model=Model(result(actions,offer=dict(kind=kind,text='Подтвердить синтетический черновик')))
    event=inbound(c,'Создай задачу: Synthetic task.',10,channel=channel)
    c.e.process(event)
    if send:c.d.flush()
    task=c.s.rows('SELECT * FROM tasks')[0]
    c.clock[0]+=1
    return task


def accept(c, task, text='Да', *, number=20, channel='bot', sender='test_operator', at=None, proposal=None, callback=None):
    actions=[dict(op='update',task_id=task['id'],status='confirmed',set_fields=['status'],evidence_lines=[1])]
    c.e.model=Model(proposal if proposal is not None else result(actions,task=task['id']),callback)
    event=inbound(c,text,number,channel=channel,sender=sender,at=at)
    c.e.process(event)
    return c.e.model,event


def assert_denied(c,task,event,model):
    assert c.s.rows('SELECT status,revision FROM tasks WHERE id=?',(task['id'],)) == [dict(status=task['status'],revision=task['revision'])]
    assert not model.calls
    assert c.s.rows('SELECT state,error FROM events WHERE id=?',(event,))==[dict(state='done',error=None)]
    rows=c.s.rows('SELECT * FROM outbox WHERE dedupe=?',('short-consent:'+event,))
    assert len(rows)==1 and rows[0]['text']==consent.NOTICE and rows[0]['task_id'] is None


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_delivered_proposal_phase_unavailable_confirms_exact_draft(case,channel):
    task=prompt(case,channel=channel)
    assert case.s.rows('SELECT phase FROM answer_citation_turns')==[dict(phase='unavailable')]
    model,event=accept(case,task,channel=channel)
    assert len(model.calls)==1
    after=case.s.rows('SELECT status,revision FROM tasks WHERE id=?',(task['id'],))[0]
    assert after==dict(status='confirmed',revision=task['revision']+1)
    assert '_state_provenance' not in model.calls[0]['dialogue']
    case.d.flush()
    assert case.s.rows('SELECT state FROM events WHERE id=?',(event,))==[dict(state='done')]


@pytest.mark.parametrize('text',['Да',' да! ','ДА.'])
def test_same_second_delivery_floor_allowed(case,text):
    task=prompt(case)
    case.clock[0]-=1  # delivery at .25; Telegram original date is integer second.
    model,_=accept(case,task,text)
    assert len(model.calls)==1 and case.s.rows('SELECT status FROM tasks')[0]['status']=='confirmed'


@pytest.mark.parametrize('kind',['pending','recorded_unsent','unavailable_unsent','failed'])
def test_intervening_accepted_turn_never_uses_old_offer(case,kind):
    task=prompt(case)
    event=inbound(case,'Напиши синтетический черновик другого сообщения',15)
    if kind!='pending':
        if kind=='recorded_unsent':case.e.model=Model(result(offer=dict(kind='explain',text='Новое предложение')))
        elif kind=='unavailable_unsent':case.e.model=Model(result([dict(op='update',task_id=task['id'],next_step='Другой шаг',set_fields=['next_step'],evidence_lines=[1])],task=task['id']))
        else:
            class Broken:
                def propose(self,*a):raise ValueError('synthetic')
            case.e.model=Broken()
        if kind=='failed':
            with pytest.raises(ValueError):case.e.process(event)
        else:case.e.process(event)
    # Snapshot AFTER the intervening legitimate edit, not fabricate no edits.
    task=case.s.rows('SELECT * FROM tasks')[0]
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


@pytest.mark.parametrize('state',['pending','sent','unknown','sending','blocked','cancelled'])
def test_new_service_summary_barrier(case,state):
    task=prompt(case)
    case.s.queue('GOLDWAY','test_operator','Synthetic service summary','service:summary',case.clock[0],preferred_transport='bot')
    if state=='sent':case.d.flush()
    else:case.s.execute('UPDATE outbox SET state=? WHERE dedupe=?',(state,'service:summary'))
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


@pytest.mark.parametrize('state',['pending','unknown','sending','blocked','retry'])
def test_prompt_not_actually_sent_denies(case,state):
    task=prompt(case,send=False)
    case.s.execute('UPDATE outbox SET state=?',(state,))
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


def test_old_original_timestamp_processed_after_new_prompt_denied(case):
    task=prompt(case)
    model,event=accept(case,task,at=int(case.clock[0])-2)
    assert_denied(case,task,event,model)


def test_missing_original_timestamp_trusted_direct_engine_clarifies(case):
    task=prompt(case)
    model=case.e.model=Model(result())
    event='direct-missing-time'
    case.e.reply(event,'test_operator','Да',source_id='telegram:1:30',channel='bot')
    case.e.process(event)
    assert_denied(case,task,event,model)


@pytest.mark.parametrize('change',['missing','future','duplicate'])
def test_successful_delivery_metadata_required(case,change):
    task=prompt(case)
    if change=='missing':case.s.execute('DELETE FROM deliveries')
    elif change=='future':case.s.execute('UPDATE deliveries SET at=?',(case.clock[0]+1,))
    else:case.s.execute("INSERT INTO deliveries(outbox_id,at,state,detail) SELECT outbox_id,at,'sent','synthetic duplicate' FROM deliveries")
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


@pytest.mark.parametrize('change',['channel','connection','recipient','business','thread','chat'])
def test_delivery_private_scope_must_match(case,change):
    task=prompt(case,channel='business')
    field,value={'channel':('transport','bot'),'connection':('destination_connection','different'),
        'recipient':('recipient','frost'),'business':('business','OTHER'),'thread':('destination_thread',2),
        'chat':('destination_chat','2')}[change]
    case.s.execute('UPDATE outbox SET '+field+'=?',(value,))
    model,event=accept(case,task,channel='business')
    assert_denied(case,task,event,model)


def test_current_task_source_permission_revoked(case):
    task=prompt(case)
    case.e.intelligence.index.upsert(dict(source_id='synthetic-source',document_id='synthetic-doc',title='Synthetic',
        url='https://example.invalid',text='Synthetic',readers=[reader_key('GOLDWAY','test_operator')],
        observed_at=case.clock[0]-2,permission_expires_at=case.clock[0]+100,confirmed=True))
    ref=case.s.rows('SELECT id FROM knowledge_chunks')[0]['id']
    case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE business=? AND task_id=?',(json.dumps([ref]),'GOLDWAY',task['id']))
    case.e.intelligence.index.revoke_source('synthetic-source',observed_at=case.clock[0])
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


@pytest.mark.parametrize('change',['revision','completed','legacy','missing_state','intelligence_business'])
def test_fresh_current_prospective_anchor_required(case,change):
    task=prompt(case)
    if change=='revision':case.s.execute('UPDATE tasks SET revision=revision+1')
    elif change=='completed':case.s.execute("UPDATE tasks SET status='completed'")
    elif change in ('legacy','missing_state'):
        key=case.s.rows("SELECT key FROM runtime WHERE key LIKE 'dialogue:v1:%'")[0]['key']
        doc=json.loads(case.s.rows('SELECT value FROM runtime WHERE key=?',(key,))[0]['value'])
        for entry in doc['pending']+doc['confirmed']+([doc['checkpoint']] if doc['checkpoint'] else []):
            if change=='legacy':entry.pop('anchor',None)
            else:entry['state']=None
        case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(doc),key))
    else:case.e.intelligence.b='OTHER'
    task=case.s.rows('SELECT * FROM tasks')[0]
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


def test_other_private_channel_does_not_replace_bot_prompt(case):
    task=prompt(case)
    other=inbound(case,'Напиши другое письмо',15,channel='business')
    case.e.model=Model(result());case.e.process(other);case.d.flush()
    model,_=accept(case,task)
    assert len(model.calls)==1 and case.s.rows('SELECT status FROM tasks')[0]['status']=='confirmed'


def test_no_old_offer_with_only_source_id_and_no_real_current_turn(case):
    task=prompt(case)
    payload=dict(sender='test_operator',text='Да',channel='bot',source_id='telegram:1:20',link='not-an-event',message_at=int(case.clock[0]))
    with pytest.raises(consent.ConsentAmbiguous):consent.resolve(case.e,'not-an-event',payload)


def test_apply_direct_short_yes_cannot_bypass_anchor(case):
    task=prompt(case)
    payload=dict(sender='test_operator',text='Да',channel='bot',source_id='telegram:1:20',link='no-current-barrier',message_at=int(case.clock[0]))
    action=dict(op='update',task_id=task['id'],status='confirmed',set_fields=['status'],evidence_lines=[1])
    context=dict(kind='operator_chat',task_intent='task_action',tasks=[task])
    with pytest.raises(consent.ConsentAmbiguous):case.e._apply(action,context,'synthetic','reply',payload)
    assert case.s.rows('SELECT status FROM tasks')[0]['status']=='proposed'


@pytest.mark.parametrize('op',['create','title','different_task','completed'])
def test_valid_anchor_cannot_authorize_other_action(case,op):
    task=prompt(case)
    action=dict(op='update',task_id=task['id'],status='confirmed',set_fields=['status'],evidence_lines=[1])
    if op=='create':action=dict(op='create',title='Invented',next_step='Invented',criterion='Invented',status='proposed',evidence_lines=[1])
    elif op=='title':action.update(title='Invented',set_fields=['status','title'])
    elif op=='different_task':action['task_id']='different'
    else:action['status']='completed'
    model,event=accept(case,task,proposal=result([action],task=task['id']))
    assert len(model.calls)==1
    assert case.s.rows('SELECT status,revision FROM tasks')==[dict(status='proposed',revision=task['revision'])]
    assert case.s.rows('SELECT text FROM outbox WHERE dedupe=?',('short-consent:'+event,))==[dict(text=consent.NOTICE)]


def test_explicit_stated_task_confirmation_unaffected_by_barrier(case):
    task=prompt(case)
    inbound(case,'Новый незавершённый вопрос',15)
    model,_=accept(case,task,'Подтверждаю задачу №1')
    assert len(model.calls)==1 and case.s.rows('SELECT status FROM tasks')[0]['status']=='confirmed'


def test_replay_fixed_clarification_no_extra_queue_or_send(case):
    task=prompt(case)
    inbound(case,'Новый вопрос',15)
    model,event=accept(case,task)
    assert_denied(case,task,event,model)
    case.d.flush();calls=len(case.api.calls);count=len(case.s.rows('SELECT id FROM outbox'))
    case.e.process(event);case.d.flush()
    assert len(case.api.calls)==calls and len(case.s.rows('SELECT id FROM outbox'))==count


def test_no_outbox_body_source_payload_reads_in_anchor(case):
    task=prompt(case)
    def authorizer(action,table,column,*_):
        if action==sqlite3.SQLITE_READ and ((table=='outbox' and column=='text') or
            (table=='sources' and column in ('body','link')) or (table=='events' and column=='payload')):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    event=inbound(case,'Да',20)
    payload=json.loads(case.s.rows('SELECT payload FROM events WHERE id=?',(event,))[0]['payload'])
    case.s.db.set_authorizer(authorizer)
    try:assert consent.resolve(case.e,event,payload).task_id==task['id']
    finally:case.s.db.set_authorizer(None)


def test_newer_inbound_accepted_during_model_forces_clarification(case):
    task=prompt(case)
    def intervening():inbound(case,'Поздний новый вопрос',21)
    model,event=accept(case,task,callback=intervening)
    assert len(model.calls)==1
    assert case.s.rows('SELECT status FROM tasks')[0]['status']=='proposed'
    assert case.s.rows('SELECT text FROM outbox WHERE dedupe=?',('short-consent:'+event,))==[dict(text=consent.NOTICE)]


def test_delivery_lookup_uses_added_index(case):
    plan=case.s.rows("EXPLAIN QUERY PLAN SELECT at FROM deliveries WHERE outbox_id=? AND state='sent'",(1,))
    assert any('consent_delivery_lookup' in row['detail'] and 'SEARCH' in row['detail'] for row in plan)


@pytest.mark.parametrize('text',['Давай','Давайте','Можно','Хорошо','Ок','Окей'])
def test_other_short_assent_never_upgrades_existing_simple_yes_contract(case,text):
    task=prompt(case)
    model,event=accept(case,task,text)
    assert_denied(case,task,event,model)


@pytest.mark.parametrize('change',['done','missing'])
def test_current_real_pending_engine_event_required(case,change):
    task=prompt(case)
    event=inbound(case,'Да',20)
    payload=json.loads(case.s.rows('SELECT payload FROM events WHERE id=?',(event,))[0]['payload'])
    if change=='done':case.s.execute("UPDATE events SET state='done' WHERE id=?",(event,))
    else:case.s.execute('DELETE FROM events WHERE id=?',(event,))
    with pytest.raises(consent.ConsentAmbiguous):consent.resolve(case.e,event,payload)


def test_latest_explanation_acceptance_conversation_still_works(case):
    # No task action: Davai accepts exactly the delivered explanation offer.
    offer=dict(kind='explain',text='Рассказать подробнее о синтетическом примере')
    case.e.model=Model(result(offer=offer))
    event=inbound(case,'Расскажи синтетический пример',10)
    case.e.process(event);case.d.flush();case.clock[0]+=1
    case.e.model=Model(result())
    event=inbound(case,'Давай',20);case.e.process(event)
    assert len(case.e.model.calls)==1 and not case.s.rows('SELECT * FROM tasks')
    assert case.e.model.calls[0]['dialogue']['pending_offer']==offer


def test_outbox_window_budget_does_not_scan_archive(case):
    task=prompt(case)
    for i in range(128):case.s.queue('OTHER','frost','Synthetic','filler:'+str(i),case.clock[0])
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


def test_explicit_owner_deadline_literal_update_unaffected(case):
    task=prompt(case)
    inbound(case,'Новый незавершённый вопрос',15)
    text='По задаче №1 назначь ответственного alen, срок 2026-10-20.'
    action=dict(op='update',task_id=task['id'],owner='alen',deadline='2026-10-20',
        owner_lines=[1],deadline_lines=[1],evidence_lines=[1],set_fields=['owner','deadline'])
    model,event=accept(case,task,text,proposal=result([action],task=task['id']))
    assert len(model.calls)==1
    assert case.s.rows('SELECT owner,deadline,status FROM tasks')==[dict(owner='alen',deadline='2026-10-20',status='proposed')]
    assert not case.s.rows('SELECT id FROM outbox WHERE dedupe=?',('short-consent:'+event,))


def full_action(task):
    return dict(op='update',task_id=task['id'],status='confirmed',title=None,owner=None,deadline=None,
        next_step=None,criterion=None,set_fields=['status'],evidence_lines=[1],owner_lines=[],deadline_lines=[])


def test_actual_schema_full_inert_status_only_update_confirms(case):
    task=prompt(case)
    action=full_action(task)
    # Same required action fields as actual response_schema, not sparse fixtures.
    from goldway.model import response_schema
    context=dict(intelligence=True,kind='operator_chat',tasks=[task],knowledge=dict(evidence=[]))
    schema=response_schema(context)['properties']['actions']['items']
    assert set(schema['required'])<=set(action)
    model,_=accept(case,task,proposal=result([action],task=task['id']))
    assert len(model.calls)==1 and case.s.rows('SELECT status FROM tasks')[0]['status']=='confirmed'


@pytest.mark.parametrize('field,value',[('title','Invented'),('owner','alen'),('deadline','2026-10-20'),
    ('next_step','Invented'),('criterion','Invented'),('owner_lines',[1]),('deadline_lines',[1])])
def test_full_envelope_noninert_edits_rejected(case,field,value):
    task=prompt(case)
    action=full_action(task);action[field]=value
    model,event=accept(case,task,proposal=result([action],task=task['id']))
    assert len(model.calls)==1 and case.s.rows('SELECT status FROM tasks')[0]['status']=='proposed'
    assert case.s.rows('SELECT text FROM outbox WHERE dedupe=?',('short-consent:'+event,))==[dict(text=consent.NOTICE)]


def test_model_confirm_offer_without_actual_host_task_card_denied(case):
    task=prompt(case)
    # A later ordinary conversation may emit a model confirm_task offer and
    # refer to an already permitted draft. Its sender reply has task_id=None.
    case.e.model=Model(result(offer=dict(kind='confirm_task',text='Рассказать подробнее?'),task=task['id']))
    event=inbound(case,'Просто расскажи о синтетическом примере',15)
    case.e.process(event);case.d.flush();case.clock[0]+=1
    assert case.s.rows("SELECT task_id FROM outbox WHERE dedupe=?",('clarify:'+event,))==[dict(task_id=None)]
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


def test_resolve_rejects_host_card_task_id_drift(case):
    task=prompt(case)
    case.s.execute('UPDATE outbox SET task_id=NULL')
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


def test_consent_acl_register_cap_denies_without_archive_scan(case):
    task=prompt(case)
    for i in range(128):
        case.s.execute('''INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created)
            VALUES('GOLDWAY',?,'Unrelated',NULL,'','','completed','synthetic',?)''',(str(i),case.clock[0]))
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


def test_explicit_reply_to_task_conversation_does_not_make_card_authority(case):
    task=prompt(case)
    case.e.model=Model(result(offer=dict(kind='confirm_task',text='Рассказать подробнее?'),task=task['id']))
    event='explicit-reply'
    case.e.reply(event,'test_operator','Объясни эту карточку',task_id=task['id'],source_id='telegram:1:15',
                 channel='bot',message_at=int(case.clock[0]))
    case.e.process(event);case.d.flush();case.clock[0]+=1
    assert case.s.rows('SELECT task_id FROM outbox WHERE dedupe=?',('clarify:'+event,))==[dict(task_id=None)]
    model,event=accept(case,task)
    assert_denied(case,task,event,model)


def test_task_id_alone_without_canonical_host_card_dedupe_not_authority(case):
    task=prompt(case)
    case.s.execute("UPDATE outbox SET dedupe='clarify:synthetic-service'")
    model,event=accept(case,task)
    assert_denied(case,task,event,model)
