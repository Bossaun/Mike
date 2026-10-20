"""Actual JsonModel request contracts; fake HTTP/public synthetic context only.

Tests do not establish real model adherence, semantic acceptance or delivery.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import httpx
import pytest
from goldway.dialogue_focus import build_followup
from goldway.store import Store

spec=importlib.util.spec_from_file_location('goldway.explain_test_model',Path(__file__).parents[1]/'goldway'/'model.py')
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
NAMES=['PUSY','VOIS','The Act']
FIRST_REQUEST='Перечисли три бренда и предложи пояснить каждый в том же порядке списком 1, 2, 3.'
FIRST_ANSWER=('GOLDWAY — оригинальная косметика в Латвии. В выборке есть PUSY, VOIS, The Act. '
 'Могу пояснить каждый из этих трёх брендов в том же порядке отдельными пунктами 1, 2, 3.')
EXPLANATION=('1. PUSY — в выбранных публичных позициях есть «PUSY Body slime scrub» 250g и «PUSY Brow fix gel professional» 15ml.\n'
 '2. VOIS — в доступной выборке указана «VOIS энзимная пудра». Это название позиции, а не подтверждение эффекта.\n'
 '3. The Act — среди показанных названий есть «THE ACT Hand Cream XX» 65 мл. Происхождение и остатки этими данными не подтверждены.')

def knowledge():
 values=[('site','GOLDWAY — оригинальная косметика в Латвии'),('catalog-pusy','В поле brand: PUSY. Позиции: PUSY Body slime scrub 250g; PUSY Brow fix gel professional 15ml.'),('catalog-vois','В поле brand: VOIS. Позиция: VOIS энзимная пудра.'),('catalog-act','В поле brand: The Act. Позиция: THE ACT Hand Cream XX 65 мл.')]
 return dict(evidence=[dict(id=key,confirmed=True,title=key,url='https://goldwaybaltic.com' if key=='site' else 'https://gw-api.abdulwaheedaziz8.workers.dev?api=catalog',text=text) for key,text in values],facts=[],conflicts=[])

def accepted_context():
 dialogue=dict(topic='GOLDWAY / публичный каталог',entities=NAMES[:],pending_offer=dict(kind='explain',text='Пояснить три бренда в том же порядке отдельными пунктами 1, 2, 3'),task_id=None,turns=[dict(role='user',content=FIRST_REQUEST),dict(role='assistant',content=FIRST_ANSWER)])
 return dict(kind='operator_chat',intelligence=True,text='1 | Давай',tasks=[],sender_role='admin',can_create_tasks=True,capabilities=dict(task_mutations=False),dialogue=dialogue,followup=build_followup('Давай',dialogue),knowledge=knowledge())

def result(text):
 return dict(actions=[],summary='Синтетическая проверка пояснения',intent='conversation',reply_text=text,knowledge_mode='grounded',evidence_ids=['site','catalog-pusy','catalog-vois','catalog-act'],dialogue_state=dict(topic='Публичный каталог',entities=NAMES[:],pending_offer=None,task_id=None))

def actual_request(monkeypatch,api,context,reply):
 requests=[];client_class=httpx.Client
 cfg=dict(provider='openai',model='gpt-4.1-mini',api=api,daily_budget=1.0,input_per_million=0.4,output_per_million=1.6,max_context_chars=44000,max_output_tokens=6000,timeout_seconds=60,json_mode=False)
 before=copy.deepcopy(context);cfg_before=copy.deepcopy(cfg)
 def handle(request):
  requests.append(request);raw=json.dumps(reply,ensure_ascii=False)
  if api=='responses':data=dict(status='completed',output=[dict(type='message',role='assistant',status='completed',content=[dict(type='output_text',text=raw)])],usage=dict(input_tokens=31,output_tokens=19))
  else:data=dict(choices=[dict(message=dict(content=raw),finish_reason='stop')],usage=dict(prompt_tokens=31,completion_tokens=19))
  return httpx.Response(200,json=data)
 def client(**kw):
  assert kw==dict(timeout=60)
  return client_class(transport=httpx.MockTransport(handle))
 monkeypatch.setattr(module.httpx,'Client',client)
 store=Store(':memory:')
 try:
  assert module.JsonModel(store,'GOLDWAY',cfg,'synthetic-key',lambda:1000).propose(context)==reply
  assert context==before and cfg==cfg_before and len(requests)==1
  usage=store.rows('SELECT * FROM usage');assert len(usage)==1 and usage[0]['input_tokens']==31 and usage[0]['output_tokens']==19 and 0<usage[0]['reserved_cost']<1
  for table in ('tasks','events','outbox'):assert not store.rows('SELECT 1 FROM '+table+' LIMIT 1')
 finally:store.db.close()
 payload=json.loads(requests[0].content);assert payload['model']=='gpt-4.1-mini'
 if api=='responses':
  assert requests[0].url.path=='/v1/responses' and payload['store'] is False
  instructions=payload['instructions'];messages=payload['input'];schema=payload['text']['format']['schema']
 else:
  assert requests[0].url.path=='/v1/chat/completions'
  instructions=payload['messages'][0]['content'];messages=payload['messages'][1:];schema=payload['response_format']['json_schema']['schema']
 assert set(schema['properties'])=={'actions','summary','reply_text','intent','dialogue_state','evidence_ids','knowledge_mode'}
 assert schema['properties']['evidence_ids']['items']['enum']==['catalog-act','catalog-pusy','catalog-vois','site']
 return ' '.join(instructions.split()),messages

@pytest.mark.parametrize('api',['chat_completions','responses'])
def test_bare_acceptance_current_focus_and_immediate_per_entity_explanation_rules(monkeypatch,api):
 context=accepted_context();assert context['followup']['kind']=='continue_last_turn' and context['followup']['pending_offer']['kind']=='explain'
 instructions,messages=actual_request(monkeypatch,api,context,result(EXPLANATION))
 assert messages[:-1]==context['dialogue']['turns'];current=json.loads(messages[-1]['content'])
 assert current['text']=='1 | Давай' and current['dialogue']['turns']==[] and current['dialogue']['entities']==NAMES
 assert current['followup']==context['followup'] and current['followup']['target_answer']==FIRST_ANSWER
 assert current['tasks']==[] and current['knowledge']==knowledge()
 # Check semantic clauses at the actual transport, not equality to a constant.
 for clause in ('pending_offer.kind=explain','source-derived detail for EACH','same order','in THIS reply','not a promise','Расскажу','one flat','separate line','not an inline','pending_offer=null','1. <exact name>','catalog names','benefits','brand history'):
  assert clause in instructions

@pytest.mark.parametrize('api',['chat_completions','responses'])
def test_initial_public_request_location_not_origin_and_limited_catalog_evidence(monkeypatch,api):
 context=accepted_context();context.pop('followup');context['text']='1 | Что такое GOLDWAY и что видно о брендах в каталоге?';context['dialogue']=dict(turns=[],topic='',entities=[],pending_offer=None,task_id=None)
 instructions,messages=actual_request(monkeypatch,api,context,result(FIRST_ANSWER))
 current=json.loads(messages[-1]['content']);assert current['dialogue']['turns']==[] and 'followup' not in current
 assert current['knowledge']['evidence'][0]['text']=='GOLDWAY — оригинальная косметика в Латвии'
 for clause in ('в Латвии','из Латвии','market/location','country of origin','geographic qualifiers','Do not infer','source-derived detail','catalog names'):
  assert clause in instructions
