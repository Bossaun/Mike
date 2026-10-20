"""New help discovery -> actual published metadata/input-template handlers only."""
import hashlib
import re
import sqlite3
from types import SimpleNamespace

import pytest

from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
from goldway import preview_help_command as help
from goldway import knowledge_selection_catalog as catalog
from goldway import selected_knowledge_command as selected


NOW = 1792174600
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']
PAGE_ONE_SHA256 = 'd8d1f05571b020d78acc33d132044d888a7f452ed6e8f44f7366e2c5ed102f3f'


class NoModel:
    def propose(self, *args, **kwargs):
        pytest.fail('help discovery invoked model')


@pytest.fixture
def c(monkeypatch):
    config = dict(business='GOLDWAY', timezone='Europe/Moscow',
        employees={member: dict(name='Synthetic ' + member, telegram_user_id=i + 1)
                   for i, member in enumerate(MEMBERS)},
        admins=['test_operator'], business_account_user_id=99,
        team_intake=dict(recipients=MEMBERS[:], started_at=100),
        allowed_live_recipients=MEMBERS[:], team_roster_verified=True,
        recipients_verified=True, live_send=False, review_mode=True, sheets_write=False,
        daily_summary=False, allow_onboarding_replies=False, allow_registered_replies=False,
        allowed_group_ids=[], group_destinations={}, meeting_recipient=None,
        bot_user_id=100, bot_user_id_verified=True, intelligence=dict(enabled=True, sources=[]))
    store = Store(':memory:')
    engine = Engine(store, config, NoModel(), lambda: NOW)
    def forbidden(*args, **kwargs):
        pytest.fail('discovery invoked context/search/source/provider/network/constructor')
    engine._context = forbidden
    engine.intelligence.sources.refresh = forbidden
    monkeypatch.setattr(KnowledgeIndex, '__init__', forbidden)
    monkeypatch.setattr(KnowledgeIndex, 'search_selected', forbidden)
    # Exact I100 header-only input does not invoke this method. Pinning its
    # unchanged class identity still succeeds in the actual host gate.
    import httpx, socket
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    reads = []
    def authorize(action, table, column, *_):
        if action == sqlite3.SQLITE_READ:
            reads.append((table, column))
            if ((table == 'knowledge_chunks' and column == 'text') or table == 'knowledge_facts'
                    or (table == 'sources' and column == 'body')):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    store.db.set_authorizer(authorize)
    yield SimpleNamespace(e=engine, s=store, c=config, reads=reads)
    store.db.set_authorizer(None)
    store.db.close()


def reply(c, text, event='help', channel='bot', sender='test_operator', source=None):
    uid = c.c['employees'][sender]['telegram_user_id']
    return c.e.reply(event, sender, text, channel=channel, message_at=NOW,
        source_id=source or (f'business:conn:{uid}:20' if channel == 'business' else f'telegram:{uid}:20'))


def last(c):
    return c.s.rows('SELECT * FROM outbox ORDER BY id DESC LIMIT 1')[0]


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_actual_private_help_discovery_reaches_three_published_handlers_without_bodies(c, channel):
    assert reply(c, 'Что умеешь 2', channel=channel)
    page = last(c)
    assert page['text'] == help.build_preview_help(2)
    assert page['recipient'] == 'test_operator' and page['preferred_transport'] == channel
    assert page['task_id'] is None and page['parse_mode'] is None
    discovered = [text for text in re.findall('«([^»]+)»', page['text'])
                  if catalog.is_command(text) or selected.is_command(text)]
    assert discovered == [catalog.SOURCES, catalog.DOCUMENTS, selected.HEADER]
    assert reply(c, 'Что умеешь 2', channel=channel) is False
    assert len(c.s.rows('SELECT * FROM outbox')) == 1
    for i, header in enumerate(discovered, 1):
        assert reply(c, header, event='discovered-' + str(i), channel=channel)
        actual = last(c)
        assert actual['recipient'] == 'test_operator' and actual['preferred_transport'] == channel
        assert actual['task_id'] is None
        if header == catalog.SOURCES:
            assert 'Сейчас нет доступных' in actual['text']
            assert actual['dedupe'].startswith(catalog.PREFIX)
        elif header == catalog.DOCUMENTS:
            assert actual['text'] == catalog.TEMPLATE and len(actual['text'].splitlines()) == 2
        else:
            assert actual['text'] == selected.TEMPLATE and len(actual['text'].splitlines()) == 4
            for field in ('Источник:', 'Документ:', 'Запрос:'):
                assert field in page['text'] and field in actual['text']
    assert all(row['state'] == 'done' for row in c.s.rows('SELECT state FROM events'))
    for table in ('tasks', 'sources', 'usage', 'task_status_history', 'knowledge_documents', 'knowledge_readers'):
        assert not c.s.rows('SELECT 1 FROM ' + table + ' LIMIT 1')
    assert ('knowledge_chunks', 'text') not in c.reads
    assert not any(table == 'knowledge_facts' for table, column in c.reads)


def test_help_scopes_manual_scenarios_and_current_selected_evidence_without_fake_availability():
    page = help.build_preview_help(2)
    manual = page.index('Для перечисленных выше сценариев:')
    scoped = page.index('Работа с зарегистрированными знаниями')
    assert manual < page.index('рабочие системы не подключаются') < scoped
    assert 'если они есть' in page[scoped:] and 'Списки могут быть неполными.' in page[scoped:]
    assert 'только к выбранным документам' in page[scoped:]
    assert 'не заменяют независимую проверку фактов компании' in page[scoped:]
    assert all(opaque not in page for opaque in ('reader:', 'source:', 'trusted:', 'telegram:', 'GOLDWAY', 'manual'))
    assert page.endswith('Первая страница: «Что умеешь 1».')
    assert len(page.encode('utf-16-le')) // 2 <= 3500


def test_first_page_stays_byte_identical_and_six_descriptors_remain():
    page = help.build_preview_help()
    assert page == help.build_preview_help(1)
    assert hashlib.sha256(page.encode()).hexdigest() == PAGE_ONE_SHA256
    assert help.MAX_DESCRIPTORS == 6 and len(help.DESCRIPTORS) == 6
    assert help.MAX_OUTPUT_UNITS == 3500 and page.endswith('Ещё команды: «Что умеешь 2».')


@pytest.mark.parametrize('scope', ['staff', 'group'])
def test_new_knowledge_help_does_not_bypass_existing_unavailable_scope(c, scope):
    # Preserve the existing fallback, rather than claiming staff may use the
    # new private administrator knowledge-selection workflow.
    sender = 'frost' if scope == 'staff' else 'test_operator'
    channel = 'bot' if scope == 'staff' else 'group'
    c.e._context = lambda *args, **kwargs: {'tasks': []}
    assert reply(c, 'Что умеешь 2', sender=sender, channel=channel,
                 source='telegram:2:20' if scope == 'staff' else 'telegram:-20:20')
    assert not c.s.rows('SELECT * FROM outbox')
    assert c.s.rows('SELECT state FROM events')[0]['state'] == 'pending'


def test_whole_expanded_page_bound_failure_rolls_back_help_output(c, monkeypatch):
    monkeypatch.setattr(help, 'MAX_OUTPUT_UNITS', len(help.build_preview_help(2).encode('utf-16-le')) // 2 - 1)
    with pytest.raises(help.PreviewHelpUnavailable):
        reply(c, 'Что умеешь 2')
    assert not c.s.rows('SELECT * FROM outbox') and not c.s.rows('SELECT * FROM events')
