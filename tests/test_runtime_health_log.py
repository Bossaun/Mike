"""Fresh worker diagnostics: aggregate-only output and real loop placement."""
import json
import signal
from types import SimpleNamespace

import pytest

import goldway.cli as cli
from goldway.store import Store


def engine():
    return SimpleNamespace(s=Store(':memory:'), b='private-business-marker', c={})


def test_health_reports_scoped_counts_without_private_content(capsys):
    e = engine()
    e.c.update(team_intake={}, token='SECRET_TOKEN', employees={'PRIVATE_ID': {}})
    for business, key, state in [(e.b, 'PRIVATE_EVENT', 'done'), (e.b, 'FAILED_ID', 'failed'), ('other', 'OTHER', 'done')]:
        e.s.execute('INSERT INTO events(business,id,kind,payload,state,error) VALUES(?,?,?,?,?,?)',
                    (business,key,'message','PRIVATE_MESSAGE',state,'PRIVATE_ERROR'))
    e.s.execute("INSERT INTO outbox(business,recipient,text,dedupe,state,created) VALUES(?,?,?,?,?,?)",
                (e.b,'PRIVATE_RECIPIENT','PRIVATE_REPLY','PRIVATE_DEDUPE','blocked',1))
    cli.runtime_health(e, {'id':123456789,'username':'GOLDWAYCHAT_BOT','token':'SECRET_TOKEN'},
                       dict(poll_batches=2,received_updates=3,poll_errors=1,completed_cycles=2))
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result['events'] == {'pending':0,'done':1,'failed':1,'superseded':0}
    assert result['outbox']['blocked'] == 1
    assert result['bot_username'] == 'GOLDWAYCHAT_BOT'
    assert result['team_gate'] is True
    assert result['received_updates'] == 3
    for secret in ('PRIVATE','SECRET','123456789','private-business-marker','OTHER'):
        assert secret not in raw


@pytest.mark.parametrize('username', [None,123,True,'bad\nSECRET_TOKEN','https://private.invalid','a'*100])
def test_invalid_username_is_never_logged(username,capsys):
    cli.runtime_health(engine(), {'username':username}, dict(poll_batches=0,received_updates=0,poll_errors=0,completed_cycles=0))
    assert json.loads(capsys.readouterr().out)['bot_username'] is None


def test_storage_failure_logs_constant_code_without_aborting(capsys):
    e=engine()
    e.s=SimpleNamespace(execute=lambda *args:(_ for _ in ()).throw(RuntimeError('PRIVATE_ERROR')))
    cli.runtime_health(e,{},dict(poll_batches=0,received_updates=0,poll_errors=0,completed_cycles=0))
    assert json.loads(capsys.readouterr().out) == {'schema':'goldway_runtime_health_v1','unavailable':True}


def test_log_output_failure_does_not_abort_worker(monkeypatch):
    def broken(*args,**kwargs):
        raise OSError('PRIVATE_LOG_ERROR')
    monkeypatch.setattr('builtins.print',broken)
    cli.runtime_health(engine(),{},dict(poll_batches=0,received_updates=0,poll_errors=0,completed_cycles=0))


def test_real_loop_logs_startup_and_five_minute_aggregate_without_extra_polls(monkeypatch,capsys):
    e=engine()
    e.tz=None
    e.tick=lambda:None
    e.c['daily_summary']=False
    handlers={}
    polls=[]
    class Api:
        def __init__(self,token):self.client=SimpleNamespace(close=lambda:None)
        def call(self,method,**kwargs):
            if method=='getMe':return {'id':900,'is_bot':True,'username':'GOLDWAYCHAT_BOT'}
            assert method=='getUpdates'
            polls.append(kwargs)
            if len(polls)==3:handlers[signal.SIGTERM]()
            return []
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','SECRET_TOKEN')
    monkeypatch.setattr(cli,'Telegram',Api)
    monkeypatch.setattr(cli,'bootstrap_groups',lambda *args:None)
    monkeypatch.setattr(cli,'Updates',lambda *args:None)
    for module, name in [('weekly_review_command','make_delivery_guard'),
                         ('personal_reference_discovery','make_delivery_guard'),
                         ('own_impediments','make_delivery_guard'),
                         ('design_brief_command','make_task_brief_guard'),
                         ('selected_knowledge_command','make_delivery_guard')]:
        monkeypatch.setattr('goldway.'+module+'.'+name,lambda *args:None)
    monkeypatch.setattr(cli,'Dispatcher',lambda *args,**kwargs:SimpleNamespace(recover=lambda:None,flush=lambda *args:None))
    monkeypatch.setattr(cli.signal,'signal',lambda sig,callback:handlers.update({sig:callback}))
    times=iter([0,100,301,302])
    monkeypatch.setattr(cli.time,'monotonic',lambda:next(times))
    cli.run(e)
    reports=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(polls)==3
    assert [r['completed_cycles'] for r in reports]==[0,2]
    assert [r['poll_batches'] for r in reports]==[0,2]
    assert all(r['received_updates']==0 for r in reports)
    assert all(p['allowed_updates']==cli.ALLOWED_UPDATES for p in polls)
    assert e.s.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
    assert e.s.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
