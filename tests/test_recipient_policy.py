"""Synthetic-only regression of the owner's recipient/link restriction."""
import copy
import pytest

from goldway.recipient_policy import outgoing_text, context_policy, apply_result
from goldway.telegram import Dispatcher, DeliveryError
from test_catalog_question_isolation import c


@pytest.mark.parametrize('recipient', ['nastya', 'dasha_it', 'frost', 'designer'])
@pytest.mark.parametrize('body,mode', [
    ('Детали https://example.com/catalog?api=1', None),
    ('Детали www.example.com/catalog', None),
    ('Детали example.com/catalog', None),
    ('Детали пример.рф/каталог', None),
    ('Детали tg://user?id=123', None),
    ('Детали [каталог](https://example.com/catalog)', 'Markdown'),
    ('Детали <a href="https://example.com/catalog">каталог</a>', 'HTML'),
    ('Детали &lt;a href=&quot;https://example.com/catalog&quot;&gt;каталог&lt;/a&gt;', None),
    ('Детали https&#58;//example.com/catalog', None),
    ('Детали 127.0.0.1/catalog', None),
    ('Детали @sample_bot', None),
])
def test_staff_never_receive_link_forms(recipient, body, mode):
    result = outgoing_text({'team_intake': {}}, recipient, body, mode)
    assert 'example.com' not in result and 'пример.рф' not in result
    assert '127.0.0.1' not in result and 'sample_bot' not in result
    assert '://' not in result and 'href' not in result
    assert 'Детали' in result


@pytest.mark.parametrize('recipient', ['test_operator', 'alen'])
def test_only_two_named_people_keep_links(recipient):
    text = 'Каталог https://example.com/catalog'
    assert outgoing_text({'team_intake': {}}, recipient, text) == text


@pytest.mark.parametrize('question', [
    'Все товары выкуплены, вы уточняете, нужно ли что-то еще заказать?',
    'Что ещё нужно купить?',
    'Какие товары заказать?',
    'Нужно ли ещё что-нибудь заказать?',
    'Надо заказать что-нибудь ещё?',
    'Подскажите, что докупить',
    'Выберите новые товары для закупки.',
    'Каких товаров не хватает?',
])
def test_new_purchase_selection_is_only_for_alen(question):
    config = {'team_intake': {}}
    result = outgoing_text(config, 'nastya', question)
    assert result.startswith('Список новых закупок определяет Алэн.')
    assert '?' not in result and 'Все товары выкуплены' not in result
    assert outgoing_text(config, 'alen', question) == question


@pytest.mark.parametrize('text', [
    'Когда оплатили согласованный заказ?',
    'Какова сумма оплаты и дата отправки?',
    'Какие из согласованных товаров уже выкуплены?',
    'Нужно заказать согласованные позиции. Срок: завтра.',
    'Закупка выполнена: 2 штуки, 1 776 ₽.',
])
def test_agreed_purchase_execution_stays_usable(text):
    assert outgoing_text({'team_intake': {}}, 'nastya', text) == text


def test_policy_context_does_not_grant_permissions(c):
    before = copy.deepcopy(c.c)
    p = dict(sender='nastya', text='Статус согласованного заказа', message_at=c.clock[0])
    policy = c.e._context(p, 'reply')['communication_policy']
    assert policy['procurement_execution_only'] is True
    assert policy['links_allowed'] is False
    assert policy['purchase_selection_questions_allowed'] is False
    assert context_policy(c.c, 'alen')['purchase_selection_questions_allowed'] is True
    assert c.c == before


class CaptureAPI:
    def __init__(self, callback=None, fail=False):
        self.sent = []
        self.callback = callback
        self.fail = fail

    def call(self, method, **params):
        if method == 'getBusinessConnection':
            if self.callback:
                self.callback()
            return dict(is_enabled=True, rights=dict(can_reply=True), user=dict(id=99))
        assert method == 'sendMessage'
        self.sent.append(params)
        if self.fail:
            raise DeliveryError('unknown', 'synthetic ambiguous send')
        return {'message_id': 101}


def route(c, person):
    uid = c.c['employees'][person]['telegram_user_id']
    c.s.execute('INSERT OR REPLACE INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',
                (c.e.b, person, str(uid), str(uid), 'conn', c.clock[0]))


@pytest.mark.parametrize('channel', ['bot', 'business'])
@pytest.mark.parametrize('state', ['pending', 'retry', 'blocked'])
def test_old_outbox_is_sanitized_on_actual_dispatch(c, channel, state):
    route(c, 'nastya')
    text = 'Заказ получен.\n\nИсточники:\nКаталог — https://example.com/catalog'
    c.s.queue(c.e.b, 'nastya', text, 'old-queue', c.clock[0], preferred_transport=channel)
    c.s.execute('UPDATE outbox SET state=?', (state,))
    api = CaptureAPI()
    Dispatcher(c.s, c.c, api, now=lambda: c.clock[0]).flush()
    row = c.s.rows('SELECT * FROM outbox')[0]
    assert row['state'] == 'sent' and row['attempts'] == 1
    assert api.sent[0]['text'] == row['text'] == 'Заказ получен.'
    assert ('business_connection_id' in api.sent[0]) == (channel == 'business')


def test_send_time_guard_applies_after_route_callback(c):
    route(c, 'nastya')
    c.s.queue(c.e.b, 'nastya', 'Принято.', 'latest-body', c.clock[0], preferred_transport='business')
    api = CaptureAPI(lambda: c.s.execute("UPDATE outbox SET text='Ссылка https://example.com'"))
    Dispatcher(c.s, c.c, api, now=lambda: c.clock[0]).flush()
    assert api.sent[0]['text'] == 'Ссылка'


def test_changed_recipient_cannot_reuse_old_route(c):
    route(c, 'nastya')
    c.s.queue(c.e.b, 'nastya', 'Принято.', 'changed-recipient', c.clock[0], preferred_transport='business')
    api = CaptureAPI(lambda: c.s.execute("UPDATE outbox SET recipient='alen',text='https://example.com'"))
    Dispatcher(c.s, c.c, api, now=lambda: c.clock[0]).flush()
    assert not api.sent
    assert c.s.rows('SELECT * FROM outbox')[0]['state'] == 'blocked'


def test_unknown_delivery_is_still_not_retried(c):
    route(c, 'nastya')
    c.s.queue(c.e.b, 'nastya', 'Детали https://example.com', 'ambiguous', c.clock[0])
    api = CaptureAPI(fail=True)
    dispatcher = Dispatcher(c.s, c.c, api, now=lambda: c.clock[0])
    dispatcher.flush()
    c.clock[0] += 600
    dispatcher.flush()
    assert len(api.sent) == 1 and api.sent[0]['text'] == 'Детали'
    assert c.s.rows('SELECT * FROM outbox')[0]['state'] == 'unknown'


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_real_engine_citations_memory_and_dispatch_follow_policy(c, channel):
    from goldway.intelligence import reader_key
    from goldway.dialogue import scope_from_payload
    route(c, 'nastya')
    c.e.intelligence.index.upsert(dict(source_id='synthetic-private', document_id='order',
        title='Согласованный заказ', url='https://example.com/order', text='Заказ ALPHA: 2 товара.',
        readers=[reader_key(c.e.b, 'nastya')], observed_at=c.clock[0]-1,
        permission_expires_at=c.clock[0]+600, confirmed=True))

    class Model:
        def propose(self, context):
            assert context['communication_policy']['procurement_execution_only']
            return dict(actions=[], summary='Синтетическая проверка', intent='conversation',
                reply_text='В согласованном заказе ALPHA два товара.',
                dialogue_state=dict(topic='Заказ ALPHA', entities=[], pending_offer=None, task_id=None),
                evidence_ids=[x['id'] for x in context['knowledge']['evidence']], knowledge_mode='grounded')

    c.e.model = Model()
    source = 'telegram:4:1' if channel == 'bot' else 'business:conn:4:1'
    assert c.e.reply('recipient-case', 'nastya', 'Сколько товаров в заказе ALPHA?', channel=channel, source_id=source, message_at=c.clock[0])
    c.e.process('recipient-case')
    api = CaptureAPI()
    Dispatcher(c.s, c.c, api, now=lambda: c.clock[0]).flush()
    actual = api.sent[0]['text']
    assert actual == 'В согласованном заказе ALPHA два товара.'
    scope = scope_from_payload(dict(sender='nastya', channel=channel, source_id=source), c.e.b)
    memory = c.e.intelligence.memory.get(scope, readable_ids=c.e.intelligence.readable_ids('nastya', c.clock[0]), now=c.clock[0])
    assert memory['turns'][-1]['content'] == actual
    assert not c.e.tasks() and not c.s.rows('SELECT 1 FROM usage')


def test_policy_keeps_internal_evidence_and_clears_prohibited_offer():
    original = dict(reply_text='Что ещё нужно купить?\n\nИсточники:\nhttps://example.com',
                    actions=[], evidence_ids=['allowed-evidence'],
                    dialogue_state=dict(topic='Заказ', pending_offer={'kind':'clarify','text':'Что купить?'}))
    result = apply_result({'team_intake': {}}, 'nastya', original)
    assert result['evidence_ids'] == original['evidence_ids']
    assert result['dialogue_state']['pending_offer'] is None
    assert original['dialogue_state']['pending_offer'] is not None


def test_legacy_profiles_unchanged():
    text = 'Что ещё нужно купить? https://example.com'
    assert outgoing_text({}, 'nastya', text) == text
