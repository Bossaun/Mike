"""Only the new typed history failure -> safe event -> real trace boundary."""
import json
import pytest
import goldway.engine as engine_module
from goldway.engine import Engine
from goldway.errors import event_error,event_log_error
from goldway.model import ScriptedModel
from goldway.model_trace import SafeReason
from goldway.store import Store
from goldway.task_transition_history import HistoryError
from goldway.trace_adapter import _snapshot

CODE='ValueError: task_history_unavailable'
PRIVATE='SYNTHETIC_PRIVATE_TEXT_KEY_TASK_SOURCE_NEVER_DIAGNOSTIC'
NOW=1792174600
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']


def test_exact_typed_fixed_error_gets_canonical_code_and_safe_log_allowlist():
    exc=HistoryError('task_history_unavailable')
    assert event_error(exc)==CODE
    assert event_log_error(CODE,RuntimeError(PRIVATE))==CODE
    assert event_log_error(PRIVATE,exc)==CODE
    assert SafeReason.TASK_HISTORY_UNAVAILABLE.value=='task_history_unavailable'


class ChildHistory(HistoryError):pass
class PretendHistory(ValueError):pass


@pytest.mark.parametrize(('exc','expected'),[
    (HistoryError(PRIVATE),'HistoryError'),
    (HistoryError('task_history_unavailable '+PRIVATE),'HistoryError'),
    (HistoryError('prefix task_history_unavailable'),'HistoryError'),
    (HistoryError('task_history_unavailable',PRIVATE),'HistoryError'),
    (HistoryError(),'HistoryError'),
    (HistoryError(True),'HistoryError'),
    (ChildHistory('task_history_unavailable'),'ChildHistory'),
    (PretendHistory('task_history_unavailable'),'PretendHistory'),
    (ValueError('task_history_unavailable'),'ValueError'),
    (RuntimeError('task_history_unavailable'),'RuntimeError'),
])
def test_nearby_or_unowned_errors_remain_class_only(exc,expected):
    assert event_error(exc)==expected
    assert event_log_error(PRIVATE,exc)==expected
    assert PRIVATE not in event_error(exc)


def test_message_subclass_not_accepted_and_arbitrary_str_not_invoked():
    class FixedText(str):pass
    class Secret:
        def __str__(self):pytest.fail('diagnostic stringified arbitrary argument')
    assert event_error(HistoryError(FixedText('task_history_unavailable')))=='HistoryError'
    assert event_error(HistoryError(Secret()))=='HistoryError'


@pytest.fixture
def actual(monkeypatch):
    s=Store(':memory:')
    config={'business':'GOLDWAY','timezone':'Europe/Moscow',
      'employees':{key:{'name':key.title(),'telegram_user_id':i+1} for i,key in enumerate(MEMBERS)},
      'admins':['test_operator'],'business_account_user_id':99,
      'team_intake':{'recipients':MEMBERS[:],'started_at':100},
      'allowed_live_recipients':MEMBERS[:],'team_roster_verified':True,'recipients_verified':True,
      'live_send':False,'review_mode':True,'sheets_write':False,'daily_summary':False,
      'allow_onboarding_replies':False,'allow_registered_replies':False,
      'allowed_group_ids':[],'group_destinations':{},'meeting_recipient':None,
      'model':{'key':PRIVATE}}
    e=Engine(s,config,None,lambda:NOW)
    import builtins,socket,httpx
    def forbidden(*a,**k):pytest.fail('history diagnostic boundary attempted external call')
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(httpx,'Client',forbidden)
    yield e,s
    s.db.close()


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_ledger_failure_rolls_back_then_private_trace_aggregates_safe_code(actual,monkeypatch,channel):
    e,s=actual
    text='Frost prepares '+PRIVATE+' by 2026-10-21.'
    action=dict(op='create',title=PRIVATE,owner='frost',owner_evidence='Frost',
                deadline='2026-10-21',deadline_evidence='2026-10-21',
                next_step=PRIVATE,criterion=PRIVATE,status='confirmed',evidence=text)
    e.model=ScriptedModel({'actions':[action],'summary':PRIVATE})
    real=engine_module.append_transition
    def ledger_failure(*args,**kwargs):
        real(*args,**kwargs)
        raise HistoryError('task_history_unavailable')
    monkeypatch.setattr(engine_module,'append_transition',ledger_failure)
    event=e.transcript(text,link=PRIVATE)
    with pytest.raises(HistoryError,match='^task_history_unavailable$'):e.process(event)
    assert s.rows('SELECT id FROM tasks')==[]
    assert s.rows('SELECT revision FROM task_status_history')==[]
    row=s.execute('SELECT state,error FROM events WHERE business=? AND id=?',('GOLDWAY',event)).fetchone()
    assert tuple(row)==('failed',CODE)
    assert event_log_error(row['error'],HistoryError(PRIVATE))==CODE
    # Unknown/legacy/raw and another business cannot masquerade as this reason.
    for business,ident,error in [('GOLDWAY','legacy','HistoryError'),('GOLDWAY','raw',PRIVATE),('OTHER','foreign',CODE)]:
        s.execute('INSERT INTO events(business,id,kind,payload,state,error) VALUES(?,?,?,?,?,?)',
                  (business,ident,'reply',json.dumps({'text':PRIVATE}),'failed',error))
    snapshot=_snapshot(s,business='GOLDWAY',now=NOW)
    assert dict(snapshot.errors)[SafeReason.TASK_HISTORY_UNAVAILABLE]==1
    assert dict(snapshot.errors)[SafeReason.UNKNOWN]==2
    class NeverModel:
        def propose(self,*a,**kw):pytest.fail('/trace called a model')
    e.model=NeverModel()
    assert e.reply('trace-new','test_operator','/trace',channel=channel,source_id=PRIVATE)
    reply=s.execute('SELECT text FROM outbox WHERE business=? AND dedupe=?',('GOLDWAY','trace:trace-new')).fetchone()[0]
    assert 'task_history_unavailable=1' in reply and PRIVATE not in reply
    assert 'Предложенные действия: нет данных; применённые действия: нет данных' in reply
    assert 'unknown не означает успех' in reply
    assert len(reply.encode('utf-16-le'))//2<3500
    assert not s.rows('SELECT id FROM usage')
    assert not s.rows('SELECT id FROM tasks')
    assert not s.rows('SELECT revision FROM task_status_history')
