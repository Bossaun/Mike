"""Railway bootstrap; disabled by default and never logs configuration/secrets."""
import json
import os
from pathlib import Path
import signal
import tempfile
import time


def prepare_config(raw, directory):
    config = json.loads(raw)
    return _prepare_config(config, directory)


def _prepare_config(config, directory, after_validation=None):
    from .config import load
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Validate before replacing the configuration. Never overwrite SQLite.
    fd, name = tempfile.mkstemp(prefix='.config-', suffix='.json', dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(config, stream, ensure_ascii=False)
            stream.write('\n')
        load(temporary)
        if after_validation:
            after_validation()
        temporary.replace(directory / 'config.json')
    finally:
        temporary.unlink(missing_ok=True)
    return directory / 'config.json'


def prepare_personal_config(raw, directory, now=time.time):
    """Narrow the existing private profile; persist its first cutoff and identity."""
    config = json.loads(raw)
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    employees = config.get('employees')
    person = employees.get('test_operator') if isinstance(employees, dict) else None
    uid = person.get('telegram_user_id') if isinstance(person, dict) else None
    if type(uid) is not int or uid <= 0:
        raise ValueError('Personal mode requires the existing test operator identity')
    directory = Path(directory)
    marker = directory / 'personal_start.json'
    create_marker = not marker.exists()
    if create_marker:
        start = int(now())
        if start <= 0:
            raise ValueError('Personal mode requires a positive start time')
        record = {'telegram_user_id': uid, 'started_at': start}
    else:
        record = json.loads(marker.read_text())
        if (not isinstance(record, dict)
                or set(record) != {'telegram_user_id', 'started_at'}
                or type(record['telegram_user_id']) is not int
                or record['telegram_user_id'] <= 0
                or type(record['started_at']) is not int or record['started_at'] <= 0
                or marker.stat().st_mode & 0o777 != 0o600):
            raise ValueError('Invalid private personal start marker')
        if record['telegram_user_id'] != uid:
            raise ValueError('Personal start marker identity changed')
    config['employees'] = {'test_operator': person}
    config['admins'] = ['test_operator']
    config['allowed_live_recipients'] = ['test_operator']
    config['live_send'] = config['recipients_verified'] = config['review_mode'] = True
    for flag in ('sheets_write', 'daily_summary', 'allow_onboarding_replies', 'allow_registered_replies'):
        config[flag] = False
    config.update(business_account_user_id=None, allowed_group_ids=[],
                  group_destinations={}, meeting_recipient=None)
    for gate in ('controlled_intake', 'test_intake', 'personal_intake', 'ignore_messages_before'):
        config.pop(gate, None)
    config['personal_intake'] = {'recipient': 'test_operator', 'started_at': record['started_at']}

    def save_marker():
        if not create_marker:
            return
        fd, name = tempfile.mkstemp(prefix='.personal-start-', dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic, exclusive creation: a concurrent bootstrap cannot reset it.
            os.link(temporary, marker)
        finally:
            temporary.unlink(missing_ok=True)

    return _prepare_config(config, directory, after_validation=save_marker)


def main():
    if os.environ.get('GOLDWAY_WORKER_ENABLED', 'false').lower() != 'true':
        print('GOLDWAY worker disabled; no Telegram polling, model calls or database writes.', flush=True)
        stopped = False
        def stop(*_):
            nonlocal stopped
            stopped = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopped:
            time.sleep(1)
        return
    raw = os.environ.get('GOLDWAY_CONFIG_JSON')
    if not raw:
        raise ValueError('GOLDWAY_CONFIG_JSON missing')
    directory = Path('/data')
    directory.mkdir(exist_ok=True)
    # Railway volumes may start root-owned. Bootstrap then drop root privileges.
    if os.geteuid() == 0:
        os.chown(directory, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    personal = os.environ.get('GOLDWAY_PERSONAL_ONLY', 'false').lower() == 'true'
    config = (prepare_personal_config(raw, directory) if personal
              else prepare_config(raw, directory))
    os.environ.pop('GOLDWAY_CONFIG_JSON', None)
    os.execvp('goldway', ['goldway', '--config', str(config), '--db',
                          str(directory / ('personal.sqlite' if personal else 'goldway.sqlite')), 'run'])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Some parse/validation exceptions can contain private config excerpts.
        raise SystemExit('Railway bootstrap failed: ' + type(error).__name__) from None
