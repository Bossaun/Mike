"""New engine rewrite boundary only, actual synthetic dispatcher delivery."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
from goldway.ordinal_followup import is_ordinal_request,ordinal_index,select_numbered_item
from goldway.model import response_schema
MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600;TAIL='ESSENTIAL-FINAL-CONDITION'

@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:NOW);e.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','500','conn',NOW))
 class API:
  def call(self,method,**kw):
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';return dict(message_id=11)
 d=Dispatcher(s,c,API(),lambda:NOW)
 import builtins,socket,httpx
 def fail(*a,**kw):pytest.fail('external I/O')
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield SimpleNamespace(e=e,s=s,c=c,d=d)
 s.db.close()

def response(body='Кратко '+TAIL,**changes):
 r=dict(actions=[],summary='Synthetic',reply_text=body,dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed');r.update(changes);return r

def original(c,body=None,event='original',grounded=False,send=True,channel='bot',entities=None):
 body=body or 'A'*2900+TAIL
 if grounded:c.e.intelligence.index.upsert(dict(source_id='source-a',document_id='doc',title='Source A',url='https://example.invalid/A',text='ALPHA source',readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW-20,permission_expires_at=NOW+1000,confirmed=True))
 class Model:
  def propose(self,ctx):
   r=response(body,intent='conversation')
   r['dialogue_state']['entities']=entities or []
   if grounded:r.update(knowledge_mode='grounded',evidence_ids=[ctx['knowledge']['evidence'][0]['id']])
   return r
 c.e.model=Model();source='telegram:1:1' if channel=='bot' else 'business:conn:500:1'
 c.e.reply(event,'test_operator','Расскажи об ALPHA' if grounded else 'Напиши черновик письма',source_id=source,channel=channel);c.e.process(event)
 if send:c.d.flush()
 return c.s.execute('SELECT text FROM outbox WHERE dedupe=?',('clarify:'+event,)).fetchone()[0]

def rewrite(c,text='Расскажи о втором бренде',event='rewrite',result=None,channel='bot',source=None,task_id=None,callback=None):
 seen=[]
 class Model:
  def propose(self,ctx):
   seen.append(ctx)
   if callback:callback(ctx)
   return result or response()
 c.e.model=Model()
 def fail(*a,**kw):pytest.fail('rewrite used old retrieval/context path')
 c.e.intelligence.memory.get=fail;c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e._context=fail
 c.e.reply(event,'test_operator',text,source_id=source or ('telegram:1:2' if channel=='bot' else 'business:conn:500:2'),channel=channel,task_id=task_id);c.e.process(event)
 return seen,c.s.execute('SELECT * FROM outbox WHERE dedupe=?',('ordinal-followup:'+event,)).fetchone()

LIST='Бренды:\n1. Alpha — first item only.\n2. Beta — selected condition ONLY-BETA.\n  Details already provided.\n3. Gamma — other item.'

@pytest.mark.parametrize('entities',[[],['Beta','Gamma'],['Gamma','Beta','Alpha'],['invented'],['Alpha','Beta','Gamma']])
def test_actual_numbering_overrides_missing_partial_reversed_model_state(case,entities):
 original(case,LIST,entities=entities);seen,row=rewrite(case)
 assert len(seen)==1 and seen[0]['ordinal_only'] and seen[0]['followup']['selected_entity']=='Beta'
 assert seen[0]['followup']['selected_number']==2 and seen[0]['followup']['numbered_count']==3
 target=seen[0]['followup']['target_answer'];assert 'ONLY-BETA' in target and 'Alpha' not in target and 'Gamma' not in target
 assert seen[0]['tasks']==[] and seen[0]['knowledge']['evidence']==[] and seen[0]['dialogue']['entities']==[]
 assert row and not case.s.rows('SELECT id FROM tasks')

@pytest.mark.parametrize('text',['второй бренд','Подробнее о втором бренде','Объясни про 2-й бренд','про второй','о 2 бренде','двенадцатый вариант'])
def test_supported_ordinal_expressions(text):assert is_ordinal_request(text)

@pytest.mark.parametrize('text',['да','давай','Сократи ответ','Подтверждаю задачу №2','Перенеси вторую задачу','создай второй бренд','Что значит второй бренд в тексте?'])
def test_unrelated_and_mutation_requests_not_intercepted(text):assert not is_ordinal_request(text)


def test_bare_ordinal_actual_route(case):
 original(case,LIST);seen,row=rewrite(case,'второй бренд');assert len(seen)==1 and 'ONLY-BETA' in seen[0]['followup']['target_answer']

@pytest.mark.parametrize('body',[
 'Alpha, Beta, Gamma', '- Alpha\n- Beta', '1. Alpha\n3. Beta', '2. Alpha\n3. Beta',
 '1. Alpha\n2. Beta\n2. Gamma','1. Alpha\n2) Beta','1. Alpha\n  1. Nested\n2. Beta',
 '1. Alpha\n2. Alpha','1. Alpha\n2. Beta\nOutro changes conditions',
 '1. Alpha\n2. **Beta**','01. Alpha\n02. Beta','1. Alpha\n2. Beta\n13. Gamma',
 '\n'.join(f'{i}. Name{i}' for i in range(1,14)), '1. Alpha',
 '1. Alpha\n2. Beta\n\nИсточники:\n1. Other list'])
def test_unprovable_list_clarifies_without_call_or_refresh(case,body):
 original(case,body,entities=['Alpha','Beta']);seen,row=rewrite(case)
 assert not seen and 'явной нумерацией' in row['text']


def test_full_answer_tail_after_2000_selectable(case):
 body='1. Alpha — '+'x'*2150+'\n2. Beta — ESSENTIAL-FINAL-CONDITION'
 original(case,body);seen,row=rewrite(case)
 assert len(seen)==1 and TAIL in seen[0]['followup']['target_answer'] and 'x'*100 not in seen[0]['followup']['target_answer']

@pytest.mark.parametrize('state',['pending','retry','unknown','blocked','failed','sending'])
def test_latest_unsent_does_not_fall_back_to_old_list(case,state):
 original(case,LIST,event='old');original(case,'1. New\n2. Latest',event='new',send=False)
 case.s.execute('UPDATE outbox SET state=? WHERE dedupe=?',(state,'clarify:new'))
 seen,row=rewrite(case);assert not seen and 'Не могу однозначно' in row['text']

@pytest.mark.parametrize('change',['revoke','content','legacy','unsupported'])
def test_unavailable_latest_never_old_memory(case,change):
 original(case,LIST,event='old');original(case,LIST,event='new',grounded=True)
 if change=='revoke':case.e.intelligence.index.revoke_source('source-a')
 elif change=='content':case.s.execute("UPDATE knowledge_chunks SET text='changed'")
 elif change=='legacy':case.s.execute('DELETE FROM answer_citation_turns')
 else:case.e.reply('trace','test_operator','/trace',source_id='telegram:1:10',channel='bot');case.d.flush()
 seen,row=rewrite(case);assert not seen and 'Не могу однозначно' in row['text']

@pytest.mark.parametrize('channel,source',[('business','business:conn:500:2'),('bot','telegram:777:2'),('business','business:other:500:2')])
def test_foreign_scope_no_fallback(case,channel,source):
 original(case,LIST);seen,row=rewrite(case,channel=channel,source=source);assert not seen and 'Не могу однозначно' in row['text']


def test_sources_remain_saved_authority_and_source_followup_priority(case):
 original(case,LIST,grounded=True)
 case.e.reply('cite','test_operator','Откуда ты это знаешь?',source_id='telegram:1:9',channel='bot');case.d.flush()
 seen,row=rewrite(case);assert len(seen)==1 and 'Source A' not in seen[0]['followup']['target_answer']
 assert 'Источники исходного ответа:\nSource A' in row['text']
 assert delivery_permitted(case.s,'GOLDWAY','test_operator',row['id'],NOW,case.c)
 receipt=case.s.rows('SELECT phase,citations FROM answer_citation_turns ORDER BY id')[-1]
 assert receipt['phase']=='recorded' and len(json.loads(receipt['citations']))==1
 case.d.flush();case.e.reply('after','test_operator','Откуда ты это знаешь?',source_id='telegram:1:8',channel='bot')
 assert 'Source A' in case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-citations:after'").fetchone()[0]

@pytest.mark.parametrize('extra',[dict(actions=[dict(op='update',task_id='t',status='completed')],intent='task_action'),dict(evidence_ids=['invented']),dict(knowledge_mode='grounded'),dict(dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id='t'))])
def test_model_cannot_mutate_tasks_or_create_citation_authority(case,extra):
 original(case,LIST);case.s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('GOLDWAY','t','Task','test_operator','step','criterion','confirmed','source',1)")
 before=case.s.rows('SELECT * FROM tasks')
 with pytest.raises(ValueError):rewrite(case,result=response(**extra),task_id='t')
 assert before==case.s.rows('SELECT * FROM tasks') and not case.s.rows("SELECT id FROM outbox WHERE dedupe='ordinal-followup:rewrite'")

@pytest.mark.parametrize('change',['source','newer','business'])
def test_final_snapshot_before_queue(case,change):
 original(case,LIST,grounded=True)
 def mutate(ctx):
  if change=='source':case.e.intelligence.index.revoke_source('source-a')
  elif change=='newer':
   from goldway.answer_citations import begin_answer
   begin_answer(case.s,case.c,'later',dict(sender='test_operator',channel='bot',source_id='telegram:1:99'),NOW)
  else:case.c['business']='OTHER'
 seen,row=rewrite(case,callback=mutate)
 assert len(seen)==1 and (row is None or 'Не могу однозначно' in row['text'])
 if change=='business':assert row is None

@pytest.mark.parametrize('change',['intelligence','business'])
def test_queue_drift_rolls_back(case,monkeypatch,change):
 original(case,LIST);queue=case.s.queue;before=case.s.rows('SELECT * FROM runtime')
 def drift(*a,**kw):
  value=queue(*a,**kw)
  if a[3]=='ordinal-followup:rewrite':
   if change=='business':case.c['business']='OTHER'
   else:case.e.intelligence=None
  return value
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises(ValueError):rewrite(case)
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='ordinal-followup:rewrite'") and case.s.rows('SELECT * FROM runtime')==before


def test_actual_offline_provider_contract_selected_item_only(case,monkeypatch):
 from goldway.model import JsonModel
 import httpx
 original(case,LIST,grounded=True);calls=[]
 class Client:
  def __init__(self,**kw):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def post(self,url,**kw):
   calls.append(kw['json']);return SimpleNamespace(raise_for_status=lambda:None,json=lambda:dict(choices=[dict(message=dict(content=json.dumps(response('Beta: ONLY-BETA. Дополнительные сведения неизвестны.'))),finish_reason='stop')],usage=dict(prompt_tokens=10,completion_tokens=5)))
 monkeypatch.setattr(httpx,'Client',Client)
 case.e.model=JsonModel(case.s,'GOLDWAY',dict(provider='openai',model='synthetic-offline',input_per_million=1,output_per_million=1,daily_budget=1,max_output_tokens=3000,max_context_chars=44000),'synthetic-unused',lambda:NOW)
 case.e.reply('json','test_operator','второй бренд',source_id='telegram:1:2',channel='bot');case.e.process('json')
 assert len(calls)==1 and len(case.s.rows('SELECT id FROM usage'))==1
 schema=calls[0]['response_format']['json_schema']['schema']['properties'];assert schema['actions']['maxItems']==0 and schema['evidence_ids']['maxItems']==0
 assert schema['knowledge_mode']['enum']==['not_needed'] and schema['dialogue_state']['properties']['task_id']=={'type':'null'}
 messages=calls[0]['messages'];assert 'missing details are unknown' in messages[0]['content']
 payload=messages[-1]['content'];assert 'ONLY-BETA' in payload and 'Alpha' not in payload and 'Gamma' not in payload


def test_shared_original_rewrite_still_uses_complete_target(case):
 original(case,LIST);seen=[]
 class Model:
  def propose(self,ctx):seen.append(ctx);return response('Shortened')
 case.e.model=Model();case.e.reply('short','test_operator','Сократи ответ',source_id='telegram:1:3',channel='bot');case.e.process('short')
 assert len(seen)==1 and seen[0]['followup']['target_answer']==LIST and not seen[0]['ordinal_only']
 assert case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-rewrite:short'").fetchone()[0]=='Shortened'
