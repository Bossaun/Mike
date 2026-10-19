"""Prospective actual Engine/SQLite transaction boundaries; no provider or network."""
import copy
import json
import sqlite3

import pytest

from goldway.engine import Engine
from goldway.model import response_schema
from goldway.store import Store
from goldway import trace_adapter as trace
from goldway.model_trace import TraceRecord

SECRET='SYNTHETIC_SECRET_SOURCE_TASK_TEXT_NEVER_IN_JOURNAL'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
NOW=1792400000
TEXT='Создай задачу: подготовить контрольный список. '+SECRET


class Model:
    def __init__(self,mode='create'):
        self.mode=mode;self.calls=0;self.hook=None;self.envelopes=[]

    def propose(self,context):
        self.calls+=1
        if self.hook:self.hook()
        if self.mode=='failure':raise RuntimeError(SECRET)
        if self.mode=='crash':raise KeyboardInterrupt()
        if self.mode=='malformed':return None
        actions=[]
        if self.mode!='conversation':
            action=dict(op='create',task_id=None,title='Synthetic '+SECRET,owner=None,deadline=None,
                next_step='Synthetic next '+SECRET,criterion='Synthetic criterion '+SECRET,status='confirmed',
                evidence_lines=[1],owner_lines=[],deadline_lines=[],set_fields=[])
            actions=[action]
            if self.mode=='invalid_fields':action['criterion']=None
            if self.mode=='invalid_op':action['op']=SECRET
            if self.mode=='oversize':actions=[copy.deepcopy(action) for _ in range(21)]
            if self.mode=='two_calls':actions.append(copy.deepcopy(action))
            if self.mode=='many':actions=[copy.deepcopy(action) for _ in range(20)]
            if self.mode=='mixed':
                bad=dict(action,op='update',task_id=SECRET,title=None,next_step=None,criterion=None,
                         status='in_progress',set_fields=['status'])
                actions.append(bad)
            required=set(response_schema(context)['properties']['actions']['items']['required'])
            assert all(set(row)==required for row in actions)
        self.envelopes=copy.deepcopy(actions)
        result=dict(actions=actions,intent='task_action' if actions else 'conversation',
                    summary=SECRET,reply_text='Synthetic direct reply '+SECRET)
        schema=response_schema(context)
        if 'dialogue_state' in schema['required']:
            result.update(dialogue_state=dict(topic='Synthetic',entities=[],pending_offer=None,task_id=None),
                          evidence_ids=[],knowledge_mode='not_needed')
        if 'meeting_question_lines' in schema['required']:result['meeting_question_lines']=[]
        assert set(result)==set(schema['required'])
        return result


def config(business='GOLDWAY'):
    return dict(business=business,timezone='Europe/Moscow',
        employees={key:dict(name=key,telegram_user_id=i+1) for i,key in enumerate(MEMBERS)},
        admins=['test_operator'],business_account_user_id=99,
        team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],
        team_roster_verified=True,recipients_verified=True,live_send=False,review_mode=True,
        sheets_write=False,daily_summary=False,allow_onboarding_replies=False,
        allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None)


@pytest.fixture
def case(tmp_path,monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:pytest.fail('No external calls allowed'))
    path=tmp_path/'synthetic.sqlite';store=Store(str(path));model=Model();c=config()
    return Engine(store,c,model,lambda:NOW),store,model,path


def run(engine,*,event='event-'+SECRET,text=TEXT):
    engine.reply(event,'test_operator',text,source_id='telegram:1:'+SECRET,channel='bot',message_at=NOW-1)
    engine.process(event)


def document(store,business='GOLDWAY'):
    rows=store.rows('SELECT key,value FROM runtime WHERE key=?',(trace._action_key(business),))
    assert len(rows)==1
    assert business not in rows[0]['key'] and SECRET not in rows[0]['key']
    assert SECRET not in rows[0]['value']
    assert len(rows[0]['value'].encode())<=65536
    return json.loads(rows[0]['value'])


def report(engine,store,event='trace-command'):
    engine.reply(event,'test_operator','/trace',source_id='telegram:1:trace',channel='bot')
    return store.execute('SELECT text FROM outbox WHERE business=? AND dedupe=?',
                         (engine.b,'trace:'+event)).fetchone()[0]


def test_schema_shaped_actual_committed_apply_is_separate_from_employee_completion(case,monkeypatch):
    e,s,m,_=case;run(e);row=document(s)['records'][0]
    assert m.calls==1 and m.envelopes
    assert len(e.tasks())==1 and row['phase']=='committed'
    assert row['proposed_ops']==['create_task'] and row['applied_count']==1 and row['queued_count']==1
    seen=[];actual=trace.trace_views
    def views(records):
        assert all(type(r) is TraceRecord and r.usage.input_tokens is None and not r.delivery_states for r in records)
        seen.extend(records);return actual(records)
    monkeypatch.setattr(trace,'trace_views',views)
    text=report(e,s)
    assert len(seen)==1 and 'Применено: 1' in text and 'не завершение сотрудником' in text
    assert 'не наблюдалось=1' in text and SECRET not in text


def test_two_committed_apply_calls_are_not_a_claim_of_two_unique_completed_tasks(case):
    e,s,m,_=case;m.mode='two_calls';run(e)
    assert len(e.tasks())==1 and document(s)['records'][0]['applied_count']==2


def test_observed_noaction_conversation_has_known_zero(case):
    e,s,m,_=case;m.mode='conversation';run(e,text='Подготовь короткое приветствие для встречи')
    row=document(s)['records'][0]
    assert row['proposed_known'] and row['proposed_ops']==[] and row['applied_count']==0
    assert row['queued_count']==1 and not e.tasks()


@pytest.mark.parametrize('mode',['invalid_fields','invalid_op','oversize','mixed','malformed','failure'])
def test_rejection_or_model_failure_never_claims_applied_action(case,mode):
    e,s,m,_=case;m.mode=mode
    if mode=='mixed':run(e)  # Existing ambiguity guard clarifies without raising.
    else:
        with pytest.raises(Exception):run(e)
    row=document(s)['records'][0]
    assert not e.tasks() and row['applied_count'] is None and row['queued_count'] is None
    assert row['phase'] in ('failed','rejected') and row['phase']!='committed'
    assert s.execute("SELECT state FROM events WHERE id=?",('event-'+SECRET,)).fetchone()[0]==('done' if mode=='mixed' else 'failed')
    if mode in ('invalid_op','oversize','malformed','failure'):assert row['proposed_known'] is False
    assert SECRET not in report(e,s)


def test_real_late_queue_failure_rolls_back_task_and_never_preserves_applied_count(case,monkeypatch):
    e,s,m,_=case;actual=s.queue;once=[False];inserts=[]
    s.db.set_trace_callback(lambda sql:inserts.append(sql) if sql.startswith('INSERT INTO tasks') else None)
    def fail(*args,**kwargs):
        value=actual(*args,**kwargs)
        if not once[0] and args[3].startswith('action:'):
            once[0]=True;raise ValueError(SECRET)
        return value
    monkeypatch.setattr(s,'queue',fail)
    with pytest.raises(ValueError):run(e)
    s.db.set_trace_callback(None)
    assert inserts and not e.tasks()
    assert document(s)['records'][0]['applied_count'] is None
    assert not s.rows("SELECT id FROM outbox WHERE dedupe LIKE 'action:%'")


def test_final_business_commit_fault_cannot_leave_a_successful_diagnostic(case,monkeypatch):
    e,s,m,_=case;actual=s.execute;once=[False]
    def execute(sql,args=()):
        if sql=='COMMIT' and m.calls and not once[0]:
            once[0]=True;raise sqlite3.OperationalError('Synthetic commit fault')
        return actual(sql,args)
    monkeypatch.setattr(s,'execute',execute)
    with pytest.raises(sqlite3.OperationalError):run(e)
    assert not e.tasks() and document(s)['records'][0]['applied_count'] is None


def test_crash_keeps_pending_unknown_and_does_not_invent_zero(case):
    e,s,m,path=case;m.mode='crash'
    with pytest.raises(KeyboardInterrupt):run(e)
    row=document(s)['records'][0]
    assert row['phase']=='pending' and row['applied_count'] is None and not row['proposed_known']
    text=report(e,s)
    assert 'Предложено: нет данных' in text and 'Применено: нет данных' in text
    assert not s.db.in_transaction


def test_replay_and_reopen_do_not_create_another_trace_or_model_call(case):
    e,s,m,path=case;run(e);raw=s.execute('SELECT value FROM runtime WHERE key=?',(trace._action_key(e.b),)).fetchone()[0]
    e.process('event-'+SECRET);assert m.calls==1
    other=Store(str(path));next_model=Model();reopened=Engine(other,e.c,next_model,lambda:NOW)
    reopened.process('event-'+SECRET)
    assert next_model.calls==0 and document(other)['records']==document(s)['records']
    assert other.execute('SELECT value FROM runtime WHERE key=?',(trace._action_key(e.b),)).fetchone()[0]==raw


def test_business_hash_keys_and_records_are_partitioned(case):
    e,s,m,_=case;run(e)
    second=Engine(s,config('SECOND'),Model('conversation'),lambda:NOW)
    run(second,text='Подготовь краткое приветствие')
    a=document(s)['records'][0];b=document(s,'SECOND')['records'][0]
    assert a['event']!=b['event'] and a['applied_count']==1 and b['applied_count']==0
    assert 'Применено: 1' not in report(second,s)


@pytest.mark.parametrize('fault',['start','proposal','commit'])
def test_diagnostic_sql_fault_does_not_prevent_normal_business_commit(case,fault):
    e,s,m,_=case
    operation='INSERT' if fault=='start' else 'UPDATE'
    condition='' if fault=='start' else " AND NEW.value LIKE '%\"phase\":\""+('proposed' if fault=='proposal' else 'committed')+"\"%'"
    s.execute('CREATE TRIGGER synthetic_diag_fault BEFORE '+operation+' ON runtime WHEN NEW.key LIKE '+
              repr(trace.ACTION_PREFIX+'%')+condition+" BEGIN SELECT RAISE(ABORT,'synthetic diagnostic write fault'); END")
    run(e)
    assert len(e.tasks())==1 and s.execute('SELECT state FROM events WHERE id=?',('event-'+SECRET,)).fetchone()[0]=='done'
    assert len(s.rows("SELECT id FROM outbox WHERE dedupe LIKE 'action:%'"))==1
    assert 'Перспективная диагностика действий недоступна' in report(e,s)
    if fault!='start':assert document(s)['records'][0]['applied_count'] is None


def test_diagnostic_serialization_failure_does_not_block_valid_action(case,monkeypatch):
    e,s,m,_=case
    monkeypatch.setattr(trace,'_json',lambda value:(_ for _ in ()).throw(ValueError(SECRET)))
    run(e)
    assert len(e.tasks())==1 and 'диагностика действий недоступна' in report(e,s)


def test_transaction_fatal_diagnostic_trigger_aborts_business_and_reports_failure(case):
    e,s,m,_=case
    s.execute("CREATE TRIGGER synthetic_diag_fatal BEFORE UPDATE ON runtime "
              "WHEN NEW.key LIKE 'runtime-action-trace:v1:%' "
              "AND NEW.value LIKE '%\"phase\":\"committed\"%' "
              "BEGIN SELECT RAISE(ROLLBACK,'synthetic transaction-fatal diagnostic fault'); END")
    with pytest.raises(sqlite3.OperationalError):run(e)
    assert m.calls==1 and not e.tasks() and not s.db.in_transaction
    event=s.execute('SELECT state,error FROM events WHERE business=? AND id=?',
                    (e.b,'event-'+SECRET)).fetchone()
    assert event['state']=='failed' and event['error']
    assert not s.rows("SELECT id FROM outbox WHERE business=? AND dedupe LIKE 'action:%'",(e.b,))
    notice=s.execute('SELECT text FROM outbox WHERE business=? AND dedupe=?',
                     (e.b,'error:event-'+SECRET)).fetchone()
    assert notice and 'не могу его обработать' in notice['text']
    row=document(s)['records'][0]
    assert row['phase']!='committed' and row['applied_count'] is None and row['queued_count'] is None


@pytest.mark.parametrize('value',[SECRET,'x'*65537,'{"version":1,"version":1,"records":[]}','{"version":1,"records":[{}]}'])
def test_corrupt_or_oversize_journal_is_not_repaired_and_does_not_block_business(case,value):
    e,s,m,_=case;s.execute('INSERT INTO runtime VALUES(?,?)',(trace._action_key(e.b),value));run(e)
    assert len(e.tasks())==1
    assert s.execute('SELECT value FROM runtime WHERE key=?',(trace._action_key(e.b),)).fetchone()[0]==value
    text=report(e,s);assert SECRET not in text and 'диагностика действий недоступна' in text


def test_recent_sample_has_hard_record_and_output_bounds(case):
    e,s,m,_=case;m.mode='conversation'
    for i in range(66):run(e,event='synthetic-'+str(i),text='Подготовь приветствие '+str(i))
    doc=document(s);assert len(doc['records'])==64
    text=report(e,s)
    assert text.count('Наблюдение ')<=3 and len(text.encode('utf-16-le'))//2<=3500 and len(text.encode())<=8000
    assert 'не вся история' in text and len(e.tasks())==0


@pytest.mark.parametrize(('sender','channel'),[('frost','bot'),('test_operator','group'),('test_operator',None),('outsider','bot')])
def test_trace_remains_private_current_admin_only(case,sender,channel):
    e,s,m,_=case;run(e);before=m.calls
    e.reply('denied',sender,'/trace',channel=channel)
    assert not s.rows("SELECT id FROM outbox WHERE dedupe='trace:denied'") and m.calls==before


def test_loss_of_admin_or_business_scope_cannot_read_action_sample(case,monkeypatch):
    e,s,m,_=case;run(e);e.c['admins']=[]
    monkeypatch.setattr(trace,'_read_actions',lambda *a,**k:pytest.fail('Unauthorized action read'))
    e.reply('denied','test_operator','/trace',channel='bot')
    assert not s.rows("SELECT id FROM outbox WHERE dedupe='trace:denied'")


def test_action_sample_never_reconstructs_payload_audit_or_usage(case):
    e,s,m,_=case;run(e)
    def authorizer(action,table,column,*rest):
        if action==sqlite3.SQLITE_READ and table in ('events','audit','sources','tasks','usage'):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    s.db.set_authorizer(authorizer)
    try:lines=trace._action_lines(e)
    finally:s.db.set_authorizer(None)
    assert any('Применено: 1' in line for line in lines)


def test_deterministic_command_and_preexisting_events_stay_unobserved(case):
    e,s,m,_=case
    s.event(e.b,'old-event','reply',{'text':SECRET});s.execute("UPDATE events SET state='done' WHERE id='old-event'")
    text=report(e,s)
    assert m.calls==0 and 'пока не наблюдались' in text
    assert not s.rows('SELECT key FROM runtime WHERE key=?',(trace._action_key(e.b),))


def test_initialized_intelligence_full_schema_action_and_actual_private_trace(case):
    e,s,m,_=case;c=copy.deepcopy(e.c);c['intelligence']=dict(enabled=True,sources=[])
    initialized=Engine(s,c,m,lambda:NOW)
    run(initialized)
    row=document(s)['records'][0]
    assert initialized.intelligence is not None and len(initialized.tasks())==1
    assert row['proposed_ops']==['create_task'] and row['applied_count']==1
    assert SECRET not in report(initialized,s)


def test_twenty_proposals_per_observation_keep_whole_sample_render_bounded(case):
    e,s,m,_=case;m.mode='many'
    for i in range(3):run(e,event='many-'+str(i),text=TEXT+' '+str(i))
    assert all(len(row['proposed_ops'])==20 and row['applied_count']==20 for row in document(s)['records'])
    text=report(e,s)
    assert text.count('Наблюдение ') in (1,2,3)
    assert len(text.encode('utf-16-le'))//2<=3500 and len(text.encode())<=8000
