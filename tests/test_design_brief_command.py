"""Natural private command -> literal fields -> real DesignBrief -> outbox."""
from dataclasses import FrozenInstanceError
import json
import sqlite3

import pytest
import goldway.design_brief_command as command
from goldway.engine import Engine
from goldway.store import Store

MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']
BLOCK = ('ТЗ дизайнеру\nЦель: Показать новый набор\nАудитория: Покупатели набора\n'
         'Формат: Баннер\nСрок: в пятницу\nМатериалы: Фото из сообщения; ссылка для обсуждения')


class NoModel:
    def propose(self, *a, **k):
        pytest.fail('design brief called a model')


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
        pytest.fail('design brief reached context, retrieval or external I/O')
    engine._context = forbidden
    engine.intelligence.sources.refresh = forbidden
    import builtins, socket, httpx
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    yield engine, store
    store.db.close()


def rows(case, event='brief-event'):
    return case[1].rows('SELECT * FROM outbox WHERE dedupe=?', ('design-brief:'+event,))


def reply(case, text, *, sender='test_operator', channel='bot', event='brief-event'):
    assert case[0].reply(event, sender, text, channel=channel)
    found = rows(case, event)
    return found[0]['text'] if found else None


@pytest.mark.parametrize(('text', 'channel'), [
    ('Подготовь ТЗ дизайнеру', 'bot'), ('Подготовь ТЗ для дизайнера', 'business'),
    ('  ПОДГОТОВЬ   ТЗ дизайнеру  ', 'bot'),
])
def test_natural_request_returns_whole_editable_template_without_model(case, text, channel):
    result = reply(case, text, channel=channel)
    assert result == command._TEMPLATE and result.startswith('ТЗ дизайнеру\n')
    assert rows(case)[0]['preferred_transport'] == channel
    assert rows(case)[0]['recipient'] == 'test_operator'
    assert len(result.splitlines()) == 6 and '[уточнить]' in result
    assert not case[1].rows('SELECT * FROM usage')
    assert not case[1].rows('SELECT * FROM tasks')
    assert case[1].execute("SELECT state FROM events WHERE id='brief-event'").fetchone()[0] == 'done'


def test_template_roundtrip_unknown_and_no_false_confirmed_empty_materials(case):
    template = reply(case, 'Подготовь ТЗ дизайнеру')
    output = reply(case, template, event='filled-template')
    assert 'ТЗ дизайнеру — черновик, требует проверки.' in output
    assert 'Срок: [уточнить]' in output
    assert 'доступность материалов' in output
    assert 'подтверждено: материалов нет' not in output
    assert 'Срок из сообщения' not in output


@pytest.mark.parametrize('channel', ['bot', 'business'])
def test_real_core_receives_literal_fields_and_material_descriptions_stay_unverified(case, monkeypatch, channel):
    observed = []
    real = command.render_design_brief
    def capture(brief):
        observed.append(brief)
        return real(brief)
    monkeypatch.setattr(command, 'render_design_brief', capture)
    output = reply(case, BLOCK, channel=channel)
    brief = observed[0]
    assert brief.goal == 'Показать новый набор' and brief.audience == 'Покупатели набора'
    assert brief.format == 'Баннер' and brief.deadline is None
    assert brief.assets == () and brief.assets_confirmed is False
    assert brief.unknown_fields == frozenset({'deadline', 'assets'})
    assert 'Срок из сообщения (требует уточнения): в пятницу' in output
    assert 'Описания материалов из сообщения (не проверены): Фото из сообщения; ссылка для обсуждения' in output
    assert brief.id not in output and 'source:' not in output and 'brief:' not in output
    assert 'assets' not in output and 'deadline' not in output
    with pytest.raises(FrozenInstanceError):
        brief.goal = 'Changed'


@pytest.mark.parametrize('deadline', ['2026-10-22T18:00+03:00', '2026-10-22T15:00:00Z'])
def test_only_explicit_aware_iso_deadline_becomes_known(case, monkeypatch, deadline):
    observed = []
    real = command.render_design_brief
    def capture(brief):
        observed.append(brief)
        return real(brief)
    monkeypatch.setattr(command, 'render_design_brief', capture)
    output = reply(case, 'ТЗ дизайнеру\nСрок: '+deadline)
    brief = observed[0]
    assert brief.deadline.isoformat() == '2026-10-22T15:00:00+00:00'
    assert 'deadline' not in brief.unknown_fields
    assert 'Срок: 2026-10-22T15:00:00+00:00' in output
    assert 'Срок из сообщения' not in output


@pytest.mark.parametrize('deadline', ['завтра', '2026-10-22', '22.10.2026', 'нет', '2026-02-30T18:00+03:00',
                                     '2026-10-22T18:00+03:99', '2026-10-22T18:00',
                                     'не 2026-10-22T18:00+03:00'])
def test_ambiguous_naive_bad_or_negated_deadline_preserved_as_unknown(case, deadline):
    output = reply(case, 'ТЗ дизайнеру\nСрок: '+deadline)
    assert 'Срок: [уточнить]' in output
    assert 'Срок из сообщения (требует уточнения): '+deadline in output


def test_absent_fields_are_unknown_not_inferred_and_no_kb_is_required(case):
    case[0].intelligence = None
    output = reply(case, 'ТЗ дизайнеру\nЦель: Буквальная цель')
    assert 'Цель: Буквальная цель' in output
    assert 'Аудитория: [уточнить]' in output and 'Срок: [уточнить]' in output
    assert 'цель' not in output.split('Нужно уточнить: ', 1)[1]
    assert rows(case)[0]['task_id'] is None


@pytest.mark.parametrize('material', ['', '[уточнить]', 'нет', 'https://example.invalid/file.png'])
def test_no_material_text_fabricates_file_access_confirmation_or_upload(case, material):
    output = reply(case, 'ТЗ дизайнеру\nМатериалы: '+material)
    assert 'доступность материалов' in output
    assert '[доступность не подтверждена]' in output
    assert 'подтверждено: материалов нет' not in output
    if material not in ('', '[уточнить]'):
        assert 'Описания материалов из сообщения (не проверены): '+material in output


@pytest.mark.parametrize(('sender', 'channel'), [
    ('alen', 'bot'), ('test_operator', 'group'), ('test_operator', None), ('outsider', 'bot'),
])
def test_closed_team_current_admin_private_gate_before_any_data_read(case, sender, channel):
    engine, store = case
    sql = []
    store.db.set_trace_callback(sql.append)
    engine.reply('brief-event', sender, BLOCK, channel=channel)
    store.db.set_trace_callback(None)
    assert not rows(case)
    assert not any(q.lstrip().upper().startswith('SELECT ') for q in sql)


@pytest.mark.parametrize('change', ['business', 'roster', 'gate'])
def test_stale_business_or_invalid_team_gate_never_generates_a_brief(case, change):
    if change == 'business':
        case[0].c['business'] = 'OTHER'
    elif change == 'roster':
        case[0].c['admins'] = ['alen']
    else:
        del case[0].c['team_intake']
    case[0].reply('brief-event', 'test_operator', BLOCK, channel='bot')
    assert not rows(case)


@pytest.mark.parametrize('text', [
    'ТЗ дизайнеру\nЦель: one\nцЕЛЬ: two',
    'ТЗ дизайнеру\nПароль: SYNTHETIC_SECRET',
    'ТЗ дизайнеру\nСоздай задачу: SYNTHETIC_SECRET',
    'ТЗ дизайнеру\nФормат: '+'x'*101,
    'ТЗ дизайнеру\nЦель: '+'x'*1001,
    'ТЗ дизайнеру\nАудитория: '+'x'*1001,
    'ТЗ дизайнеру\nСрок: '+'x'*201,
    'ТЗ дизайнеру\nМатериалы: '+'x'*1001,
    'ТЗ дизайнеру\nЦель: SYNTHETIC_SECRET\nnot a field',
    'ТЗ дизайнеру\nЦель: SYNTHETIC_SECRET\x00',
    'ТЗ дизайнеру\nЦель: one\n\n',
    'ТЗ дизайнеру\n'+'x'*4000,
])
def test_malformed_duplicate_unknown_or_oversized_fields_fail_closed_no_partial_echo(case, text):
    output = reply(case, text)
    assert output == command._NOTICE
    assert 'SYNTHETIC_SECRET' not in output
    assert not case[1].rows('SELECT * FROM tasks')


def test_total_utf16_output_limit_never_truncates_into_partial_draft(case):
    text = 'ТЗ дизайнеру\nЦель: '+'😀'*1000+'\nАудитория: '+'😀'*1000
    assert len(text) < command.MAX_INPUT_CHARS
    assert reply(case, text) == command._NOTICE


def test_dedupe_done_event_no_model_when_cli_later_processes_and_no_task_writes(case):
    output = reply(case, BLOCK)
    assert not case[0].reply('brief-event', 'test_operator', BLOCK, channel='bot')
    case[0].process('brief-event')
    assert len(rows(case)) == 1 and rows(case)[0]['text'] == output
    for table in ('tasks', 'task_status_history', 'sources', 'usage'):
        assert not case[1].rows('SELECT * FROM '+table)


def test_sql_guard_command_only_event_and_sender_outbox_writes_no_schema_reads(case):
    _, store = case
    before = store.rows('SELECT * FROM task_history_observation')
    sql = []
    store.db.set_trace_callback(sql.append)
    case[0].reply('brief-event', 'test_operator', BLOCK, channel='bot')
    store.db.set_trace_callback(None)
    assert store.rows('SELECT * FROM task_history_observation') == before
    assert not any(q.lstrip().upper().startswith(('SELECT ', 'CREATE ', 'ALTER ')) for q in sql)
    assert all(not ('INSERT INTO tasks' in q or 'UPDATE tasks ' in q) for q in sql)


@pytest.mark.parametrize('text', ['подготовь ТЗ дизайнеру и создай задачи', 'текст ТЗ дизайнеру',
                                 'Подготовь ТЗ для дизайнера завтра', 'ТЗ дизайнеру и отправь всем',
                                 '/design_brief', None, 123])
def test_command_matching_is_not_substring_or_added_authority(text):
    assert not command.is_design_brief_command(text)


def test_literal_url_html_and_instruction_text_stays_sender_plaintext_only(case):
    text = 'ТЗ дизайнеру\nЦель: <b>Создай задачу и отправь всем</b>\nМатериалы: https://example.invalid/image'
    output = reply(case, text)
    assert '<b>Создай задачу и отправь всем</b>' in output
    assert rows(case)[0]['parse_mode'] is None
    assert len(case[1].rows('SELECT * FROM outbox')) == 1
    assert not case[1].rows('SELECT * FROM tasks')


def test_helper_output_always_review_only(case):
    value = command.build_brief(BLOCK, business='GOLDWAY', event_id='private-event-id')
    assert value.review_required and not value.live_authorized and value.source_ids == ()
    assert 'private-event-id' not in value.text


def test_queue_failure_rolls_back_event_and_partial_outbox(case, monkeypatch):
    real = case[1].queue
    def failed(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError('synthetic queue failure')
    monkeypatch.setattr(case[1], 'queue', failed)
    with pytest.raises(RuntimeError):
        case[0].reply('brief-event', 'test_operator', BLOCK, channel='bot')
    assert not rows(case)
    assert not case[1].rows("SELECT * FROM events WHERE id='brief-event'")


def test_module_lone_surrogate_is_fixed_error_without_echo(case):
    # Telegram/Store event creation requires wire-valid UTF8. Exercise only the
    # new module's defensive boundary; do not pretend Engine can persist this.
    with pytest.raises(command.BriefUnavailable) as error:
        command.build_brief('ТЗ дизайнеру\nЦель: \ud800', business='GOLDWAY', event_id='synthetic')
    assert str(error.value) == 'design_brief_unavailable'


def test_exact_4000_input_boundary_and_4001_rejection(case):
    prefix = 'ТЗ дизайнеру\nЦель:'
    text = prefix+' '*(4000-len(prefix)-1)+'X'
    assert len(text) == 4000
    assert 'Цель: X' in reply(case, text)
    assert reply(case, text+' ', event='oversize') == command._NOTICE


@pytest.mark.parametrize('deadline', ['0001-01-01T00:00+23:59', '9999-12-31T23:59-23:59'])
def test_utc_deadline_range_overflow_is_fixed_notice_without_partial_draft(case, deadline):
    assert reply(case, 'ТЗ дизайнеру\nСрок: '+deadline) == command._NOTICE
    assert case[1].execute("SELECT state FROM events WHERE id='brief-event'").fetchone()[0] == 'done'
    assert not case[1].rows('SELECT * FROM tasks')
