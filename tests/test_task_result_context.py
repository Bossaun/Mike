"""New result-to-work-context boundary, entirely fictional in-memory SQLite."""

import copy
import json
import sqlite3

import pytest

from goldway.store import Store
from goldway.task_result_context import (
    MAX_LINKED_SOURCES, MAX_RESULT_BYTES, MAX_SOURCE_BYTES, result_request,
)
from goldway.work_context import (
    collect_work_context, is_operational_query, readable_work_ids,
    validate_work_snapshot, work_id,
)


NOW = 1792152000.0
QUERY = 'Покажи результат задачи №3'
REPORT = 'Готов список из трёх проверенных пунктов: А, Б, В.'
PRIVATE = 'UNRELATED-PRIVATE-SENTINEL'


@pytest.fixture
def case():
    store = Store(':memory:')
    store.execute('''CREATE TABLE intelligence_task_acl(
        business TEXT,task_id TEXT,evidence_ids TEXT,PRIMARY KEY(business,task_id))''')
    config = {'business': 'fictional', 'timezone': 'Europe/Moscow',
              'admins': ['boss'], 'employees': {
                  'boss': {'name': 'Руководитель'},
                  'alice': {'name': 'Алиса'}, 'bob': {'name': 'Борис'}}}
    yield store, config
    store.db.close()


def task(case, *, task_id='finished', number=3, business='fictional',
         owner='alice', status='completed', evidence=REPORT, refs=()):
    store, _ = case
    store.execute('''INSERT INTO tasks(business,id,public_number,title,owner,deadline,
        next_step,criterion,status,source_id,evidence,created)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (business, task_id, number, 'Проверить учебный список', owner, '2026-10-15',
         'Сверить пункты', 'Три пункта', status, 'creation', evidence, NOW - 86400))
    if refs:
        store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',
                      (business, task_id, json.dumps(list(refs))))
    return work_id(business, task_id)


def source(case, source_id='report', body=None, *, task_id='finished',
           business='fictional', deleted=0, linked=True):
    store, _ = case
    store.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                  (business, source_id, body if body is not None else PRIVATE + '\n' + REPORT + '\n' + PRIVATE,
                   'fictional-link-not-for-output', deleted))
    if linked:
        store.execute('INSERT INTO task_sources VALUES(?,?,?)', (business, task_id, source_id))


def grants(case, sender='boss', docs=()):
    return set(docs) | readable_work_ids(*case, sender, set(docs))


def collect(case, sender='boss', docs=(), query=QUERY):
    return collect_work_context(*case, sender, grants(case, sender, docs), NOW, query)


def finished(case, **kwargs):
    ref = task(case, **kwargs)
    source(case)
    return ref


@pytest.mark.parametrize('query', [
    QUERY, 'Какой результат у задачи #3?', 'Что сделано по задаче 3?',
    'Чем закончилась задача №3?', 'Расскажите итог поручения №3',
    'Результат задачи №3?', 'Пожалуйста, покажите результат задачи №3.',
])
def test_explicit_result_questions_reach_existing_operational_route(query):
    assert result_request(query) == (True, 3)
    assert is_operational_query(query)


@pytest.mark.parametrize('query', [
    'Покажи результат задачи №3 и №4', 'Покажи результат задачи №03',
    'Покажи результат задачи №0', 'Покажи результат задачи №3000000000',
    'Покажи результат задачи №3.5', 'Покажи результат задачи №3a',
    'Покажи результат задачи', 'Что сделано по задаче 3 и задаче 4?',
    'Покажи результат задачи №3a и задачи 4',
    'Покажи результат задачи 3a и задачи 4',
    'Покажи результат опыта №3 по задаче 4',
])
def test_ambiguous_or_invalid_number_has_no_implicit_focus(case, query):
    finished(case)
    task(case, task_id='open', number=4, status='confirmed')
    assert result_request(query) == (True, None)
    context = collect(case, query=query)
    assert context['status'] == 'no_evidence' and context['evidence'] == []
    assert context['scope'] == 'current_authorized_registered_task_result'


@pytest.mark.parametrize('query', [
    'Я выполнил задачу №3, результат — список.',
    'Покажи результат опыта №3', 'Как идут задачи?', 'Давай',
])
def test_unrelated_text_is_not_a_result_request(query):
    assert result_request(query) == (False, None)


@pytest.mark.parametrize('query', [
    'Покажи результат задачи №3 и 4', 'Покажи результат задачи №3, 4',
    'Покажи результат задачи №3–4',
])
def test_shorthand_number_lists_never_select_the_first_result(case, query):
    finished(case)
    assert result_request(query) == (True, None)
    assert collect(case, query=query)['evidence'] == []


@pytest.mark.parametrize('query', [
    'Создай задачу №3: получить результат',
    'Покажи результат задачи №3 и измени срок',
    'Закрой задачу №3: результат готов?',
])
def test_result_route_does_not_override_existing_action_gates(query):
    assert not is_operational_query(query)


def test_collector_never_uses_result_branch_for_a_mutating_request(case):
    finished(case)
    assert collect(case, query='Покажи результат задачи №3 и измени срок')['evidence'] == []


def test_completed_lookup_projects_only_literal_registered_span(case):
    ref = finished(case)
    # source_id is the creation source, not the result's provenance.
    source(case, 'creation', 'Изначально поручено проверить список.')
    context = collect(case)
    assert context['status'] == 'grounded'
    assert len(context['evidence']) == 1
    item = context['evidence'][0]
    result = item['registered_result']
    assert item['id'] == ref and item['status'] == 'completed'
    assert item['confirmation_scope'] == 'registered_result_only'
    assert result['state'] == 'registered_report' and result['text'] == REPORT
    assert result['span'] == {'start': len(PRIVATE) + 1, 'end': len(PRIVATE) + 1 + len(REPORT)}
    assert result['source_ref'].startswith('task-source:')
    assert 'не независимая проверка' in result['limitation']
    assert item['updated_at'] is None and item['signals'] == []
    packed = json.dumps(context, ensure_ascii=False)
    assert PRIVATE not in packed and 'fictional-link' not in packed
    assert 'source_id' not in packed and 'history' not in packed and 'created' not in packed
    assert len(json.dumps(context['evidence'], ensure_ascii=False)) <= 6000
    assert validate_work_snapshot(*case, 'boss', grants(case), item)


def test_ordinary_overview_still_excludes_completed_and_never_reads_private_columns(case):
    finished(case)
    open_ref = task(case, task_id='open', number=4, status='confirmed')
    store, _ = case

    def authorize(action, table, column, *_):
        private = table in {'sources', 'task_sources', 'events', 'audit'}
        private |= table == 'tasks' and column in {'evidence', 'source_id'}
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and private else sqlite3.SQLITE_OK

    store.db.set_authorizer(authorize)
    context = collect(case, query='Как идут наши задачи?')
    assert [item['id'] for item in context['evidence']] == [open_ref]
    item = context['evidence'][0]
    assert item['confirmation_scope'] == 'record_existence_only'
    assert 'registered_result' not in item
    assert validate_work_snapshot(*case, 'boss', grants(case), item)


@pytest.mark.parametrize(('sender', 'allowed'), [
    ('boss', True), ('alice', True), ('bob', False), ('outsider', False),
])
def test_result_uses_current_role_and_ownership(case, sender, allowed):
    finished(case)
    context = collect(case, sender)
    assert bool(context['evidence']) is allowed
    if not allowed:
        assert REPORT not in json.dumps(context, ensure_ascii=False)


def test_denied_task_never_reads_result_or_source(case):
    ref = finished(case, refs={'document:restricted'})
    store, config = case

    def authorize(action, table, column, *_):
        private = table in {'sources', 'task_sources'} or table == 'tasks' and column == 'evidence'
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and private else sqlite3.SQLITE_OK

    store.db.set_authorizer(authorize)
    # The caller cannot manufacture document access by supplying a stale work grant.
    context = collect_work_context(store, config, 'boss', {ref}, NOW, QUERY)
    assert context['evidence'] == []


def test_document_permission_revocation_and_current_membership_invalidate_result(case):
    finished(case, refs={'document:restricted'})
    item = collect(case, docs={'document:restricted'})['evidence'][0]
    assert validate_work_snapshot(*case, 'boss', grants(case, docs={'document:restricted'}), item)
    assert not validate_work_snapshot(*case, 'boss', grants(case), item)
    assert collect(case)['evidence'] == []
    case[1]['admins'] = []
    case[1]['employees'].pop('boss')
    assert not validate_work_snapshot(*case, 'boss', {item['id'], 'document:restricted'}, item)


def test_foreign_business_source_cannot_supply_same_task_number_result(case):
    task(case)
    task(case, business='another-business')
    source(case, business='another-business')
    assert collect(case)['evidence'] == []
    source(case)
    item = collect(case)['evidence'][0]
    assert item['id'] == work_id('fictional', 'finished')
    assert item['registered_result']['text'] == REPORT


@pytest.mark.parametrize('problem', [
    'no_source', 'absent_span', 'unlinked', 'deleted', 'duplicate_source',
    'repeated_span', 'oversize_result', 'oversize_source', 'control_result',
    'disjoint_lines', 'missing_link_target', 'not_completed',
])
def test_missing_ambiguous_or_unbounded_provenance_has_no_result(case, problem):
    evidence = REPORT
    if problem == 'oversize_result': evidence = 'x' * (MAX_RESULT_BYTES + 1)
    if problem == 'control_result': evidence = REPORT + '\x00'
    if problem == 'disjoint_lines': evidence = 'Первая строка\n[…]\nТретья строка'
    task(case, evidence=evidence, status='confirmed' if problem == 'not_completed' else 'completed')
    if problem == 'no_source': pass
    elif problem == 'missing_link_target':
        case[0].execute('INSERT INTO task_sources VALUES(?,?,?)', ('fictional', 'finished', 'missing'))
    elif problem == 'absent_span': source(case, body='Другая запись; результата нет.')
    elif problem == 'unlinked': source(case, 'creation', linked=False)
    elif problem == 'deleted': source(case, deleted=1)
    elif problem == 'duplicate_source':
        source(case)
        source(case, 'second-report')
    elif problem == 'repeated_span': source(case, body=REPORT + '\n' + REPORT)
    elif problem == 'oversize_source': source(case, body=REPORT + 'x' * MAX_SOURCE_BYTES)
    elif problem == 'disjoint_lines': source(case, body='Первая строка\nВторая строка\nТретья строка')
    else: source(case, body=evidence)
    context = collect(case)
    assert context['status'] == 'no_evidence' and context['evidence'] == []
    assert REPORT not in json.dumps(context, ensure_ascii=False)


def test_exact_multiline_span_is_not_paraphrased_or_whitespace_normalized(case):
    report = 'Готово:\nА — проверен.\nБ — проверен.\nВ — проверен.'
    task(case, evidence=report)
    source(case, body=PRIVATE + '\n' + report + '\n' + PRIVATE)
    assert collect(case)['evidence'][0]['registered_result']['text'] == report


@pytest.mark.parametrize('change', [
    'result', 'source_outside_span', 'source_span', 'source_deleted', 'unlink',
    'second_match', 'task_status', 'task_revision', 'task_title', 'task_acl',
    'employee_owner',
])
def test_snapshot_revalidates_current_result_source_and_permissions(case, change):
    finished(case)
    sender = 'alice' if change == 'employee_owner' else 'boss'
    item = collect(case, sender)['evidence'][0]
    store, _ = case
    statements = {
        'result': ("UPDATE tasks SET evidence=? WHERE business='fictional'", ('Другой результат',)),
        'source_outside_span': ("UPDATE sources SET body=body||? WHERE business='fictional'", ('Другая частная строка',)),
        'source_span': ("UPDATE sources SET body=? WHERE business='fictional'", ('Результат удалён',)),
        'source_deleted': ("UPDATE sources SET deleted=1 WHERE business='fictional'", ()),
        'unlink': ("DELETE FROM task_sources WHERE business='fictional'", ()),
        'task_status': ("UPDATE tasks SET status='confirmed' WHERE business='fictional'", ()),
        'task_revision': ("UPDATE tasks SET revision=revision+1 WHERE business='fictional'", ()),
        'task_title': ("UPDATE tasks SET title=? WHERE business='fictional'", ('Другой заголовок',)),
        'task_acl': ('INSERT INTO intelligence_task_acl VALUES(?,?,?)',
                     ('fictional', 'finished', '["document:revoked"]')),
        'employee_owner': ("UPDATE tasks SET owner='bob' WHERE business='fictional'", ()),
    }
    if change == 'second_match': source(case, 'new-matching-source')
    else:
        sql, args = statements[change]
        store.execute(sql, args)
    assert not validate_work_snapshot(*case, sender, grants(case, sender), item)


@pytest.mark.parametrize('field', ['text', 'source_ref', 'span', 'state', 'limitation'])
def test_snapshot_cannot_be_forged_with_another_span_or_claim(case, field):
    finished(case)
    item = copy.deepcopy(collect(case)['evidence'][0])
    item['registered_result'][field] = {'start': 0, 'end': 1} if field == 'span' else 'FORGED'
    assert not validate_work_snapshot(*case, 'boss', grants(case), item)


def test_source_count_bound_is_explicit_and_fails_closed(case):
    finished(case)
    for index in range(MAX_LINKED_SOURCES - 1):
        source(case, f'other-{index:03}', body='Не относящаяся к результату запись.')
    assert collect(case)['evidence']
    source(case, 'one-over-limit', body='Не относящаяся к результату запись.')
    assert collect(case)['evidence'] == []


def test_multibyte_result_at_byte_boundary_remains_literal(case):
    report = 'Я' * (MAX_RESULT_BYTES // 2)
    assert len(report.encode()) == MAX_RESULT_BYTES
    task(case, evidence=report)
    source(case, body=report)
    item = collect(case)['evidence'][0]
    assert item['registered_result']['text'] == report
    assert len(json.dumps([item], ensure_ascii=False)) <= 6000


def test_result_collection_and_revalidation_are_strictly_read_only(case):
    finished(case)
    store, _ = case
    schema = store.rows('SELECT name,sql FROM sqlite_master ORDER BY name')
    changes = store.db.total_changes
    store.execute('PRAGMA query_only=ON')
    item = collect(case)['evidence'][0]
    assert validate_work_snapshot(*case, 'boss', grants(case), item)
    assert store.db.total_changes == changes
    assert schema == store.rows('SELECT name,sql FROM sqlite_master ORDER BY name')
    assert store.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0
    assert store.execute('SELECT COUNT(*) FROM usage').fetchone()[0] == 0
