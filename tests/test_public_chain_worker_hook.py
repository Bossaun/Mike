"""Final package and actual worker hook; no provider, process or Telegram calls."""
import importlib.util
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest
import goldway.cli as cli
import goldway.public_chain_execution as launch
from goldway.store import Store

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('enabled',[False,True])
def test_existing_worker_keeps_one_poll_loop_with_probe_hook(monkeypatch,enabled):
    monkeypatch.delenv('GOLDWAY_PUBLIC_CHAIN_RUN',raising=False)
    handlers={};polls=[];starts=[]
    e=SimpleNamespace(s=Store(':memory:'),b='synthetic',c={'daily_summary':False},tz=None,tick=lambda:None)
    if enabled:
        monkeypatch.setenv('GOLDWAY_PUBLIC_CHAIN_RUN',launch.RUN)
        monkeypatch.setattr(launch,'maybe_start',lambda config:starts.append(config) or True)
    else:
        def forbidden(*args,**kwargs):pytest.fail('Disabled probe touched profile, marker or process')
        monkeypatch.setattr(launch,'_profile',forbidden)
        monkeypatch.setattr(launch,'_consume',forbidden)
        monkeypatch.setattr(launch.subprocess,'Popen',forbidden)
    class Api:
        def __init__(self,token):self.client=SimpleNamespace(close=lambda:None)
        def call(self,method,**kwargs):
            if method=='getMe':return {'id':900,'is_bot':True,'username':'GOLDWAYCHAT_BOT'}
            assert method=='getUpdates'
            polls.append(kwargs)
            if len(polls)==2:handlers[signal.SIGTERM]()
            return []
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','synthetic')
    monkeypatch.setattr(cli,'Telegram',Api)
    monkeypatch.setattr(cli,'bootstrap_groups',lambda *args:None)
    monkeypatch.setattr(cli,'Updates',lambda *args:None)
    for module,name in [('weekly_review_command','make_delivery_guard'),
                        ('personal_reference_discovery','make_delivery_guard'),
                        ('own_impediments','make_delivery_guard'),
                        ('design_brief_command','make_task_brief_guard'),
                        ('selected_knowledge_command','make_delivery_guard')]:
        monkeypatch.setattr('goldway.'+module+'.'+name,lambda *args:None)
    monkeypatch.setattr(cli,'Dispatcher',lambda *a,**kw:SimpleNamespace(recover=lambda:None,flush=lambda *a:None))
    monkeypatch.setattr(cli.signal,'signal',lambda sig,callback:handlers.update({sig:callback}))
    cli.run(e)
    assert len(polls)==2
    assert all(p['allowed_updates']==cli.ALLOWED_UPDATES for p in polls)
    assert starts==([e.c] if enabled else [])
    assert e.s.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
    assert e.s.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0


def test_packaged_probe_exact_sources_protocol_and_public_file():
    spec=importlib.util.spec_from_file_location('packaged_probe',ROOT/'operations/public_chain/probe.py')
    probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
    assert tuple(probe.QUESTIONS)==launch._INPUTS
    assert probe.check_sources(ROOT)['format']=='provider-chain-source-freeze-v1'
    docker=(ROOT/'Dockerfile').read_text()
    assert 'COPY operations ./operations' in docker
    assert 'COPY examples/knowledge.public.json ./examples/knowledge.public.json' in docker
