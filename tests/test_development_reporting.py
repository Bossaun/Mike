"""One-shot sender protocol with synthetic files and fake API only; no live send."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
from urllib.error import HTTPError

import pytest

spec = importlib.util.spec_from_file_location(
    'goldway.development_reporting', Path(__file__).parents[1] / 'goldway/development_reporting.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
TOKEN = '123456789:synthetic_secret_only'
REPORT = 'Отчёт о разработке.\nПроверено локально; доставка не означает прочтение.'
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']


@pytest.fixture
def case(tmp_path, monkeypatch):
    config = dict(business='GOLDWAY', timezone='Europe/Moscow',
                  employees={name: dict(name=name, telegram_user_id=i + 101)
                             for i, name in enumerate(MEMBERS)},
                  admins=['test_operator'], business_account_user_id=999,
                  team_intake=dict(recipients=MEMBERS[:], started_at=100),
                  allowed_live_recipients=MEMBERS[:], team_roster_verified=True,
                  recipients_verified=True, live_send=True, review_mode=True,
                  sheets_write=False, daily_summary=False,
                  allow_onboarding_replies=False, allow_registered_replies=False,
                  allowed_group_ids=[], group_destinations={}, meeting_recipient=None)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    monkeypatch.setenv('GOLDWAY_DEVELOPMENT_REPORT_RUN', m.RUN)
    monkeypatch.setenv('GOLDWAY_DEVELOPMENT_REPORT_JSON', json.dumps(dict(id='first-1', text=REPORT)))
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', TOKEN)
    monkeypatch.setenv('MODEL_API_KEY', 'never-read-model-key')
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('live network forbidden'))
    calls = []

    def api(token, method, params):
        assert token == TOKEN
        calls.append((method, copy.deepcopy(params)))
        if method == 'getMe':
            return dict(id=888, is_bot=True, username='GOLDWAYCHAT_BOT')
        assert method == 'sendMessage'
        return dict(message_id=77, chat=dict(id=101, type='private'),
                    **{'from': dict(id=888, is_bot=True)}, text=params['text'])

    monkeypatch.setattr(m, '_api', api)
    return dict(path=path, directory=tmp_path, config=config, calls=calls, api=api)


def receipt(case, name='first-1'):
    return case['directory'] / 'development-reports' / (name + '.json')


def invoke(case):
    return m.maybe_send(case['path'], case['directory'])


def test_exact_admin_bot_only_acknowledgement_persistent_exclusive_receipt(case, capsys):
    before = case['path'].read_bytes()
    assert invoke(case) == dict(schema=m.SCHEMA, status='sent')
    assert case['calls'] == [('getMe', {}), ('sendMessage', {
        'chat_id': 101, 'text': REPORT, 'link_preview_options': {'is_disabled': True}})]
    assert receipt(case).stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt(case).read_text()) == dict(schema=m.SCHEMA, status='sent')
    assert case['path'].read_bytes() == before
    assert invoke(case)['status'] == 'already_consumed'
    assert len(case['calls']) == 2
    output = capsys.readouterr().out
    assert [json.loads(line)['status'] for line in output.splitlines()] == ['sent', 'already_consumed']
    assert all(secret not in output + receipt(case).read_text()
               for secret in (TOKEN, REPORT, 'test_operator', '101', '888', 'first-1', 'never-read-model-key'))


@pytest.mark.parametrize('flag', [None, '', 'false', '20261021-FIRST', '20261021-first ', 'other'])
def test_disabled_or_wrong_nonce_has_no_file_or_api_or_log_io(case, flag, monkeypatch, capsys):
    if flag is None:
        monkeypatch.delenv('GOLDWAY_DEVELOPMENT_REPORT_RUN')
    else:
        monkeypatch.setenv('GOLDWAY_DEVELOPMENT_REPORT_RUN', flag)
    monkeypatch.setattr('builtins.open', lambda *a, **k: pytest.fail('disabled config read'))
    monkeypatch.setattr(m, '_claim', lambda *a, **k: pytest.fail('disabled marker write'))
    assert invoke(case)['status'] == 'disabled'
    assert not case['calls'] and not capsys.readouterr().out


@pytest.mark.parametrize('payload', [
    '', 'not-json', '{}', '[]', json.dumps(dict(id='../escape', text=REPORT)),
    json.dumps(dict(id='x.json', text=REPORT)), json.dumps(dict(id='x' * 65, text=REPORT)),
    json.dumps(dict(id='first', text='')), json.dumps(dict(id='first', text=' \n ')),
    json.dumps(dict(id='first', text=None)), json.dumps(dict(id='first', text='x' * 3501)),
    json.dumps(dict(id='first', text='😀' * 1751)), json.dumps(dict(id='first', text='bad\x00text')),
    json.dumps(dict(id='first', text=REPORT, recipient=202)), 'x' * 20001,
])
def test_invalid_or_oversize_payload_refuses_before_claim_and_api(case, payload, monkeypatch):
    monkeypatch.setenv('GOLDWAY_DEVELOPMENT_REPORT_JSON', payload)
    assert invoke(case)['status'] == 'invalid_payload'
    assert not case['calls'] and not receipt(case).exists()


@pytest.mark.parametrize('change', [
    lambda c: c.update(business='OTHER'), lambda c: c.update(admins=['frost']),
    lambda c: c.update(admins=['test_operator', 'frost']),
    lambda c: c['employees'].pop('frost'),
    lambda c: c['employees'].update(extra=dict(name='extra', telegram_user_id=1000)),
    lambda c: c['employees']['test_operator'].update(telegram_user_id=-100),
    lambda c: c['employees']['test_operator'].update(telegram_user_id=True),
    lambda c: c.update(team_roster_verified=False), lambda c: c.update(recipients_verified=False),
    lambda c: c.update(live_send=False), lambda c: c.update(allowed_live_recipients=['frost']),
    lambda c: c.update(allowed_group_ids=[-100]),
])
def test_current_config_boundaries_refuse_wrong_scope_before_network(case, change):
    change(case['config'])
    case['path'].write_text(json.dumps(case['config']))
    assert invoke(case)['status'] == 'config_rejected'
    assert not case['calls'] and not receipt(case).exists()


@pytest.mark.parametrize('token', ['', 'secret-without-token-shape', '123:bad/path', '123:bad\nvalue'])
def test_missing_or_invalid_token_never_leaves_process(case, token, monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', token)
    assert invoke(case)['status'] in ('token_missing', 'config_rejected')
    assert not case['calls'] and not receipt(case).exists()


@pytest.mark.parametrize('bot', [
    dict(id=888, is_bot=False, username='GOLDWAYCHAT_BOT'),
    dict(id=888, is_bot=True, username='OTHER_BOT'),
    dict(id=888, is_bot=True, username='goldwaychat_bot'),
    dict(id=101, is_bot=True, username='GOLDWAYCHAT_BOT'),
    dict(id=999, is_bot=True, username='GOLDWAYCHAT_BOT'),
    dict(id=True, is_bot=True, username='GOLDWAYCHAT_BOT'),
])
def test_getme_identity_is_mandatory_and_attempt_consumed(case, bot, monkeypatch):
    calls = []
    monkeypatch.setattr(m, '_api', lambda token, method, params: calls.append(method) or bot)
    assert invoke(case)['status'] == 'bot_rejected'
    assert calls == ['getMe'] and json.loads(receipt(case).read_text())['status'] == 'bot_rejected'
    assert invoke(case)['status'] == 'already_consumed' and calls == ['getMe']


def test_existing_verified_bot_id_cannot_be_changed(case):
    case['config'].update(bot_user_id_verified=True, bot_user_id=777)
    case['path'].write_text(json.dumps(case['config']))
    assert invoke(case)['status'] == 'bot_rejected'
    assert [method for method, _ in case['calls']] == ['getMe']


@pytest.mark.parametrize('stage,error,status', [
    ('getMe', RuntimeError(TOKEN + REPORT), 'unknown'),
    ('sendMessage', RuntimeError(TOKEN + REPORT), 'unknown'),
    ('sendMessage', m._Rejected(), 'rejected'),
])
def test_unknown_or_rejected_no_repeat_no_raw_exception_log(case, stage, error, status, monkeypatch, capsys):
    def api(token, method, params):
        if method == stage:
            assert json.loads(receipt(case).read_text())['status'] == 'unknown'
            raise error
        return case['api'](token, method, params)
    monkeypatch.setattr(m, '_api', api)
    assert invoke(case)['status'] == status
    assert json.loads(receipt(case).read_text())['status'] == status
    assert invoke(case)['status'] == 'already_consumed'
    output = capsys.readouterr().out
    assert TOKEN not in output and REPORT not in output


@pytest.mark.parametrize('change', [
    lambda v: v.update(message_id=True), lambda v: v['chat'].update(id=102),
    lambda v: v['chat'].update(type='group'), lambda v: v['from'].update(id=777),
    lambda v: v.update(text='other text'), lambda v: v.update(business_connection_id='secret'),
])
def test_malformed_send_ack_is_unknown_not_delivery_claim(case, change, monkeypatch):
    def api(token, method, params):
        value = case['api'](token, method, params)
        if method == 'sendMessage':
            change(value)
        return value
    monkeypatch.setattr(m, '_api', api)
    assert invoke(case)['status'] == 'unknown'
    assert invoke(case)['status'] == 'already_consumed'


def test_interruption_preserves_unknown_marker_no_retry(case, monkeypatch):
    monkeypatch.setattr(m, '_api', lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        invoke(case)
    assert json.loads(receipt(case).read_text())['status'] == 'unknown'
    assert invoke(case)['status'] == 'already_consumed'


def test_partial_or_symlink_marker_is_consumed_without_read_or_replace(case):
    receipt(case).parent.mkdir(mode=0o700)
    target = case['directory'] / 'untouched.txt'
    target.write_text('do not read or replace')
    receipt(case).symlink_to(target)
    assert invoke(case)['status'] == 'already_consumed'
    assert not case['calls'] and target.read_text() == 'do not read or replace'


def test_failed_initial_fsync_makes_no_call_and_leaves_consumed_marker(case, monkeypatch):
    monkeypatch.setattr(m.os, 'fsync', lambda *a: (_ for _ in ()).throw(OSError(TOKEN)))
    assert invoke(case)['status'] == 'receipt_unavailable'
    assert not case['calls'] and receipt(case).exists()
    assert invoke(case)['status'] == 'already_consumed'


def test_new_report_id_allows_one_new_report_without_reusing_previous(case, monkeypatch):
    assert invoke(case)['status'] == 'sent'
    monkeypatch.setenv('GOLDWAY_DEVELOPMENT_REPORT_JSON', json.dumps(dict(id='next-day', text=REPORT)))
    assert invoke(case)['status'] == 'sent'
    assert receipt(case).exists() and receipt(case, 'next-day').exists() and len(case['calls']) == 4


def test_sanitized_log_failure_does_not_fail_parent_after_ack(case, monkeypatch):
    monkeypatch.setattr('builtins.print', lambda *a, **k: (_ for _ in ()).throw(OSError(TOKEN)))
    assert invoke(case)['status'] == 'sent'
    assert json.loads(receipt(case).read_text())['status'] == 'sent'


def test_actual_http_protocol_fixed_endpoint_timeout_post_json_no_redirect_proxy(monkeypatch):
    seen = []
    class Response:
        status = 200
        def __enter__(self):return self
        def __exit__(self, *a):return False
        def read(self, limit):
            assert limit == 65537
            return json.dumps(dict(ok=True, result=dict(id=888))).encode()
    class Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return Response()
    def factory(proxy, redirect):
        assert proxy.proxies == {} and isinstance(redirect, m._NoRedirect)
        assert redirect.redirect_request(None) is None
        return Opener()
    monkeypatch.setattr(m, 'build_opener', factory)
    assert m._api(TOKEN, 'getMe', {}) == dict(id=888)
    request, timeout = seen[0]
    assert request.full_url == 'https://api.telegram.org/bot' + TOKEN + '/getMe'
    assert timeout == 10 and request.method == 'POST' and json.loads(request.data) == {}


@pytest.mark.parametrize('body,status,error', [
    (b'not-json', 200, m._Unknown), (b'x' * 65537, 200, m._Unknown),
    (b'{}', 200, m._Unknown), (b'{"ok":true,"result":[]}', 200, m._Unknown),
    (b'{"ok":false,"description":"private"}', 200, m._Rejected),
    (b'{}', 503, m._Unknown),
])
def test_http_malformed_and_bounded_response_never_accepted(body, status, error, monkeypatch):
    class Response:
        def __enter__(self):return self
        def __exit__(self, *a):return False
        def read(self, limit):return body
    response = Response();response.status = status
    class Opener:
        def open(self, *a, **k):return response
    monkeypatch.setattr(m, 'build_opener', lambda *a: Opener())
    with pytest.raises(error):
        m._api(TOKEN, 'sendMessage', {})


@pytest.mark.parametrize('code,error', [(403, m._Rejected), (429, m._Rejected), (500, m._Unknown), (302, m._Unknown)])
def test_http_error_status_safe_without_raw_telegram_description(code, error, monkeypatch):
    class Opener:
        def open(self, *a, **k):
            raise HTTPError('https://secret/' + TOKEN, code, REPORT, {}, None)
    monkeypatch.setattr(m, 'build_opener', lambda *a: Opener())
    with pytest.raises(error) as caught:
        m._api(TOKEN, 'sendMessage', {})
    assert TOKEN not in str(caught.value) and REPORT not in str(caught.value)
