"""Actual Engine boundary for own-task current ACL overview; synthetic only."""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
import goldway.own_task_overview as command
from goldway.engine import Engine
from goldway.intelligence import reader_key
from goldway.knowledge import KnowledgeIndex
from goldway.store import Store
from goldway.work_context import work_id
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']


class NoExternal:
    def propose(self, *a, **k):
        pytest.fail('overview called model')
    def call(self, *a, **k):
        pytest.fail('overview called Telegram/provider')


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
        pytest.fail('overview reached context/refresh/external I/O/constructor')
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



def reply(case, text='Мои задачи', sender='alen', channel='bot', event='overview'):
    assert case[0].reply(event, sender, text, channel=channel)
    found = case[1].rows('SELECT * FROM outbox WHERE dedupe=?', ('own-task-overview:'+event,))
    return found[0] if found else None


@pytest.mark.parametrize('text', ['Мои задачи', 'Покажи мои задачи', '  МОИ   ЗАДАЧИ! ', 'Что у меня на сегодня?'])
@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_exact_commands_own_active_only_escaped(case, text, channel):
    task = seed(case, title='<b>Synthetic own</b>', deadline='2026-10-16')
    seed(case, 'other', owner='frost', title='OTHER-TITLE')
    seed(case, 'proposal', status='proposed', title='PROPOSAL-TITLE')
    seed(case, 'finished', status='completed', title='COMPLETED-TITLE')
    out = reply(case, text, channel=channel)
    assert '&lt;b&gt;Synthetic own&lt;/b&gt;' in out['text']
    assert all(x not in out['text'] for x in ['OTHER-TITLE', 'PROPOSAL-TITLE', 'COMPLETED-TITLE', task['id'], 'revision'])
    assert '№1' in out['text'] and 'Срез на' in out['text']
    assert out['task_id'] is None and out['preferred_transport'] == channel
    assert out['parse_mode'] == 'HTML'


def test_admin_still_only_own(case):
    seed(case, title='STAFF-TITLE')
    seed(case, 'admin', owner='test_operator', title='ADMIN-OWN')
    out = reply(case, sender='test_operator')['text']
    assert 'ADMIN-OWN' in out and 'STAFF-TITLE' not in out


@pytest.mark.parametrize('text', ['Мои задачи и выполни их', 'Покажи мои задачи №1', 'Что у меня на сегодня и завтра?', '/tasks', 'на сегодня'])
def test_not_substrings(text):
    assert not command.is_own_task_overview(text)


@pytest.mark.parametrize('deadline,group', [('2026-10-15','Просроченные'), ('2026-10-16','На сегодня'),
    ('2026-10-17','Будущие'), (None,'Срок не уточнён'), ('неизвестно','Срок не уточнён'),
    ('2026-10-15T22:00:00Z','Просроченные'), ('2026-10-16T20:59:59Z','На сегодня'), ('2026-10-16T21:00:00Z','Будущие')])
def test_local_day_and_utc(case, deadline, group):
    case[2][0] = datetime.fromisoformat('2026-10-16T23:00:00+03:00').timestamp()
    seed(case, deadline=deadline)
    assert '<b>'+group+'</b>' in reply(case)['text']


def test_today_whole_day_and_future_excluded(case):
    case[2][0] = datetime.fromisoformat('2026-10-16T23:59:59+03:00').timestamp()
    for id, deadline in [('TODAY','2026-10-16'), ('PAST','2026-10-15'), ('UNKNOWN',None), ('FUTURE','2026-10-17')]:
        seed(case, id, title=id+'-RECORD', deadline=deadline)
    out = reply(case, 'Что у меня на сегодня?')['text']
    assert all(x+'-RECORD' in out for x in ['TODAY','PAST','UNKNOWN']) and 'FUTURE-RECORD' not in out
    assert out.index('PAST-RECORD') < out.index('TODAY-RECORD') < out.index('UNKNOWN-RECORD')


def test_denied_documents_dependencies_not_display_read(case):
    denied = seed(case, 'denied', title='DENIED-TITLE', deadline='2020-01-01')
    acl(case, denied, [document(case, 'frost')])
    other = seed(case, 'other', owner='frost')
    dependent = seed(case, 'dependent', title='DEPENDENT-DENIED')
    acl(case, dependent, [work_id('GOLDWAY', other['id'])])
    seed(case, 'allowed', title='PERMITTED')
    queries = []
    case[1].db.set_trace_callback(queries.append)
    out = reply(case)['text']
    case[1].db.set_trace_callback(None)
    assert 'PERMITTED' in out and 'DENIED-TITLE' not in out and 'DEPENDENT-DENIED' not in out
    content = [q for q in queries if ' AS title' in q]
    assert content and all("id='allowed'" in q for q in content)


def test_deleted_source_hidden(case):
    task = seed(case)
    case[1].execute('UPDATE sources SET deleted=1 WHERE id=?', (task['source_id'],))
    assert task['title'] not in reply(case)['text']


def test_no_bodies_or_evidence_read(case):
    task = seed(case)
    acl(case, task, [document(case)])
    def auth(action, table, column, *_):
        if action == sqlite3.SQLITE_READ:
            if table == 'tasks' and column == 'evidence': return sqlite3.SQLITE_DENY
            if table == 'sources' and column in ('body','link'): return sqlite3.SQLITE_DENY
            if table in ('knowledge_documents','knowledge_chunks') and column in ('text','title','url'): return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    case[1].db.set_authorizer(auth)
    try: assert task['title'] in reply(case)['text']
    finally: case[1].db.set_authorizer(None)


def test_five_limit_all_consulted_refs_protected(case, monkeypatch):
    tasks = [seed(case, 'task'+str(i), title='Visible '+str(i), deadline='2026-10-16') for i in range(7)]
    future = seed(case, 'future', title='FUTURE-SENTINEL', deadline='2026-11-01')
    protected = []
    real = case[0].intelligence.protect_outbox
    def observe(outbox, refs):
        protected.extend(refs)
        return real(outbox, refs)
    monkeypatch.setattr(case[0].intelligence, 'protect_outbox', observe)
    out = reply(case, 'Что у меня на сегодня?')['text']
    assert sum('Visible '+str(i) in out for i in range(7)) == 5 and 'FUTURE-SENTINEL' not in out
    assert {work_id('GOLDWAY', t['id']) for t in tasks+[future]} <= set(protected)
    assert command._FOOTER in out


@pytest.mark.parametrize('change', ['owner','status','title','source','roster','midnight','clock_back','doc_expiry','unshown'])
def test_final_fresh_snapshot(case, monkeypatch, change):
    case[2][0] = datetime.fromisoformat('2026-10-16T23:59:59+03:00').timestamp()
    task = seed(case, deadline='2026-10-16')
    future = seed(case, 'future', deadline='2026-11-01')
    if change == 'doc_expiry': acl(case, task, [document(case)])
    real = command._render
    def mutate(*a, **k):
        out = real(*a, **k)
        if change in ('owner','status','title'):
            value = {'owner':'frost','status':'completed','title':'CHANGED'}[change]
            case[1].execute('UPDATE tasks SET '+change+'=? WHERE id=?', (value, task['id']))
        if change == 'source': case[1].execute('UPDATE sources SET deleted=1 WHERE id=?', (task['source_id'],))
        if change == 'roster': case[0].c['employees']['alen']['name'] = 'CHANGED'
        if change == 'midnight': case[2][0] += 1
        if change == 'clock_back': case[2][0] -= 1
        if change == 'doc_expiry': case[2][0] += 10
        if change == 'unshown': case[1].execute("UPDATE tasks SET title='CHANGED' WHERE id=?", (future['id'],))
        return out
    monkeypatch.setattr(command, '_render', mutate)
    assert reply(case, 'Что у меня на сегодня?')['text'] == command._NOTICE


@pytest.mark.parametrize('field', ['b','s','c'])
def test_intelligence_identity_guard(case, field):
    seed(case)
    setattr(case[0].intelligence, field, object())
    assert reply(case)['text'] == command._NOTICE


def test_queue_drift_rollback(case, monkeypatch):
    seed(case)
    real = case[1].queue
    def drift(*a, **k):
        result = real(*a, **k)
        case[0].intelligence.b = 'OTHER'
        return result
    monkeypatch.setattr(case[1], 'queue', drift)
    with pytest.raises(command.OwnOverviewUnavailable): reply(case)
    assert not case[1].rows('SELECT * FROM outbox') and not case[1].rows('SELECT * FROM events')


def test_duplicate_one_queue(case):
    seed(case)
    first = reply(case)
    assert case[0].reply('overview','alen','Мои задачи', channel='bot') is False
    assert case[1].rows('SELECT * FROM outbox') == [first]


@pytest.mark.parametrize('channel,sender', [('group','alen'), ('bot','outsider')])
def test_private_current_team_only(case, channel, sender):
    seed(case)
    if sender == 'outsider':
        assert case[0].reply('overview', sender, 'Мои задачи', channel=channel) is False
    else:
        assert reply(case, sender=sender, channel=channel) is None
    assert not case[1].rows('SELECT * FROM outbox')


@pytest.mark.parametrize('count,unavailable', [(128,False),(129,True)])
def test_register_bounds(case, count, unavailable):
    for i in range(count): seed(case, str(i), title='Task '+str(i))
    assert (reply(case)['text'] == command._NOTICE) == unavailable


def test_query_failure_fatal_not_skipped(case, monkeypatch):
    seed(case)
    real = command._Reader.query
    def bounded(self, sql, params=(), *, maximum=1):
        if ' AS title' in sql: raise command.MonthlyHistoryUnavailable()
        return real(self, sql, params, maximum=maximum)
    monkeypatch.setattr(command._Reader,'query',bounded)
    assert reply(case)['text'] == command._NOTICE


def test_astral_html_bounded(case):
    for i in range(5): seed(case, str(i), title='😀<'*450, next_step='😀&'*450)
    out = reply(case)['text']
    assert out != command._NOTICE and len(out.encode('utf-16-le'))//2 <= 3500
    assert '&lt;' in out and '&amp;' in out


def test_empty_current_view_honest_constant_footer(case):
    out = reply(case)['text']
    assert 'В доступных записях' in out and command._FOOTER in out
    assert 'Всего' not in out and '0 задач' not in out


def test_no_intelligence_fixed_notice(case):
    seed(case)
    case[0].intelligence = None
    assert reply(case)['text'] == command._NOTICE


def test_output_guard_fixed_notice(case, monkeypatch):
    for i in range(5): seed(case, str(i))
    # Force a renderer expansion through a supplied status label, then exercise
    # real UTF16 output bound; never queue partial content.
    monkeypatch.setitem(command.STATUS, 'confirmed', '😀'*1800)
    assert reply(case)['text'] == command._NOTICE


def test_postbuild_identity_drift_fixed_notice(case, monkeypatch):
    seed(case)
    real = command.build_own_task_overview
    def drift(*a, **k):
        draft = real(*a, **k)
        case[0].intelligence.b = 'OTHER'
        return draft
    monkeypatch.setattr(command, 'build_own_task_overview', drift)
    assert reply(case)['text'] == command._NOTICE


def test_protection_failure_rolls_back_transaction(case, monkeypatch):
    seed(case)
    def fail(*a, **k): raise RuntimeError('synthetic protection failure')
    monkeypatch.setattr(case[0].intelligence, 'protect_outbox', fail)
    with pytest.raises(RuntimeError): reply(case)
    assert not case[1].rows('SELECT * FROM outbox') and not case[1].rows('SELECT * FROM events')


def test_bounded_content_failure_never_makes_false_empty(case):
    seed(case, title='x'*4001)
    assert reply(case)['text'] == command._NOTICE


def test_other_business_not_in_view_or_counts(case):
    seed(case, 'other', business='OTHER', title='OTHER-BUSINESS-SENTINEL')
    seed(case, title='OWN-BUSINESS')
    out = reply(case)['text']
    assert 'OWN-BUSINESS' in out and 'OTHER-BUSINESS-SENTINEL' not in out


def test_task_state_never_written(case):
    seed(case)
    before = case[1].rows('SELECT * FROM tasks')
    def auth(action, table, *_):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and table in ('tasks','sources','task_sources','intelligence_task_acl'):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    case[1].db.set_authorizer(auth)
    try: assert reply(case)['text'] != command._NOTICE
    finally: case[1].db.set_authorizer(None)
    assert case[1].rows('SELECT * FROM tasks') == before
