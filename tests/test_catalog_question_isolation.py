"""Actual Engine isolation of ordinary grounded questions from meeting sidecars.

Public/synthetic in-memory evidence, fake model/HTTP/capture Dispatcher only. The
observed B run's raw model intent/spans were not retained and are not asserted.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import httpx
import pytest
import goldway
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key
from goldway.dialogue import scope_from_payload
from goldway.model import JsonModel,response_schema

spec=importlib.util.spec_from_file_location('goldway.meeting_questions',Path(__file__).parents[1]/'goldway'/'meeting_questions.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);goldway.meeting_questions=m
NOW=1792174600
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
CATALOG=('Что такое GOLDWAY и какие три подтверждённых бренда представлены в публичном каталоге? '
 'Кратко перечисли их с явными номерами 1, 2, 3 и предложи следующим ответом пояснить каждый из этих же трёх брендов в том же порядке, также списком 1, 2, 3. Не создавай задач.')
ANSWER='GOLDWAY — оригинальная косметика в Латвии. В публичной выборке указаны PUSY, VOIS и The Act.'
MEETING='Итоги обсуждения.\nКакой образец отправить клиенту?\nРешение пока не принимали.'
MISSING=object()

class API:
 def __init__(self):self.sent=[]
 def call(self,method,**params):
  if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
  assert method=='sendMessage' and str(params['chat_id']) in ('1','500')
  self.sent.append(params);return dict(message_id=1000+len(self.sent))

@pytest.fixture
def c(monkeypatch):
 config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={n:dict(name=n,telegram_user_id=i+1) for i,n in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 store=Store(':memory:');clock=[NOW];engine=Engine(store,config,None,lambda:clock[0]);engine.intelligence.sources.refresh=lambda *a,**kw:{}
 engine.intelligence.index.upsert(dict(source_id='synthetic-public',document_id='public',title='GOLDWAY: публичная выборка каталога',url='https://goldwaybaltic.com',text='GOLDWAY — оригинальная косметика в Латвии. В публичном каталоге: PUSY; VOIS; The Act.',readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW-60,permission_expires_at=NOW+600,confirmed=True))
 store.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',NOW))
 api=API();case=SimpleNamespace(s=store,e=engine,c=config,clock=clock,api=api,seen=[])
 def forbidden(*a,**kw):pytest.fail('unexpected real network/source/model/provider')
 monkeypatch.setattr(httpx,'Client',forbidden)
 import socket
 monkeypatch.setattr(socket,'socket',forbidden)
 yield case
 store.db.close()

def model_result(context,questions=MISSING,intent='conversation',actions=(),grounded=True):
 refs=[e['id'] for e in context['knowledge']['evidence']] if grounded else []
 value=dict(actions=list(actions),summary='Синтетическая проверка',intent=intent,reply_text=ANSWER if intent=='conversation' else 'Черновик по текущему сообщению.',dialogue_state=dict(topic='GOLDWAY',entities=['PUSY','VOIS','The Act'] if intent=='conversation' else [],pending_offer=None,task_id=None),evidence_ids=refs,knowledge_mode='grounded' if refs else 'not_needed')
 if questions is not MISSING:value['meeting_question_lines']=questions
 return value

def run(c,text=CATALOG,questions=MISSING,intent='conversation',actions=(),channel='bot',callback=None,grounded=True):
 class Model:
  def propose(self,context):
   c.seen.append(copy.deepcopy(context))
   if callback:callback(context)
   return model_result(context,questions,intent,actions,grounded)
 c.e.model=Model();c.clock[0]+=1
 c.payload=dict(sender='test_operator',channel=channel,source_id='telegram:1:10' if channel=='bot' else 'business:conn:1:10')
 assert c.e.reply('isolation','test_operator',text,channel=channel,source_id=c.payload['source_id'],message_at=c.clock[0]);c.e.process('isolation')
 return c.seen[0]

def rows(c):return c.s.rows('SELECT * FROM outbox ORDER BY id')
def sidecars(c):return [r for r in rows(c) if r['dedupe'].startswith('meeting-questions:')]
def no_actions(c):
 assert not c.e.tasks() and not c.s.rows('SELECT 1 FROM task_sources LIMIT 1')
 assert not sidecars(c)

@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('extension',[None,{},'1',[1],[[1]],[[999]],True])
def test_grounded_catalog_answer_dispatch_and_memory_survive_unadvertised_extension(c,extension,channel):
 context=run(c,questions=extension,channel=channel)
 assert context['capabilities']['meeting_question_draft'] is False and 'meeting_question_line_count' not in context
 assert 'meeting_question_lines' not in response_schema(context)['properties']
 output=rows(c);assert len(output)==1 and output[0]['text'].startswith(ANSWER) and m.NOTICE not in output[0]['text']
 assert output[0]['recipient']=='test_operator' and output[0]['task_id'] is None and output[0]['preferred_transport']==channel
 Dispatcher(c.s,c.c,c.api,now=lambda:c.clock[0]).flush()
 current=rows(c)[0];assert current['state']=='sent' and current['transport']==channel and c.api.sent[-1]['text']==current['text']
 scope=scope_from_payload(c.payload,'GOLDWAY');memory=c.e.intelligence.memory.get(scope,readable_ids=c.e.intelligence.readable_ids('test_operator',c.clock[0]),now=c.clock[0])
 assert memory['turns'][-1]['role']=='assistant' and memory['turns'][-1]['content']==current['text']
 from goldway.answer_citations import _resolve
 receipt=_resolve(c.s,c.c,c.payload,c.clock[0],include_answer=True)
 assert receipt['answer']==current['text'] and receipt['citations'] and all(x['kind']=='document' for x in receipt['citations'])
 no_actions(c);assert c.s.execute('SELECT state FROM events WHERE id=?',('isolation',)).fetchone()[0]=='done'

@pytest.mark.parametrize('text',[
 'Какой бренд указан в каталоге?\nPUSY: пример\nVOIS: пример',
 'Расскажи о брендах и источнике, а не разбирай планёрку.',
 'Подготовь список вопросов для будущей планёрки.',
 'Что значит «Итоги обсуждения»?',
 'Источник: публичный каталог\nДокумент: бренды\nЗапрос: перечислить'])
def test_source_question_or_draft_is_not_advertised_as_meeting(c,text):
 context=run(c,text=text,questions=[[1]])
 assert context['capabilities']['meeting_question_draft'] is False and rows(c)[0]['text'].startswith(ANSWER)
 no_actions(c)

@pytest.mark.parametrize('text,spans',[
 (MEETING,[[2]]),
 ('Итоги обсуждения. Какой образец отправить клиенту?',[[1]]),
 ('Планёрка: какой образец отправить?',[[1]]),
 ('Разбери планёрку: какой образец отправить?',[[1]]),
 ('Алекс: Какой образец отправить?\nНастя: Решение не принято.',[[1]]),
 ('Алекс: Какой образец отправить?; Настя: Решение не принято.',[[1]])])
def test_genuine_explicit_or_multispeaker_meeting_keeps_literal_sidecar(c,text,spans):
 context=run(c,text=text,questions=spans,intent='meeting',grounded=False)
 assert context['capabilities']['meeting_question_draft'] is True
 assert len(sidecars(c))==1 and 'какой образец' in sidecars(c)[0]['text'].casefold()
 assert 'Статус решения не подтверждён' in sidecars(c)[0]['text'] and sidecars(c)[0]['recipient']=='test_operator'
 assert not c.e.tasks()

@pytest.mark.parametrize('extension',[None,{},'bad',[1],[[999]],[[True]]])
def test_real_meeting_malformed_selector_remains_strict_atomic(c,extension):
 context=run(c,text=MEETING,questions=extension,intent='meeting',actions=[action()],grounded=False)
 assert context['capabilities']['meeting_question_draft'] is True
 no_actions(c);assert rows(c) and all(r['text']==m.NOTICE for r in rows(c))
 assert not c.s.rows("SELECT 1 FROM sources WHERE id='telegram:1:10'")

def action():return dict(op='create',task_id=None,title='Проверить образцы',owner=None,deadline=None,next_step='Сверить образцы',criterion='Готов отчёт',status='proposed',set_fields=[],evidence_lines=[1],owner_lines=[],deadline_lines=[])

@pytest.mark.parametrize('intent',['meeting','task_action','idea'])
def test_unadvertised_extension_never_bypasses_meeting_intent_or_action_checks(c,intent):
 context=run(c,text='Создай задачу проверить образцы.',questions=[[1]],intent=intent,actions=[action()],grounded=False)
 assert context['capabilities']['meeting_question_draft'] is False
 no_actions(c);assert rows(c) and all(r['text']==m.NOTICE for r in rows(c))

def test_conversation_cannot_smuggle_actions_through_ignored_extension(c):
 with pytest.raises(ValueError,match='User explicitly declined task creation'):run(c,questions=None,intent='conversation',actions=[action()])
 no_actions(c)
 assert c.s.execute('SELECT state FROM events WHERE id=?',('isolation',)).fetchone()[0]=='failed'
 assert not any(r['text'].startswith(ANSWER) for r in rows(c))

@pytest.mark.parametrize('extension',[MISSING,[]])
def test_normal_task_fields_remain_ordinary_proposed_task(c,extension):
 context=run(c,text='Создай задачу проверить образцы.',questions=extension,intent='task_action',actions=[action()],grounded=False)
 assert context['capabilities']['meeting_question_draft'] is False
 assert len(c.e.tasks())==1 and c.e.tasks()[0]['status']=='proposed' and c.e.tasks()[0]['title']=='Проверить образцы'
 assert not sidecars(c) and not any(r['text']==m.NOTICE for r in rows(c))

@pytest.mark.parametrize('extension',[MISSING,[]])
def test_unlabelled_legacy_meeting_extraction_still_uses_existing_review(c,extension):
 context=run(c,text='Обсудили образцы. Решили проверить их до отправки.',questions=extension,intent='meeting',actions=[action()],grounded=False)
 assert context['capabilities']['meeting_question_draft'] is False
 assert len(c.e.tasks())==1 and c.e.tasks()[0]['status']=='proposed' and any(r['dedupe'].startswith('review:') for r in rows(c))
 assert not sidecars(c)

@pytest.mark.parametrize('meeting',[False,True])
def test_actual_jsonmodel_schema_and_engine_route_share_host_input_gate(c,monkeypatch,meeting):
 requests=[];seen=[];text=MEETING if meeting else CATALOG
 class Client:
  def __init__(self,**kw):assert kw==dict(timeout=60)
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def post(self,url,**kw):
   body=kw['json'];requests.append(body);context=json.loads(body['messages'][-1]['content']);seen.append(context)
   schema=body['response_format']['json_schema']['schema']['properties']
   assert ('meeting_question_lines' in schema)==meeting
   value=model_result(context,[[2]] if meeting else None,'meeting' if meeting else 'conversation',grounded=not meeting)
   return httpx.Response(200,request=httpx.Request('POST',url),json=dict(choices=[dict(message=dict(content=json.dumps(value)),finish_reason='stop')],usage=dict(prompt_tokens=1,completion_tokens=1)))
 monkeypatch.setattr(httpx,'Client',Client)
 cfg=dict(provider='openai',model='gpt-4.1-mini',daily_budget=1,input_per_million=.4,output_per_million=1.6,max_context_chars=44000,max_output_tokens=6000)
 c.e.model=JsonModel(c.s,'GOLDWAY',cfg,'synthetic-key',lambda:c.clock[0]);c.clock[0]+=1
 c.e.reply('http','test_operator',text,channel='bot',source_id='telegram:1:10',message_at=c.clock[0]);c.e.process('http')
 assert len(requests)==1 and seen[0]['capabilities']['meeting_question_draft']==meeting
 assert bool(sidecars(c))==meeting and not c.e.tasks()
 if not meeting:assert rows(c)[0]['text'].startswith(ANSWER)


def test_catalog_bad_selector_does_not_replace_grounded_reply_with_meeting_notice(c):
 run(c,questions=None)
 assert len(rows(c))==1 and rows(c)[0]['text'].startswith(ANSWER)
 assert not any(r['text']==m.NOTICE for r in rows(c));no_actions(c)


def test_bare_acceptance_without_prior_offer_still_clarifies_before_model(c):
 class NoModel:
  def propose(self,*a):pytest.fail('unanchored acceptance bypassed existing provenance gate')
 c.e.model=NoModel();c.clock[0]+=1
 c.e.reply('bare','test_operator','Давай',channel='bot',source_id='telegram:1:10',message_at=c.clock[0]);c.e.process('bare')
 no_actions(c);assert rows(c) and all(r['text']!=m.NOTICE for r in rows(c))
