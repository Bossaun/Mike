import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from goldway.store import Store
from goldway.group_daily import GroupDaily, start, SYSTEM, SCHEMA
from goldway.telegram import DeliveryError
from goldway.model import JsonModel


class API:
    def __init__(self):
        self.sent=[]; self.role='administrator'; self.privacy=True; self.error=None
    def call(self, method, **kw):
        if method=='getMe':return {'id':99,'can_read_all_group_messages':not self.privacy}
        if method=='getChat':return {'id':-123,'type':'group','permissions':{'can_send_messages':True}}
        if method=='getChatMember':return {'status':self.role}
        assert method=='sendMessage'
        self.sent.append(kw)
        if self.error:raise self.error
        return {'message_id':777}


@pytest.fixture
def rig(tmp_path):
    clock=[datetime(2026,10,29,7,tzinfo=timezone.utc).timestamp()]
    config=dict(enabled=True,verified=True,chat_id=-123,title='Synthetic group',timezone='Europe/Moscow',hour=10,minute=0,started_at=int(clock[0]-60),seed_context='На 29 октября: синтетическая карточка ожидает проверки.')
    path=tmp_path/'group.json';path.write_text(json.dumps(config))
    s=Store(tmp_path/'db.sqlite')
    class Model:
        def __init__(self):self.calls=[];self.result={'message':'Алэн, подтвердите результат проверки карточки.','evidence_ids':['seed']};self.hook=None
        def propose(self,c):
            self.calls.append(c)
            if self.hook:self.hook()
            return self.result
    e=SimpleNamespace(s=s,b='GOLDWAY',now=lambda:clock[0],c={'live_send':True,'employees':{'alen':{'name':'Алэн','telegram_user_id':5}},'business_account_user_id':6},model=Model())
    api=API();d=GroupDaily(e,api,{'id':99},path)
    yield SimpleNamespace(e=e,s=s,api=api,d=d,path=path,c=config,clock=clock)
    s.db.close()


def message(r, **changes):
    m={'message_id':1,'date':int(r.clock[0]),'chat':{'id':-123,'type':'group'},'from':{'id':5},'text':'Проверка пока не готова.'}
    m.update(changes)
    return {'update_id':1,'message':m}


def test_new_group_text_and_edits_are_isolated_and_deduplicated(rig):
    r=rig;u=message(r);assert r.d.handle(u);assert r.d.handle(u)
    assert len(r.s.rows('SELECT * FROM group_daily_messages'))==1
    edited={'update_id':2,'edited_message':dict(u['message'],text='Уже проверил.',edit_date=int(r.clock[0]))}
    assert r.d.handle(edited);r.d.handle(u)
    assert r.s.rows('SELECT * FROM group_daily_messages')[0]['body']=='Уже проверил.'
    assert r.s.rows('SELECT * FROM events')==[] and r.s.rows('SELECT * FROM outbox')==[]


@pytest.mark.parametrize('change',[{'chat':{'id':-555,'type':'group'}},{'chat':{'id':-123,'type':'private'}},{'from':{'id':99,'is_bot':True}},{'forward_origin':{}},{'sender_chat':{'id':-123}},{'date':1},{'text':'','photo':[{}]}])
def test_unapproved_old_forwarded_bot_and_media_not_ingested(rig,change):
    r=rig;r.d.handle(message(r,**change));assert not r.s.rows('SELECT * FROM group_daily_messages')


def test_member_without_full_read_blocks_generation_and_send(rig):
    r=rig;r.api.role='member';r.d.tick();assert not r.e.model.calls and not r.api.sent
    assert not r.s.rows('SELECT * FROM group_daily_runs')
    r.api.privacy=False;r.clock[0]+=301;r.d.tick();assert len(r.api.sent)==1


def test_schedule_single_send_survives_restart_and_no_old_day_catchup(rig):
    r=rig;r.clock[0]-=61;r.d.tick();assert not r.api.sent
    r.clock[0]+=61;r.d.tick();assert len(r.api.sent)==1
    GroupDaily(r.e,r.api,{'id':99},r.path).tick();assert len(r.api.sent)==1
    r.clock[0]+=3*86400;r.e.model.result['message']='Даша, какой следующий шаг по оставшейся карточке?';r.d.tick()
    assert len(r.api.sent)==2 and len(r.s.rows('SELECT * FROM group_daily_runs'))==2


def test_unknown_delivery_is_never_retried_even_next_day(rig):
    r=rig;r.api.error=DeliveryError('unknown','synthetic timeout');r.d.tick()
    assert r.s.rows('SELECT state FROM group_daily_runs')[0]['state']=='unknown'
    r.api.error=None;r.clock[0]+=86400;GroupDaily(r.e,r.api,{'id':99},r.path).tick()
    assert len(r.api.sent)==1


def test_restart_after_send_claim_becomes_unknown(rig):
    r=rig;r.s.execute("INSERT INTO group_daily_runs(business,chat_id,day,state,created) VALUES('GOLDWAY',-123,'2026-10-28','sending',0)")
    GroupDaily(r.e,r.api,{'id':99},r.path).tick();assert not r.api.sent


@pytest.mark.parametrize('revoke',['config','rights','live_send'])
def test_revocation_during_review_prevents_send(rig,revoke):
    r=rig
    def hook():
        if revoke=='config':r.path.write_text('{"enabled":false}')
        if revoke=='rights':r.api.role='left'
        if revoke=='live_send':r.e.c['live_send']=False
    r.e.model.hook=hook;r.d.tick();assert not r.api.sent


@pytest.mark.parametrize('result',[{'message':'done','evidence_ids':[]},{'message':'done','evidence_ids':['invented']},{'message':'sk-secretabc123456789','evidence_ids':['seed']},{'message':'Позвонить +7 999 123-45-67','evidence_ids':['seed']},{'message':'ok','evidence_ids':['seed'],'actions':[{}]}])
def test_invalid_or_sensitive_model_output_never_sent(rig,result):
    rig.e.model.result=result;rig.d.tick();assert not rig.api.sent


def test_links_removed_before_send_and_repeated_text_quiet(rig):
    r=rig;r.e.model.result['message']='Проверить карточку https://example.com. Даша, когда будет готово?';r.d.tick()
    assert 'example.com' not in r.api.sent[0]['text']
    r.clock[0]+=86400;r.d.tick();assert len(r.api.sent)==1


def test_empty_review_no_placeholder(rig):
    r=rig;r.e.model.result={'message':'','evidence_ids':[]};r.d.tick();assert not r.api.sent


def test_missing_or_invalid_config_does_not_stop_private_worker(rig,tmp_path):
    r=rig;assert GroupDaily(r.e,r.api,{'id':99},tmp_path/'absent').c is None
    r.path.write_text('{}');assert start(r.e,r.api,{'id':99},r.path) is None


def test_same_budget_ledger_and_no_tools_or_private_context(rig,monkeypatch):
    r=rig
    import goldway.model as module
    cfg={'model':'gpt-4.1-mini','provider':'openai','api':'chat_completions','input_per_million':0.4,'output_per_million':1.6,'daily_budget':1,'max_output_tokens':1000}
    def respond(req):
        p=json.loads(req.content);assert p['model']=='gpt-4.1-mini' and 'tools' not in p
        assert p['messages'][0]['content']==SYSTEM
        assert p['response_format']['json_schema']['schema']==SCHEMA
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(r.e.model.result)}}],'usage':{'prompt_tokens':50,'completion_tokens':20}})
    client=httpx.Client(transport=httpx.MockTransport(respond));monkeypatch.setattr(module.httpx,'Client',lambda **kw:client)
    model=JsonModel(r.s,r.e.b,cfg,'synthetic',r.e.now);model.propose(r.d.context())
    assert len(r.s.rows('SELECT * FROM usage'))==1
    r.s.execute('UPDATE usage SET reserved_cost=1')
    with pytest.raises(ValueError,match='budget'):model.propose(r.d.context())
    assert len(r.s.rows('SELECT * FROM usage'))==1


def test_existing_worker_ingests_and_sends_with_one_poller(rig,monkeypatch):
    import signal
    import goldway.cli as cli
    import goldway.group_daily as module
    r=rig;handlers={};polls=[];private=[]
    r.e.tz=timezone.utc;r.e.tick=lambda:None;r.e.c['daily_summary']=False
    class WorkerAPI(API):
        def __init__(self,token):
            super().__init__();self.client=SimpleNamespace(close=lambda:None)
        def call(self,method,**kw):
            if method=='getUpdates':
                polls.append(kw)
                if len(polls)==1:return [message(r)]
                handlers[signal.SIGTERM]()
                return []
            return r.api.call(method,**kw)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','synthetic')
    monkeypatch.setattr(cli,'Telegram',WorkerAPI)
    monkeypatch.setattr(cli,'bootstrap_groups',lambda *a:None)
    monkeypatch.setattr(module,'start',lambda e,a,b:GroupDaily(e,a,b,r.path))
    monkeypatch.setattr(cli,'Updates',lambda *a:SimpleNamespace(handle=lambda u:private.append(u)))
    for mod,name in [('weekly_review_command','make_delivery_guard'),('personal_reference_discovery','make_delivery_guard'),('own_impediments','make_delivery_guard'),('design_brief_command','make_task_brief_guard'),('selected_knowledge_command','make_delivery_guard')]:
        monkeypatch.setattr('goldway.'+mod+'.'+name,lambda *a:None)
    monkeypatch.setattr(cli,'Dispatcher',lambda *a,**kw:SimpleNamespace(recover=lambda:None,flush=lambda *a:None))
    monkeypatch.setattr(cli.signal,'signal',lambda sig,f:handlers.update({sig:f}))
    cli.run(r.e)
    assert len(polls)==2 and len(r.api.sent)==1
    assert len(r.s.rows('SELECT * FROM group_daily_messages'))==1
    assert not private and r.s.execute("SELECT value FROM runtime WHERE key='GOLDWAY:offset'").fetchone()[0]=='2'
