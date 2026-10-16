"""New Store-to-week-comparison boundary; fictional in-memory records only."""

import json
import socket
import sqlite3
import time
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, timedelta

import pytest

import goldway.meeting_week_adapter as adapter
from goldway.meeting_review import Agreement, Meeting, ReviewError, Source, Span, TaskRecord
from goldway.meeting_week_adapter import ResultBinding, TaskBinding, project_week
from goldway.store import Store
from goldway.work_context import work_id


DAY = date(2026, 10, 12)
END = DAY + timedelta(days=7)
NOW = 1792152000.0
BUSINESS = 'fictional'
PRIVATE = 'UNSELECTED-PRIVATE-TEXT'


def span(source):
    return Span(source.id, 2, 2, source.lines[1])


def persist_source(store, source):
    store.execute('INSERT INTO sources VALUES(?,?,?,?,0)',
                  (source.business, source.id, '\n'.join(source.lines), 'PRIVATE-UNRETURNED-LINK'))


def persist_task(store, task_id='T1', *, business=BUSINESS, owner='alice', status='completed'):
    store.execute('''INSERT INTO tasks(business,id,title,owner,next_step,criterion,
        status,source_id,evidence,created,last_reply) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (business, task_id, 'Подготовить учебный список', owner, 'Проверить пункты',
         'Три пункта', status, 'task-source', 'Зарегистрировано: список готов.', NOW-86400, NOW))
    for source_id in ('task-source', 'result-source'):
        store.execute('INSERT INTO task_sources VALUES(?,?,?)', (business, task_id, source_id))


@pytest.fixture
def case():
    store = Store(':memory:')
    store.execute('''CREATE TABLE intelligence_task_acl(
        business TEXT,task_id TEXT,evidence_ids TEXT,PRIMARY KEY(business,task_id))''')
    config = {'business': BUSINESS, 'admins': ['boss'], 'employees': {
        'boss': {'name': 'Руководитель'}, 'alice': {'name': 'Алиса'}, 'bob': {'name': 'Борис'}}}
    meeting_source = Source('meeting-source', BUSINESS, DAY,
                            (PRIVATE, 'Подготовить учебный список', PRIVATE))
    task_source = Source('task-source', BUSINESS, DAY + timedelta(days=1),
                         (PRIVATE, 'Задача T1 связана с договорённостью A.', PRIVATE))
    result_source = Source('result-source', BUSINESS, DAY + timedelta(days=3),
                           (PRIVATE, 'Зарегистрировано: список готов.', PRIVATE))
    for source in (meeting_source, task_source, result_source):
        persist_source(store, source)
    persist_task(store)
    meeting = Meeting('M', BUSINESS, DAY, (meeting_source,), (
        Agreement('A', 'decision', 'Подготовить учебный список', (span(meeting_source),), True),))
    params = dict(meeting=meeting, start=DAY, end=END,
                  task_bindings=(TaskBinding(BUSINESS, 'A', 'T1', (span(task_source),), True),),
                  result_bindings=(ResultBinding(BUSINESS, 'R1', 'T1', result_source.on,
                                                 'completed', (span(result_source),), True),),
                  sources=(task_source, result_source), document_ids={'document:meeting'},
                  meeting_evidence_ids={'document:meeting'}, meeting_anchor_confirmed=True, now=NOW)
    yield store, config, params
    store.db.close()


def project(case, sender='boss', **changes):
    store, config, params = case
    return project_week(store, config, sender, **(params | changes))


@pytest.mark.parametrize('outcome', ['completed', 'blocked', 'in_progress', 'conflict'])
def test_actual_store_reaches_existing_comparator(case, monkeypatch, outcome):
    store, _, params = case
    original = adapter.compare_week
    observed = []

    def inspect(meeting, **kwargs):
        observed.append(kwargs)
        assert type(kwargs['tasks'][0]) is TaskRecord
        assert kwargs['tasks'][0].recorded_status == 'completed'
        return original(meeting, **kwargs)

    monkeypatch.setattr(adapter, 'compare_week', inspect)
    first = params['result_bindings'][0]
    sources = params['sources']
    results = (first,)
    if outcome != 'completed':
        report = 'Работа заблокирована: не хватает доступа.' if outcome in ('blocked', 'conflict') else 'Учебный список ещё проверяется.'
        source = Source('other-result', BUSINESS, first.on, (PRIVATE, report, PRIVATE))
        persist_source(store, source)
        store.execute('INSERT INTO task_sources VALUES(?,?,?)', (BUSINESS, 'T1', source.id))
        sources += (source,)
        results = (replace(first, outcome='blocked' if outcome == 'conflict' else outcome, spans=(span(source),)),)
    if outcome == 'conflict':
        results += (replace(first, id='R2', outcome='completed'),)
    draft = project(case, result_bindings=results, sources=sources)
    assert len(observed) == 1 and draft.progress[0].state == outcome
    assert draft.progress[0].tasks[0].result_state == outcome
    assert draft.review_only is True and draft.result_scope == 'caller_bound_registered_reports'
    assert 'не проверены независимо' in draft.limitation
    assert store.execute('SELECT status FROM tasks').fetchone()[0] == 'completed'
    with pytest.raises(FrozenInstanceError):
        draft.review_only = False


def test_completed_current_task_without_dated_result_stays_missing(case):
    draft = project(case, result_bindings=())
    progress = draft.progress[0]
    assert progress.state == 'missing_results'
    assert progress.tasks[0].recorded_status == 'completed'
    assert progress.tasks[0].result_state == 'missing' and progress.tasks[0].result_ids == ()


@pytest.mark.parametrize(('on', 'outcome'), [(None, 'completed'), (DAY, None), (None, None)])
def test_missing_supplied_date_or_outcome_is_never_inferred(case, on, outcome):
    result = replace(case[2]['result_bindings'][0], on=on, outcome=outcome)
    if on is not None:
        source = replace(case[2]['sources'][1], on=on)
        sources = (case[2]['sources'][0], source)
    else:
        sources = case[2]['sources']
    assert project(case, result_bindings=(result,), sources=sources).progress[0].state == 'missing_results'


def test_exact_matching_title_never_creates_a_binding(case):
    draft = project(case, task_bindings=(), result_bindings=())
    assert draft.progress[0].state == 'unlinked' and draft.progress[0].tasks == ()


@pytest.mark.parametrize('sender', ['boss', 'alice'])
def test_current_admin_or_own_task_reader_can_project(case, sender):
    assert project(case, sender).progress[0].state == 'completed'


@pytest.mark.parametrize('sender', ['bob', 'outsider'])
def test_denied_role_or_owner_cannot_read_any_source_body(case, sender):
    store, _, _ = case

    def authorize(action, table, column, *_):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'sources' else sqlite3.SQLITE_OK

    store.db.set_authorizer(authorize)
    with pytest.raises(ReviewError, match='access_unavailable'):
        project(case, sender)


@pytest.mark.parametrize('problem', ['empty', 'unconfirmed', 'revoked', 'work_ref'])
def test_meeting_requires_confirmed_nonempty_current_document_anchor_before_reads(case, problem):
    changes = {'empty': {'meeting_evidence_ids': set()},
               'unconfirmed': {'meeting_anchor_confirmed': False},
               'revoked': {'document_ids': set()},
               'work_ref': {'document_ids': {work_id(BUSINESS, 'T1')},
                            'meeting_evidence_ids': {work_id(BUSINESS, 'T1')}}}[problem]
    case[0].db.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK)
    with pytest.raises(ReviewError) as error:
        project(case, **changes)
    assert PRIVATE not in str(error.value) and 'Подготовить' not in str(error.value)


def test_current_document_task_acl_is_additional_to_meeting_anchor(case):
    store, _, _ = case
    store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',
                  (BUSINESS, 'T1', '["document:task"]'))
    with pytest.raises(ReviewError, match='weekly_task_access_unavailable'):
        project(case)
    assert project(case, document_ids={'document:meeting', 'document:task'}).progress[0].state == 'completed'
    store.execute('UPDATE intelligence_task_acl SET evidence_ids=?', ('not-json',))
    with pytest.raises(ReviewError, match='weekly_task_access_unavailable'):
        project(case, document_ids={'document:meeting', 'document:task'})


@pytest.mark.parametrize('change', ['config', 'task', 'result', 'source'])
def test_foreign_business_never_returns_progress(case, change):
    if change == 'config': case[1]['business'] = 'foreign'
    options = {}
    if change == 'task': options['task_bindings'] = (replace(case[2]['task_bindings'][0], business='foreign'),)
    if change == 'result': options['result_bindings'] = (replace(case[2]['result_bindings'][0], business='foreign'),)
    if change == 'source': options['sources'] = (replace(case[2]['sources'][0], business='foreign'), case[2]['sources'][1])
    with pytest.raises(ReviewError):
        project(case, **options)


@pytest.mark.parametrize('change', [
    'deleted_meeting', 'deleted_task', 'deleted_result', 'missing_source',
    'outside_span_change', 'result_span_change', 'unlinked_source',
    'foreign_link', 'foreign_source', 'forged_span', 'result_date_mismatch',
])
def test_bad_canonical_provenance_is_rejected_before_comparator(case, monkeypatch, change):
    store, _, params = case
    options = {}
    if change.startswith('deleted_'):
        source_id = {'deleted_meeting': 'meeting-source', 'deleted_task': 'task-source', 'deleted_result': 'result-source'}[change]
        store.execute('UPDATE sources SET deleted=1 WHERE id=?', (source_id,))
    elif change == 'missing_source': store.execute("DELETE FROM sources WHERE id='result-source'")
    elif change == 'outside_span_change': store.execute("UPDATE sources SET body=body||'changed' WHERE id='result-source'")
    elif change == 'result_span_change': store.execute("UPDATE sources SET body='not a result' WHERE id='result-source'")
    elif change == 'unlinked_source': store.execute("DELETE FROM task_sources WHERE source_id='result-source'")
    elif change == 'foreign_link': store.execute("UPDATE task_sources SET business='foreign' WHERE source_id='result-source'")
    elif change == 'foreign_source': store.execute("UPDATE sources SET business='foreign' WHERE id='result-source'")
    elif change == 'forged_span':
        result = params['result_bindings'][0]
        options['result_bindings'] = (replace(result, spans=(replace(result.spans[0], quote='Invented result'),)),)
    elif change == 'result_date_mismatch':
        options['result_bindings'] = (replace(params['result_bindings'][0], on=DAY),)

    def forbidden(*args, **kwargs):
        pytest.fail('invalid provenance reached comparator')

    monkeypatch.setattr(adapter, 'compare_week', forbidden)
    with pytest.raises(ReviewError):
        project(case, **options)


def test_date_is_only_explicit_source_and_result_binding_not_db_clock(case):
    source = replace(case[2]['sources'][1], on=END)
    result = replace(case[2]['result_bindings'][0], on=END)
    draft = project(case, sources=(case[2]['sources'][0], source), result_bindings=(result,))
    # The exact registered text exists, but its explicit bound date is outside [start,end).
    assert draft.progress[0].state == 'missing_results'


@pytest.mark.parametrize('problem', ['task_duplicate', 'result_duplicate', 'task_unknown', 'agreement_unknown', 'source_conflict'])
def test_ambiguous_or_unlinked_bindings_are_not_resolved_by_guessing(case, problem):
    params = case[2]
    changes = {}
    if problem == 'task_duplicate': changes['task_bindings'] = params['task_bindings'] * 2
    elif problem == 'result_duplicate': changes['result_bindings'] = params['result_bindings'] * 2
    elif problem == 'task_unknown': changes['result_bindings'] = (replace(params['result_bindings'][0], task_id='unknown'),)
    elif problem == 'agreement_unknown': changes['task_bindings'] = (replace(params['task_bindings'][0], agreement_id='unknown'),)
    elif problem == 'source_conflict': changes['sources'] = params['sources'] + (replace(params['sources'][1], on=DAY),)
    with pytest.raises(ReviewError):
        project(case, **changes)


@pytest.mark.parametrize('change', ['task_owner', 'task_status', 'task_acl', 'source_deleted', 'source_content', 'source_link', 'member_removed'])
def test_final_guard_catches_changes_during_pure_projection(case, monkeypatch, change):
    store, config, _ = case
    original = adapter.compare_week

    def mutate_after_compare(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'task_owner': store.execute("UPDATE tasks SET owner='bob'")
        elif change == 'task_status': store.execute("UPDATE tasks SET status='blocked'")
        elif change == 'task_acl': store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)', (BUSINESS, 'T1', '["document:revoked"]'))
        elif change == 'source_deleted': store.execute("UPDATE sources SET deleted=1 WHERE id='result-source'")
        elif change == 'source_content': store.execute("UPDATE sources SET body=body||'changed' WHERE id='result-source'")
        elif change == 'source_link': store.execute("DELETE FROM task_sources WHERE source_id='result-source'")
        elif change == 'member_removed': config['employees'].pop('alice')
        return result

    monkeypatch.setattr(adapter, 'compare_week', mutate_after_compare)
    with pytest.raises(ReviewError):
        project(case, 'alice')


def test_only_selected_literal_spans_enter_bounded_draft(case):
    draft = project(case)
    packed = json.dumps(asdict(draft), ensure_ascii=False, default=str)
    assert PRIVATE not in packed and 'PRIVATE-UNRETURNED-LINK' not in packed
    assert 'created' not in packed and 'last_reply' not in packed
    assert 'Зарегистрировано: список готов.' in packed
    assert 'Задача T1 связана с договорённостью A.' in packed
    assert len(packed) <= adapter.MAX_DRAFT_CHARS


def test_finite_input_and_output_budgets_fail_closed(case):
    with pytest.raises(ReviewError, match='invalid_bounded_bindings'):
        project(case, task_bindings=case[2]['task_bindings'] * (adapter.MAX_BINDINGS + 1))
    bindings = list(case[2]['task_bindings'])
    for index in range(adapter.MAX_BINDINGS - 1):
        task_id = f'extra-{index}'
        persist_task(case[0], task_id)
        bindings.append(replace(bindings[0], task_id=task_id))
    with pytest.raises(ReviewError, match='weekly_draft_too_large'):
        project(case, task_bindings=tuple(bindings), result_bindings=())


@pytest.mark.parametrize('record', ['task', 'result'])
def test_confirmation_is_explicit_and_typed(case, record):
    binding = case[2]['task_bindings' if record == 'task' else 'result_bindings'][0]
    with pytest.raises(ReviewError, match='explicit_binding_confirmation_required'):
        replace(binding, confirmed=1)


@pytest.mark.parametrize('changes', [
    {'now': True}, {'now': float('nan')}, {'now': 10**1000},
    {'end': END + timedelta(days=1)},
])
def test_explicit_observation_and_seven_day_window_are_bounded(case, changes):
    with pytest.raises(ReviewError):
        project(case, **changes)


def test_projection_performs_only_selects_no_migration_io_model_or_clock(case, monkeypatch):
    import builtins

    store, _, _ = case
    schema = store.rows('SELECT name,sql FROM sqlite_master ORDER BY name')
    changes = store.db.total_changes
    store.execute('PRAGMA query_only=ON')

    def forbidden(*args, **kwargs):
        pytest.fail('unexpected effect')

    for obj, name in ((builtins, 'open'), (socket, 'socket'), (sqlite3, 'connect'), (time, 'time'), (Store, '__init__')):
        monkeypatch.setattr(obj, name, forbidden)
    assert project(case).progress[0].state == 'completed'
    assert store.db.total_changes == changes
    assert store.rows('SELECT name,sql FROM sqlite_master ORDER BY name') == schema
    assert store.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0
    assert store.execute('SELECT COUNT(*) FROM usage').fetchone()[0] == 0


def _unrelated_rows(store, table, count, *, business=BUSINESS):
    for index in range(count):
        task_id = f'unrelated-{index}'
        if table == 'tasks':
            persist_task(store, task_id, business=business)
        else:
            store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)', (business, task_id, '[]'))


@pytest.mark.parametrize('table', ['tasks', 'intelligence_task_acl'])
def test_acl_archive_budget_rejects_before_fields_or_body_projection(case, monkeypatch, table):
    monkeypatch.setattr(adapter, 'MAX_CURRENT_ROWS', 3)
    _unrelated_rows(case[0], table, 4)

    def forbidden(*args, **kwargs):
        pytest.fail('over-budget archive reached full ACL evaluation')

    monkeypatch.setattr(adapter, 'readable_work_ids', forbidden)
    case[0].db.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
                             if action == sqlite3.SQLITE_READ and name == 'sources' else sqlite3.SQLITE_OK)
    with pytest.raises(ReviewError, match='weekly_acl_row_budget_exceeded'):
        project(case)


@pytest.mark.parametrize('table', ['tasks', 'intelligence_task_acl'])
def test_acl_archive_growth_is_bounded_before_final_evaluation(case, monkeypatch, table):
    monkeypatch.setattr(adapter, 'MAX_CURRENT_ROWS', 3)
    original_compare = adapter.compare_week
    original_acl = adapter.readable_work_ids
    calls = []

    def counted_acl(*args, **kwargs):
        calls.append(True)
        assert len(calls) == 1, 'grown archive reached second full ACL evaluation'
        return original_acl(*args, **kwargs)

    def grow_after_compare(*args, **kwargs):
        result = original_compare(*args, **kwargs)
        _unrelated_rows(case[0], table, 4)
        return result

    monkeypatch.setattr(adapter, 'readable_work_ids', counted_acl)
    monkeypatch.setattr(adapter, 'compare_week', grow_after_compare)
    with pytest.raises(ReviewError, match='weekly_acl_row_budget_exceeded'):
        project(case)
    assert len(calls) == 1


def test_acl_archive_budget_ignores_other_business_rows_and_absent_acl_table(case, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_CURRENT_ROWS', 3)
    _unrelated_rows(case[0], 'tasks', 4, business='foreign')
    case[0].execute('DROP TABLE intelligence_task_acl')
    assert project(case).progress[0].state == 'completed'


def test_acl_archive_growth_after_preflight_still_has_sql_row_limit(case, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_CURRENT_ROWS', 3)
    store = case[0]
    original_rows = store.rows
    observed = []

    def grow_before_fields(sql, args=()):
        if sql.startswith('SELECT id,public_number,'):
            _unrelated_rows(store, 'tasks', 4)
            assert sql.endswith('LIMIT ?') and args[-1] == 4
            rows = original_rows(sql, args)
            observed.append(len(rows))
            return rows
        return original_rows(sql, args)

    monkeypatch.setattr(store, 'rows', grow_before_fields)
    with pytest.raises(ReviewError, match='weekly_acl_row_budget_exceeded'):
        project(case)
    assert observed == [4]
