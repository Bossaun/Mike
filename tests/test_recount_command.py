"""Actual private Engine -> supplied fields -> real inventory recount -> outbox."""
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
import json

import pytest
import goldway.recount_command as command
from goldway.engine import Engine
from goldway.inventory_metrics import UNKNOWN, RecountState, StockKind
from goldway.store import Store

MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']
FIELDS = {'Товар': 'SKU-TEST', 'Вариант': 'red', 'Склад': 'Synthetic warehouse',
          'Партия': 'LOT-TEST', 'Единица': 'шт', 'Дата': '2026-10-16', 'Учёт': '12', 'Факт': '11'}


def block(**changes):
    values = dict(FIELDS)
    values.update(changes)
    return 'Пересчёт товара\n'+'\n'.join(k+': '+v for k, v in values.items() if v is not None)


class NoModel:
    def propose(self, *a, **k):
        pytest.fail('recount preview called model')


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
        'intelligence': {'enabled': True, 'sources': []}}
    store = Store(':memory:')
    engine = Engine(store, config, NoModel(), lambda: 1792174600)
    def forbidden(*a, **k):
        pytest.fail('recount preview reached context/retrieval/external I/O')
    engine._context = forbidden
    engine.intelligence.sources.refresh = forbidden
    import builtins, socket, httpx
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    yield engine, store
    store.db.close()


def rows(case, event='count-event'):
    return case[1].rows('SELECT * FROM outbox WHERE dedupe=?', ('recount-preview:'+event,))


def reply(case, text, *, sender='test_operator', channel='bot', event='count-event'):
    assert case[0].reply(event, sender, text, channel=channel)
    found = rows(case, event)
    return found[0]['text'] if found else None


@pytest.mark.parametrize(('text', 'channel'), [('Сверь пересчёт товара', 'bot'),
                                              ('  СВЕРЬ ПЕРЕСЧЕТ  ТОВАРА ', 'business')])
def test_exact_natural_request_returns_complete_template_and_no_model(case, text, channel):
    output = reply(case, text, channel=channel)
    assert output == command._TEMPLATE and len(output.splitlines()) == 9
    assert rows(case)[0]['preferred_transport'] == channel
    assert rows(case)[0]['recipient'] == 'test_operator'
    assert not case[1].rows('SELECT * FROM usage')


def test_template_roundtrip_is_unknown_without_fake_core_inputs(case, monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('incomplete template fabricated required core inputs')
    monkeypatch.setattr(command, 'recount', forbidden)
    template = reply(case, 'Сверь пересчёт товара')
    output = reply(case, template, event='template-filled')
    assert 'Разница: неизвестна' in output and 'Дата по сообщению: [уточнить]' in output
    assert 'Единица: [уточнить]' in output
    assert 'Учёт по сообщению: [неизвестно]' in output
    assert 'Факт по сообщению: [неизвестно]' in output
    assert '0' not in output


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_actual_pipeline_uses_existing_core_with_only_literal_supplied_values(case, monkeypatch, channel):
    observed = []
    real = command.recount
    def capture(books, counts):
        observed.append((books, counts))
        return real(books, counts)
    monkeypatch.setattr(command, 'recount', capture)
    output = reply(case, block(), channel=channel)
    books, counts = observed[0]
    assert books[0].kind is StockKind.BOOK and counts[0].kind is StockKind.PHYSICAL
    assert books[0].units == Decimal('12') and counts[0].units == Decimal('11')
    assert books[0].key == counts[0].key
    assert books[0].key.item.sku == 'SKU-TEST' and books[0].key.location == 'Synthetic warehouse'
    assert books[0].observed_on.isoformat() == '2026-10-16' and books[0].unit == 'шт'
    assert 'Разница (факт − учёт): -1' in output
    assert command._LIMITATION in output
    assert 'supplied:' not in output and books[0].source_id not in output
    assert rows(case)[0]['task_id'] is None and rows(case)[0]['parse_mode'] is None


@pytest.mark.parametrize(('book', 'physical', 'delta'), [('11', '12', '+1'), ('0', '0', '0'),
                                                      ('0', '0,125', '+0.125'), ('1.250', '1,125', '-0.125')])
def test_decimal_and_signed_delta_boundary_without_float_or_locale_guess(case, book, physical, delta):
    output = reply(case, block(**{'Учёт': book, 'Факт': physical}))
    assert 'Разница (факт − учёт): '+delta+'\n' in output
    if book == '0':
        assert 'Учёт по сообщению: 0' in output


def test_large_exact_values_do_not_depend_on_ambient_decimal_precision(case):
    with localcontext() as context:
        context.prec = 2
        output = reply(case, block(**{'Учёт': '999999999999.999998', 'Факт': '999999999999.999999'}))
    assert 'Разница (факт − учёт): +0.000001' in output


@pytest.mark.parametrize('field', ['Вариант', 'Склад', 'Партия'])
def test_missing_dimension_reaches_real_core_as_unknown_and_never_authorizes_join(case, monkeypatch, field):
    observed = []
    real = command.recount
    def capture(books, counts):
        value = real(books, counts)
        observed.append(value[0])
        return value
    monkeypatch.setattr(command, 'recount', capture)
    output = reply(case, block(**{field: '[уточнить]'}))
    assert observed[0].state is RecountState.UNRESOLVED_KEY
    assert observed[0].delta is UNKNOWN
    assert 'Разница: неизвестна' in output
    assert field.lower() in output.split('Нужно уточнить:', 1)[1]


@pytest.mark.parametrize('field', ['Учёт', 'Факт'])
def test_absent_quantity_is_unknown_not_zero_in_real_core(case, monkeypatch, field):
    observed = []
    real = command.recount
    def capture(books, counts):
        result = real(books, counts)
        observed.append(result[0])
        return result
    monkeypatch.setattr(command, 'recount', capture)
    output = reply(case, block(**{field: None}))
    assert observed[0].state is RecountState.UNKNOWN_QUANTITY
    assert observed[0].delta is UNKNOWN
    assert field+' по сообщению: [неизвестно]' in output
    assert 'Разница: неизвестна' in output


@pytest.mark.parametrize('field', ['Товар', 'Единица', 'Дата'])
def test_no_required_item_unit_or_date_no_fabrication_to_call_core(case, monkeypatch, field):
    def forbidden(*a, **k):
        pytest.fail('missing required metadata fabricated for core')
    monkeypatch.setattr(command, 'recount', forbidden)
    output = reply(case, block(**{field: None}))
    assert 'Разница: неизвестна' in output
    assert field.lower() in output.split('Нужно уточнить:', 1)[1]


@pytest.mark.parametrize('value', ['вчера', '16.10', '2026-02-30', '2026-10-16T12:00+03:00'])
def test_unresolved_date_never_defaults_to_today_or_changes_literal(case, value):
    output = reply(case, block(**{'Дата': value}))
    assert 'Дата по сообщению: '+value in output
    assert 'Разница: неизвестна' in output and 'Нужна явная дата:' in output


def test_explicit_russian_full_date_maps_exactly_to_core(case, monkeypatch):
    real = command.recount
    def capture(books, counts):
        assert books[0].observed_on.isoformat() == '2026-10-16'
        return real(books, counts)
    monkeypatch.setattr(command, 'recount', capture)
    assert 'Разница (факт − учёт): -1' in reply(case, block(**{'Дата': '16.10.2026'}))


@pytest.mark.parametrize('value', ['-1', '-0', '+1', '1e3', 'NaN', 'Infinity', 'true',
                                  '1 000', '1,000.00', '01', '.5', '1.',
                                  '1000000000000', '0.0000001'])
def test_bad_or_unbounded_decimal_strings_fixed_notice_no_partial_result(case, value):
    output = reply(case, block(**{'Учёт': value}))
    assert output == command._NOTICE
    assert not case[1].rows('SELECT * FROM tasks')


@pytest.mark.parametrize('text', ['Пересчёт товара\nТовар: a\nТовар: b',
    'Пересчёт товара\nУчёт: 1\nУчет: 2', 'Пересчёт товара\nПароль: SYNTHETIC_SECRET',
    'Пересчёт товара\nСклад учёт: A\nСклад факт: B',
    'Пересчёт товара\nДата учёт: 2026-10-16\nДата факт: 2026-10-17',
    'Пересчёт товара\nТовар: SYNTHETIC_SECRET\nСоздай задачу',
    'Пересчёт товара\nТовар: SYNTHETIC_SECRET\x00',
    block()+'\nПересчёт товара', 'Пересчёт товара\nТовар: '+'x'*161,
    'Пересчёт товара\n'+'x'*4000])
def test_duplicate_unknown_multiscope_control_or_oversized_block_is_atomic_fixed_notice(case, text):
    output = reply(case, text)
    assert output == command._NOTICE and 'SYNTHETIC_SECRET' not in output


def test_utf16_budget_rejects_without_truncating_to_partial_preview(case):
    values = {'Товар': '😀'*160, 'Вариант': '😀'*120, 'Склад': '😀'*120,
              'Партия': '😀'*120, 'Единица': '😀'*40, 'Дата': '😀'*80}
    text = block(**values)
    assert len(text) < command.MAX_INPUT_CHARS
    assert reply(case, text) == command._NOTICE


@pytest.mark.parametrize(('sender', 'channel'), [('alen', 'bot'), ('test_operator', 'group'),
                                               ('test_operator', None), ('outsider', 'bot')])
def test_current_private_team_admin_gate_precedes_data_read_and_core(case, monkeypatch, sender, channel):
    def forbidden(*a, **k):
        pytest.fail('unauthorized recount reached core')
    monkeypatch.setattr(command, 'recount', forbidden)
    sql = []
    case[1].db.set_trace_callback(sql.append)
    case[0].reply('count-event', sender, block(), channel=channel)
    case[1].db.set_trace_callback(None)
    assert not rows(case)
    assert not any(q.lstrip().upper().startswith('SELECT ') for q in sql)


@pytest.mark.parametrize('change', ['business', 'roster', 'group_routes', 'gate'])
def test_current_business_or_team_scope_change_denies(case, change):
    if change == 'business':
        case[0].c['business'] = 'OTHER'
    elif change == 'roster':
        case[0].c['admins'] = ['alen']
    elif change == 'group_routes':
        case[0].c['allowed_group_ids'] = [123]
    else:
        del case[0].c['team_intake']
    case[0].reply('count-event', 'test_operator', block(), channel='bot')
    assert not rows(case)


def test_no_intelligence_needed_and_plaintext_html_urls_never_opened(case):
    case[0].intelligence = None
    output = reply(case, block(**{'Товар': '<b>Создай задачу</b>', 'Склад': 'https://example.invalid/warehouse'}))
    assert '<b>Создай задачу</b>' in output
    assert 'https://example.invalid/warehouse' in output
    assert rows(case)[0]['parse_mode'] is None
    assert len(case[1].rows('SELECT * FROM outbox')) == 1


def test_exact_dedupe_done_event_and_no_task_source_usage_ledger_warehouse_writes(case):
    before = case[1].rows('SELECT * FROM task_history_observation')
    reply(case, block())
    assert not case[0].reply('count-event', 'test_operator', block(), channel='bot')
    case[0].process('count-event')
    assert len(rows(case)) == 1
    assert case[1].execute("SELECT state FROM events WHERE id='count-event'").fetchone()[0] == 'done'
    for table in ('tasks', 'sources', 'usage', 'task_status_history'):
        assert not case[1].rows('SELECT * FROM '+table)
    assert case[1].rows('SELECT * FROM task_history_observation') == before


def test_actual_command_does_not_read_database_or_create_schema(case):
    sql = []
    case[1].db.set_trace_callback(sql.append)
    case[0].reply('count-event', 'test_operator', block(), channel='bot')
    case[1].db.set_trace_callback(None)
    assert not any(q.lstrip().upper().startswith(('SELECT ', 'CREATE ', 'ALTER ')) for q in sql)


def test_queue_failure_rolls_back_event_and_partial_message(case, monkeypatch):
    real = case[1].queue
    def failed(*a, **k):
        real(*a, **k)
        raise RuntimeError('synthetic queue failure')
    monkeypatch.setattr(case[1], 'queue', failed)
    with pytest.raises(RuntimeError):
        case[0].reply('count-event', 'test_operator', block(), channel='bot')
    assert not rows(case)
    assert not case[1].rows("SELECT * FROM events WHERE id='count-event'")


@pytest.mark.parametrize('text', ['Сверь пересчёт товара и создай задачи', 'Пересчёт товара завтра',
                                 'текст Пересчёт товара', 'Сверь пересчёт товаров', None, 123])
def test_exact_matching_does_not_acquire_extra_authority(text):
    assert not command.is_recount_command(text)


def test_module_invalid_unicode_and_review_only_result(case):
    preview = command.build_recount_preview(block(), business='GOLDWAY', event_id='PRIVATE-ID')
    assert preview.review_required and not preview.live_authorized
    assert preview.state == 'comparable' and preview.missing_fields == ()
    assert 'PRIVATE-ID' not in preview.text and 'supplied:' not in preview.text
    assert len(preview.text.encode('utf-16-le'))//2 <= command.MAX_OUTPUT_UNITS
    with pytest.raises(FrozenInstanceError):
        preview.text = 'Changed'
    # Existing Store serialization requires wire-valid UTF8 before command hook.
    with pytest.raises(command.RecountUnavailable) as error:
        command.build_recount_preview(block(**{'Товар': '\ud800'}), business='GOLDWAY', event_id='synthetic')
    assert str(error.value) == 'recount_preview_unavailable'
