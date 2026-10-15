"""Finite work-context checks using fictional records and local SQLite only."""

import copy
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from goldway.store import Store
from goldway.work_context import (
    collect_work_context, is_operational_query, readable_work_ids,
    validate_work_snapshot, work_id,
)


NOW = datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc).timestamp()
QUESTION = 'Какие есть проблемы в нашем бизнесе?'


@pytest.fixture
def case(tmp_path):
    store = Store(str(tmp_path / 'fictional-work.sqlite'))
    store.execute('''CREATE TABLE intelligence_task_acl(
        business TEXT NOT NULL, task_id TEXT NOT NULL, evidence_ids TEXT NOT NULL,
        PRIMARY KEY(business,task_id))''')
    config = {'business': 'fictional-team', 'timezone': 'Europe/Moscow',
              'admins': ['boss'], 'employees': {
                  'boss': {'name': 'Руководитель'}, 'alice': {'name': 'Алиса'},
                  'bob': {'name': 'Борис'}}}
    yield store, config
    store.db.close()


def task(case, task_id, *, business=None, refs=(), **fields):
    store, config = case
    row = dict(business=business or config['business'], id=task_id,
               title='Проверить вымышленный список ' + task_id, owner='alice',
               deadline='2026-10-20', next_step='Проверить три пункта',
               criterion='Три пункта проверены', status='confirmed',
               source_id='PRIVATE-MESSAGE-SOURCE', evidence='PRIVATE-RAW-EVIDENCE',
               created=NOW - 86400)
    row.update(fields)
    columns = ','.join(row)
    placeholders = ','.join('?' for _ in row)
    store.execute(f'INSERT INTO tasks({columns}) VALUES({placeholders})', tuple(row.values()))
    if refs:
        store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',
                      (row['business'], task_id, json.dumps(list(refs))))
    return work_id(row['business'], task_id)


def grants(case, sender='boss', documents=()):
    store, config = case
    return set(documents) | readable_work_ids(store, config, sender, set(documents))


def collect(case, sender='boss', documents=(), *, now=NOW, query=QUESTION):
    return collect_work_context(*case, sender, grants(case, sender, documents), now, query)


def test_work_refs_are_stable_business_scoped_and_unambiguous():
    assert work_id('a', 'b') == work_id('a', 'b')
    assert work_id('a', 'b') != work_id('b', 'a')
    assert work_id('a:b', 'c') != work_id('a', 'b:c')
    assert work_id('fictional-team', '秘密') != work_id('another-team', '秘密')
    assert len(work_id('a', 'b')) == len('work-task:') + 64


def test_membership_business_ownership_and_proposals(case):
    own = task(case, 'own')
    foreign = task(case, 'foreign', owner='bob')
    proposed = task(case, 'proposal', status='proposed')
    unregistered = task(case, 'unregistered-owner', owner='employee')
    other_business = task(case, 'other-business', business='other-team')
    store, config = case
    assert readable_work_ids(store, config, 'boss', set()) == {own, foreign, proposed, unregistered}
    assert readable_work_ids(store, config, 'alice', set()) == {own}
    assert readable_work_ids(store, config, 'bob', set()) == {foreign}
    assert readable_work_ids(store, config, 'employee', set()) == set()
    assert readable_work_ids(store, config, {'role': 'admin', 'sender': 'boss'}, set()) == set()
    assert other_business not in grants(case)
    employee = collect(case, 'alice')
    assert employee['audience_scope'] == 'own_tasks_only'
    assert [item['id'] for item in employee['evidence']] == [own]
    proposals = [item for item in collect(case)['evidence'] if item['id'] == proposed]
    assert proposals[0]['proposal'] is True and proposals[0]['status'] == 'proposed'


def test_unregistered_sender_has_no_task_metadata_even_with_forged_grants(case):
    ref = task(case, 'secret', owner='employee', title='HIDDEN-TITLE', status='blocked')
    context = collect_work_context(*case, 'employee', {ref}, NOW, QUESTION)
    assert context['status'] == 'no_evidence' and context['evidence'] == []
    packed = json.dumps(context)
    assert 'HIDDEN-TITLE' not in packed and ref not in packed and 'count' not in packed


def test_document_revocation_removes_nested_dependents(case):
    b = case[1]['business']
    leaf = task(case, 'leaf', refs={'document:private'})
    middle = task(case, 'middle', refs={leaf})
    root = task(case, 'root', refs={middle})
    ordinary = task(case, 'ordinary')
    store, config = case
    assert readable_work_ids(store, config, 'boss', {'document:private'}) == {leaf, middle, root, ordinary}
    # Stale work grants cannot substitute for a missing current document grant.
    assert readable_work_ids(store, config, 'boss', {leaf, middle, root}) == {ordinary}
    assert {item['id'] for item in collect(case)['evidence']} == {ordinary}
    assert work_id(b, 'root') == root


def test_self_reference_is_valid_only_for_a_current_eligible_row(case):
    business = case[1]['business']
    self_ref = work_id(business, 'self')
    task(case, 'self', refs={self_ref})
    proposed_ref = work_id(business, 'proposed')
    task(case, 'proposed', status='proposed', refs={proposed_ref})
    assert readable_work_ids(*case, 'alice', set()) == {self_ref}
    assert readable_work_ids(*case, 'boss', set()) == {self_ref, proposed_ref}


def test_cycles_require_every_node_to_be_eligible(case):
    business = case[1]['business']
    a, b = work_id(business, 'a'), work_id(business, 'b')
    task(case, 'a', refs={b})
    task(case, 'b', owner='bob', refs={a})
    assert readable_work_ids(*case, 'boss', set()) == {a, b}
    assert readable_work_ids(*case, 'alice', {b}) == set()
    assert readable_work_ids(*case, 'bob', {a}) == set()


def test_missing_and_cross_business_work_dependencies_cannot_be_forged(case):
    foreign = task(case, 'same-id', business='other-team')
    missing = work_id(case[1]['business'], 'missing')
    task(case, 'missing-dependent', refs={missing})
    task(case, 'foreign-dependent', refs={foreign})
    assert readable_work_ids(*case, 'boss', {missing, foreign}) == set()


@pytest.mark.parametrize('acl', ['not-json', 'null', '{}', '[null]', '[{}]', '"document:private"', '[""]'])
def test_malformed_acl_denies_task_and_dependents(case, acl):
    protected = task(case, 'protected')
    task(case, 'dependent', refs={protected})
    ordinary = task(case, 'ordinary')
    store, config = case
    store.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)', (config['business'], 'protected', acl))
    assert readable_work_ids(store, config, 'boss', {'document:private'}) == {ordinary}


def test_projection_reads_no_private_body_tables_and_performs_no_writes(case):
    task(case, 'record')
    store, config = case
    store.execute('PRAGMA query_only=ON')
    changed = store.db.total_changes

    def authorize(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and (
                table in {'sources', 'events', 'sheet_snapshots', 'audit'}
                or table == 'tasks' and column in {'evidence', 'source_id'}):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store.db.set_authorizer(authorize)
    context = collect(case)
    item = context['evidence'][0]
    assert validate_work_snapshot(store, config, 'boss', grants(case), item)
    assert store.db.total_changes == changed
    packed = json.dumps(context)
    assert 'PRIVATE-' not in packed
    assert not {'url', 'source_id', 'evidence', 'body', 'history'} & item.keys()
    assert item['source_kind'] == 'task'
    assert item['confirmed'] is True and item['confirmation_scope'] == 'record_existence_only'
    assert item['observed_at'] == NOW and item['updated_at'] is None


def test_ordinary_tasks_do_not_require_creating_an_acl_table(case):
    ref = task(case, 'ordinary')
    store, config = case
    store.execute('DROP TABLE intelligence_task_acl')
    store.execute('PRAGMA query_only=ON')
    assert readable_work_ids(store, config, 'boss', set()) == {ref}
    assert collect(case)['evidence'][0]['id'] == ref
    assert store.execute("SELECT 1 FROM sqlite_master WHERE name='intelligence_task_acl'").fetchone() is None


def test_bounded_output_excludes_closed_tasks_and_unknown_owner_identity(case):
    for index in range(9):
        task(case, f'open-{index}', title='Длинное название ' * 100,
             next_step='Длинный шаг ' * 100, criterion='Длинный критерий ' * 100,
             owner='UNCONFIGURED-OWNER-DO-NOT-EXPOSE')
    task(case, 'complete', status='completed', title='COMPLETED-TITLE')
    task(case, 'cancelled', status='cancelled', title='CANCELLED-TITLE')
    context = collect(case)
    evidence = context['evidence']
    assert 1 <= len(evidence) <= 5
    assert len(json.dumps(evidence, ensure_ascii=False)) <= 6000
    assert all(len(item['title']) <= 180 and len(item['next_step']) <= 240
               and len(item['criterion']) <= 180 for item in evidence)
    assert all(item['owner'] is None and 'missing_owner' in item['signals'] for item in evidence)
    packed = json.dumps(context)
    assert all(secret not in packed for secret in ['UNCONFIGURED-OWNER', 'COMPLETED-TITLE', 'CANCELLED-TITLE'])
    assert context['limited'] is True
    assert 'count' not in packed and 'total' not in context


def test_at_most_five_short_items_and_only_configured_owner_names(case):
    for index in range(8):
        task(case, f'short-{index}', title='Кратко', next_step='Шаг', criterion='Готово')
    evidence = collect(case)['evidence']
    assert len(evidence) == 5
    assert all(item['owner'] == {'key': 'alice', 'name': 'Алиса'} for item in evidence)


def test_ranking_blocked_then_overdue_then_missing_owner_then_missing_date(case):
    task(case, 'ordinary', deadline='2026-10-20')
    task(case, 'missing-date', deadline=None)
    task(case, 'missing-owner', owner=None)
    task(case, 'overdue', deadline='2026-10-14')
    task(case, 'blocked', status='blocked')
    assert [item['task_id'] for item in collect(case)['evidence']] == [
        'blocked', 'overdue', 'missing-owner', 'missing-date', 'ordinary']


def test_date_only_deadlines_follow_configured_local_day(case):
    case[1]['timezone'] = 'America/Los_Angeles'
    task(case, 'today', deadline='2026-10-14')
    task(case, 'yesterday', deadline='2026-10-13')
    task(case, 'past-time', deadline='2026-10-14T17:00:00-07:00')
    task(case, 'future-time', deadline='2026-10-14T18:00:00-07:00')
    task(case, 'local-naive-time', deadline='2026-10-14T17:00:00')
    items = {item['task_id']: item for item in collect(case)['evidence']}
    assert 'overdue' not in items['today']['signals']
    assert 'overdue' in items['yesterday']['signals']
    assert 'overdue' in items['past-time']['signals']
    assert 'overdue' not in items['future-time']['signals']
    assert 'overdue' in items['local-naive-time']['signals']


def test_invalid_date_has_no_invented_deadline(case):
    task(case, 'invalid', deadline='PRIVATE-INVALID-DATE')
    item = collect(case)['evidence'][0]
    assert item['deadline'] is None and 'missing_deadline' in item['signals']
    assert 'PRIVATE-INVALID-DATE' not in json.dumps(item)


def test_no_visible_tasks_returns_no_hidden_metadata(case):
    task(case, 'hidden', owner='bob', title='HIDDEN-TITLE')
    context = collect(case, 'alice')
    assert context['evidence'] == [] and context['status'] == 'no_evidence'
    assert context['as_of'] == NOW and context['limited'] is True
    assert 'HIDDEN-TITLE' not in json.dumps(context) and 'count' not in json.dumps(context)


def test_short_operational_followup_can_collect_after_caller_resolves_it(case):
    ref = task(case, 'record')
    assert not is_operational_query('Давай')
    assert collect(case, query='Давай')['evidence'][0]['id'] == ref


@pytest.mark.parametrize('change', [
    {'title': 'Изменённое название'}, {'deadline': '2026-11-01'},
    {'owner': 'bob'}, {'status': 'completed'}, {'next_step': 'Изменённый шаг'},
    {'criterion': 'Изменённый результат'}, {'revision': 2},
])
def test_snapshot_rejects_changed_current_task(case, change):
    task(case, 'record')
    item = collect(case)['evidence'][0]
    store, config = case
    assert validate_work_snapshot(store, config, 'boss', grants(case), item)
    key, value = next(iter(change.items()))
    store.execute(f'UPDATE tasks SET {key}=? WHERE business=? AND id=?',
                  (value, config['business'], 'record'))
    assert not validate_work_snapshot(store, config, 'boss', grants(case), item)


def test_snapshot_rejects_revoked_document_grants_and_changed_acl(case):
    task(case, 'record', refs={'document:private'})
    item = collect(case, documents={'document:private'})['evidence'][0]
    assert validate_work_snapshot(*case, 'boss', grants(case, documents={'document:private'}), item)
    assert not validate_work_snapshot(*case, 'boss', grants(case), item)
    store, config = case
    store.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE business=? AND task_id=?',
                  (json.dumps(['document:another']), config['business'], 'record'))
    assert not validate_work_snapshot(*case, 'boss', grants(case, documents={'document:another'}), item)


def test_snapshot_rejects_changed_dependency_role_owner_name_or_projection(case):
    leaf = task(case, 'leaf')
    task(case, 'root', refs={leaf})
    item = next(item for item in collect(case)['evidence'] if item['task_id'] == 'root')
    store, config = case
    assert validate_work_snapshot(store, config, 'boss', grants(case), item)
    forged = dict(item, title='A fabricated field under the original digest')
    assert not validate_work_snapshot(store, config, 'boss', grants(case), forged)
    store.execute('UPDATE tasks SET revision=2 WHERE business=? AND id=?', (config['business'], 'leaf'))
    assert not validate_work_snapshot(store, config, 'boss', grants(case), item)
    store.execute('UPDATE tasks SET revision=1 WHERE business=? AND id=?', (config['business'], 'leaf'))
    config['employees']['alice']['name'] = 'Новое имя'
    assert not validate_work_snapshot(store, config, 'boss', grants(case), item)
    config['employees']['alice']['name'] = 'Алиса'
    own_item = collect(case, 'alice')['evidence'][0]
    config['admins'].append('alice')
    assert not validate_work_snapshot(store, config, 'alice', grants(case, 'alice'), own_item)
    config['admins'].remove('alice')
    del config['employees']['alice']
    assert not validate_work_snapshot(store, config, 'alice', {item['id']}, own_item)


def test_snapshot_rejects_deleted_and_cross_business_records(case):
    task(case, 'record')
    item = collect(case)['evidence'][0]
    other_config = copy.deepcopy(case[1])
    other_config['business'] = 'another-team'
    assert not validate_work_snapshot(case[0], other_config, 'boss', {item['id']}, item)
    case[0].execute('DELETE FROM tasks WHERE business=? AND id=?', (case[1]['business'], 'record'))
    assert not validate_work_snapshot(*case, 'boss', {item['id']}, item)


@pytest.mark.parametrize('query', [
    QUESTION, 'Какие риски есть у нас?', 'Что у нас с рисками?', 'Есть ли блокеры?',
    'Где все застряло?', 'Что просрочено?', 'Что мешает работе?', 'Что мешает?',
    'Где у нас узкие места?', 'Покажи проблемы по задачам', 'Что сейчас тормозит бизнес?',
    'Какие задачи заблокированы?', 'Как идут задачи команды?', 'Каково состояние задач?',
    'Почему работа постоянно застревает?', 'Какие есть риски?', 'Есть риски?',
    'Где узкие места?', 'Что заблокировано?',
])
def test_explicit_operational_questions(query):
    assert is_operational_query(query)


@pytest.mark.parametrize('query', [
    '', 'Давай', 'Сделай покороче', 'Откуда ты это знаешь?', 'Расскажи о Норива',
    'Какие риски у бренда Норива?', 'Какие проблемы решает крем?',
    'Что мешает продукту Норива продаваться?', 'Напиши пост о проблемах нашего бизнеса',
    'Составь обзор рисков бизнеса', 'Создай задачу проверить блокеры',
    'Отметь задачу №1 как заблокированную', 'Подтверди просроченную задачу №1',
    'Перенеси задачу №1 на завтра', 'Разберись с задачей №1', 'Что мне мешает спать?',
    'У нас проблема с задачей, отметь ее завершенной', 'Почему не работает крем?',
    'Можно создать задачу: что мешает бизнесу?',
    'Что мешает бизнесу? Давай запишем задачу',
    'Какие проблемы в бизнесе описать в посте? Нужно составить текст',
])
def test_brand_drafting_and_task_mutations_do_not_trigger_operational_context(query):
    assert not is_operational_query(query)
