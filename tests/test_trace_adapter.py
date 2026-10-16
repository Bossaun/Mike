"""In-memory scoped aggregates; no provider, transport, file or private data."""
import json

import pytest

from goldway.engine import Engine
from goldway.model_trace import DeliveryState,Processing,SafeReason
from goldway.store import Store
from goldway.trace_adapter import _snapshot,handle_trace


MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
SECRET='SYNTHETIC_RAW_TEXT_ID_KEY_NEVER_IN_TRACE'


class NoModel:
    def propose(self,*args,**kwargs):
        pytest.fail('Trace called a model provider')


def setup():
    config={'business':'GOLDWAY','timezone':'Europe/Moscow',
      'employees':{key:{'name':'Synthetic '+key,'telegram_user_id':i+1} for i,key in enumerate(MEMBERS)},
      'admins':['test_operator'],'business_account_user_id':99,
      'team_intake':{'recipients':MEMBERS[:],'started_at':100},
      'allowed_live_recipients':MEMBERS[:],'team_roster_verified':True,'recipients_verified':True,
      'live_send':False,'review_mode':True,'sheets_write':False,'daily_summary':False,
      'allow_onboarding_replies':False,'allow_registered_replies':False,
      'allowed_group_ids':[],'group_destinations':{},'meeting_recipient':None,
      'model':{'provider':'synthetic-provider-'+SECRET,'key':SECRET,'daily_budget':99}}
    store=Store(':memory:')
    return Engine(store,config,NoModel(),lambda:100000),store


def seed(store,business='GOLDWAY'):
    for i,(kind,state,error) in enumerate([
        ('reply','done',None),('transcript','failed','ValueError: knowledge_citations'),
        (SECRET,'done',SECRET),('reply',SECRET,'ValueError: '+SECRET)]):
        store.execute('INSERT INTO events(business,id,kind,payload,state,error) VALUES(?,?,?,?,?,?)',
                      (business,str(i)+SECRET,kind,json.dumps({'text':SECRET,'customer_id':SECRET}),state,error))
    for i,state in enumerate(['sent','unknown','pending',SECRET]):
        store.execute('INSERT INTO outbox(business,recipient,text,dedupe,state,created) VALUES(?,?,?,?,?,?)',
                      (business,SECRET,SECRET,'synthetic-'+str(i),state,90000))
    store.execute('INSERT INTO usage(business,at,provider,input_tokens,output_tokens,reserved_cost) VALUES(?,?,?,?,?,?)',
                  (business,99999,SECRET,100,20,12345.67))


def response(store):
    rows=store.rows("SELECT text,recipient,preferred_transport FROM outbox WHERE business=? AND dedupe=?",('GOLDWAY','trace:command'))
    return rows


@pytest.mark.parametrize('channel',['bot','business'])
def test_authorized_admin_receives_only_payload_free_aggregates_without_provider(channel):
    engine,store=setup();seed(store);seed(store,'OTHER')
    assert engine._closed_team()
    before=store.execute('SELECT COUNT(*) FROM usage').fetchone()[0]
    engine.reply('command','test_operator','/trace',source_id=SECRET,channel=channel,message_at=99999)
    rows=response(store);assert len(rows)==1
    row=rows[0];assert row['recipient']=='test_operator' and row['preferred_transport']==channel
    assert SECRET not in row['text'] and '12345.67' not in row['text']
    assert 'done=2' in row['text'] and 'failed=1' in row['text']
    assert 'sent=1' in row['text'] and 'unknown=2' in row['text']
    assert 'Предложенные действия: нет данных; применённые действия: нет данных' in row['text']
    assert 'входных токенов: 100' in row['text'] and 'выходных токенов: 20' in row['text']
    assert 'unknown не означает успех' in row['text']
    assert len(row['text'])<4096
    assert store.execute('SELECT state FROM events WHERE business=? AND id=?',('GOLDWAY','command')).fetchone()[0]=='done'
    assert store.execute('SELECT COUNT(*) FROM usage').fetchone()[0]==before
    assert not store.rows('SELECT id FROM tasks')


def test_exact_command_skips_task_context_and_external_knowledge_refresh():
    engine,store=setup()
    def forbidden(*a,**k):pytest.fail('Trace read task context or refreshed an external source')
    engine._context=forbidden
    class NoIntelligence:
        @property
        def sources(self):pytest.fail('source refresh reached')
    engine.intelligence=NoIntelligence()
    engine.reply('command','test_operator','/trace',channel='bot')
    assert len(response(store))==1


@pytest.mark.parametrize(('sender','channel'),[
    ('alen','bot'),('frost','business'),('test_operator','group'),('test_operator',None),
    ('test_operator','unknown'),('outsider','bot')])
def test_staff_groups_unknown_channels_and_outsiders_get_no_diagnostics(sender,channel):
    engine,store=setup();seed(store)
    reads=[];store.db.set_trace_callback(lambda sql:reads.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
    engine.reply('command',sender,'/trace',channel=channel)
    store.db.set_trace_callback(None)
    assert reads==[] and not response(store)
    assert store.execute("SELECT COUNT(*) FROM outbox WHERE dedupe LIKE 'trace:%'").fetchone()[0]==0


@pytest.mark.parametrize('change',['invalid_roster','not_admin','personal','business_mismatch'])
def test_gate_changes_do_not_expose_aggregates(change):
    engine,store=setup()
    if change=='invalid_roster':engine.c['team_roster_verified']=False
    if change=='not_admin':engine.c['admins']=[]
    if change=='personal':engine.c.pop('team_intake')
    if change=='business_mismatch':engine.c['business']='OTHER'
    engine.reply('command','test_operator','/trace',channel='bot')
    assert not response(store)


def test_repeat_same_event_is_not_requeued_or_model_processed():
    engine,store=setup()
    assert engine.reply('command','test_operator','/trace',channel='bot')
    assert not engine.reply('command','test_operator','/trace',channel='bot')
    engine.process('command')  # already done: no provider/context path
    assert len(response(store))==1


def test_every_aggregate_query_has_business_predicate_and_no_payload_select():
    engine,store=setup();seed(store);seed(store,'OTHER')
    reads=[];store.db.set_trace_callback(lambda sql:reads.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
    engine.reply('command','test_operator','/trace',channel='bot')
    store.db.set_trace_callback(None)
    assert len(reads)==5
    for sql in reads:
        lower=sql.lower()
        assert "where business='goldway'" in lower
        assert not any(field in lower for field in ['payload','audit','source_id','recipient','select *','reserved_cost','provider'])
        assert SECRET not in sql


def test_cross_business_rows_do_not_change_same_business_snapshot():
    engine,store=setup();seed(store)
    a=_snapshot(store,business='GOLDWAY',now=100000)
    seed(store,'OTHER')
    assert _snapshot(store,business='GOLDWAY',now=100000)==a
    assert dict(a.events)[Processing.UNKNOWN]==1
    assert dict(a.errors)[SafeReason.KNOWLEDGE_CITATIONS]==1
    assert dict(a.errors)[SafeReason.UNKNOWN]==2
    assert dict(a.messages)[DeliveryState.UNKNOWN]==2
    assert a.proposed_actions is None and a.applied_actions is None


@pytest.mark.parametrize(('input_tokens','output_tokens'),[(0,20),(100,0),(-1,20),(100,10_000_001)])
def test_ambiguous_usage_placeholders_or_invalid_values_remain_unknown(input_tokens,output_tokens):
    engine,store=setup()
    store.execute('INSERT INTO usage(business,at,provider,input_tokens,output_tokens,reserved_cost) VALUES(?,?,?,?,?,?)',
                  ('GOLDWAY',99999,SECRET,input_tokens,output_tokens,0))
    result=_snapshot(store,business='GOLDWAY',now=100000)
    assert (result.usage.input_tokens is None)==(input_tokens<=0 or input_tokens>10_000_000)
    assert (result.usage.output_tokens is None)==(output_tokens<=0 or output_tokens>10_000_000)


def test_usage_window_excludes_old_future_and_foreign_rows():
    engine,store=setup()
    for business,at in [('GOLDWAY',1000),('GOLDWAY',100001),('OTHER',99999)]:
        store.execute('INSERT INTO usage(business,at,provider,input_tokens,output_tokens,reserved_cost) VALUES(?,?,?,?,?,?)',
                      (business,at,SECRET,9000000,9000000,999))
    result=_snapshot(store,business='GOLDWAY',now=100000)
    assert result.usage_rows_last24h==0 and result.usage.input_tokens is None
    seed(store)
    assert _snapshot(store,business='GOLDWAY',now=100000).usage.input_tokens==100


def test_over_supported_aggregate_token_bound_not_zero_or_overflow():
    engine,store=setup()
    for _ in range(2):
        store.execute('INSERT INTO usage(business,at,provider,input_tokens,output_tokens,reserved_cost) VALUES(?,?,?,?,?,?)',
                      ('GOLDWAY',99999,SECRET,9000000,9000000,0))
    result=_snapshot(store,business='GOLDWAY',now=100000)
    assert result.usage_rows_last24h==2 and result.usage.input_tokens is None and result.usage.output_tokens is None


@pytest.mark.parametrize('now',[True,-1,float('nan'),float('inf'),10**1000,'100000'])
def test_clock_validation_no_raw_payload_error(now):
    engine,store=setup()
    with pytest.raises(ValueError,match='trace_invalid_scope_or_clock'):_snapshot(store,business='GOLDWAY',now=now)


@pytest.mark.parametrize('text',['/trace ',' /trace','/trace anything','/TRACE'])
def test_nonexact_command_is_not_a_diagnostic_namespace(text):
    engine,store=setup()
    with pytest.raises(ValueError,match='trace_exact_command_required'):
        handle_trace(engine,'command',{'sender':'test_operator','channel':'bot','text':text})
    assert not response(store)


def test_no_network_files_or_dispatch_during_actual_hook(monkeypatch):
    import builtins,socket,time
    engine,store=setup()
    def forbidden(*a,**k):pytest.fail('external I/O, clock or dispatch')
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(time,'time',forbidden)
    engine.reply('command','test_operator','/trace',channel='bot')
    assert len(response(store))==1
