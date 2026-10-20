"""New actual admission/register/local question/self-report integration boundaries."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace,MethodType
import json
import sqlite3

import pytest

from goldway import customer_touch_review_loop as module
from goldway import customer_touch_register as register_module
from goldway import customer_ingest as ingest_module
from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
from goldway.intelligence import Intelligence
from goldway.telegram import Dispatcher

core=module.core
NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']


class NoModel:
    def propose(self,*a,**kw):pytest.fail('local review invoked model')


class Provider:
    def __init__(self):self.pages=[];self.calls=0;self.allowed=False
    def fetch(self,cursor,limit=32):
        assert self.allowed,'review fetched provider'
        self.calls+=1;return self.pages.pop(0)


def event(identifier,**kw):
    row=dict(event_id=identifier,customer_id='customer',thread_id='thread',at=NOW-timedelta(hours=2),direction='outgoing',confirmed=True,reply_to=None)
    row.update(kw);return row


def window():return core.Window(NOW-timedelta(hours=1),NOW+timedelta(hours=2))
def permission(c,**kw):
    data=dict(id='permission',conversation=c.conv,event_id='permission',granted=True,opt_out=False,
        window=core.Window(NOW-timedelta(hours=3),NOW+timedelta(hours=3)));data.update(kw)
    return core.ContactPermission(**data)


def plan(c,identifier='plan1',**kw):
    data=dict(id=identifier,conversation=c.conv,event_id='basis',owner_id='test_operator',permission_id='permission',readiness=True,
        window=window(),draft=ingest_module.REVIEW_DRAFT);data.update(kw)
    return core.TouchPlan(**data)


@pytest.fixture
def c(tmp_path,monkeypatch):
    config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={m:dict(name='Synthetic '+m,telegram_user_id=i+1) for i,m in enumerate(MEMBERS)},
        admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),
        allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=False,review_mode=True,
        sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,
        allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[]))
    path=tmp_path/'synthetic.sqlite';s=Store(str(path));clock_state={'now':NOW,'callback':None}
    def clock():
        if clock_state['callback']:clock_state['callback']()
        return clock_state['now'].timestamp()
    e=Engine(s,config,NoModel(),clock);provider=Provider()
    source=ingest_module.SourceRegistration('GOLDWAY','fake','synthetic','synthetic-account',1,NOW-timedelta(days=2),NOW+timedelta(days=2))
    state={'source':source,'callback':None}
    def current():
        if state['callback']:state['callback']()
        return state['source']
    result=SimpleNamespace(s=s,e=e,config=config,path=path,p=provider,clock=clock,clock_state=clock_state,source=source,state=state,current=current,cursor=None)
    result.conv=core.Conversation('customer','synthetic','thread',source.source_id)
    result.ingest=ingest_module.CustomerIngest(store=s,current_source=current,provider=provider,enabled=True)
    def bind(**kwargs):
        result.r=register_module.CustomerTouchRegister(store=result.s,ingest=result.ingest,source=source,conversation=result.conv,enabled=True)
        result.loop=module.TouchReviewLoop(engine=result.e,register=result.r,source=source,conversation=result.conv,enabled=True,**kwargs)
    result.bind=bind
    def admit(rows,now=NOW):
        provider.pages.append(dict(mode='incremental',cursor_before=result.cursor,cursor_after='cursor'+str(provider.calls+1),events=rows))
        provider.allowed=True
        try:r=result.ingest.ingest_once(now=now)
        finally:provider.allowed=False
        result.cursor=r.cursor
    result.admit=admit
    admit([event('basis'),event('permission',direction='incoming',at=NOW-timedelta(minutes=90)),
        event('out1',at=NOW-timedelta(minutes=10)),event('out2',at=NOW-timedelta(minutes=9)),
        event('wrong-incoming',direction='incoming',at=NOW-timedelta(minutes=8)),event('outside',at=NOW-timedelta(hours=2))])
    bind();result.r.record_permission(permission(result),now=NOW);result.r.record_plan(plan(result),now=NOW)
    result.payload=dict(sender='test_operator',channel='bot',source_id='telegram:1:10')
    originals={cls:cls.__init__ for cls in (Store,Engine,KnowledgeIndex,Intelligence,Dispatcher)}
    Store_init=Store.__init__;Engine_init=Engine.__init__
    def reopen():
        result.s.db.close();result.s=object.__new__(Store);Store_init(result.s,str(path))
        result.e=object.__new__(Engine)
        with monkeypatch.context() as restore:
            for cls,init in originals.items():restore.setattr(cls,'__init__',init)
            Engine_init(result.e,result.s,config,NoModel(),clock)
        result.ingest=ingest_module.CustomerIngest(store=result.s,current_source=current,provider=provider,enabled=True);bind()
    result.reopen=reopen
    def forbidden(*a,**kw):pytest.fail('unexpected constructor/network/model/send')
    for cls in (Store,Engine,KnowledgeIndex,Intelligence,Dispatcher):monkeypatch.setattr(cls,'__init__',forbidden)
    yield result
    result.s.db.set_authorizer(None)
    if result.s.db.in_transaction:result.s.db.rollback()
    result.s.db.close()


def prepare(c,**kw):
    data=dict(now=NOW,reporting_window=window(),plan_ids=('plan1',),expires_at=NOW+timedelta(hours=1));data.update(kw)
    return c.loop.prepare(c.payload,**data)


def submit(c,q,**kw):
    data=dict(now=NOW,question_id=q.question_id,reported_count=1,selected_ordinals=(1,),outgoing_event_ids=('out1',));data.update(kw)
    return c.loop.submit(c.payload,**data)


def denied(call):
    with pytest.raises(module.TouchReviewUnavailable,match='^customer_touch_review_unavailable$'):call()


def register_raw(c):
    return c.s.execute('SELECT value FROM runtime WHERE key=?',(c.r._scope()[1],)).fetchone()[0]


def questions(c):return c.s.rows("SELECT key,value FROM runtime WHERE key LIKE 'customer-touch-question:v1:%'")


def test_actual_admitted_anchor_question_selfreport_uncertain_and_replay(c,monkeypatch):
    called=[];actual=core.reconcile_touches
    def reconcile(*a,**kw):called.append(kw);return actual(*a,**kw)
    monkeypatch.setattr(core,'reconcile_touches',reconcile)
    ingest_before=c.s.execute('SELECT value FROM runtime WHERE key=?',(c.source.runtime_key,)).fetchone()[0]
    old=json.loads(register_raw(c));q=prepare(c);question=json.loads(questions(c)[0]['value'])
    assert q.item_count==1 and q.requires_review and not q.live_authorized
    assert 'Касание 1' in q.text and 'локальный черновик' in q.text and 'customer' not in q.text
    assert q.question_id not in q.text and 'test_operator' not in q.text and question['state']=='pending'
    assert prepare(c)==q
    ack=submit(c,q);saved=register_raw(c);state=json.loads(saved)
    assert ack.summary.uncertain_ids==('plan1',) and ack.summary.confirmed_ids==() and not ack.replayed
    assert ack.requires_review and not ack.live_authorized and 'не подтверждены' in ack.text
    assert state['revision']==old['revision']+1 and len(state['receipts'])==1
    assert state['receipts'][0]['payload']['confirmed'] is False and state['receipts'][0]['payload']['event_id']=='out1'
    assert submit(c,q).replayed and register_raw(c)==saved
    assert c.s.execute('SELECT value FROM runtime WHERE key=?',(c.source.runtime_key,)).fetchone()[0]==ingest_before
    assert c.p.calls==1 and called and json.loads(questions(c)[0]['value'])['state']=='reported'
    for table in ('tasks','outbox','sources','usage','task_status_history'):
        assert not c.s.rows('SELECT 1 FROM '+table+' LIMIT 1')
    with pytest.raises(FrozenInstanceError):q.text='changed'


def test_zero_closes_only_selfreport_and_replay(c):
    before=register_raw(c);q=prepare(c);ack=submit(c,q,reported_count=0,selected_ordinals=(),outgoing_event_ids=())
    assert ack.summary.pending_ids==('plan1',) and not ack.summary.uncertain_ids and register_raw(c)==before
    assert 'Квитанции касаний не созданы' in ack.text
    assert submit(c,q,reported_count=0,selected_ordinals=(),outgoing_event_ids=()).replayed
    denied(lambda:submit(c,q))


@pytest.mark.parametrize('change',['count_only','mismatch','bool_count','bool_ordinal','list','duplicate','missing','foreign','incoming','outside','future'])
def test_invalid_selfreport_never_creates_receipt(c,change):
    q=prepare(c);kwargs={}
    if change=='count_only':kwargs=dict(selected_ordinals=(),outgoing_event_ids=())
    elif change=='mismatch':kwargs=dict(reported_count=2)
    elif change=='bool_count':kwargs=dict(reported_count=True)
    elif change=='bool_ordinal':kwargs=dict(selected_ordinals=(True,))
    elif change=='list':kwargs=dict(selected_ordinals=[1])
    elif change=='duplicate':kwargs=dict(reported_count=2,selected_ordinals=(1,1),outgoing_event_ids=('out1','out2'))
    elif change=='missing':kwargs=dict(outgoing_event_ids=('',))
    elif change=='foreign':kwargs=dict(outgoing_event_ids=('not-admitted',))
    elif change=='incoming':kwargs=dict(outgoing_event_ids=('wrong-incoming',))
    elif change=='outside':kwargs=dict(outgoing_event_ids=('outside',))
    else:
        # Already-admitted future events remain observations, not completed work.
        state=json.loads(c.s.execute('SELECT value FROM runtime WHERE key=?',(c.source.runtime_key,)).fetchone()[0])
        for row in state['events']:
            if row['event_id']=='out1':row['at']=(NOW+timedelta(minutes=5)).isoformat()
        c.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),c.source.runtime_key))
    before=register_raw(c);pending=questions(c)[0]['value'];denied(lambda:submit(c,q,**kwargs))
    assert register_raw(c)==before and questions(c)[0]['value']==pending


def test_actual_two_plan_batch_one_revision_shared_anchor_rejected(c):
    c.r.record_plan(plan(c,'plan2'),now=NOW);q=prepare(c,plan_ids=('plan2','plan1'));before=json.loads(register_raw(c))
    pending=questions(c)[0]['value'];denied(lambda:submit(c,q,reported_count=2,selected_ordinals=(1,2),outgoing_event_ids=('out1','out1')))
    assert questions(c)[0]['value']==pending and json.loads(register_raw(c))==before
    ack=submit(c,q,reported_count=2,selected_ordinals=(1,2),outgoing_event_ids=('out2','out1'))
    assert ack.summary.uncertain_ids==('plan1','plan2')
    assert json.loads(register_raw(c))['revision']==before['revision']+1


@pytest.mark.parametrize('change',['staff','group','other_uid','multiple_admins','not_closed','wrong_business','intel','connection','clock','method','wrong_store','disabled','disabled_ingest','disabled_register'])
def test_gates_and_captured_hosts_deny_before_question_sql(c,change,monkeypatch):
    if change=='staff':c.payload.update(sender='frost',source_id='telegram:2:10')
    elif change=='group':c.payload['source_id']='telegram:-123:10'
    elif change=='other_uid':c.payload['source_id']='telegram:2:10'
    elif change=='multiple_admins':c.config['admins'].append('frost')
    elif change=='not_closed':c.config['team_roster_verified']=False
    elif change=='wrong_business':c.e.b='OTHER'
    elif change=='intel':c.e.intelligence=object()
    elif change=='connection':c.loop._db=object()
    elif change=='clock':c.e.now=lambda:NOW.timestamp()
    elif change=='method':c.r.review_snapshot=MethodType(lambda self,**kw:None,c.r)
    elif change=='wrong_store':c.loop._store=object()
    elif change=='disabled':c.loop._enabled=False
    elif change=='disabled_ingest':c.ingest._enabled=False
    else:c.r._enabled=False
    before=register_raw(c) if change not in ('connection','wrong_store') else None
    reads=[];original=c.s.execute
    def execute(sql,params=()):
        reads.append(sql);return original(sql,params)
    monkeypatch.setattr(c.s,'execute',execute)
    denied(lambda:prepare(c))
    if change not in ('disabled_ingest','disabled_register'):
        assert not any(sql.startswith('SELECT ') for sql in reads)
    assert not questions(c)
    if before is not None:assert register_raw(c)==before


@pytest.mark.parametrize('change',['revoked','expired','registration','new_admission','new_permission','new_plan','role','question_expiry'])
def test_stale_source_actor_cohort_window_denies_without_mutation(c,change):
    q=prepare(c)
    if change=='revoked':c.state['source']=replace(c.source,revoked=True)
    elif change=='expired':c.state['source']=replace(c.source,expires_at=NOW)
    elif change=='registration':c.state['source']=replace(c.source,revision=2)
    elif change=='new_admission':c.admit([event('later',at=NOW-timedelta(minutes=1))])
    elif change=='new_permission':
        c.admit([event('optout',direction='incoming',at=NOW-timedelta(minutes=1))]);c.r.record_permission(permission(c,id='optout',event_id='optout',opt_out=True,granted=False,window=None),now=NOW)
    elif change=='new_plan':c.r.record_plan(plan(c,'plan2'),now=NOW)
    elif change=='role':c.config['admins']=['frost']
    else:c.clock_state['now']=NOW+timedelta(hours=1)
    before=register_raw(c);pending=questions(c)[0]['value']
    denied(lambda:submit(c,q,now=c.clock_state['now']))
    assert register_raw(c)==before and questions(c)[0]['value']==pending


@pytest.mark.parametrize('change',['foreign_owner','unready','unknown_permission','out_of_report','empty','nine','duplicates','naive','clock_mismatch','expiry'])
def test_prepare_whole_refusal_with_no_question(c,change):
    kwargs={}
    if change in ('foreign_owner','unready','unknown_permission','out_of_report'):
        c.r.record_plan(plan(c,'plan2',**{'foreign_owner':dict(owner_id='frost'),'unready':dict(readiness=None),'unknown_permission':dict(permission_id='missing'),
            'out_of_report':dict(window=core.Window(NOW-timedelta(hours=2),NOW+timedelta(hours=2)))}[change]),now=NOW)
        kwargs['plan_ids']=('plan1','plan2')
    elif change=='empty':kwargs['plan_ids']=()
    elif change=='nine':kwargs['plan_ids']=tuple(str(i) for i in range(9))
    elif change=='duplicates':kwargs['plan_ids']=('plan1','plan1')
    elif change=='naive':kwargs['now']=NOW.replace(tzinfo=None)
    elif change=='clock_mismatch':c.clock_state['now']=NOW+timedelta(seconds=1)
    else:kwargs['expires_at']=NOW
    before=register_raw(c);denied(lambda:prepare(c,**kwargs));assert not questions(c) and register_raw(c)==before


def test_explicit_replacement_old_id_never_regains_authority(c):
    first=prepare(c);denied(lambda:prepare(c,expires_at=NOW+timedelta(minutes=45)))
    second=prepare(c,expires_at=NOW+timedelta(minutes=45),replace_question_id=first.question_id)
    assert second.question_id!=first.question_id and len(questions(c))==1
    denied(lambda:submit(c,first));submit(c,second,reported_count=0,selected_ordinals=(),outgoing_event_ids=())
    third=prepare(c,replace_question_id=second.question_id)
    assert third.question_id not in (first.question_id,second.question_id)
    denied(lambda:submit(c,first));denied(lambda:submit(c,second))


@pytest.mark.parametrize('fault',['register_write','register_cas','question_write','question_cas','postregister_revoke','postquestion_role','last_clock_register_drift'])
def test_shared_transaction_rolls_back_both_register_and_question(c,monkeypatch,fault):
    q=prepare(c);before=register_raw(c);pending=questions(c)[0]['value'];rkey=c.r._scope()[1];qkey=questions(c)[0]['key'];original=c.s.execute
    def execute(sql,params=()):
        if sql.startswith('UPDATE runtime SET value='):
            if params[1]==rkey and fault.startswith('register_'):
                if fault=='register_write':raise sqlite3.OperationalError('PRIVATE_PAYLOAD')
                return SimpleNamespace(rowcount=0)
            if params[1]==qkey and fault.startswith('question_'):
                if fault=='question_write':raise sqlite3.OperationalError('PRIVATE_PAYLOAD')
                return SimpleNamespace(rowcount=0)
            result=original(sql,params)
            if params[1]==rkey and fault=='postregister_revoke':c.state['source']=replace(c.source,revoked=True)
            if params[1]==qkey and fault=='postquestion_role':c.config['admins']=['frost']
            if params[1]==qkey and fault=='last_clock_register_drift':
                c.clock_state['callback']=lambda:c.s.execute('UPDATE runtime SET value=? WHERE key=?',(before,rkey))
            return result
        return original(sql,params)
    monkeypatch.setattr(c.s,'execute',execute)
    denied(lambda:submit(c,q))
    assert register_raw(c)==before and questions(c)[0]['value']==pending and not c.s.db.in_transaction


@pytest.mark.parametrize('fault',['oversize','blob','malformed','tamper'])
def test_bounded_current_question_metadata_refuses_whole(c,fault):
    q=prepare(c);key=questions(c)[0]['key'];data=questions(c)[0]['value'];before=register_raw(c)
    if fault=='oversize':data='x'*(module.MAX_STATE_BYTES+1)
    elif fault=='blob':data=sqlite3.Binary(data.encode())
    elif fault=='malformed':data='{'
    else:parsed=json.loads(data);parsed['plans']=['other'];data=json.dumps(parsed)
    c.s.execute('UPDATE runtime SET value=? WHERE key=?',(data,key));denied(lambda:submit(c,q));assert register_raw(c)==before


def test_new_public_snapshot_exact_store_and_owned_batch_contract(c):
    snap=c.r.review_snapshot(now=NOW,reporting_window=window(),expected_store=c.s)
    assert snap.evaluation==c.r.preview(now=NOW,reporting_window=window()) and snap.local_revision==2
    assert snap.conversation==c.conv and 'account' not in repr(snap)
    with pytest.raises(register_module.TouchRegisterUnavailable):c.r.review_snapshot(now=NOW,reporting_window=window(),expected_store=object())
    receipt=core.TouchReceipt('typed',c.conv,'plan1','out1',False)
    args=dict(now=NOW,reporting_window=window(),expected_store=c.s,expected_snapshot_digest=snap.digest)
    with pytest.raises(register_module.TouchRegisterUnavailable):c.r.record_self_reported_receipts_in_transaction((receipt,),**args)
    with c.s.transaction():
        result=c.r.record_self_reported_receipts_in_transaction((receipt,),**args)
        assert c.s.db.in_transaction and result.local_revision==3 and result.evaluation.summary.uncertain_ids==('plan1',)
    assert json.loads(register_raw(c))['revision']==3


@pytest.mark.parametrize('fault',['confirmed','stale','list','foreign','capacity'])
def test_checked_batch_refuses_before_one_write(c,fault):
    snap=c.r.review_snapshot(now=NOW,reporting_window=window(),expected_store=c.s)
    receipt=core.TouchReceipt('typed',c.conv,'plan1','out1',False);rows=(receipt,)
    if fault=='confirmed':rows=(replace(receipt,confirmed=True),)
    elif fault=='list':rows=[receipt]
    elif fault=='foreign':rows=(replace(receipt,conversation=replace(c.conv,thread_id='foreign')),)
    elif fault=='capacity':
        # A second eligible plan plus a full prior receipt capacity.
        c.r.record_plan(plan(c,'plan2'),now=NOW)
        for i in range(16):c.r.record_receipt(replace(receipt,id='old'+str(i)),now=NOW)
        snap=c.r.review_snapshot(now=NOW,reporting_window=window(),expected_store=c.s)
        rows=(replace(receipt,plan_id='plan2',event_id='out2'),)
    args=dict(now=NOW,reporting_window=window(),expected_store=c.s,expected_snapshot_digest='0'*64 if fault=='stale' else snap.digest)
    before=register_raw(c)
    with c.s.transaction():
        with pytest.raises(register_module.TouchRegisterUnavailable):c.r.record_self_reported_receipts_in_transaction(rows,**args)
        assert c.s.db.in_transaction and register_raw(c)==before
    assert register_raw(c)==before


def test_shared_cas_preserves_existing_single_record_idempotence_and_transaction_rejection(c):
    before=register_raw(c);r=c.r.record_plan(plan(c),now=NOW)
    assert r.already_recorded and r.local_revision==2 and register_raw(c)==before
    with pytest.raises(register_module.TouchRegisterUnavailable):c.r.record_plan(plan(c,readiness=False),now=NOW)
    with c.s.transaction():
        with pytest.raises(register_module.TouchRegisterUnavailable):c.r.record_receipt(core.TouchReceipt('old',c.conv,'plan1','out1',False),now=NOW)
        assert register_raw(c)==before
    c.r.record_receipt(core.TouchReceipt('old',c.conv,'plan1','out1',False),now=NOW)
    assert c.r.preview(now=NOW,reporting_window=window()).summary.uncertain_ids==('plan1',)


@pytest.mark.parametrize('channel',['bot','business'])
def test_genuine_file_close_reopen_question_then_receipts_idempotent(c,channel):
    c.payload.update(channel=channel,source_id='business:synthetic:1:10' if channel=='business' else 'telegram:1:10')
    q=prepare(c);saved=questions(c)[0]['value'];c.reopen()
    assert questions(c)[0]['value']==saved
    ack=submit(c,q);saved_register=register_raw(c);saved_question=questions(c)[0]['value']
    c.reopen();again=submit(c,q)
    assert again.replayed and again.summary==ack.summary and register_raw(c)==saved_register and questions(c)[0]['value']==saved_question
    assert c.p.calls==1 and c.s.db.execute('PRAGMA database_list').fetchone()[2]==str(c.path)


def test_actual_maximum_eight_plans_full_uncertain_batch(c):
    rows=[event('out'+str(i),at=NOW-timedelta(minutes=8)+timedelta(seconds=i)) for i in range(3,9)]
    c.admit(rows)
    for i in range(2,9):c.r.record_plan(plan(c,'plan'+str(i)),now=NOW)
    ids=tuple('plan'+str(i) for i in range(1,9));q=prepare(c,plan_ids=ids)
    assert q.item_count==8 and 'Касание 8' in q.text and len(q.text.encode('utf-16-le'))//2<=3500
    before=json.loads(register_raw(c))['revision']
    result=submit(c,q,reported_count=8,selected_ordinals=tuple(range(1,9)),outgoing_event_ids=tuple('out'+str(i) for i in range(1,9)))
    assert result.summary.uncertain_ids==ids and not result.summary.confirmed_ids
    assert json.loads(register_raw(c))['revision']==before+1


def test_budget_composition_instrumentation_and_question_point_projection(c,monkeypatch):
    counts=dict(register=0,ingest=0,question=0);queries=[]
    for name,cls in [('register',register_module._Reader),('ingest',ingest_module._Reader),('question',module._Reader)]:
        original=cls.__init__
        def init(self,*a,_name=name,_original=original,**kw):counts[_name]+=1;_original(self,*a,**kw)
        monkeypatch.setattr(cls,'__init__',init)
    c.s.db.set_trace_callback(queries.append)
    q=prepare(c);assert counts==dict(register=2,ingest=6,question=1)
    counts.update(register=0,ingest=0,question=0);submit(c,q)
    assert counts==dict(register=3,ingest=12,question=1)
    counts.update(register=0,ingest=0,question=0);submit(c,q)
    assert counts==dict(register=2,ingest=6,question=1)
    point=[q for q in queries if q.startswith('SELECT CASE') and 'customer-touch-question:' in q]
    assert point and all('WHERE key=' in q and 'LIMIT 2' in q and 'length(CAST(value AS BLOB))<=16384' in q for q in point)


def test_current_source_callback_drift_after_question_cas_rolls_back(c,monkeypatch):
    q=prepare(c);before=register_raw(c);pending=questions(c)[0]['value'];key=questions(c)[0]['key'];actual=c.s.execute
    def execute(sql,params=()):
        result=actual(sql,params)
        if sql.startswith('UPDATE runtime SET value=') and params[1]==key:
            c.state['callback']=lambda:c.config.update(admins=['frost'])
        return result
    monkeypatch.setattr(c.s,'execute',execute);denied(lambda:submit(c,q))
    assert register_raw(c)==before and questions(c)[0]['value']==pending


def test_batch_savepoint_preserves_outer_transaction_if_caller_catches_cas_fault(c,monkeypatch):
    snapshot=c.r.review_snapshot(now=NOW,reporting_window=window(),expected_store=c.s);before=register_raw(c);actual=c.s.execute
    def execute(sql,params=()):
        result=actual(sql,params)
        if sql.startswith('UPDATE runtime SET value=') and params[1]==c.r._scope()[1]:
            c.state['source']=replace(c.source,revoked=True)
        return result
    monkeypatch.setattr(c.s,'execute',execute)
    with c.s.transaction():
        with pytest.raises(register_module.TouchRegisterUnavailable):
            c.r.record_self_reported_receipts_in_transaction((core.TouchReceipt('typed',c.conv,'plan1','out1',False),),now=NOW,
                reporting_window=window(),expected_store=c.s,expected_snapshot_digest=snapshot.digest)
        assert c.s.db.in_transaction and register_raw(c)==before
    assert register_raw(c)==before


@pytest.mark.parametrize('fault',['revoke','new_register','role','clock'])
def test_prepare_final_data_phase_after_question_write_rolls_back(c,monkeypatch,fault):
    before=register_raw(c);actual=c.s.execute
    def execute(sql,params=()):
        result=actual(sql,params)
        if sql.startswith('INSERT INTO runtime') and params[0].startswith('customer-touch-question:'):
            if fault=='revoke':c.state['source']=replace(c.source,revoked=True)
            elif fault=='role':c.config['admins']=['frost']
            elif fault=='clock':c.clock_state['now']=NOW+timedelta(seconds=1)
            else:
                state=json.loads(before);state['revision']+=1
                actual('UPDATE runtime SET value=? WHERE key=?',(json.dumps(state),c.r._scope()[1]))
        return result
    monkeypatch.setattr(c.s,'execute',execute)
    denied(lambda:prepare(c));assert not questions(c) and register_raw(c)==before


def test_changed_policy_and_new_ingest_can_explicitly_replace_stale_question(c):
    old=prepare(c);c.config['employees']['test_operator']['name']='Changed synthetic label'
    denied(lambda:submit(c,old));denied(lambda:prepare(c))
    q=prepare(c,replace_question_id=old.question_id)
    assert q.question_id!=old.question_id
    c.admit([event('new-incoming',direction='incoming',at=NOW-timedelta(seconds=1))])
    denied(lambda:submit(c,q))
    fresh=prepare(c,replace_question_id=q.question_id)
    denied(lambda:submit(c,q));assert submit(c,fresh).summary.uncertain_ids==('plan1',)


@pytest.mark.parametrize('method',['review_snapshot','record_self_reported_receipts_in_transaction'])
def test_new_public_expected_store_and_disabled_checks_precede_transfer(c,monkeypatch,method):
    def forbidden(*a,**kw):pytest.fail('invalid public binding read data')
    monkeypatch.setattr(c.s,'execute',forbidden)
    args=dict(now=NOW,reporting_window=window(),expected_store=object())
    if method=='record_self_reported_receipts_in_transaction':args['expected_snapshot_digest']='0'*64
    call=lambda:getattr(c.r,method)(*(((),) if method!='review_snapshot' else ()),**args)
    with pytest.raises(register_module.TouchRegisterUnavailable):call()
    args['expected_store']=c.s;c.r._enabled=False
    with pytest.raises(register_module.TouchRegisterUnavailable):call()


def test_positive_receipt_with_future_outgoing_anchor_is_rejected_by_actual_core(c):
    c.admit([event('future',at=NOW+timedelta(minutes=10))]);q=prepare(c);before=register_raw(c);pending=questions(c)[0]['value']
    denied(lambda:submit(c,q,outgoing_event_ids=('future',)))
    assert register_raw(c)==before and questions(c)[0]['value']==pending


def test_register_and_question_statement_abort_rolls_back_without_disclosing_fault(c):
    q=prepare(c);before=register_raw(c);pending=questions(c)[0]['value'];key=c.r._scope()[1]
    c.s.execute("CREATE TEMP TRIGGER synthetic_abort BEFORE UPDATE ON runtime WHEN NEW.key='"+key+"' BEGIN SELECT RAISE(ABORT,'PRIVATE_FAULT'); END")
    denied(lambda:submit(c,q));assert register_raw(c)==before and questions(c)[0]['value']==pending
