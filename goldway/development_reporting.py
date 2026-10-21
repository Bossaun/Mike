"""Opt-in administrator report via the bot API; no worker, DB or Business route.

The exclusive, fsynced receipt consumes an attempt before either API call. Any
ambiguous response, interruption or receipt failure is never retried with that
id. ``sent`` means a validated Bot API acknowledgement, not human readership.
"""
import json
import os
from pathlib import Path
import re
import stat
from urllib.error import HTTPError
from urllib.request import (HTTPRedirectHandler, ProxyHandler, Request, build_opener)

RUN = '20261021-first'
SCHEMA = 'goldway_development_report_v1'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
_TOKEN = re.compile(r'[0-9]+:[A-Za-z0-9_-]+')


class _Unknown(Exception):
    pass


class _Rejected(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _api(token, method, params):
    if method not in ('getMe', 'sendMessage'):
        raise _Rejected()
    request = Request('https://api.telegram.org/bot' + token + '/' + method,
                      data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        # No proxy environment, redirects, client debug logs or automatic retry.
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=10) as response:
            body = response.read(65537)
            if response.status != 200 or len(body) > 65536:
                raise _Unknown()
            value = json.loads(body)
    except HTTPError as error:
        if 400 <= error.code < 500:
            raise _Rejected() from None
        raise _Unknown() from None
    except Exception:
        raise _Unknown() from None
    if type(value) is not dict or type(value.get('ok')) is not bool:
        raise _Unknown()
    if value['ok'] is False:
        raise _Rejected()
    if type(value.get('result')) is not dict:
        raise _Unknown()
    return value['result']


def _record(status):
    return {'schema': SCHEMA, 'status': status}


def _emit(status):
    result = _record(status)
    try:
        print(json.dumps(result, separators=(',', ':')), flush=True)
    except Exception:
        pass
    return result


def _payload():
    raw = os.environ.get('GOLDWAY_DEVELOPMENT_REPORT_JSON', '')
    if not 0 < len(raw) <= 20000:
        raise ValueError()
    value = json.loads(raw)
    if type(value) is not dict or set(value) != {'id', 'text'}:
        raise ValueError()
    identifier, text = value['id'], value['text']
    if (type(identifier) is not str or _ID.fullmatch(identifier) is None
            or type(text) is not str or not text.strip() or len(text) > 3500
            or len(text.encode('utf-16-le')) // 2 > 3500
            or any(ord(c) < 32 and c not in '\n\t' for c in text)):
        raise ValueError()
    return identifier, text


def _write(fd, status):
    value = json.dumps(_record(status), separators=(',', ':')).encode('ascii') + b'\n'
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    written = 0
    while written < len(value):
        count = os.write(fd, value[written:])
        if count <= 0:
            raise OSError()
        written += count
    os.fsync(fd)


def _claim(directory, identifier):
    folder = Path(directory) / 'development-reports'
    folder.mkdir(mode=0o700, exist_ok=True)
    mode = folder.lstat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o022:
        raise OSError()
    fd = os.open(folder / (identifier + '.json'),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        _write(fd, 'unknown')
        parent = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _send(config_path, directory):
    try:
        identifier, text = _payload()
    except Exception:
        return 'invalid_payload'
    try:
        from .config import load
        from .intake import validate_team
        config = load(config_path)
        employees, account, _ = validate_team(config)
        if config['business'] != 'GOLDWAY' or config.get('live_send') is not True:
            raise ValueError()
        recipient = config['employees']['test_operator']['telegram_user_id']
    except Exception:
        return 'config_rejected'
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not 0 < len(token) <= 256 or _TOKEN.fullmatch(token) is None:
        return 'token_missing'
    try:
        fd = _claim(directory, identifier)
    except FileExistsError:
        return 'already_consumed'
    except Exception:
        return 'receipt_unavailable'
    status = 'unknown'
    try:
        bot = _api(token, 'getMe', {})
        if (bot.get('is_bot') is not True or bot.get('username') != 'GOLDWAYCHAT_BOT'
                or type(bot.get('id')) is not int or bot['id'] <= 0
                or bot['id'] in employees or bot['id'] == account
                or (config.get('bot_user_id_verified') is True
                    and bot['id'] != config.get('bot_user_id'))):
            status = 'bot_rejected'
        else:
            message = _api(token, 'sendMessage', {
                'chat_id': recipient, 'text': text,
                'link_preview_options': {'is_disabled': True}})
            chat, author = message.get('chat'), message.get('from')
            if (type(message.get('message_id')) is int and message['message_id'] > 0
                    and type(chat) is dict and type(chat.get('id')) is int
                    and chat['id'] == recipient and chat.get('type') == 'private'
                    and type(author) is dict and author.get('is_bot') is True
                    and type(author.get('id')) is int and author['id'] == bot['id']
                    and message.get('text') == text
                    and 'business_connection_id' not in message
                    and 'sender_business_bot' not in message):
                status = 'sent'
    except _Rejected:
        status = 'rejected'
    except Exception:
        status = 'unknown'
    finally:
        try:
            _write(fd, status)
        except Exception:
            status = 'unknown'
        try:
            os.close(fd)
        except Exception:
            pass
    return status


def maybe_send(config_path, directory):
    """Default-off startup hook, bounded API timeouts, sanitized result only.

    Validation failures before claim make no API call. After claim, every state
    consumes this id, including unknown/rejection. No background task or retry.
    Deliberate process shutdown (BaseException) propagates with receipt unknown.
    """
    if os.environ.get('GOLDWAY_DEVELOPMENT_REPORT_RUN') != RUN:
        return _record('disabled')
    try:
        status = _send(config_path, directory)
    except Exception:
        status = 'unknown'
    return _emit(status)
