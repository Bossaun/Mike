import json
from pathlib import Path
import subprocess
import sys
import os
import pytest
from goldway.railway import prepare_config


def config():
    return json.loads(Path('examples/telegram-only.json').read_text())


def test_private_config_and_existing_database(tmp_path):
    db = tmp_path / 'goldway.sqlite'
    db.write_bytes(b'existing database must not be touched')
    path = prepare_config(json.dumps(config()), tmp_path)
    assert json.loads(path.read_text())['live_send'] is False
    assert path.stat().st_mode & 0o777 == 0o600
    assert db.read_bytes() == b'existing database must not be touched'
    assert not list(tmp_path.glob('.config-*'))


def test_invalid_config_keeps_previous_file(tmp_path):
    previous = tmp_path / 'config.json'
    previous.write_text('previous')
    c = config()
    c['business'] = 'other'
    with pytest.raises(ValueError):
        prepare_config(json.dumps(c), tmp_path)
    assert previous.read_text() == 'previous'
    assert not list(tmp_path.glob('.config-*'))


def test_disabled_worker_needs_no_config_or_tokens():
    env = {k:v for k,v in os.environ.items() if k not in
           ('MODEL_API_KEY','TELEGRAM_BOT_TOKEN','GOLDWAY_CONFIG_JSON','GOLDWAY_WORKER_ENABLED')}
    process = subprocess.Popen([sys.executable,'-u','-m','goldway.railway'],env=env,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert 'worker disabled' in process.stdout.readline()
        process.terminate()
        _, error = process.communicate(timeout=5)
        assert process.returncode == 0
        assert not error
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_enabled_worker_fails_without_private_config():
    env = dict(os.environ, GOLDWAY_WORKER_ENABLED='true')
    env.pop('GOLDWAY_CONFIG_JSON',None)
    result = subprocess.run([sys.executable,'-m','goldway.railway'],env=env,
                            capture_output=True,text=True)
    assert result.returncode != 0
    assert result.stderr.strip() == 'Railway bootstrap failed: ValueError'
