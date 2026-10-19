"""Synthetic Store-owned transactions: connection swaps never control another TX."""
import sqlite3
import pytest
from goldway.store import Store
from goldway.engine import Engine
from goldway.model import response_schema


@pytest.fixture
def pair():
 first=Store(':memory:');second=Store(':memory:');original=first.db
 yield first,second,original
 first.db=original
 for db in (original,second.db):
  try:db.set_authorizer(None);db.close()
  except sqlite3.ProgrammingError:pass


def put(db,key):db.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(key,'synthetic'))
def keys(db):return [r[0] for r in db.execute('SELECT key FROM runtime ORDER BY key')]


@pytest.mark.parametrize('error',[None,ValueError('body'),KeyboardInterrupt('body'),SystemExit('body')])
def test_body_swap_rolls_back_original_preserves_unrelated_transaction_and_primary(pair,error):
 store,second,original=pair;replacement=second.db
 replacement.execute('BEGIN IMMEDIATE');put(replacement,'unrelated')
 observed=[];replacement.set_trace_callback(observed.append)
 expected=RuntimeError if error is None else type(error)
 with pytest.raises(expected) as caught:
  with store.transaction():
   put(original,'owned');store.db=replacement
   if error is not None:raise error
 if error is not None:assert caught.value is error
 else:assert str(caught.value)=='store_transaction_connection_changed'
 assert store.db is replacement and not original.in_transaction and keys(original)==[]
 assert replacement.in_transaction and keys(replacement)==['unrelated']
 assert not any(q in ('COMMIT','ROLLBACK') for q in observed)
 replacement.rollback();assert keys(replacement)==[]


@pytest.mark.parametrize('error',[ValueError('body'),KeyboardInterrupt('body'),SystemExit('body'),GeneratorExit('body')])
def test_stable_body_failure_preserves_same_exception_and_rolls_back(pair,error):
 store,_,original=pair
 with pytest.raises(type(error)) as caught:
  with store.transaction():put(original,'owned');raise error
 assert caught.value is error and not original.in_transaction and keys(original)==[]


def test_stable_normal_commit_remains_interceptable(pair,monkeypatch):
 store,_,original=pair;actual=store.execute;seen=[]
 def execute(sql,args=()):seen.append(sql);return actual(sql,args)
 monkeypatch.setattr(store,'execute',execute)
 with store.transaction():put(original,'committed')
 assert keys(original)==['committed'] and not original.in_transaction
 assert seen==['BEGIN IMMEDIATE','COMMIT']


@pytest.mark.parametrize('error',[sqlite3.OperationalError('commit'),KeyboardInterrupt('commit'),SystemExit('commit')])
def test_commit_hook_failure_before_commit_rolls_back_original(pair,monkeypatch,error):
 store,_,original=pair;actual=store.execute
 def execute(sql,args=()):
  if sql=='COMMIT':raise error
  return actual(sql,args)
 monkeypatch.setattr(store,'execute',execute)
 with pytest.raises(type(error)) as caught:
  with store.transaction():put(original,'owned')
 assert caught.value is error and keys(original)==[] and not original.in_transaction


@pytest.mark.parametrize('error',[sqlite3.OperationalError('after-commit'),KeyboardInterrupt('after-commit')])
def test_commit_hook_actually_commits_then_raises_reports_uncertainty_no_undo(pair,monkeypatch,error):
 store,second,original=pair;actual=store.execute
 second.db.execute('BEGIN IMMEDIATE');put(second.db,'unrelated')
 def execute(sql,args=()):
  result=actual(sql,args)
  if sql=='COMMIT':store.db=second.db;raise error
  return result
 monkeypatch.setattr(store,'execute',execute)
 with pytest.raises(type(error)) as caught:
  with store.transaction():put(original,'committed-before-fault')
 assert caught.value is error and keys(original)==['committed-before-fault'] and not original.in_transaction
 assert second.db.in_transaction and keys(second.db)==['unrelated']


def test_commit_hook_returns_without_committing_refuses_and_rolls_back(pair,monkeypatch):
 store,_,original=pair;actual=store.execute
 monkeypatch.setattr(store,'execute',lambda sql,args=():None if sql=='COMMIT' else actual(sql,args))
 with pytest.raises(RuntimeError,match='store_transaction_commit_incomplete'):
  with store.transaction():put(original,'owned')
 assert not original.in_transaction and not keys(original)


def test_commit_hook_swaps_without_commit_refuses_original_only(pair,monkeypatch):
 store,second,original=pair;actual=store.execute
 second.db.execute('BEGIN IMMEDIATE');put(second.db,'unrelated')
 def execute(sql,args=()):
  if sql=='COMMIT':store.db=second.db;return None
  return actual(sql,args)
 monkeypatch.setattr(store,'execute',execute)
 with pytest.raises(RuntimeError):
  with store.transaction():put(original,'owned')
 assert not original.in_transaction and not keys(original)
 assert second.db.in_transaction and keys(second.db)==['unrelated']


@pytest.mark.parametrize('error',[sqlite3.OperationalError('begin'),KeyboardInterrupt('begin')])
@pytest.mark.parametrize('opened',[False,True])
def test_begin_hook_failure_ownership_cleanup_body_never_runs(pair,monkeypatch,error,opened):
 store,_,original=pair;actual=store.execute;body=[]
 def execute(sql,args=()):
  if sql=='BEGIN IMMEDIATE':
   if opened:actual(sql,args);put(original,'begin-hook')
   raise error
  return actual(sql,args)
 monkeypatch.setattr(store,'execute',execute)
 with pytest.raises(type(error)) as caught:
  with store.transaction():body.append(1)
 assert caught.value is error and not body and not original.in_transaction and not keys(original)


def test_begin_hook_returns_without_transaction_never_runs_body(pair,monkeypatch):
 store,_,original=pair;actual=store.execute;body=[]
 monkeypatch.setattr(store,'execute',lambda sql,args=():None if sql=='BEGIN IMMEDIATE' else actual(sql,args))
 with pytest.raises(RuntimeError,match='store_transaction_begin_unavailable'):
  with store.transaction():body.append(1)
 assert not body and not original.in_transaction


def test_begin_hook_open_then_swap_rolls_back_only_original(pair,monkeypatch):
 store,second,original=pair;actual=store.execute;body=[]
 second.db.execute('BEGIN IMMEDIATE');put(second.db,'unrelated')
 def execute(sql,args=()):
  result=actual(sql,args)
  if sql=='BEGIN IMMEDIATE':store.db=second.db
  return result
 monkeypatch.setattr(store,'execute',execute)
 with pytest.raises(RuntimeError,match='store_transaction_connection_changed'):
  with store.transaction():body.append(1)
 assert not body and not original.in_transaction
 assert second.db.in_transaction and keys(second.db)==['unrelated']


def test_external_original_transaction_not_rolled_back_or_body_run(pair):
 store,_,original=pair;original.execute('BEGIN IMMEDIATE');put(original,'external');body=[]
 with pytest.raises(sqlite3.OperationalError):
  with store.transaction():body.append(1)
 assert not body and original.in_transaction and keys(original)==['external']
 original.commit();assert keys(original)==['external']


def test_nested_context_rejected_without_rolling_back_outer(pair):
 store,_,original=pair;body=[]
 with store.transaction():
  put(original,'outer')
  with pytest.raises(sqlite3.OperationalError):
   with store.transaction():body.append(1)
  assert original.in_transaction and keys(original)==['outer']
 assert not body and keys(original)==['outer'] and not original.in_transaction


@pytest.mark.parametrize('body_raises',[False,True])
def test_closed_original_cleanup_preserves_primary_without_reopen(pair,body_raises):
 store,_,original=pair;error=ValueError('primary')
 with pytest.raises(ValueError if body_raises else sqlite3.ProgrammingError) as caught:
  with store.transaction():
   put(original,'owned');original.close()
   if body_raises:raise error
 if body_raises:assert caught.value is error
 assert store.db is original


def test_closed_replacement_not_touched_original_still_rolled_back(pair):
 store,second,original=pair;second.db.close()
 with pytest.raises(RuntimeError,match='store_transaction_connection_changed'):
  with store.transaction():put(original,'owned');store.db=second.db
 assert not original.in_transaction and not keys(original)


def test_cleanup_authorizer_failure_preserves_primary_and_reports_no_immunity(pair):
 store,_,original=pair;error=ValueError('primary cleanup failure')
 def authorizer(action,arg,*unused):
  return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_TRANSACTION and arg=='ROLLBACK' else sqlite3.SQLITE_OK
 with pytest.raises(ValueError) as caught:
  with store.transaction():put(original,'owned');original.set_authorizer(authorizer);raise error
 assert caught.value is error
 # Actual SQLite denial prevented rollback; no automatic repair or success claim.
 assert original.in_transaction
 original.set_authorizer(None);original.rollback();assert not keys(original)


def test_transaction_fatal_sqlite_preserves_primary_no_masking_rollback(pair):
 store,_,original=pair
 original.execute("CREATE TRIGGER fatal BEFORE INSERT ON runtime WHEN NEW.key='fatal' BEGIN SELECT RAISE(ROLLBACK,'synthetic fatal'); END")
 with pytest.raises(sqlite3.IntegrityError,match='synthetic fatal'):
  with store.transaction():put(original,'owned');put(original,'fatal')
 assert not original.in_transaction and not keys(original)


def test_body_manual_rollback_cannot_be_reported_as_committed(pair):
 store,_,original=pair
 with pytest.raises(sqlite3.OperationalError):
  with store.transaction():put(original,'owned');original.rollback()
 assert not original.in_transaction and not keys(original)


def test_actual_file_close_reopen_commit_vs_failure(tmp_path):
 path=str(tmp_path/'synthetic-transactions.sqlite');s=Store(path)
 with s.transaction():put(s.db,'committed')
 with pytest.raises(ValueError):
  with s.transaction():put(s.db,'rolled-back');raise ValueError('fixture')
 s.db.close();s=Store(path)
 try:assert keys(s.db)==['committed'] and not s.db.in_transaction
 finally:s.db.close()


class Model:
 def propose(self,context):
  action=dict(op='create',task_id=None,title='Подготовить контрольный список',owner=None,deadline=None,next_step='Составить список',criterion='Список подготовлен',status='confirmed',evidence_lines=[1],owner_lines=[],deadline_lines=[],set_fields=[])
  result=dict(actions=[action],intent='task_action',summary='Synthetic',reply_text='Черновик подготовлен')
  schema=response_schema(context)
  if 'dialogue_state' in schema['required']:result.update(dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
  if 'meeting_question_lines' in schema['required']:result['meeting_question_lines']=[]
  assert set(result)==set(schema['required'])
  return result


def engine_fixture():
 members=['test_operator','frost','alen','nastya','dasha_it']
 c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(members)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=members[:],started_at=100),allowed_live_recipients=members[:],team_roster_verified=True,recipients_verified=True,live_send=False,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None)
 s=Store(':memory:');return Engine(s,c,Model(),lambda:1792400000),s


@pytest.mark.parametrize('commit_fails',[False,True])
def test_actual_engine_task_and_action_outbox_commit_or_rollback_together(monkeypatch,commit_fails):
 e,s=engine_fixture();actual=s.execute;fault=[]
 e.reply('synthetic-create','test_operator','Создай задачу: подготовить контрольный список.',source_id='telegram:1:fixture',channel='bot')
 def execute(sql,args=()):
  if sql=='COMMIT' and commit_fails and not fault and s.db.execute('SELECT count(*) FROM tasks').fetchone()[0]:
   fault.append(1);raise sqlite3.OperationalError('synthetic late commit failure')
  return actual(sql,args)
 monkeypatch.setattr(s,'execute',execute)
 try:
  if commit_fails:
   with pytest.raises(sqlite3.OperationalError,match='synthetic late commit failure'):e.process('synthetic-create')
  else:e.process('synthetic-create')
  tasks=s.rows('SELECT * FROM tasks');queued=s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'action:%'")
  assert bool(tasks)==bool(queued)==(not commit_fails) and not s.db.in_transaction
 finally:s.db.close()
