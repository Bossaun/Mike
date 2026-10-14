"""Railway bootstrap; disabled by default and never logs configuration/secrets."""
import json
import os
from pathlib import Path
import signal
import tempfile
import time


def prepare_config(raw, directory):
    from .config import load
    config = json.loads(raw)
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
        temporary.replace(directory / 'config.json')
    finally:
        temporary.unlink(missing_ok=True)
    return directory / 'config.json'


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
    config = prepare_config(raw, directory)
    os.environ.pop('GOLDWAY_CONFIG_JSON', None)
    os.execvp('goldway', ['goldway', '--config', str(config), '--db',
                          str(directory / 'goldway.sqlite'), 'run'])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Some parse/validation exceptions can contain private config excerpts.
        raise SystemExit('Railway bootstrap failed: ' + type(error).__name__) from None
