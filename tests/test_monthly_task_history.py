"""Synthetic actual Store/ledger/index boundaries; no providers or runtime hook."""
from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

import pytest

from goldway import monthly_task_history as adapter
from goldway.intake import TEAM_RECIPIENTS
from goldway.knowledge import KnowledgeIndex
from goldway.operational_reports import Period
from goldway.store import Store
from goldway.task_transition_history import _hash, append_transition, snapshot, start_observing
from goldway.work_context import work_id


ZONE = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 10, 20, 12, tzinfo=ZONE)
CUTOFF = datetime(2026, 10, 10, 9, tzinfo=ZONE).timestamp()


@pytest.fixture
def case():
    store = Store(':memory:')
    index = KnowledgeIndex(store)
    config = dict(business='GOLDWAY', timezone='Europe/Moscow',
        employees={key: dict(name='Synthetic '+key, telegram_user_id=n+1)
                   for n, key in enumerate(TEAM_RECIPIENTS)},
        admins=['test_operator'], business_account_user_id=99,
        team_intake=dict(recipients=list(TEAM_RECIPIENTS), started_at=100),
        allowed_live_recipients=list(TEAM_RECIPIENTS), team_roster_verified=True,
        recipients_verified=True, live_send=False, review_mode=True,
        sheets_write=False, daily_summary=False, allow_onboarding_replies=False,
        allow_registered_replies=False, allowed_group_ids=[], group_destinations={}, meeting_recipient=None)
    with store.transaction():
        start_observing(store, 'GOLDWAY', at=CUTOFF, timezone_name='Europe/Moscow')
    yield store, index, config
    store.db.close()


def create(case, task='a', *, at=CUTOFF+60, origin='new', status='confirmed',
           owner='frost', deadline='2026-10-21', business='GOLDWAY', history=True):
    store, _, _ = case
    with store.transaction():
        store.execute('''INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                      (business, task, 'PRIVATE-TITLE', owner, deadline, 'PRIVATE-NEXT', 'PRIVATE-CRITERION',
                       status, 'PRIVATE-SOURCE', 'PRIVATE-RESULT', at))
        if history:
            append_transition(store, business, task, None, at=at,
                              source='import' if origin == 'import' else 'reply_bot', creation=origin)
    return task


def change(case, task='a', *, at=CUTOFF+120, source='reply_bot', **fields):
    store = case[0]
    with store.transaction():
        before = snapshot(store, 'GOLDWAY', task)
        store.execute('UPDATE tasks SET '+','.join(key+'=?' for key in fields)+
                      ',revision=revision+1 WHERE business=? AND id=?', (*fields.values(), 'GOLDWAY', task))
        append_transition(store, 'GOLDWAY', task, before, at=at, source=source)


def report(case, task_ids=('a',), **kw):
    store, index, config = case
    return adapter.build_monthly_task_history(store, config, 'test_operator', index,
        task_ids=task_ids, period=kw.pop('period', Period(date(2026,10,1), date(2026,11,1))),
        as_of=kw.pop('as_of', NOW), **kw)


def acl(case, task, refs):
    store = case[0]
    store.execute('CREATE TABLE IF NOT EXISTS intelligence_task_acl(business TEXT,task_id TEXT,evidence_ids TEXT,PRIMARY KEY(business,task_id))')
    store.execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',
                  ('GOLDWAY', task, json.dumps(refs)))


def document(case, *, reader='test_operator', observed=CUTOFF, expires=NOW.timestamp()+1):
    index = case[1]
    index.upsert(dict(source_id='synthetic', document_id='doc', title='PRIVATE-DOC-TITLE',
        url='https://example.invalid/doc', text='PRIVATE-DOCUMENT-CONTENT',
        readers=['reader:'+_hash('GOLDWAY', reader)], observed_at=observed,
        permission_expires_at=expires))
    return case[0].rows('SELECT id FROM knowledge_chunks')[0]['id']


def denied(call):
    with pytest.raises(adapter.MonthlyHistoryUnavailable, match='^monthly_history_unavailable$'):
        call()


def test_actual_transition_counts_are_not_distinct_tasks_or_actual_completion(case):
    create(case)
    change(case, status='completed')
    change(case, at=CUTOFF+180, status='in_progress')
    change(case, at=CUTOFF+240, status='completed')
    value = report(case)
    assert (value.registered_creations, value.registered_changes, value.completion_transitions,
            value.distinct_tasks_with_completion_transition) == (1, 3, 2, 1)
    assert value.actual_completed_tasks is value.overdue_tasks is None
    assert not value.coverage_complete and value.core_status == 'provisional'
    assert value.core_observed_tasks == 1
    assert value.evidence_ids == (work_id('GOLDWAY', 'a'),)
    body = '\n'.join(value.lines)
    assert 'Переходы не означают фактическое выполнение' in body
    assert all(secret not in body for secret in ('PRIVATE', 'GOLDWAY', 'reader:', 'work-task:', 'frost'))


def test_real_monthly_core_receives_only_provisional_unknown_completion(case, monkeypatch):
    create(case)
    change(case, status='completed', deadline='2026-10-22')
    real = adapter.reports.monthly_snapshot
    captured = {}
    def spy(**kw):
        captured.update(kw)
        return real(**kw)
    monkeypatch.setattr(adapter.reports, 'monthly_snapshot', spy)
    report(case)
    assert captured['coverage'].complete is False
    row, = captured['records']
    assert row.completed_on is None and row.completion_state == 'unknown' and row.due_on is None
    assert row.owner == 'reader:'+_hash('GOLDWAY', 'frost')


def test_import_legacy_unknown_and_current_gap_are_separate(case):
    create(case, 'imported', origin='import', status='completed')
    create(case, 'legacy', history=False, status='completed')
    create(case, 'gap')
    case[0].execute("UPDATE tasks SET status='completed',revision=revision+1 WHERE id='gap'")
    value = report(case, ('imported', 'legacy', 'gap'))
    assert (value.selected_tasks, value.tracked_tasks, value.unknown_prior_tasks, value.gap_tasks) == (3,0,2,1)
    assert value.registered_creations is value.completion_transitions is None


def test_persistent_gap_and_nonmetric_revision_skip(case):
    create(case)
    change(case, title='OTHER-PRIVATE-TITLE')
    change(case, status='completed', at=CUTOFF+180)
    assert report(case).completion_transitions == 1
    case[0].execute("UPDATE tasks SET status='blocked',revision=revision+1 WHERE id='a'")
    change(case, status='confirmed', at=CUTOFF+240)
    assert report(case).gap_tasks == 1


@pytest.mark.parametrize('origin', ['new', 'recurring'])
def test_local_calendar_month_registration_boundary(case, origin):
    create(case, origin=origin, at=datetime(2026,10,31,23,59,tzinfo=ZONE).timestamp())
    change(case, at=datetime(2026,11,1,0,0,tzinfo=ZONE).timestamp(), status='completed')
    now = datetime(2026,11,2,tzinfo=ZONE)
    october = report(case, as_of=now)
    november = report(case, as_of=now, period=Period(date(2026,11,1), date(2026,12,1)))
    assert (october.registered_creations, october.completion_transitions) == (1,0)
    assert (november.registered_creations, november.completion_transitions) == (0,1)


def test_import_source_completion_is_not_completion_transition(case):
    create(case)
    change(case, source='import', status='completed')
    assert report(case).completion_transitions == 0


@pytest.mark.parametrize('owner', [None, 'not-configured'])
def test_unknown_assignment_does_not_manufacture_owner_or_hide_registered_counts(case, owner):
    create(case, owner=owner)
    value = report(case)
    assert value.registered_creations == 1 and value.core_observed_tasks == 0 and value.unattributed_tasks == 1


def test_changed_owner_keeps_global_counts_without_attributing_historical_performance(case):
    create(case)
    change(case, owner='alen')
    value = report(case)
    assert value.registered_changes == 1 and value.unattributed_tasks == 1


def test_empty_explicit_sample_is_unknown_not_business_zero(case):
    value = report(case, ())
    assert value.selected_tasks == 0 and value.registered_creations is None
    assert value.evidence_ids == () and 'счётчики неизвестны' in '\n'.join(value.lines)


def test_dependency_scope_and_every_consulted_reference_returned(case):
    create(case)
    create(case, 'dep')
    ref = document(case)
    acl(case, 'a', [work_id('GOLDWAY','dep')])
    acl(case, 'dep', [ref])
    denied(lambda: report(case))
    value = report(case, dependency_task_ids=('a','dep'))
    assert value.selected_tasks == 1 and value.registered_creations == 1
    assert set(value.evidence_ids) == {work_id('GOLDWAY','a'), work_id('GOLDWAY','dep'), ref}


@pytest.mark.parametrize('kind', ['foreign', 'missing', 'malformed'])
def test_no_missing_foreign_or_malformed_dependency_grants(case, kind):
    create(case)
    if kind == 'foreign':
        create(case, 'foreign', business='OTHER', history=False)
        refs = [work_id('OTHER','foreign')]
    elif kind == 'missing':
        refs = ['unknown-document-ref']
    else:
        refs = {'not': 'a-list'}
    acl(case, 'a', refs)
    denied(lambda: report(case, dependency_task_ids=('foreign',) if kind == 'foreign' else ()))


@pytest.mark.parametrize('kind', ['other_reader', 'expired', 'future', 'deleted', 'revoked'])
def test_fresh_current_document_acl(case, kind):
    create(case)
    ref = document(case, reader='alen' if kind == 'other_reader' else 'test_operator',
                   expires=NOW.timestamp() if kind == 'expired' else NOW.timestamp()+100,
                   observed=NOW.timestamp()+1 if kind == 'future' else CUTOFF)
    acl(case, 'a', [ref])
    if kind == 'deleted':
        case[0].execute('DELETE FROM knowledge_chunks WHERE id=?', (ref,))
    if kind == 'revoked':
        case[1].revoke_source('synthetic', observed_at=NOW.timestamp())
    denied(lambda: report(case))


@pytest.mark.parametrize('kind', ['employee', 'roster', 'admin', 'timezone', 'other_index', 'no_marker'])
def test_current_team_admin_marker_and_index_identity(case, kind):
    create(case)
    store,index,config = case
    sender='test_operator'
    if kind == 'employee': sender='frost'
    elif kind == 'roster': config['team_roster_verified']=False
    elif kind == 'admin': config['admins']=['frost']
    elif kind == 'timezone': config['timezone']='UTC'
    elif kind == 'other_index': index=KnowledgeIndex(Store(':memory:'))
    elif kind == 'no_marker':
        store.execute('DROP TRIGGER task_history_observation_no_delete')
        store.execute('DELETE FROM task_history_observation')
    denied(lambda: adapter.build_monthly_task_history(store,config,sender,index,task_ids=('a',),
        period=Period(date(2026,10,1),date(2026,11,1)),as_of=NOW))
    if kind == 'other_index': index.store.db.close()


@pytest.mark.parametrize('kind', ['task', 'acl', 'doc_permission', 'doc_deletion', 'ledger', 'config'])
def test_snapshot_revalidation_after_actual_core_projection(case, monkeypatch, kind):
    create(case)
    ref = document(case)
    acl(case, 'a', [ref])
    real = adapter.reports.monthly_snapshot
    def mutate(**kw):
        value=real(**kw)
        if kind == 'task': case[0].execute("UPDATE tasks SET status='blocked',revision=revision+1 WHERE id='a'")
        elif kind == 'acl': acl(case, 'a', ['revoked'])
        elif kind == 'doc_permission': case[0].execute('DELETE FROM knowledge_readers')
        elif kind == 'doc_deletion': case[0].execute('DELETE FROM knowledge_chunks WHERE id=?',(ref,))
        elif kind == 'ledger': change(case,status='completed')
        else: case[2]['employees']['frost']['telegram_user_id']=777
        return value
    monkeypatch.setattr(adapter.reports,'monthly_snapshot',mutate)
    denied(lambda: report(case))


@pytest.mark.parametrize('kind', ['list', 'duplicate', 'many', 'missing', 'naive', 'period', 'future_row'])
def test_input_and_time_boundaries(case, kind):
    create(case)
    kwargs={}
    selected=('a',)
    if kind == 'list': selected=['a']
    elif kind == 'duplicate': selected=('a','a')
    elif kind == 'many': selected=tuple('t'+str(n) for n in range(65))
    elif kind == 'missing': selected=('missing',)
    elif kind == 'naive': kwargs['as_of']=NOW.replace(tzinfo=None)
    elif kind == 'period': kwargs['period']=Period(date(2026,10,2),date(2026,11,1))
    elif kind == 'future_row': kwargs['as_of']=datetime.fromtimestamp(CUTOFF,ZONE)
    denied(lambda: report(case,selected,**kwargs))


def test_archive_never_scanned_and_no_titles_sources_bodies_or_writes(case, monkeypatch):
    create(case)
    for n in range(100): create(case,'unselected-'+str(n),history=False)
    trace=[]
    case[0].db.set_trace_callback(trace.append)
    def forbidden(*a,**k): pytest.fail('adapter attempted external I/O')
    import builtins, socket
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    before=case[0].db.total_changes
    value=report(case)
    assert value.selected_tasks == 1 and case[0].db.total_changes == before
    assert all(sql.lstrip().upper().startswith('SELECT ') for sql in trace)
    assert not any(word in ' '.join(trace).lower() for word in ('title','evidence from','body','next_step','criterion','count('))
    assert all(('business=' in sql and ('id=' in sql or 'task_ref=' in sql))
               for sql in trace if ' FROM tasks ' in sql or ' FROM task_status_history ' in sql)


def test_history_budget_preflight_rejects_before_payload_query(case, monkeypatch):
    create(case)
    for n in range(64):
        change(case,at=CUTOFF+120+n,status='blocked' if n%2==0 else 'confirmed')
    statements=[]
    case[0].db.set_trace_callback(statements.append)
    denied(lambda: report(case))
    assert any('SELECT 1 AS present FROM task_status_history' in sql for sql in statements)
    assert not any('AS task_ref' in sql for sql in statements)


def test_total_history_budget_and_second_snapshot_budget_growth(case, monkeypatch):
    create(case)
    monkeypatch.setattr(adapter,'MAX_HISTORY',0)
    denied(lambda: report(case))


def test_oversized_acl_cell_rejected_in_sql_without_transfer(case):
    create(case)
    acl(case,'a',[])
    case[0].execute('UPDATE intelligence_task_acl SET evidence_ids=?',('x'*100000,))
    denied(lambda: report(case))


def test_completed_at_creation_is_not_a_registered_completion_transition(case):
    create(case,status='completed')
    value=report(case)
    assert value.registered_creations == 1 and value.completion_transitions == 0


def test_selected_legacy_tasks_still_participate_in_provenance(case):
    create(case,'legacy',history=False)
    ref=document(case)
    acl(case,'legacy',[ref])
    value=report(case,('legacy',))
    assert set(value.evidence_ids)=={work_id('GOLDWAY','legacy'),ref}
    assert value.unknown_prior_tasks==1


def test_indexed_query_plans(case):
    plans=[case[0].rows('EXPLAIN QUERY PLAN SELECT revision FROM task_status_history WHERE business=? AND task_ref=? ORDER BY revision',
                       ('GOLDWAY',work_id('GOLDWAY','a'))),
           case[0].rows('EXPLAIN QUERY PLAN SELECT revision FROM tasks WHERE business=? AND id=?',('GOLDWAY','a'))]
    assert all(any('SEARCH' in row['detail'] and 'INDEX' in row['detail'] for row in plan) for plan in plans)
