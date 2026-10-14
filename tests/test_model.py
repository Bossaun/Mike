import json
import httpx
import pytest
import goldway.model as module
from goldway.model import JsonModel

@pytest.mark.parametrize('provider',['openai','anthropic'])
def test_provider_adapter_structured_response_and_usage(env,monkeypatch,provider):
    e,s,c,clock=env; config=dict(c['model'],provider=provider)
    def respond(request):
        assert request.url.scheme=='https'
        payload=json.loads(request.content)
        assert 'model' in payload
        value='{"actions":[],"summary":"ok"}'
        data={'choices':[{'message':{'content':value}}],'usage':{'prompt_tokens':10,'completion_tokens':5}} if provider=='openai' else {'content':[{'type':'text','text':value}],'usage':{'input_tokens':10,'output_tokens':5}}
        return httpx.Response(200,json=data)
    client=httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module.httpx,'Client',lambda **kw:client)
    model=JsonModel(s,e.b,config,'test',lambda:clock[0])
    assert model.propose({'text':'a'})['summary']=='ok'
    usage=s.rows('SELECT * FROM usage')[0]
    assert usage['input_tokens']==10 and usage['output_tokens']==5 and usage['reserved_cost']>0

def test_budget_stops_call_before_network(env):
    e,s,c,clock=env; config=dict(c['model'],daily_budget=0)
    model=JsonModel(s,e.b,config,'test',lambda:clock[0])
    with pytest.raises(ValueError,match='budget'): model.propose({'text':'a'})
    assert not s.rows('SELECT * FROM usage')

def test_context_limit_before_network(env):
    e,s,c,*_=env; config=dict(c['model'],max_context_chars=2)
    with pytest.raises(ValueError,match='context'): JsonModel(s,e.b,config,'test').propose({'text':'abc'})
    assert not s.rows('SELECT * FROM usage')

def test_failure_keeps_cost_reservation_without_secret(env,monkeypatch):
    e,s,c,clock=env
    client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401,json={})))
    monkeypatch.setattr(module.httpx,'Client',lambda **kw:client)
    model=JsonModel(s,e.b,c['model'],'PRIVATE_KEY',lambda:clock[0])
    with pytest.raises(RuntimeError) as exc: model.propose({'text':'abc'})
    assert 'PRIVATE_KEY' not in str(exc.value)
    assert s.rows('SELECT * FROM usage')[0]['reserved_cost']>0

def test_api_balance_error_is_diagnosed_without_secret(env,monkeypatch):
    e,s,c,clock=env
    client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(429,json={'error':{'code':'credit_balance_exhausted','message':'do not echo PRIVATE_KEY'}})))
    monkeypatch.setattr(module.httpx,'Client',lambda **kw:client)
    with pytest.raises(RuntimeError,match='429.*credit_balance_exhausted') as exc:
        JsonModel(s,e.b,c['model'],'PRIVATE_KEY',lambda:clock[0]).propose({'text':'synthetic'})
    assert 'PRIVATE_KEY' not in str(exc.value)
