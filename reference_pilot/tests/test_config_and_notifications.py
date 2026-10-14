import asyncio
from dataclasses import replace
import pytest
from goldway.config import ConfigurationError, load_settings
from goldway.notifications import NotificationLog

@pytest.fixture(autouse=True)
def cleanup(monkeypatch):
    for key in ['GOLDWAY_MEMBERS_JSON','GOLDWAY_LIVE_ENABLED','GOLDWAY_SHEETS_WRITE_ENABLED',
                'GOLDWAY_AUTO_SEND_ENABLED','GOOGLE_SERVICE_ACCOUNT_JSON','GOOGLE_APPLICATION_CREDENTIALS',
                'TELEGRAM_BOT_TOKEN','GOLDWAY_SPREADSHEET_ID','GOLDWAY_TIMEZONE','GOLDWAY_DIGEST_TIMES']:
        monkeypatch.delenv(key,raising=False)

def test_safe_defaults():
    conf=load_settings()
    assert conf.live_enabled is False
    assert conf.sheets_write_enabled is False
    assert conf.auto_send_enabled is False

def test_write_cannot_be_on_without_live(monkeypatch):
    monkeypatch.setenv('GOLDWAY_SHEETS_WRITE_ENABLED','true')
    with pytest.raises(ConfigurationError): load_settings()

def test_unknown_sheet_id_refused(monkeypatch):
    monkeypatch.setenv('GOLDWAY_SPREADSHEET_ID','wrong')
    with pytest.raises(ConfigurationError): load_settings()

def test_bad_role_mapping(monkeypatch):
    monkeypatch.setenv('GOLDWAY_MEMBERS_JSON','{"42":{"role":"customer"}}')
    with pytest.raises(ConfigurationError): load_settings()

def test_live_requires_token(monkeypatch):
    monkeypatch.setenv('GOLDWAY_LIVE_ENABLED','true')
    with pytest.raises(ConfigurationError,match='TELEGRAM_BOT_TOKEN'): load_settings()

def test_unrecognized_bool(monkeypatch):
    monkeypatch.setenv('GOLDWAY_AUTO_SEND_ENABLED','maybe')
    with pytest.raises(ConfigurationError): load_settings()

def test_invalid_timezone(monkeypatch):
    monkeypatch.setenv('GOLDWAY_TIMEZONE','Mars/Olympus')
    with pytest.raises(ConfigurationError): load_settings()

def test_invalid_clock(monkeypatch):
    monkeypatch.setenv('GOLDWAY_DIGEST_TIMES','9:00')
    with pytest.raises(ConfigurationError): load_settings()

class Sender:
    def __init__(self, fail=False): self.calls=[]; self.fail=fail
    async def send_message(self, chat_id, text):
        if self.fail: raise RuntimeError('not delivered')
        self.calls.append((chat_id,text))

def test_notification_dedup_across_restart(tmp_path):
    path=str(tmp_path/'notices.db')
    log=NotificationLog(path); sender=Sender()
    assert asyncio.run(log.send_once(sender,'a',42,'message')) is True
    log.close()
    log=NotificationLog(path)
    assert asyncio.run(log.send_once(sender,'a',42,'message')) is False
    assert len(sender.calls)==1
    log.close()

def test_failed_send_not_marked(tmp_path):
    log=NotificationLog(str(tmp_path/'notices.db'))
    with pytest.raises(RuntimeError):
        asyncio.run(log.send_once(Sender(fail=True),'b',42,'message'))
    assert not log.was_sent('b')
    log.close()
