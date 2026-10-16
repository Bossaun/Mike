"""Actual Engine -> current ACL/PK/source checks -> real escaped task card."""
import copy
import json
import sqlite3
from dataclasses import FrozenInstanceError

import pytest
import goldway.task_card_command as command
from goldway.engine import Engine
from goldway.intelligence import delivery_permitted, reader_key
from goldway.knowledge import KnowledgeIndex
from goldway.store import Store
from goldway.telegram import Updates
from goldway.work_context import work_id

INDEX_INIT = KnowledgeIndex.__init__
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']


class NoExternal:
    def propose(self, *a, **k):
        pytest.fail('task card called model')
    def call(self, *a, **k):
        pytest.fail('task card called Telegram/provider')


@pytest.fixture
def case(monkeypatch):
    config = {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow',
        'employees': {x: {'name': 'Synthetic '+x, 'telegram_user_id': i+1} for i, x in enumerate(MEMBERS)},
        'admins': ['test_operator'], 'business_account_user_id': 99,
        'team_intake': {'recipients': MEMBERS[:], 'started_at': 100},
        'allowed_live_recipients': MEMBERS[:], 'team_roster_verified': True, 'recipients_verified': True,
        'live_send': False, 'review_mode': True, 'sheets_write': False, 'daily_summary': False,
        'allow_onboarding_replies': False, 'allow_registered_replies': False,
        'allowed_group_ids': [], 'group_destinations': {}, 'meeting_recipient': None,
        'bot_user_id': 100, 'bot_user_id_verified': True,
        'intelligence': {'enabled': True, 'sources': []}}
    clock = [1792174600.0]
    store = Store(':memory:')
    engine = Engine(store, config, NoExternal(), lambda: clock[0])
    def forbidden(*a, **k):
        pytest.fail('task card reached context/refresh/external I/O/constructor')
    engine._context = forbidden
    engine.intelligence.sources.refresh = forbidden
    import builtins, socket, httpx
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(KnowledgeIndex, '__init__', forbidden)
    yield engine, store, clock
    store.db.close()


def seed(case, id='PRIVATE-TASK-ID', **changes):
    engine, store, clock = case
    data = dict(business='GOLDWAY', id=id, title='Synthetic permitted title', owner='alen',
        deadline='2026-10-19', next_step='Synthetic next step', criterion='Synthetic criterion',
        status='confirmed', source_id='PRIVATE-SOURCE:'+id, evidence='PRIVATE-RESULT-BODY', created=clock[0])
    data.update(changes)
    store.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,0)',
                  (data['business'], data['source_id'], 'PRIVATE-SOURCE-BODY', 'PRIVATE-URL'))
    store.execute(f"INSERT INTO tasks({','.join(data)}) VALUES({','.join('?' for _ in data)})", tuple(data.values()))
    return store.rows('SELECT * FROM tasks WHERE business=? AND id=?', (data['business'], id))[0]


def acl(case, task, refs):
    case[1].execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',
                     (task['business'], task['id'], json.dumps(refs)))


def document(case, sender='alen', *, expired=False, future=False):
    engine, store, clock = case
    engine.intelligence.index.upsert(dict(source_id='private-document-source', document_id='synthetic-doc',
        title='DOCUMENT-TITLE-MUST-NOT-BE-READ', url='https://example.invalid/synthetic',
        text='DOCUMENT-BODY-MUST-NOT-BE-READ', readers=[reader_key('GOLDWAY', sender)],
        observed_at=clock[0]+1 if future else clock[0]-100,
        permission_expires_at=clock[0] if expired else clock[0]+10, confirmed=True))
    return store.rows('SELECT id FROM knowledge_chunks')[0]['id']


def rows(case, event='card-event'):
    return case[1].rows('SELECT * FROM outbox WHERE business=? AND dedupe=?', ('GOLDWAY', 'task-card:'+event))


def reply(case, text='Покажи задачу №1', *, sender='test_operator', channel='bot', event='card-event', task_id=None):
    assert case[0].reply(event, sender, text, task_id=task_id, channel=channel)
    found = rows(case, event)
    return found[0]['text'] if found else None


def observed_render(case, monkeypatch, callback):
    real = command.task_card
    def observed(*a, **k):
        result = real(*a, **k)
        callback()
        return result
    monkeypatch.setattr(command, 'task_card', observed)


def assert_no_content_reads(sql):
    assert not any(' AS title' in q or ' AS next_step' in q or ' AS criterion' in q for q in sql)


@pytest.mark.parametrize(('sender', 'channel'), [('test_operator', 'bot'), ('alen', 'business')])
def test_actual_pk_acl_render_queue_is_current_permitted_and_source_bodies_unread(case, sender, channel):
    task = seed(case)
    reads = []
    def authorizer(action, table, column, *_):
        if action == sqlite3.SQLITE_READ:
            reads.append((table, column))
            if (table == 'tasks' and column == 'evidence') or (table == 'sources' and column in ('body', 'link')):
                return sqlite3.SQLITE_DENY
            if table in ('knowledge_chunks', 'knowledge_documents') and column in ('text', 'title', 'url'):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    case[1].db.set_authorizer(authorizer)
    try:
        output = reply(case, sender=sender, channel=channel)
    finally:
        case[1].db.set_authorizer(None)
    assert '<b>Задача №1 · Поручение</b>' in output
    assert 'Synthetic permitted title' in output
    assert 'Synthetic next step' in output and 'Synthetic criterion' in output
    assert 'Срез на ' in output and 'МСК' in output
    assert 'результат независимо не проверен' in output
    assert 'revision' not in output and 'Версия' not in output
    assert 'PRIVATE' not in output and task['id'] not in output
    row = rows(case)[0]
    assert row['task_id'] == task['id'] and row['parse_mode'] == 'HTML'
    assert row['preferred_transport'] == channel and row['recipient'] == sender
    protection = case[1].rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?', (row['id'],))[0]
    assert json.loads(protection['evidence_ids']) == [work_id('GOLDWAY', task['id'])]


@pytest.mark.parametrize('status', ['proposed', 'confirmed', 'in_progress', 'blocked', 'completed'])
def test_admin_can_read_permitted_registered_states_without_result_body(case, status):
    seed(case, status=status)
    output = reply(case)
    assert 'Задача №1' in output and output != command._NOTICE and 'PRIVATE-RESULT' not in output


@pytest.mark.parametrize('kind', ['other_owner', 'own_proposed', 'missing', 'foreign_business'])
def test_missing_forbidden_or_foreign_task_same_notice_before_title_transfer(case, kind):
    if kind == 'other_owner':
        seed(case, owner='frost')
    elif kind == 'own_proposed':
        seed(case, owner='alen', status='proposed')
    elif kind == 'foreign_business':
        seed(case, business='OTHER')
    sql = []
    case[1].db.set_trace_callback(sql.append)
    output = reply(case, sender='alen')
    case[1].db.set_trace_callback(None)
    assert output == command._NOTICE and rows(case)[0]['task_id'] is None
    assert_no_content_reads(sql)


@pytest.mark.parametrize('permission', ['allowed', 'wrong_reader', 'expired', 'future', 'revoked', 'bad_acl'])
def test_existing_actual_document_grants_checked_without_document_content(case, permission):
    task = seed(case)
    ref = document(case, sender='frost' if permission == 'wrong_reader' else 'alen',
                   expired=permission == 'expired', future=permission == 'future')
    acl(case, task, [ref])
    if permission == 'revoked':
        case[0].intelligence.index.revoke_source('private-document-source')
    if permission == 'bad_acl':
        case[1].execute("UPDATE intelligence_task_acl SET evidence_ids='{' WHERE task_id=?", (task['id'],))
    sql = []
    case[1].db.set_trace_callback(sql.append)
    output = reply(case, sender='alen')
    case[1].db.set_trace_callback(None)
    assert (output != command._NOTICE) is (permission == 'allowed')
    if permission != 'allowed':
        assert_no_content_reads(sql)
    assert 'DOCUMENT' not in output


@pytest.mark.parametrize('owner', ['alen', 'frost'])
def test_dependency_role_acl_and_every_reference_protected(case, owner):
    root = seed(case)
    dependency = seed(case, id='private-dependency', owner=owner)
    acl(case, root, [work_id('GOLDWAY', dependency['id'])])
    output = reply(case, sender='alen')
    if owner == 'frost':
        assert output == command._NOTICE
    else:
        assert output != command._NOTICE
        protected = case[1].rows('SELECT evidence_ids FROM intelligence_outbox_acl')[0]['evidence_ids']
        assert set(json.loads(protected)) == {work_id('GOLDWAY', root['id']), work_id('GOLDWAY', dependency['id'])}


def test_missing_dependency_denies_without_guessing_external_task(case):
    task = seed(case)
    acl(case, task, [work_id('GOLDWAY', 'not-registered')])
    assert reply(case) == command._NOTICE


@pytest.mark.parametrize('source', ['deleted_primary', 'missing_primary', 'deleted_link', 'missing_link'])
def test_source_provenance_state_before_display_content(case, source):
    task = seed(case)
    if source == 'deleted_primary':
        case[1].execute('UPDATE sources SET deleted=1')
    elif source == 'missing_primary':
        case[1].execute('DELETE FROM sources')
    else:
        case[1].execute('INSERT INTO task_sources VALUES(?,?,?)', ('GOLDWAY', task['id'], 'second-source'))
        if source == 'deleted_link':
            case[1].execute('INSERT INTO sources VALUES(?,?,?,?,1)', ('GOLDWAY', 'second-source', 'PRIVATE', 'PRIVATE'))
    sql = []
    case[1].db.set_trace_callback(sql.append)
    output = reply(case)
    case[1].db.set_trace_callback(None)
    assert output == command._NOTICE
    assert_no_content_reads(sql)


@pytest.mark.parametrize('change', ['revision', 'title', 'owner', 'status', 'source', 'grant', 'business', 'roster'])
def test_final_fresh_snapshot_or_policy_change_suppresses_private_card(case, monkeypatch, change):
    task = seed(case)
    acl(case, task, [document(case, sender='test_operator')])
    def mutate():
        if change in ('revision', 'title', 'owner', 'status'):
            field, value = {'revision': ('revision', 22), 'title': ('title', 'Changed'),
                            'owner': ('owner', 'frost'), 'status': ('status', 'blocked')}[change]
            case[1].execute(f'UPDATE tasks SET {field}=? WHERE id=?', (value, task['id']))
        elif change == 'source':
            case[1].execute('UPDATE sources SET deleted=1')
        elif change == 'grant':
            case[1].execute('DELETE FROM knowledge_readers')
        elif change == 'business':
            case[0].c['business'] = 'OTHER'
        else:
            case[0].c['admins'] = ['frost']
    observed_render(case, monkeypatch, mutate)
    assert reply(case) == command._NOTICE
    assert rows(case)[0]['task_id'] is None


def test_final_clock_read_catches_permission_expiry_during_render(case, monkeypatch):
    task = seed(case)
    acl(case, task, [document(case, sender='test_operator')])
    observed_render(case, monkeypatch, lambda: case[2].__setitem__(0, case[2][0]+11))
    assert reply(case) == command._NOTICE


@pytest.mark.parametrize('field', ['b', 's', 'c'])
def test_mismatched_intelligence_context_denies_before_read_and_wrong_namespace_protection(case, field):
    seed(case)
    setattr(case[0].intelligence, field, 'OTHER' if field == 'b' else object())
    sql = []
    case[1].db.set_trace_callback(sql.append)
    output = reply(case)
    case[1].db.set_trace_callback(None)
    assert output == command._NOTICE
    assert_no_content_reads(sql)
    assert not case[1].rows('SELECT * FROM intelligence_outbox_acl')


def test_intelligence_business_drift_during_render_denies_before_private_queue(case, monkeypatch):
    seed(case)
    observed_render(case, monkeypatch, lambda: setattr(case[0].intelligence, 'b', 'OTHER'))
    assert reply(case) == command._NOTICE
    assert not case[1].rows('SELECT * FROM intelligence_outbox_acl')


def test_intelligence_drift_inside_queue_aborts_whole_transaction(case, monkeypatch):
    seed(case)
    real = case[1].queue
    def changed(*a, **k):
        result = real(*a, **k)
        case[0].intelligence.b = 'OTHER'
        return result
    monkeypatch.setattr(case[1], 'queue', changed)
    with pytest.raises(command.CardUnavailable):
        case[0].reply('card-event', 'test_operator', 'Покажи задачу №1', channel='bot')
    assert not rows(case) and not case[1].rows("SELECT * FROM events WHERE id='card-event'")


@pytest.mark.parametrize(('sender', 'channel'), [('alen', 'group'), ('test_operator', None), ('outsider', 'bot')])
def test_closed_team_private_gate_denied(case, sender, channel):
    seed(case)
    case[0].reply('card-event', sender, 'Покажи задачу №1', channel=channel)
    assert not rows(case)


def test_without_current_index_honest_fixed_notice_not_unprotected_fallback(case):
    seed(case)
    case[0].intelligence = None
    assert reply(case) == command._NOTICE


@pytest.mark.parametrize('field', ['title', 'next_step', 'criterion', 'deadline'])
def test_sql_size_guard_rejects_oversized_selected_content_without_echo(case, field):
    seed(case, **{field: 'PRIVATE-OVERSIZE'*300})
    assert reply(case) == command._NOTICE


def test_html_escape_and_utf16_budget_no_partial_card(case):
    seed(case, title='<b>Literal & safe</b>')
    assert '&lt;b&gt;Literal &amp; safe&lt;/b&gt;' in reply(case)
    case[1].execute("UPDATE tasks SET title=?,next_step=?,criterion=?", ('&'*220, '&'*220, '&'*180))
    case[0].c['employees']['alen']['name'] = '&'*256
    assert reply(case, event='too-long') == command._NOTICE


def test_whole_business_register_cap_before_archive_filters(case):
    seed(case)
    for i in range(command.MAX_REGISTER):
        seed(case, id='archived-'+str(i), status='completed')
    sql = []
    case[1].db.set_trace_callback(sql.append)
    assert reply(case) == command._NOTICE
    case[1].db.set_trace_callback(None)
    assert_no_content_reads(sql)
    register = [q for q in sql if ' FROM tasks WHERE business=' in q and 'public_number' not in q and ' AS id' in q]
    assert register and 'LIMIT' in register[0]


def test_exact_event_dedupe_no_model_task_source_or_history_changes(case):
    task = seed(case)
    before = case[1].rows('SELECT * FROM tasks')
    reply(case)
    assert not case[0].reply('card-event', 'test_operator', 'Покажи задачу №1', channel='bot')
    case[0].process('card-event')
    assert len(rows(case)) == 1 and case[1].rows('SELECT * FROM tasks') == before
    assert not case[1].rows('SELECT * FROM usage') and not case[1].rows('SELECT * FROM task_status_history')
    assert case[1].rows('SELECT body FROM sources')[0]['body'] == 'PRIVATE-SOURCE-BODY'


def test_queue_failure_rolls_back_event_and_partial_body(case, monkeypatch):
    seed(case)
    real = case[1].queue
    def failed(*a, **k):
        real(*a, **k)
        raise RuntimeError('synthetic queue failure')
    monkeypatch.setattr(case[1], 'queue', failed)
    with pytest.raises(RuntimeError):
        case[0].reply('card-event', 'test_operator', 'Покажи задачу №1', channel='bot')
    assert not rows(case) and not case[1].rows("SELECT * FROM events WHERE id='card-event'")


def test_existing_delivery_acl_blocks_reassignment_and_source_grant_revocation(case, monkeypatch):
    task = seed(case)
    acl(case, task, [document(case)])
    reply(case, sender='alen')
    row = rows(case)[0]
    # The preexisting delivery guard constructs its own index; the new command
    # itself ran above with constructor calls forbidden. Scope this legacy API.
    monkeypatch.setattr(KnowledgeIndex, '__init__', INDEX_INIT)
    assert delivery_permitted(case[1], 'GOLDWAY', 'alen', row['id'], case[2][0], case[0].c)
    case[1].execute("UPDATE tasks SET owner='frost'")
    assert not delivery_permitted(case[1], 'GOLDWAY', 'alen', row['id'], case[2][0], case[0].c)
    case[1].execute("UPDATE tasks SET owner='alen'")
    case[0].intelligence.index.revoke_source('private-document-source')
    assert not delivery_permitted(case[1], 'GOLDWAY', 'alen', row['id'], case[2][0], case[0].c)


def test_optional_without_trusted_reply_has_no_latest_task_fallback(case):
    seed(case)
    assert reply(case, 'Покажи задачу') == command._NOTICE


def test_existing_actual_telegram_reply_mapper_links_sent_card_to_new_read(case):
    task = seed(case)
    reply(case, sender='alen')
    row = rows(case)[0]
    # Synthetic receipt only; no send/Dispatcher/API occurs.
    case[1].execute("UPDATE outbox SET state='sent',message_id='777',transport='bot',destination_chat='3' WHERE id=?", (row['id'],))
    now = int(case[2][0])
    message = {'message_id': 778, 'date': now, 'chat': {'id': 3, 'type': 'private'},
               'from': {'id': 3, 'is_bot': False}, 'text': 'Покажи задачу',
               'reply_to_message': {'message_id': 777, 'date': now, 'chat': {'id': 3, 'type': 'private'},
                                    'from': {'id': 100, 'is_bot': True}, 'text': row['text']}}
    Updates(case[0], NoExternal(), 100).handle({'update_id': 501, 'message': message})
    new = rows(case, 'update:501')
    assert len(new) == 1 and new[0]['task_id'] == task['id']
    assert 'Задача №1' in new[0]['text']


@pytest.mark.parametrize('text', ['Покажи задачу №1 и закрой её', 'Покажи задачи №1',
                                 'текст Покажи задачу №1', 'Покажи задачу №1 №2', None, 123])
def test_finite_matching_never_interprets_extra_actions_or_ambiguous_targets(text):
    assert not command.is_task_card_command(text)


def test_internal_fingerprint_immutable_no_revision_in_product(case):
    task = seed(case)
    result = command.build_task_card(case[1], case[0].c, 'alen', case[0].intelligence.index,
                                     clock=case[0].now, number=task['public_number'])
    assert len(result.snapshot_digest) == 64 and result.snapshot_digest not in result.text
    with pytest.raises(FrozenInstanceError):
        result.task_id = 'other'
