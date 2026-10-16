"""Synthetic Store/index -> existing projection -> actual report core only."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest
import goldway.operational_agenda_adapter as module
from goldway.operational_agenda_adapter import AgendaUnavailable, build_agenda_preview
from goldway.intelligence import reader_key
from goldway.knowledge import KnowledgeIndex
from goldway.store import Store
from goldway.work_context import work_id

ZONE = ZoneInfo('Europe/Moscow')
WHEN = datetime(2026, 10, 19, 9, tzinfo=ZONE)


@pytest.fixture
def case(monkeypatch):
    import builtins, socket, httpx
    store = Store(':memory:')
    index = KnowledgeIndex(store)
    store.execute('''CREATE TABLE intelligence_task_acl(
        business TEXT NOT NULL, task_id TEXT NOT NULL, evidence_ids TEXT NOT NULL,
        PRIMARY KEY(business,task_id))''')
    config = {'business': 'GOLDWAY', 'admins': ['admin'],
              'employees': {'alice': {'name': 'Synthetic A'}, 'bob': {'name': 'Synthetic B'}},
              'timezone': 'Europe/Moscow'}
    def forbidden(*a, **k):
        pytest.fail('adapter attempted constructor, file/network I/O or outbox write')
    monkeypatch.setattr(KnowledgeIndex, '__init__', forbidden)
    monkeypatch.setattr(Store, 'queue', forbidden)
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    yield {'store': store, 'index': index, 'config': config, 'as_of': WHEN, 'trace': []}
    store.db.set_authorizer(None)
    store.db.set_trace_callback(None)
    store.db.close()


def seed(case, id='task', **values):
    row = dict(business='GOLDWAY', id=id, title='Synthetic task', owner='alice',
               deadline='2026-10-18', next_step='Synthetic next', criterion='Synthetic criterion',
               status='blocked', source_id='synthetic-source', evidence='BODY-MUST-NOT-BE-READ',
               created=WHEN.timestamp()-3600)
    row.update(values)
    case['store'].execute(f"INSERT INTO tasks({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
    return id


def acl(case, id, refs):
    case['store'].execute('INSERT OR REPLACE INTO intelligence_task_acl VALUES(?,?,?)',
                          ('GOLDWAY', id, json.dumps(refs)))


def document(case, *, reader='admin', observed=None, expires=None, source='synthetic-source', id='doc'):
    now = WHEN.timestamp()
    case['index'].upsert(dict(source_id=source, document_id=id, title='Synthetic', url='https://example.invalid/synthetic',
                             text='Private synthetic knowledge body', readers=[reader_key('GOLDWAY', reader)],
                             observed_at=observed if observed is not None else now-60,
                             permission_expires_at=expires if expires is not None else now+60, confirmed=True))
    return next(iter(case['index'].allowed_ids(reader_key('GOLDWAY', reader), now=now))) if (
        expires is None or expires > now) else case['store'].rows('SELECT id FROM knowledge_chunks')[-1]['id']


def authorizer(action, table, column, *_):
    if action not in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        if table in ('sources', 'events', 'outbox', 'deliveries', 'usage', 'chats', 'connections'):
            return sqlite3.SQLITE_DENY
        if (table == 'tasks' and column in ('evidence', 'source_id', 'last_reply')) or (
                table == 'knowledge_chunks' and column == 'text'):
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def call(case, sender='admin', **kwargs):
    store = case['store']
    case['trace'].clear()
    store.db.set_trace_callback(case['trace'].append)
    store.db.set_authorizer(authorizer)
    try:
        return build_agenda_preview(store, case['config'], sender, case['index'],
                                    as_of=kwargs.pop('as_of', case['as_of']), **kwargs)
    finally:
        store.db.set_authorizer(None)
        store.db.set_trace_callback(None)


def during_projection(case, monkeypatch, change):
    real = module.digest_preview
    def callback(**kwargs):
        result = real(**kwargs)
        case['store'].db.set_authorizer(None)
        try:
            change()
        finally:
            case['store'].db.set_authorizer(authorizer)
        return result
    monkeypatch.setattr(module, 'digest_preview', callback)


def unavailable(case, **kwargs):
    with pytest.raises(AgendaUnavailable, match='^operational_agenda_unavailable$'):
        call(case, **kwargs)


def test_actual_store_projection_reaches_real_core_and_is_immutable_review_only(case):
    seed(case)
    draft = call(case)
    assert draft.agenda.facts[0].sources == (work_id('GOLDWAY', 'task'),)
    assert draft.agenda.facts[0].kind == 'overdue'
    assert 'В записи задачи №1' in draft.agenda.facts[0].text
    assert 'статус «blocked»' in draft.agenda.facts[0].text
    assert draft.digest.sources == (work_id('GOLDWAY', 'task'),)
    assert draft.requires_review and not draft.live_send_authorized
    assert draft.scope == 'current_authorized_task_records' and draft.limited
    assert draft.metrics_status == 'not_supplied' and draft.limitation in draft.digest.lines
    assert draft.digest.scheduled_for == WHEN and draft.digest.due_today
    with pytest.raises(FrozenInstanceError):
        draft.business = 'FOREIGN'
    assert all(sql.lstrip().upper().startswith('SELECT ') for sql in case['trace'])
    assert len(case['trace']) <= module.MAX_QUERIES
    assert 'BODY-MUST-NOT-BE-READ' not in repr(draft)


def test_employee_only_own_nonproposal_tasks_and_foreign_business_isolated(case):
    seed(case, 'own', title='OWN')
    seed(case, 'other', owner='bob', title='HIDDEN-OTHER')
    seed(case, 'proposal', title='HIDDEN-PROPOSAL', status='proposed')
    seed(case, 'foreign', title='HIDDEN-BUSINESS', business='FOREIGN')
    draft = call(case, sender='alice')
    assert len(draft.agenda.facts) == 1 and 'OWN' in draft.agenda.facts[0].text
    assert 'HIDDEN' not in repr(draft)
    assert draft.digest.sources == (work_id('GOLDWAY', 'own'),)


def test_outsider_denied_before_any_query(case):
    seed(case)
    unavailable(case, sender='stranger')
    assert case['trace'] == []


@pytest.mark.parametrize('status', ['completed', 'cancelled', 'canceled', 'deleted'])
def test_closed_tasks_do_not_become_current_agenda_or_historical_results(case, status):
    seed(case, status=status)
    draft = call(case)
    assert draft.agenda.facts == () and draft.digest.sources == ()
    assert 'не подтверждение отсутствия проблем' in ' '.join(draft.digest.lines)
    assert draft.metrics_status == 'not_supplied'


def test_admin_proposal_explicit_and_missing_fields_are_register_flags(case):
    seed(case, status='proposed', owner=None, deadline=None)
    text = call(case).agenda.facts[0].text
    assert 'предложение, ещё не подтверждено' in text
    assert 'нет подтверждённого исполнителя' in text and 'нет распознанного срока' in text


@pytest.mark.parametrize('deadline,overdue', [
    ('2026-10-19', False), ('2026-10-18', True), ('2026-10-20', False),
    ('2026-10-19T09:00:00+03:00', False), ('2026-10-19T08:59:59+03:00', True),
    ('2026-10-19T06:00:00Z', False), ('malformed', False), (None, False),
])
def test_current_local_day_and_aware_deadline_semantics_reuse_work_projection(case, deadline, overdue):
    seed(case, status='confirmed', deadline=deadline)
    text = call(case).agenda.facts[0].text
    assert ('срок прошёл' in text) is overdue
    if deadline in (None, 'malformed'):
        assert 'нет распознанного срока' in text


def test_timezone_day_is_configured_day_not_input_datetime_day(case):
    seed(case, status='confirmed', deadline='2026-10-19')
    draft = call(case, as_of=datetime(2026, 10, 18, 22, tzinfo=timezone.utc))
    assert draft.digest.scheduled_for.date().isoformat() == '2026-10-19'
    assert not draft.digest.due_today
    assert 'срок прошёл' not in draft.agenda.facts[0].text


def test_sample_omissions_are_limited_and_all_sample_refs_protected(case):
    for i in range(7):
        seed(case, f'task-{i}')
    draft = call(case)
    assert len(draft.agenda.facts) == 5 and len(draft.digest.lines) <= 6
    assert len(draft.digest.sources) == 5
    assert 'Ещё пунктов для проверки: 2' in draft.digest.lines
    assert draft.digest.lines[-1] == module.LIMITATION
    assert len({source for source in draft.digest.sources}) == 5
    # No claim about all seven database rows, only the five-item core sample.
    assert draft.limited and 'всего 7' not in ' '.join(draft.digest.lines)


def test_smaller_agenda_limit_still_protects_omitted_sample_provenance(case):
    for i in range(5):
        seed(case, f'task-{i}')
    draft = call(case, max_items=1)
    assert len(draft.agenda.facts) == 1
    assert len(draft.digest.sources) == 5
    assert 'Ещё пунктов для проверки: 4' in draft.digest.lines
    assert len(draft.digest.lines) <= 6


@pytest.mark.parametrize('access', ['correct', 'other_reader', 'expired', 'revoked', 'malformed_acl'])
def test_actual_document_grants_control_task_evidence(case, access):
    seed(case)
    ref = document(case, reader='bob' if access == 'other_reader' else 'admin',
                   expires=WHEN.timestamp() if access == 'expired' else None)
    acl(case, 'task', [ref])
    if access == 'revoked':
        case['index'].revoke_source('synthetic-source')
    if access == 'malformed_acl':
        case['store'].execute("UPDATE intelligence_task_acl SET evidence_ids='{' WHERE task_id='task'")
    draft = call(case)
    assert bool(draft.agenda.facts) is (access == 'correct')
    assert 'Private synthetic knowledge body' not in repr(draft)


def test_work_acl_dependencies_use_existing_fixed_point_and_role_rules(case):
    seed(case, 'dependency', owner='bob')
    seed(case, 'own', owner='alice')
    acl(case, 'own', [work_id('GOLDWAY', 'dependency')])
    assert call(case, sender='alice').agenda.facts == ()
    assert len(call(case).agenda.facts) == 2


@pytest.mark.parametrize('change', ['revision', 'status', 'delete', 'revoke', 'business', 'membership'])
def test_final_fresh_acl_and_snapshot_check_blocks_changes(case, monkeypatch, change):
    seed(case)
    ref = document(case)
    acl(case, 'task', [ref])
    def mutate():
        if change == 'revision':
            case['store'].execute('UPDATE tasks SET revision=revision+1')
        elif change == 'status':
            case['store'].execute("UPDATE tasks SET status='completed'")
        elif change == 'delete':
            case['store'].execute('DELETE FROM tasks')
        elif change == 'revoke':
            case['index'].revoke_source('synthetic-source')
        elif change == 'business':
            case['config']['business'] = 'FOREIGN'
        else:
            case['config']['admins'].remove('admin')
    during_projection(case, monkeypatch, mutate)
    unavailable(case)


def test_final_grants_recomputed_from_actual_expiry_metadata(case, monkeypatch):
    seed(case)
    acl(case, 'task', [document(case)])
    during_projection(case, monkeypatch, lambda: case['store'].execute(
        'UPDATE knowledge_documents SET permission_expires_at=?', (WHEN.timestamp(),)))
    unavailable(case)


@pytest.mark.parametrize('kwargs', [
    {'as_of': datetime(2026, 10, 19)}, {'as_of': '2026-10-19'}, {'as_of': None},
    {'max_items': True}, {'max_items': 0}, {'max_items': 6},
])
def test_explicit_aware_clock_and_bounded_output_inputs(case, kwargs):
    unavailable(case, **kwargs)


def test_nonexistent_wall_time_and_invalid_zone_fail_closed(case):
    bad = datetime(2026, 3, 29, 2, 30, tzinfo=ZoneInfo('Europe/Berlin'))
    unavailable(case, as_of=bad)
    case['config']['timezone'] = 'INVALID/ZONE'
    unavailable(case)


@pytest.mark.parametrize('field', ['title', 'next_step', 'criterion'])
def test_sql_text_guards_reject_oversized_row_without_echo(case, field):
    seed(case, **{field: 'SYNTHETIC-SECRET'*1000})
    unavailable(case)


def test_archive_row_budget_fails_closed_with_bounded_select_even_for_rare_owner(case):
    for i in range(module.MAX_ROWS+1):
        seed(case, f'task-{i}', owner='bob', status='completed')
    unavailable(case, sender='alice')
    task_queries = [q for q in case['trace'] if ' FROM tasks ' in q]
    assert task_queries and all('LIMIT 129' in q and 'owner=' not in q for q in task_queries)


def test_exact_task_row_boundary_is_supported(case):
    for i in range(module.MAX_ROWS):
        seed(case, f'task-{i}', status='completed')
    assert call(case).agenda.facts == ()


def test_acl_row_budget_and_byte_guard_are_fail_closed(case):
    for i in range(module.MAX_ROWS+1):
        acl(case, f'missing-{i}', [])
    unavailable(case)
    case['store'].execute('DELETE FROM intelligence_task_acl')
    acl(case, 'task', ['SYNTHETIC-SECRET'*1000])
    unavailable(case)


def test_expired_reader_associations_are_bounded_before_freshness_filter(case):
    # No document body or old archive traversal is needed to hit association cap.
    for i in range(module.MAX_DOCUMENT_GRANTS+1):
        case['store'].execute('INSERT INTO knowledge_documents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (f'd{i}', f's{i}', 'doc', 'Synthetic', 'https://example.invalid', None, None,
             WHEN.timestamp()-100, WHEN.timestamp()-1, 1, None))
        case['store'].execute('INSERT INTO knowledge_readers VALUES(?,?)', (f'd{i}', reader_key('GOLDWAY', 'admin')))
    unavailable(case)
    assert not any(' FROM knowledge_chunks ' in q for q in case['trace'])


def test_no_optional_acl_table_or_runtime_intelligence_needed(case):
    case['store'].execute('DROP TABLE intelligence_task_acl')
    seed(case)
    assert call(case).agenda.facts


def test_no_model_or_live_hooks_are_imported_or_required(case):
    seed(case, title='A\nB\rC')
    result = call(case)
    assert all('\n' not in line and '\r' not in line for line in result.digest.lines)
    assert not result.live_send_authorized and result.requires_review
    assert case['store'].rows('SELECT * FROM outbox') == []
    assert case['store'].rows('SELECT * FROM events') == []
