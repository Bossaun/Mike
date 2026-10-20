"""New ordinary-command integration; all sources, users and deliveries synthetic."""
import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher, Updates, DeliveryError
from goldway.intelligence import reader_key
from goldway.knowledge import KnowledgeIndex
from goldway import selected_knowledge_command as command

NOW = 1792174600
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']
FORM = command.HEADER + '\nИсточник: регистрация manual\nДокумент: guide\nЗапрос: shipping'


class NoModel:
    def propose(self, *args, **kwargs):
        pytest.fail('model forbidden')


class API:
    def __init__(self):
        self.sent, self.lookups = [], []
        self.callback = self.error = None
        self.account, self.enabled, self.can_reply = 99, True, True

    def call(self, method, **params):
        if method == 'getBusinessConnection':
            self.lookups.append(params)
            if self.callback:
                self.callback()
            return dict(user=dict(id=self.account), is_enabled=self.enabled,
                        rights=dict(can_reply=self.can_reply))
        assert method == 'sendMessage'
        self.sent.append(params)
        if self.error:
            raise self.error
        return dict(message_id=100 + len(self.sent))


def config():
    return dict(business='GOLDWAY', timezone='Europe/Moscow',
                employees={x: dict(name=x, telegram_user_id=i + 1) for i, x in enumerate(MEMBERS)},
                admins=['test_operator'], business_account_user_id=99,
                team_intake=dict(recipients=MEMBERS[:], started_at=100),
                allowed_live_recipients=MEMBERS[:], team_roster_verified=True,
                recipients_verified=True, live_send=True, review_mode=True, sheets_write=False,
                daily_summary=False, allow_onboarding_replies=False, allow_registered_replies=False,
                allowed_group_ids=[], group_destinations={}, meeting_recipient=None,
                bot_user_id=100, bot_user_id_verified=True,
                intelligence=dict(enabled=True, sources=[dict(id='manual', kind='http',
                    url='https://example.invalid/manual', readers=['test_operator'])]))


def make(path=':memory:', cfg=None, registry=None, bindings=()):
    store = Store(path)
    clock = [NOW]
    cfg = config() if cfg is None else cfg
    engine = Engine(store, cfg, NoModel(), lambda: clock[0], trusted_registry=registry, trusted_sources=bindings)
    api = API()
    return SimpleNamespace(s=store, e=engine, c=cfg, api=api, clock=clock)


@pytest.fixture
def c(monkeypatch):
    value = make()
    def forbidden(*args, **kwargs):
        pytest.fail('context, refresh, constructor or network forbidden')
    value.e._context = forbidden
    value.e.intelligence.sources.refresh = forbidden
    import httpx, socket
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    yield value
    value.s.db.set_authorizer(None)
    value.s.db.close()


def doc(c, identifier='guide', **kwargs):
    data = dict(source_id=c.e.intelligence.sources.specs[0]['id'], document_id=identifier,
                title='Shipping guide', url='https://example.invalid/guide',
                text='Shipping takes three days. Literal source evidence.',
                readers=[reader_key('GOLDWAY', 'test_operator')],
                observed_at=c.clock[0] - 1, permission_expires_at=c.clock[0] + 1000,
                confirmed=True, updated_at='2026-10-16', facts=[])
    data.update(kwargs)
    c.e.intelligence.index.upsert(data)
    key = hashlib.sha256(json.dumps([data['source_id'], data['document_id']],
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return tuple(r['id'] for r in c.s.rows('SELECT id FROM knowledge_chunks WHERE document_key=? ORDER BY id', (key,)))


def ready(c, guard=True):
    c.s.execute('INSERT OR REPLACE INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',
                ('GOLDWAY', 'test_operator', '1', '1', 'conn', c.clock[0]))
    return dispatch(c, guard)


def dispatch(c, guard=True):
    return Dispatcher(c.s, c.c, c.api, lambda: c.clock[0],
        selected_knowledge_guard=command.make_delivery_guard(c.e) if guard else None)


def ask(c, text=FORM, event='request', sender='test_operator', channel='bot', source=None):
    uid = c.c['employees'][sender]['telegram_user_id']
    return c.e.reply(event, sender, text, source_id=source or
        (f'telegram:{uid}:10' if channel == 'bot' else f'business:conn:{uid}:10'),
        channel=channel, message_at=c.clock[0])


def update(c, text=FORM, channel='bot', number=10):
    updates = Updates(c.e, c.api, 100)
    if channel == 'business':
        updates.handle(dict(update_id=number - 1, business_connection=dict(id='conn',
            user=dict(id=99), is_enabled=True, rights=dict(can_reply=True))))
    message = dict(message_id=number, date=c.clock[0], chat=dict(id=1, type='private'),
                   **{'from': dict(id=1, is_bot=False)}, text=text)
    if channel == 'business':
        message['business_connection_id'] = 'conn'
    incoming = dict(update_id=number, **{'business_message' if channel == 'business' else 'message': message})
    updates.handle(incoming)
    return updates, incoming


def outputs(c):
    return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'selected-knowledge-preview:%'")


def receipts(c):
    return c.s.rows("SELECT * FROM runtime WHERE key LIKE 'selected-knowledge-preview:v1:%'")


def protected(c):
    return json.loads(c.s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',
                                 (outputs(c)[0]['id'],)).fetchone()[0])


def assert_no_preview(c):
    assert not outputs(c) and not receipts(c)
    assert all('Shipping takes' not in row['text'] for row in c.s.rows('SELECT text FROM outbox'))


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_actual_updates_i98_complete_own_protected_delivery(c, monkeypatch, channel):
    refs = doc(c)
    calls = []
    actual = KnowledgeIndex.search_selected
    def search(self, *args, **kwargs):
        calls.append(kwargs['documents'])
        return actual(self, *args, **kwargs)
    monkeypatch.setattr(KnowledgeIndex, 'search_selected', search)
    d = dispatch(c)
    updates, incoming = update(c, channel=channel)
    assert len(outputs(c)) == len(receipts(c)) == 1 and len(calls) == 2
    row = outputs(c)[0]
    assert row['recipient'] == 'test_operator' and row['task_id'] is None
    assert row['parse_mode'] == 'HTML' and row['preferred_transport'] == channel
    assert 'Shipping takes three days' in row['text']
    assert 'Источник 1' in row['text'] and 'независимо не проверены' in row['text']
    assert all(ref not in row['text'] for ref in refs) and 'reader:' not in row['text']
    assert protected(c) == sorted(refs)
    receipt = json.loads(receipts(c)[0]['value'])
    assert 'Shipping takes' not in receipts(c)[0]['value'] and receipt['refs'] == sorted(refs)
    updates.handle(incoming)
    assert len(outputs(c)) == len(receipts(c)) == 1
    d.flush()
    assert outputs(c)[0]['state'] == 'sent' and len(c.api.sent) == 1 and len(calls) == 4
    assert c.api.sent[0]['chat_id'] == '1'
    assert bool(c.api.sent[0].get('business_connection_id')) == (channel == 'business')
    assert not c.s.rows('SELECT * FROM tasks') and not c.s.rows('SELECT * FROM task_status_history')
    assert not c.s.rows('SELECT * FROM usage')


def test_header_only_fillable_template_has_no_private_source_reads(c):
    doc(c)
    trace = []
    c.s.db.set_trace_callback(trace.append)
    update(c, command.HEADER)
    c.s.db.set_trace_callback(None)
    assert_no_preview(c)
    template = c.s.rows('SELECT text FROM outbox')[0]['text']
    assert template == command.TEMPLATE and len(template.splitlines()) == 4
    assert not any('FROM knowledge_' in sql for sql in trace)
    ask(c, template.replace('<код регистрации>', 'manual').replace('<код документа>', 'guide')
        .replace('<вопрос до 256 символов>', 'shipping'), event='filled')
    assert len(outputs(c)) == 1


@pytest.mark.parametrize('text', [
    FORM + '\nExtra: no', FORM.replace('Источник:', 'Source:'), FORM.replace('Документ:', 'Документы:'),
    FORM.replace('регистрация manual', 'manual'), FORM.replace('регистрация manual', 'регистрация m/a'),
    FORM.replace('guide', ' guide'), FORM.replace('guide', 'guide '), FORM.replace('guide', ''),
    FORM.replace('shipping', ''), FORM.replace('shipping', 'q' * 257),
    FORM.replace('guide', 'g' * 257), FORM.replace('shipping', 'a\tb'),
    FORM.replace('shipping', 'a\rb'), FORM.replace('shipping', 'a\u2028b'),
    FORM.replace('Источник:', 'Источник: регистрация manual\nДокумент: guide\nИсточник:'),
    command.HEADER + '\n' + ('Источник: регистрация manual\nДокумент: guide\n' * 4) + 'Запрос: shipping',
])
def test_malformed_whole_form_never_partial_private_output(c, text):
    doc(c)
    ready(c)
    ask(c, text)
    assert_no_preview(c)
    assert c.s.rows('SELECT state FROM events')[0]['state'] == 'done'


@pytest.mark.parametrize('kind', ['employee', 'wrong_uid', 'group', 'multi_admin', 'business_drift',
    'no_team', 'no_intel', 'unowned_source', 'spec_removed', 'spec_reader', 'registration_reader', 'prefix'])
def test_scope_and_actual_source_ownership_deny_before_any_source_body(c, kind):
    doc(c)
    ready(c)
    if kind == 'multi_admin': c.c['admins'].append('alen')
    elif kind == 'business_drift': c.e.b = 'OTHER'
    elif kind == 'no_team': c.c.pop('team_intake')
    elif kind == 'no_intel': c.e.intelligence = None
    elif kind == 'spec_removed': c.e.intelligence.sources.specs.clear()
    elif kind == 'spec_reader': c.e.intelligence.sources.specs[0]['readers'] = [reader_key('GOLDWAY', 'alen')]
    elif kind == 'registration_reader': c.c['intelligence']['sources'][0]['readers'] = ['alen']
    elif kind == 'prefix': c.e.intelligence.prefix = 'source:other:'
    def deny_bodies(action, table, column, *args):
        if action == sqlite3.SQLITE_READ and table in ('knowledge_chunks', 'knowledge_documents', 'knowledge_facts'):
            if column in ('text', 'title', 'url', 'quote', 'value'):
                pytest.fail('source body read before ownership gate')
        return sqlite3.SQLITE_OK
    c.s.db.set_authorizer(deny_bodies)
    ask(c, FORM.replace('manual', 'foreign') if kind == 'unowned_source' else FORM,
        sender='alen' if kind == 'employee' else 'test_operator',
        channel='group' if kind == 'group' else 'bot',
        source='telegram:333:10' if kind == 'wrong_uid' else None)
    c.s.db.set_authorizer(None)
    assert_no_preview(c)


def test_outsider_not_accepted_and_ordinary_command_unchanged(c):
    assert c.e.reply('outsider', 'unknown', FORM) is False
    update(c, '/start')
    assert not outputs(c) and not receipts(c)
    assert c.s.rows('SELECT text FROM outbox')[0]['text'].startswith('Вы подключены')


@pytest.mark.parametrize('kind', ['grant', 'expired', 'future', 'missing', 'wrong_source', 'blob', 'oversize', 'fact_blob'])
def test_selected_core_current_grants_and_sql_body_gates(c, kind):
    doc(c, facts=[dict(subject='Shipping', predicate='duration', value='three', quote='Shipping takes three days.')])
    if kind == 'grant': c.s.execute('DELETE FROM knowledge_readers')
    elif kind == 'expired': c.s.execute('UPDATE knowledge_documents SET permission_expires_at=?', (NOW,))
    elif kind == 'future': c.s.execute('UPDATE knowledge_documents SET observed_at=?', (NOW + 1,))
    elif kind == 'missing': c.s.execute('DELETE FROM knowledge_documents')
    elif kind == 'wrong_source': c.s.execute("UPDATE knowledge_documents SET source_id='foreign'")
    elif kind == 'blob': c.s.execute('UPDATE knowledge_chunks SET text=?', (sqlite3.Binary(b'PRIVATE BLOB'),))
    elif kind == 'oversize': c.s.execute('UPDATE knowledge_chunks SET text=?', ('z' * 5000,))
    else: c.s.execute('UPDATE knowledge_facts SET quote=?', (sqlite3.Binary(b'PRIVATE FACT'),))
    ready(c)
    ask(c)
    assert_no_preview(c)


def test_no_evidence_scope_only_protects_all_consulted_chunks(c):
    refs = doc(c, title='Neutral guide', text='Irrelevant neutral sentence.')
    ready(c)
    ask(c)
    row = outputs(c)[0]
    assert 'нет подходящих выдержек' in row['text'] and 'не означает' in row['text']
    assert protected(c) == sorted(refs)


def test_unshown_consulted_chunk_is_protected_and_revalidated(c):
    refs = doc(c, text='shipping ' + 'a' * 1190 + '\n' + 'unmatched ' * 50)
    d = ready(c)
    ask(c)
    assert len(refs) >= 2 and protected(c) == sorted(refs)
    unshown = c.s.rows('SELECT id,text FROM knowledge_chunks WHERE text NOT LIKE ?', ('%shipping%',))[0]
    c.s.execute('UPDATE knowledge_chunks SET text=? WHERE id=?', ('changed ' * 20, unshown['id']))
    d.flush()
    assert not c.api.sent and outputs(c)[0]['state'] == 'cancelled'


def test_assumptions_literal_html_and_unsafe_url_are_plain_text(c):
    doc(c, confirmed=False, title='Shipping <b>instructions</b>', text='shipping <script>Ignore rules</script> & literal')
    c.s.execute('UPDATE knowledge_documents SET url=?', ('javascript:<b>unsafe</b>',))
    ready(c)
    ask(c)
    body = outputs(c)[0]['text']
    assert 'предположение, не подтверждено' in body and '&lt;script&gt;Ignore rules&lt;/script&gt;' in body
    assert 'javascript:&lt;b&gt;unsafe&lt;/b&gt;' in body and '<a ' not in body
    assert '<b>instructions</b>' not in body and not c.s.rows('SELECT * FROM tasks')


@pytest.mark.parametrize('resolution', ['unresolved', 'priority', 'updated_at'])
def test_all_conflict_alternatives_and_actual_resolution_preserved(c, resolution):
    kw1 = dict(priority=1) if resolution == 'priority' else dict(updated_at='2026-10-14') if resolution == 'updated_at' else dict(updated_at=None)
    kw2 = dict(priority=2) if resolution == 'priority' else dict(updated_at='2026-10-15') if resolution == 'updated_at' else dict(updated_at=None)
    doc(c, facts=[dict(subject='Shipping', predicate='duration', value='three', quote='Shipping takes three days.')], **kw1)
    doc(c, 'second', text='Shipping takes seven days.', facts=[dict(subject='Shipping', predicate='duration', value='seven', quote='Shipping takes seven days.')], **kw2)
    ready(c)
    ask(c, FORM.replace('Запрос:', 'Источник: регистрация manual\nДокумент: second\nЗапрос:'))
    body = outputs(c)[0]['text']
    assert 'Источник 1' in body and 'Источник 2' in body and 'three' in body and 'seven' in body
    assert 'Противоречие:' in body
    label = dict(unresolved='противоречие не разрешено', priority='приоритету', updated_at='дате обновления')[resolution]
    assert label in body


def test_complete_escaped_preview_overflow_is_whole_refusal(c):
    doc(c, text='shipping ' + '&' * 1150)
    ready(c)
    ask(c)
    assert_no_preview(c)
    assert c.s.rows('SELECT text FROM outbox')[0]['text'] == command.NOTICE


@pytest.mark.parametrize('phase', ['queue', 'protect'])
@pytest.mark.parametrize('kind', ['content', 'fact', 'grant', 'observation', 'config', 'intel', 'index', 'clock', 'source'])
def test_queue_and_protect_callback_drift_roll_back_private_bundle(c, monkeypatch, phase, kind):
    doc(c, facts=[dict(subject='Shipping', predicate='duration', value='three', quote='Shipping takes three days.')])
    ready(c)
    def drift():
        if kind == 'content': c.s.execute("UPDATE knowledge_chunks SET text=replace(text,'three','seven')")
        elif kind == 'fact': c.s.execute("UPDATE knowledge_facts SET value='different'")
        elif kind == 'grant': c.s.execute('DELETE FROM knowledge_readers')
        elif kind == 'observation': c.s.execute('UPDATE knowledge_documents SET observed_at=observed_at-1')
        elif kind == 'config': c.e.c = copy.deepcopy(c.e.c)
        elif kind == 'intel': c.e.intelligence = copy.copy(c.e.intelligence)
        elif kind == 'index': c.e.intelligence.index = copy.copy(c.e.intelligence.index)
        elif kind == 'clock': c.e.now = lambda: NOW
        else: c.e.intelligence.sources.specs[0]['readers'].clear()
    if phase == 'queue':
        actual = c.s.queue
        def call(*args, **kwargs):
            result = actual(*args, **kwargs)
            if args[3].startswith(command.PREFIX): drift()
            return result
        monkeypatch.setattr(c.s, 'queue', call)
    else:
        actual = c.e.intelligence.protect_outbox
        def call(*args, **kwargs):
            result = actual(*args, **kwargs)
            drift()
            return result
        monkeypatch.setattr(c.e.intelligence, 'protect_outbox', call)
    ask(c)
    assert_no_preview(c)
    assert not c.s.rows('SELECT * FROM intelligence_outbox_acl')


@pytest.mark.parametrize('phase', ['queue', 'protect', 'receipt'])
def test_storage_failure_rolls_back_event_preview_receipt_and_protection(c, monkeypatch, phase):
    doc(c)
    ready(c)
    if phase == 'receipt':
        c.s.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON runtime WHEN NEW.key LIKE 'selected-knowledge-preview:%' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    else:
        target, name = (c.s, 'queue') if phase == 'queue' else (c.e.intelligence, 'protect_outbox')
        actual = getattr(target, name)
        def fail(*args, **kwargs):
            result = actual(*args, **kwargs)
            if phase == 'protect' or args[3].startswith(command.PREFIX):
                raise sqlite3.IntegrityError('synthetic')
            return result
        monkeypatch.setattr(target, name, fail)
    with pytest.raises(sqlite3.IntegrityError): ask(c)
    assert_no_preview(c)
    assert not c.s.rows('SELECT * FROM intelligence_outbox_acl') and not c.s.rows('SELECT * FROM events')


@pytest.mark.parametrize('kind', ['content', 'fact', 'grant', 'observation', 'expiry', 'title', 'url', 'source_policy'])
@pytest.mark.parametrize('phase', ['after_queue', 'business_route'])
def test_changed_source_after_queue_or_route_cancels_without_old_body_send(c, kind, phase):
    doc(c, facts=[dict(subject='Shipping', predicate='duration', value='three', quote='Shipping takes three days.')])
    d = ready(c)
    ask(c, channel='business')
    def drift():
        if kind == 'content': c.s.execute("UPDATE knowledge_chunks SET text=replace(text,'three','seven')")
        elif kind == 'fact': c.s.execute("UPDATE knowledge_facts SET value='seven'")
        elif kind == 'grant': c.s.execute('DELETE FROM knowledge_readers')
        elif kind == 'observation': c.s.execute('UPDATE knowledge_documents SET observed_at=observed_at-1')
        elif kind == 'expiry': c.clock[0] = NOW + 1000
        elif kind == 'title': c.s.execute("UPDATE knowledge_documents SET title='Changed title'")
        elif kind == 'url': c.s.execute("UPDATE knowledge_documents SET url='https://example.invalid/changed'")
        else: c.e.intelligence.sources.specs[0]['readers'].clear()
    if phase == 'after_queue': drift()
    else: c.api.callback = drift
    d.flush()
    assert not c.api.sent and outputs(c)[0]['state'] == 'cancelled'


@pytest.mark.parametrize('kind', ['missing', 'receipt', 'duplicate_key', 'receipt_blob', 'receipt_large',
    'body', 'body_blob', 'body_large', 'refs', 'accepted', 'accepted_blob', 'accepted_large',
    'recipient', 'task', 'parse', 'transport'])
def test_point_receipt_accepted_turn_body_and_refs_tamper_never_delivered(c, kind):
    doc(c)
    d = ready(c)
    ask(c)
    if kind == 'missing': c.s.execute("DELETE FROM runtime WHERE key LIKE 'selected-knowledge-preview:%'")
    elif kind == 'receipt':
        value = json.loads(receipts(c)[0]['value']); value['snapshot'] = '0' * 64
        c.s.execute('UPDATE runtime SET value=? WHERE key=?', (json.dumps(value), receipts(c)[0]['key']))
    elif kind == 'duplicate_key': c.s.execute('UPDATE runtime SET value=? WHERE key=?', ('{"version":1,"version":1}', receipts(c)[0]['key']))
    elif kind.startswith('receipt_'): c.s.execute('UPDATE runtime SET value=? WHERE key=?',
        (sqlite3.Binary(b'PRIVATE') if kind == 'receipt_blob' else 'x' * 16385, receipts(c)[0]['key']))
    elif kind in ('body', 'body_blob', 'body_large'): c.s.execute('UPDATE outbox SET text=?',
        ('tampered' if kind == 'body' else sqlite3.Binary(b'PRIVATE') if kind == 'body_blob' else 'x' * 8001,))
    elif kind == 'refs': c.s.execute("UPDATE intelligence_outbox_acl SET evidence_ids='[]'")
    elif kind.startswith('accepted'):
        if kind == 'accepted':
            value = json.loads(c.s.rows('SELECT payload FROM events')[0]['payload']); value['text'] = FORM.replace('shipping', 'different')
            value = json.dumps(value)
        else: value = sqlite3.Binary(b'PRIVATE') if kind == 'accepted_blob' else 'x' * 16385
        c.s.execute('UPDATE events SET payload=?', (value,))
    else: c.s.execute('UPDATE outbox SET ' + dict(recipient='recipient', task='task_id', parse='parse_mode', transport='preferred_transport')[kind] + '=?',
        (dict(recipient='alen', task='foreign', parse='Markdown', transport='business')[kind],))
    d.flush()
    assert not c.api.sent and outputs(c)[0]['state'] == 'cancelled'


def test_latest_accepted_turn_cancels_pending_preview_without_fallback(c):
    doc(c)
    d = ready(c)
    ask(c)
    ask(c, command.HEADER, event='new-turn')
    d.flush()
    assert outputs(c)[0]['state'] == 'cancelled'
    assert all('Shipping takes' not in row['text'] for row in c.api.sent)


def test_absent_guard_default_denies_only_new_namespace(c):
    doc(c)
    d = ready(c, False)
    ask(c)
    c.s.queue('GOLDWAY', 'test_operator', 'ordinary safe', 'ordinary', NOW, preferred_transport='bot')
    d.flush()
    assert outputs(c)[0]['state'] == 'cancelled' and [row['text'] for row in c.api.sent] == ['ordinary safe']


@pytest.mark.parametrize('kind', ['wrong_uid', 'wrong_connection', 'thread', 'fallback', 'window'])
def test_exact_original_private_route_only_no_business_fallback(c, monkeypatch, kind):
    doc(c)
    c.c['business_bot_fallback'] = True
    d = ready(c)
    ask(c, channel='business')
    if kind == 'window': c.clock[0] = NOW + 86400
    elif kind == 'fallback': c.api.can_reply = False
    else:
        route = dict(chat_id='333' if kind == 'wrong_uid' else '1', business_connection_id='other' if kind == 'wrong_connection' else 'conn')
        if kind == 'thread': route['message_thread_id'] = 5
        monkeypatch.setattr(d, 'route', lambda *a: (route, 'business'))
    d.flush()
    assert not c.api.sent and outputs(c)[0]['state'] == 'cancelled'


@pytest.mark.parametrize('kind', ['unknown', 'sending', 'sent'])
def test_no_replay_or_replacement_of_uncertain_or_final_delivery(c, kind):
    doc(c)
    d = ready(c)
    ask(c)
    c.s.execute('UPDATE outbox SET state=?', (kind,))
    c.s.execute('DELETE FROM knowledge_readers')
    d.flush()
    assert not c.api.sent and outputs(c)[0]['state'] == kind
    command.cancel(c.s, 'GOLDWAY', outputs(c)[0]['id'])
    assert outputs(c)[0]['state'] == kind


def test_actual_unknown_delivery_is_not_automatically_retried(c):
    doc(c)
    d = ready(c)
    ask(c)
    c.api.error = RuntimeError('synthetic uncertain result')
    d.flush(); d.flush()
    assert len(c.api.sent) == 1 and outputs(c)[0]['state'] == 'unknown'


def test_file_close_reopen_same_current_scope_and_source_allows_pending_preview(tmp_path):
    path = str(tmp_path / 'synthetic.sqlite')
    c = make(path)
    doc(c)
    ready(c)
    ask(c)
    # Restart retires old initialized local grants; a NEW observation with exactly
    # the prior provenance is not faked to resurrect it. Preserve that boundary.
    c.s.db.close()
    c = make(path)
    d = dispatch(c)
    assert not c.s.rows('SELECT * FROM knowledge_documents')
    d.flush()
    assert outputs(c)[0]['state'] == 'cancelled' and not c.api.sent
    c.clock[0] = NOW + 2
    doc(c)
    ask(c, event='fresh-after-reopen')
    d.flush()
    assert outputs(c)[1]['state'] == 'sent' and len(c.api.sent) == 1
    c.s.db.close()


def test_no_intelligence_factory_safe_and_ordinary_delivery_unaffected():
    cfg = config(); cfg['intelligence']['enabled'] = False
    c = make(cfg=cfg)
    d = ready(c)
    c.s.queue('GOLDWAY', 'test_operator', 'safe', 'ordinary', NOW, preferred_transport='bot')
    d.flush()
    assert [row['text'] for row in c.api.sent] == ['safe']
    ask(c)
    assert_no_preview(c)
    c.s.db.close()


@pytest.mark.parametrize('phase', ['queue', 'delivery_clock'])
def test_last_clock_callback_mutation_is_before_last_actual_selected_data_read(c, monkeypatch, phase):
    doc(c)
    d = ready(c)
    active = [False]
    original_clock = c.e.now
    def clock():
        if active[0]: c.s.execute('DELETE FROM knowledge_readers')
        return original_clock()
    c.e.now = clock
    # Factory pins the actual clock, so create it after explicit host setup.
    d = dispatch(c)
    if phase == 'queue':
        original_queue = c.s.queue
        def queue(*args, **kwargs):
            result = original_queue(*args, **kwargs)
            if args[3].startswith(command.PREFIX): active[0] = True
            return result
        monkeypatch.setattr(c.s, 'queue', queue)
        ask(c)
        assert_no_preview(c)
    else:
        ask(c)
        active[0] = True
        d.flush()
        assert not c.api.sent and outputs(c)[0]['state'] == 'cancelled'


def test_actual_helper_reader_budgets_are_cumulative_and_lookup_counts_bounded(c, monkeypatch):
    doc(c)
    metadata, data = [], []
    class Reader(command._Reader):
        def __init__(self, *args):
            super().__init__(*args); metadata.append(self)
    import goldway.knowledge as knowledge
    class Budget(knowledge._SelectedBudget):
        def __init__(self, *args):
            super().__init__(*args); data.append(self)
    monkeypatch.setattr(command, '_Reader', Reader)
    monkeypatch.setattr(knowledge, '_SelectedBudget', Budget)
    d = ready(c)
    ask(c)
    assert len(metadata) == 1 and len(data) == 2
    assert metadata[0].bytes <= 4 * 1024**2 and all(x.bytes <= 1024**2 for x in data)
    d.flush()
    assert len(metadata) == 3 and len(data) == 4
    assert all(x.queries <= 2048 and x.rows <= 8192 and x.bytes <= 4 * 1024**2 for x in metadata)
    assert all(x.queries <= 64 and x.rows <= 256 and x.bytes <= 1024**2 for x in data)


def test_generic_acl_independent_after_guard_before_actual_claim(c, monkeypatch):
    doc(c)
    d = ready(c)
    ask(c)
    actual = d.selected_knowledge_guard
    calls = []
    def guarded(row, route=None, transport=None):
        result = actual(row, route, transport)
        calls.append(route)
        if route is not None: c.s.execute('DELETE FROM knowledge_readers')
        return result
    d.selected_knowledge_guard = guarded
    d.flush()
    assert len(calls) == 2 and not c.api.sent and outputs(c)[0]['state'] == 'cancelled'


@pytest.mark.parametrize('kind', ['none', 'binding_removed', 'business', 'source_id_method'])
def test_actual_public_trusted_bridge_binding_and_index_grant(c, monkeypatch, kind):
    from goldway.trusted_provider import TrustedProviders, SourceSpec, ProviderSnapshot, Document
    members = frozenset(reader_key('GOLDWAY', member) for member in MEMBERS)
    registry = TrustedProviders(business='GOLDWAY', members=members, clock=lambda: NOW)
    credential = object()
    calls = []
    def provider(request, supplied):
        assert supplied is credential
        calls.append(request)
        return ProviderSnapshot('synthetic', 'manual', 1, NOW,
            (Document('guide', 'Shipping guide', 'Shipping takes three days.'),))
    registry.register_provider('synthetic', provider, credential=credential)
    binding = SourceSpec('synthetic', 'manual', frozenset({reader_key('GOLDWAY', 'test_operator')}), 1, 300, False)
    registry.register_source(binding)
    value = make(registry=registry, bindings=(binding,))
    bridge = value.e.intelligence.sources.trusted_bridge
    assert bridge.refresh(NOW) == {'trusted:GOLDWAY:synthetic:manual': 'ok'} and len(calls) == 1
    def forbidden(*args, **kwargs): pytest.fail('command refreshed provider or registry')
    monkeypatch.setattr(registry, 'refresh', forbidden)
    monkeypatch.setattr(registry, 'documents', forbidden)
    d = ready(value)
    ask(value, FORM.replace('регистрация manual', 'провайдер synthetic/manual'))
    assert len(outputs(value)) == 1 and 'предположение, не подтверждено' in outputs(value)[0]['text']
    if kind == 'binding_removed': bridge.bindings = ()
    elif kind == 'business': registry.business = 'OTHER'
    elif kind == 'source_id_method': bridge.source_id = lambda spec: 'trusted:OTHER:synthetic:manual'
    d.flush()
    assert outputs(value)[0]['state'] == ('sent' if kind == 'none' else 'cancelled')
    assert len(value.api.sent) == (1 if kind == 'none' else 0) and len(calls) == 1
    value.s.db.close()


def test_three_exact_documents_and_all_consulted_refs_not_only_top_evidence(c):
    refs = set()
    for identifier in ('first', 'second', 'third'):
        refs.update(doc(c, identifier=identifier, text='shipping ' + identifier))
    ready(c)
    form = command.HEADER + ''.join('\nИсточник: регистрация manual\nДокумент: ' + x for x in ('first', 'second', 'third')) + '\nЗапрос: shipping'
    ask(c, form)
    assert set(protected(c)) == refs
    assert all('Источник ' + str(n) in outputs(c)[0]['text'] for n in (1, 2, 3))


@pytest.mark.parametrize('kind', ['source', 'index', 'store', 'connection', 'config', 'method', 'clock'])
def test_guard_pins_initialized_host_and_public_methods(c, kind):
    doc(c)
    d = ready(c)
    ask(c)
    if kind == 'source': c.e.intelligence.sources = copy.copy(c.e.intelligence.sources)
    elif kind == 'index': c.e.intelligence.index = copy.copy(c.e.intelligence.index)
    elif kind == 'store': c.e.s = copy.copy(c.s)
    elif kind == 'connection':
        other = Store(':memory:')
        original_row = outputs(c)[0]
        other.queue('GOLDWAY', 'test_operator', 'foreign sentinel', original_row['dedupe'], NOW)
        before = other.rows('SELECT * FROM outbox')
        saved = c.s.db
        c.s.db = other.db
        # Original captured connection is checked; cancellation cannot write into
        # this unrelated Store. Restore it solely to observe the original row.
        assert not d.selected_knowledge_guard(original_row)
        d.flush()
        assert other.rows('SELECT * FROM outbox') == before
        c.s.db = saved
        other.db.close()
        assert outputs(c)[0]['state'] == 'pending' and not c.api.sent
        return
    elif kind == 'config': c.e.c = copy.deepcopy(c.e.c)
    elif kind == 'method': c.e.intelligence.index.search_selected = lambda *a, **k: pytest.fail('replacement method called')
    else: c.e.now = lambda: NOW
    d.flush()
    assert not c.api.sent and outputs(c)[0]['state'] == 'cancelled'


@pytest.mark.parametrize('kind', ['payload_blob', 'payload_large', 'wrong_payload_field'])
def test_accepted_payload_gate_before_new_source_body_read(c, monkeypatch, kind):
    doc(c)
    actual = c.s.event
    def event(*args, **kwargs):
        result = actual(*args, **kwargs)
        if kind == 'wrong_payload_field':
            value = dict(args[3]); value['source_id'] = 'telegram:333:10'; value = json.dumps(value)
        else: value = sqlite3.Binary(b'PRIVATE') if kind == 'payload_blob' else 'x' * 16385
        c.s.execute('UPDATE events SET payload=?', (value,))
        return result
    monkeypatch.setattr(c.s, 'event', event)
    def deny(self, *args, **kwargs): pytest.fail('selected source read before accepted payload proof')
    monkeypatch.setattr(KnowledgeIndex, 'search_selected', deny)
    ask(c)
    assert_no_preview(c)


def test_unselected_huge_private_source_is_not_read_by_new_handler(c):
    doc(c)
    doc(c, identifier='unselected', title='Unselected sentinel')
    key = c.s.execute("SELECT id FROM knowledge_documents WHERE document_id='unselected'").fetchone()[0]
    c.s.execute('UPDATE knowledge_chunks SET text=? WHERE document_key=?', ('PRIVATE UNSELECTED' * 10000, key))
    actual = c.s.execute
    reads = []
    class Cursor:
        def __init__(self, wrapped): self.wrapped = wrapped
        def __iter__(self): return iter(self.wrapped)
        def fetchmany(self, *args):
            rows = self.wrapped.fetchmany(*args)
            reads.extend(dict(row) for row in rows)
            return rows
        def __getattr__(self, name): return getattr(self.wrapped, name)
    c.s.execute = lambda *a, **k: Cursor(actual(*a, **k))
    ask(c)
    assert len(outputs(c)) == 1
    assert not any('PRIVATE UNSELECTED' in str(row) or 'Unselected sentinel' in str(row) for row in reads)


def test_handler_connection_drift_never_writes_same_named_event_in_other_store(c, monkeypatch):
    doc(c)
    other = Store(':memory:')
    other.event('GOLDWAY', 'request', 'reply', {'text': 'FOREIGN SENTINEL'})
    before = other.rows('SELECT * FROM events')
    saved = c.s.db
    original = c.s.queue
    def queue(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[3].startswith(command.PREFIX): c.s.db = other.db
        return result
    monkeypatch.setattr(c.s, 'queue', queue)
    try:
        with pytest.raises(RuntimeError, match='store_transaction_connection_changed'):
            ask(c)
        assert other.rows('SELECT * FROM events') == before and not other.rows('SELECT * FROM outbox')
    finally:
        c.s.db = saved
        other.db.close()
    assert_no_preview(c)
    assert not c.s.rows('SELECT * FROM events')


def test_observation_outside_datetime_display_range_refuses_whole_preview(c):
    c.clock[0] = 2**53 - 2
    doc(c, observed_at=2**53 - 3, permission_expires_at=2**53 - 1)
    ask(c)
    assert_no_preview(c)
    assert c.s.rows('SELECT text FROM outbox')[0]['text'] == command.NOTICE


@pytest.mark.parametrize('phase', ['handler', 'guard'])
def test_new_metadata_reader_budget_failure_is_fatal_not_partial_output(c, monkeypatch, phase):
    doc(c)
    d = ready(c)
    actual = command._Reader
    class Failing(actual):
        def query(self, *args, **kwargs):
            self.bytes = 4 * 1024**2
            return super().query(*args, **kwargs)
    if phase == 'guard': ask(c)
    monkeypatch.setattr(command, '_Reader', Failing)
    if phase == 'handler':
        ask(c)
        assert_no_preview(c)
    else:
        d.flush()
        assert outputs(c)[0]['state'] == 'cancelled' and not c.api.sent


def test_current_source_map_callback_runs_before_final_data_phase(c, monkeypatch):
    doc(c)
    ready(c)
    original = command._Host.documents
    calls = []
    def documents(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        calls.append(1)
        if len(calls) == 2: c.s.execute('DELETE FROM knowledge_readers')
        return result
    monkeypatch.setattr(command._Host, 'documents', documents)
    ask(c)
    assert calls == [1, 1]
    assert_no_preview(c)


def test_cli_injects_optional_guard_from_same_initialized_engine_only():
    # Structural new-wiring check, no CLI execution, account, token or poller.
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1] / 'goldway/cli.py').read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'Dispatcher']
    assert len(calls) == 1
    guards = {kw.arg: kw.value for kw in calls[0].keywords}
    assert set(guards) >= {'weekly_guard', 'personal_list_guard', 'own_impediments_guard',
                          'task_brief_guard', 'selected_knowledge_guard'}
    selected = guards['selected_knowledge_guard']
    assert isinstance(selected, ast.Call) and selected.func.id == 'make_selected_knowledge_guard'
    assert len(selected.args) == 1 and selected.args[0].id == 'e'


def trusted():
    from goldway.trusted_provider import TrustedProviders, SourceSpec, ProviderSnapshot, Document
    members = frozenset(reader_key('GOLDWAY', member) for member in MEMBERS)
    state = dict(version=1, observed=NOW, text='Shipping takes three days.', calls=0)
    registry = TrustedProviders(business='GOLDWAY', members=members, clock=lambda: NOW)
    credential = object()
    def provider(request, supplied):
        assert supplied is credential
        state['calls'] += 1
        return ProviderSnapshot('synthetic', 'manual', state['version'], state['observed'],
            (Document('guide', 'Shipping guide', state['text']),))
    registry.register_provider('synthetic', provider, credential=credential)
    binding = SourceSpec('synthetic', 'manual', frozenset({reader_key('GOLDWAY', 'test_operator')}), 1, 300, False)
    registry.register_source(binding)
    c = make(registry=registry, bindings=(binding,))
    assert c.e.intelligence.sources.trusted_bridge.refresh(NOW) == {'trusted:GOLDWAY:synthetic:manual': 'ok'}
    c.registry, c.provider_state, c.binding = registry, state, binding
    c.form = FORM.replace('регистрация manual', 'провайдер synthetic/manual')
    return c


def test_public_source_metadata_is_frozen_body_clock_provider_free_and_readonly(monkeypatch):
    from dataclasses import FrozenInstanceError
    from goldway.trusted_provider import SourceMetadata, ProviderSnapshot
    c = trusted()
    before = c.s.rows('SELECT * FROM knowledge_documents')
    original = ProviderSnapshot.__getattribute__
    def no_documents(self, name):
        if name == 'documents': pytest.fail('metadata accessed provider documents')
        return original(self, name)
    def forbidden(*args, **kwargs): pytest.fail('metadata invoked clock, provider or documents')
    monkeypatch.setattr(ProviderSnapshot, '__getattribute__', no_documents)
    monkeypatch.setattr(c.registry, '_now', forbidden)
    monkeypatch.setattr(c.registry, 'refresh', forbidden)
    monkeypatch.setattr(c.registry, 'documents', forbidden)
    result = c.registry.source_metadata('manual', reader=reader_key('GOLDWAY', 'test_operator'), now=NOW)
    assert type(result) is SourceMetadata and result.business == 'GOLDWAY' and result.spec is c.binding
    assert result.version == 1 and result.observed_at == NOW and result.expires_at == NOW + 300
    assert len(result.fingerprint) == 64 and c.provider_state['calls'] == 1
    with pytest.raises(FrozenInstanceError): result.version = 2
    assert c.s.rows('SELECT * FROM knowledge_documents') == before
    c.s.db.close()


@pytest.mark.parametrize('kind', ['unknown', 'audience', 'expired', 'future', 'nan', 'infinity', 'bool', 'revoked'])
def test_public_metadata_refuses_invalid_or_withdrawn_scope_without_provider_call(kind):
    from goldway.trusted_provider import ProviderError
    c = trusted()
    if kind == 'revoked': c.registry.revoke('manual')
    now = dict(expired=NOW + 300, future=NOW - 1, nan=float('nan'), infinity=float('inf'), bool=True).get(kind, NOW)
    with pytest.raises(ProviderError):
        c.registry.source_metadata('absent' if kind == 'unknown' else 'manual',
            reader=reader_key('GOLDWAY', 'alen' if kind == 'audience' else 'test_operator'), now=now)
    assert c.provider_state['calls'] == 1
    c.s.db.close()


@pytest.mark.parametrize('phase', ['handler', 'pre_route', 'pre_claim'])
def test_immediate_registry_revoke_without_bridge_refresh_blocks_at_actual_boundaries(monkeypatch, phase):
    c = trusted()
    def forbidden(*args, **kwargs): pytest.fail('command refreshed/read provider documents')
    monkeypatch.setattr(c.registry, 'refresh', forbidden)
    monkeypatch.setattr(c.registry, 'documents', forbidden)
    d = ready(c)
    if phase == 'handler': c.registry.revoke('manual')
    ask(c, c.form, channel='business')
    # The old index is deliberately still readable by its own independent grant;
    # I100 must honor the current local registry withdrawal without a refresh.
    assert c.s.rows('SELECT * FROM knowledge_documents') and c.s.rows('SELECT * FROM knowledge_readers')
    if phase == 'handler': assert_no_preview(c)
    else:
        if phase == 'pre_route': c.registry.revoke('manual')
        else: c.api.callback = lambda: c.registry.revoke('manual')
        d.flush()
        assert outputs(c)[0]['state'] == 'cancelled' and not c.api.sent
        assert len(c.api.lookups) == (1 if phase == 'pre_claim' else 0)
    assert c.provider_state['calls'] == 1
    c.s.db.close()


@pytest.mark.parametrize('phase', ['handler', 'guard'])
@pytest.mark.parametrize('kind', ['version', 'indexed_version', 'indexed_expiry', 'observation', 'fingerprint'])
def test_public_cached_version_matches_actual_index_metadata_before_body(c, monkeypatch, phase, kind):
    c = trusted()
    d = ready(c)
    if phase == 'guard': ask(c, c.form)
    if kind == 'version':
        c.provider_state.update(version=2, text='Shipping takes seven days.')
        c.registry.refresh('manual')  # Explicit synthetic fixture update, no bridge refresh.
    elif kind == 'indexed_version': c.s.execute('UPDATE trusted_source_versions SET version=2')
    elif kind == 'indexed_expiry': c.s.execute('UPDATE knowledge_documents SET permission_expires_at=permission_expires_at+1')
    elif kind == 'observation': c.s.execute('UPDATE trusted_source_versions SET observed_at=observed_at-1')
    else: c.s.execute("UPDATE trusted_source_versions SET fingerprint=?", ('0' * 64,))
    def no_body(self, *args, **kwargs): pytest.fail('index body read before registry/version proof')
    monkeypatch.setattr(KnowledgeIndex, 'search_selected', no_body)
    # Pin the explicit spy before constructing this request/factory. Guard cases
    # still deny at method identity first if changed, so assert metadata directly
    # through a fresh factory as well, exercising the actual registry comparison.
    if phase == 'handler':
        ask(c, c.form)
        assert_no_preview(c)
    else:
        d.selected_knowledge_guard = command.make_delivery_guard(c.e)
        d.flush()
        assert outputs(c)[0]['state'] == 'cancelled' and not c.api.sent
    c.s.db.close()


@pytest.mark.parametrize('phase', ['handler_final', 'pre_claim'])
def test_public_registry_metadata_callback_is_before_last_actual_i98_lookup(monkeypatch, phase):
    from goldway.trusted_provider import TrustedProviders
    c = trusted()
    actual = TrustedProviders.source_metadata
    armed = [False]
    def metadata(self, *args, **kwargs):
        result = actual(self, *args, **kwargs)
        if armed[0]: c.s.execute('DELETE FROM knowledge_readers')
        return result
    monkeypatch.setattr(TrustedProviders, 'source_metadata', metadata)
    d = ready(c)
    if phase == 'handler_final':
        queue = c.s.queue
        def arm(*args, **kwargs):
            result = queue(*args, **kwargs)
            if args[3].startswith(command.PREFIX): armed[0] = True
            return result
        monkeypatch.setattr(c.s, 'queue', arm)
    ask(c, c.form, channel='business')
    if phase == 'handler_final': assert_no_preview(c)
    else:
        c.api.callback = lambda: armed.__setitem__(0, True)
        d.flush()
        assert outputs(c)[0]['state'] == 'cancelled' and not c.api.sent
    c.s.db.close()


def test_guard_pins_public_registry_metadata_method(monkeypatch):
    c = trusted()
    d = ready(c)
    ask(c, c.form)
    monkeypatch.setattr(c.registry, 'source_metadata', lambda *a, **k: pytest.fail('replacement metadata method called'))
    d.flush()
    assert outputs(c)[0]['state'] == 'cancelled' and not c.api.sent
    c.s.db.close()


@pytest.mark.parametrize('permission', ['grant', 'registry'])
@pytest.mark.parametrize('phase', ['pre_route', 'pre_claim'])
def test_new_helper_never_projects_saved_outbox_body_after_current_revoke(c, monkeypatch, permission, phase):
    value = trusted() if permission == 'registry' else c
    if permission == 'grant': doc(value)
    d = ready(value)
    ask(value, value.form if permission == 'registry' else FORM, channel='business')
    body = outputs(value)[0]['text']
    armed = [False]
    def revoke():
        armed[0] = True
        if permission == 'registry': value.registry.revoke('manual')
        else: value.s.execute('DELETE FROM knowledge_readers')
    transferred = []
    original = value.s.execute
    class Cursor:
        def __init__(self, cursor, watch): self.cursor, self.watch = cursor, watch
        def fetchall(self):
            rows = self.cursor.fetchall()
            if self.watch and armed[0]: transferred.extend(dict(row) for row in rows)
            return rows
        def __iter__(self): return iter(self.cursor)
        def __getattr__(self, name): return getattr(self.cursor, name)
    def execute(sql, args=()):
        # Instrument the NEW bounded outbox helper projection only. Existing
        # Dispatcher SELECT * is deliberately separate from this regression.
        watch = sql.startswith('SELECT ') and ' FROM outbox WHERE ' in sql and 'CASE WHEN' in sql
        return Cursor(original(sql, args), watch)
    monkeypatch.setattr(value.s, 'execute', execute)
    if phase == 'pre_route': revoke()
    else: value.api.callback = revoke
    d.flush()
    assert outputs(value)[0]['state'] == 'cancelled' and not value.api.sent
    assert not any('text' in row or body in row.values() for row in transferred)
    if permission == 'registry': value.s.db.close()
