import json
from types import SimpleNamespace
import pytest
from test_group_work import r
from goldway import voice_reading as v
from goldway.photo_reading import public_message


def voice(r,mid=12):
    return {'message_id':mid,'date':int(r.clock[0]),'chat':{'type':'private'},'voice':{'file_id':'secret-file','duration':15,'file_size':100}}


def test_voice_cache_and_private_reply(r,monkeypatch):
    calls=[]
    r.d.api.voice=lambda f:b'audio'
    monkeypatch.setattr(v,'recognize',lambda *args:calls.append(1) or 'Товара нет в CRM.')
    assert r.w.handle_private(voice(r),'alen',None)
    r.e.model.result={'reply':'Понял проблему с товаром.','updates':[],'evidence_ids':['seed']}
    r.w.process(lambda:False);r.w.process(lambda:False)
    assert calls==[1]
    payload=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])
    assert payload['voice_state']=='done' and payload['text']==''
    assert 'voice' not in public_message(payload)
    assert r.e.model.calls[0]['current_messages'][0]['voice_reading']=='Товара нет в CRM.'
    assert r.s.rows('SELECT preferred_transport FROM outbox')[0]['preferred_transport']=='bot'


def test_voice_failure_not_silence_or_replayed(r,monkeypatch):
    r.d.api.voice=lambda f:b'audio'
    def fail(*args):raise RuntimeError('provider secret body')
    monkeypatch.setattr(v,'recognize',fail)
    r.w.handle_private(voice(r),'alen',None);r.w.process(lambda:False)
    payload=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])
    assert payload['voice_state']=='failed'
    assert 'расшифровать' in r.s.rows('SELECT text FROM outbox')[0]['text']
    with pytest.raises(ValueError):v.transcribe(r.w,payload)


def test_audio_does_not_grant_task_authority(r,monkeypatch):
    r.d.api.voice=lambda f:b'audio';monkeypatch.setattr(v,'recognize',lambda *args:'Всё готово, принимаю.')
    r.w.handle_private(voice(r),'alen',None)
    mid=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])['id']
    r.e.model.result={'reply':'Уточню результат.','updates':[{'task_id':'C01','message_id':mid,'kind':'accept','quote':'Всё готово, принимаю.'}],'evidence_ids':['seed']}
    r.w.process(lambda:False)
    assert r.s.rows('SELECT state FROM group_work_tasks')[0]['state']=='open'


def test_budget_shared_and_no_request_when_exhausted(r,monkeypatch):
    monkeypatch.setattr(v,'pcm',lambda b:b'wav')
    r.e.model=SimpleNamespace(config={'provider':'openai','daily_budget':1},key='test')
    r.s.execute('INSERT INTO usage(business,at,provider,input_tokens,output_tokens,reserved_cost) VALUES(?,?,?,?,?,?)',('GOLDWAY',r.clock[0],'openai',0,0,.99))
    with pytest.raises(ValueError,match='budget'):v.recognize(r.w,b'a')
    assert len(r.s.rows('SELECT * FROM usage'))==1


@pytest.mark.parametrize('duration',[0,181,True,None])
def test_reject_oversized_or_invalid_voice(duration):
    assert v.attachment({'voice':{'file_id':'id','duration':duration}}) is None
