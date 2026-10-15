"""Optional model/context upgrade: offline HTTP fixtures, no provider calls."""
import copy
import json

import httpx
import pytest

import goldway.model as module
from goldway.engine import Engine
from goldway.model import JsonModel


def completed(value=None):
    return {'status':'completed','output':[{'type':'reasoning','summary':[]},
        {'type':'message','role':'assistant','status':'completed','content':[
            {'type':'output_text','text':json.dumps(value or {'actions':[],'summary':'ok'})}]}],
        'usage':{'input_tokens':37,'output_tokens':19}}


def candidate(c):
    return dict(c['model'], provider='openai', model='gpt-6.1-sol', api='responses',
                reasoning_effort='medium', service_tier='default', input_per_million=2, output_per_million=10,
                long_context_threshold=272000,long_input_multiplier=2,long_output_multiplier=1.5,
                max_context_chars=120000, timeout_seconds=90, daily_budget=5)


def mock_client(monkeypatch, handler):
    timeouts=[]
    # Save class first because module.httpx and test.httpx reference the same module.
    cls=httpx.Client
    def make(**kwargs):
        timeouts.append(kwargs.get('timeout'))
        return cls(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module.httpx,'Client',make)
    return timeouts


def test_responses_route_keeps_model_interface_schema_privacy_and_usage(env,monkeypatch):
    e,s,c,clock=env; requests=[]
    def handle(request):requests.append(request);return httpx.Response(200,json=completed())
    timeouts=mock_client(monkeypatch,handle)
    cfg=candidate(c)
    model=JsonModel(s,e.b,cfg,'synthetic-key',lambda:clock[0])
    assert model.propose({'kind':'operator_chat','intelligence':True})['summary']=='ok'
    assert str(requests[0].url)=='https://api.openai.com/v1/responses'
    p=json.loads(requests[0].content)
    assert p['model']=='gpt-6.1-sol' and p['reasoning']['effort']=='medium'
    assert p['service_tier']=='default'
    assert p['store'] is False and not p.get('tools') and 'previous_response_id' not in p
    assert p['text']['format']['type']=='json_schema'
    assert 'dialogue_state' in p['text']['format']['schema']['required']
    assert 'ACTUAL delivered conversation' in p['instructions']
    assert timeouts==[90]
    usage=s.rows('SELECT * FROM usage')[0]
    assert usage['input_tokens']==37 and usage['output_tokens']==19 and usage['reserved_cost']>0


@pytest.mark.parametrize('mutation',[
    {'api':'arbitrary'}, {'provider':'anthropic','api':'responses'},
    {'timeout_seconds':0},{'timeout_seconds':True},{'timeout_seconds':181},
    {'long_context_threshold':False},{'long_context_threshold':0},
    {'long_input_multiplier':0},{'long_output_multiplier':float('nan')},
])
def test_invalid_model_configuration_fails_before_reservation_and_network(env,monkeypatch,mutation):
    e,s,c,clock=env;calls=[]
    mock_client(monkeypatch,lambda r:calls.append(r))
    cfg=dict(candidate(c),**mutation)
    with pytest.raises(ValueError):JsonModel(s,e.b,cfg,'synthetic-key').propose({'text':'synthetic'})
    assert not calls and not s.rows('SELECT * FROM usage')


def test_daily_budget_and_context_limits_still_stop_before_request(env,monkeypatch):
    e,s,c,clock=env;calls=[];mock_client(monkeypatch,lambda r:calls.append(r))
    for cfg in (dict(candidate(c),daily_budget=0),dict(candidate(c),max_context_chars=3)):
        with pytest.raises(ValueError):JsonModel(s,e.b,cfg,'synthetic-key').propose({'text':'too big'})
    assert not calls and not s.rows('SELECT * FROM usage')


def test_long_context_tier_is_in_reservation_before_call(env,monkeypatch):
    e,s,c,clock=env
    mock_client(monkeypatch,lambda r:httpx.Response(200,json=completed()))
    cfg=dict(candidate(c),long_context_threshold=1)
    context={'text':'synthetic'}
    JsonModel(s,e.b,cfg,'synthetic-key',lambda:clock[0]).propose(context)
    count=len(json.dumps(context,ensure_ascii=False).encode())+len(module.SYSTEM.encode())+1024
    expected=(count*2*2+cfg['max_output_tokens']*10*1.5)/1e6
    assert s.rows('SELECT reserved_cost FROM usage')[0]['reserved_cost']==pytest.approx(expected)


@pytest.mark.parametrize('bad',[{'status':'incomplete','output':[],'usage':{}},
    {'status':'completed','output':[{'type':'function_call','name':'write_sheet'}],'usage':{'input_tokens':10,'output_tokens':20}}])
def test_incomplete_or_unsolicited_tool_output_cannot_execute_or_lose_reservation(env,monkeypatch,bad):
    e,s,c,clock=env;mock_client(monkeypatch,lambda r:httpx.Response(200,json=bad))
    with pytest.raises(ValueError):JsonModel(s,e.b,candidate(c),'synthetic-key').propose({'text':'synthetic'})
    assert s.rows('SELECT reserved_cost FROM usage')[0]['reserved_cost']>0
    assert not s.rows('SELECT * FROM tasks') and not s.rows('SELECT * FROM outbox')


def test_new_larger_history_is_opt_in_bounded_and_does_not_enable_writes(env):
    e,s,c,clock=env
    before=copy.deepcopy(c)
    cfg=copy.deepcopy(c)
    cfg['intelligence']={'enabled':True,'sources':[],'history_messages':16,'history_chars':16000,'knowledge_chars':12000}
    upgraded=Engine(s,cfg,None,lambda:clock[0])
    assert upgraded.intelligence.memory.max_turns==16
    assert upgraded.intelligence.memory.max_chars==16000
    assert upgraded.intelligence.knowledge_chars==12000
    assert c==before and cfg.get('sheets_write')==before.get('sheets_write')
    assert cfg['employees']==before['employees'] and cfg['model']==before['model']


@pytest.mark.parametrize('bad',[
    {'history_messages':True},{'history_messages':51},{'history_chars':60001},
    {'knowledge_chars':True},{'knowledge_chars':511},{'knowledge_chars':24001},
])
def test_history_configuration_cannot_disable_bounds(env,bad):
    e,s,c,clock=env;cfg=copy.deepcopy(c)
    cfg['intelligence']={'enabled':True,'sources':[],**bad}
    with pytest.raises(ValueError):Engine(s,cfg,None)


def test_original_small_context_defaults_are_preserved(env):
    e,s,c,clock=env;cfg=copy.deepcopy(c);cfg['intelligence']={'enabled':True,'sources':[]}
    old=Engine(s,cfg,None)
    assert old.intelligence.memory.max_turns==8 and old.intelligence.memory.max_chars==6000
    assert old.intelligence.knowledge_chars==7000


def test_capability_context_describes_real_tools_without_granting_new_actions(env):
    e,s,c,clock=env;cfg=copy.deepcopy(c);cfg['intelligence']={'enabled':True,'sources':[]}
    upgraded=Engine(s,cfg,None)
    ctx={'tasks':[],'kind':'operator_chat','can_create_tasks':True,'can_submit_proposals':True}
    upgraded.intelligence.enrich({'sender':cfg['admins'][0],'text':'Запиши в таблицу','channel':'bot','source_id':'telegram:77:1'},ctx,clock[0])
    assert ctx['capabilities']['task_mutations']=='reviewed_admin_actions'
    for unavailable in ('spreadsheet_tool','arbitrary_browser_or_shell','external_file_write_tool'):
        assert ctx['capabilities'][unavailable] is False
    assert not s.rows('SELECT * FROM outbox') and not s.rows('SELECT * FROM tasks')


def test_legacy_employee_is_not_promised_closed_team_proposal_rights(env):
    e,s,c,clock=env;cfg=copy.deepcopy(c);cfg['intelligence']={'enabled':True,'sources':[]}
    upgraded=Engine(s,cfg,None)
    for can_propose, expected in ((False,'permitted_own_task_updates'),(True,'proposals_and_permitted_own_task_updates')):
        ctx={'tasks':[],'can_create_tasks':False,'can_submit_proposals':can_propose}
        upgraded.intelligence.enrich({'sender':'alen','text':'Что ты умеешь?','channel':'bot','source_id':'telegram:22:1'},ctx,clock[0])
        assert ctx['capabilities']['task_mutations']==expected
