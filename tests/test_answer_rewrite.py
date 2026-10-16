"""New engine rewrite boundary only, actual synthetic dispatcher delivery."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
from goldway.answer_rewrite import is_rewrite_request
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

def original(c,body=None,event='original',grounded=False,send=True,channel='bot'):
 body=body or 'A'*2900+TAIL
 if grounded:c.e.intelligence.index.upsert(dict(source_id='source-a',document_id='doc',title='Source A',url='https://example.invalid/A',text='ALPHA source',readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW-20,permission_expires_at=NOW+1000,confirmed=True))
 class Model:
  def propose(self,ctx):
   r=response(body,intent='conversation')
   if grounded:r.update(knowledge_mode='grounded',evidence_ids=[ctx['knowledge']['evidence'][0]['id']])
   return r
 c.e.model=Model();source='telegram:1:1' if channel=='bot' else 'business:conn:500:1'
 c.e.reply(event,'test_operator','Расскажи об ALPHA' if grounded else 'Напиши черновик письма',source_id=source,channel=channel);c.e.process(event)
 if send:c.d.flush()
 return c.s.execute('SELECT text FROM outbox WHERE dedupe=?',('clarify:'+event,)).fetchone()[0]

def rewrite(c,text='Сделай покороче',event='rewrite',result=None,channel='bot',source=None,task_id=None,callback=None):
 seen=[]
 class Model:
  def propose(self,ctx):
   seen.append(ctx)
   if callback:callback(ctx)
   return result or response()
 c.e.model=Model()
 def fail(*a,**kw):pytest.fail('rewrite used old retrieval/context path')
 c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e._context=fail
 c.e.reply(event,'test_operator',text,source_id=source or ('telegram:1:2' if channel=='bot' else 'business:conn:500:2'),channel=channel,task_id=task_id);c.e.process(event)
 return seen,c.s.execute('SELECT * FROM outbox WHERE dedupe=?',('answer-rewrite:'+event,)).fetchone()

def test_complete_target_essential_tail_and_one_call(case):
 parent=original(case,'A'*2970+TAIL);seen,row=rewrite(case)
 assert len(seen)==1 and seen[0]['followup']['target_answer']==parent
 assert TAIL in seen[0]['followup']['target_answer'][2000:] and TAIL in row['text']
 assert response_schema(seen[0])['properties']['actions']['maxItems']==0
 assert not case.s.rows('SELECT id FROM tasks') and not case.s.rows('SELECT id FROM usage')

@pytest.mark.parametrize('text',['покороче','Сократи ответ','Подробнее','Давай подробнее','Продолжай'])
def test_exact_existing_patterns(case,text):
 original(case);seen,row=rewrite(case,text);assert len(seen)==1 and TAIL in row['text']

@pytest.mark.parametrize('text',['да','давай','подтверждаю','Подтверждаю задачу №1','Сократи задачу №1','Перенеси срок задачи №1','Расскажи о втором бренде','текст: сделай покороче'])
def test_no_task_confirmation_edit_or_unproven_ordinal_interception(text):assert not is_rewrite_request(text)

@pytest.mark.parametrize('state',['pending','retry','unknown','blocked','failed','sending'])
def test_unsent_latest_no_fallback(case,state):
 original(case,event='old');original(case,event='new',send=False);case.s.execute('UPDATE outbox SET state=? WHERE dedupe=?',(state,'clarify:new'))
 seen,row=rewrite(case);assert not seen and 'Не могу однозначно' in row['text']

def test_unsupported_latest_not_skipped(case):
 original(case);case.e.reply('trace','test_operator','/trace',source_id='telegram:1:9',channel='bot');case.d.flush()
 seen,row=rewrite(case);assert not seen and 'Не могу однозначно' in row['text']

def test_no_receipt_asks_without_model(case):
 seen,row=rewrite(case);assert not seen and 'Пришлите нужный текст' in row['text']

@pytest.mark.parametrize('channel,source',[('business','business:conn:500:2'),('bot','telegram:777:2'),('business','business:other:500:2')])
def test_other_scope(case,channel,source):
 original(case);seen,row=rewrite(case,channel=channel,source=source);assert not seen and 'Не могу однозначно' in row['text']

def test_actual_business_delivery(case):
 original(case,channel='business');seen,row=rewrite(case,channel='business');assert len(seen)==1 and TAIL in row['text']

@pytest.mark.parametrize('change',['revoke','content','outbox'])
def test_stale_source_or_answer_not_sent_to_model(case,change):
 original(case,'ALPHA '+TAIL,grounded=True)
 if change=='revoke':case.e.intelligence.index.revoke_source('source-a')
 elif change=='content':case.s.execute("UPDATE knowledge_chunks SET text='changed'")
 else:case.s.execute("UPDATE outbox SET text='changed' WHERE dedupe='clarify:original'")
 seen,row=rewrite(case);assert not seen and 'Не могу однозначно' in row['text']

def test_saved_authority_footer_full_target_and_source_followup(case):
 parent=original(case,'ALPHA '+TAIL,grounded=True)
 case.e.reply('cite','test_operator','Откуда ты это знаешь?',source_id='telegram:1:8',channel='bot');case.d.flush()
 seen,row=rewrite(case)
 assert seen[0]['followup']['target_answer']==parent and 'Источники:\n' in parent
 assert 'Источники исходного ответа:\nSource A — https://example.invalid/A' in row['text']
 assert delivery_permitted(case.s,'GOLDWAY','test_operator',row['id'],NOW,case.c)
 receipt=case.s.rows('SELECT phase,citations FROM answer_citation_turns ORDER BY id')[-1];assert receipt['phase']=='recorded' and len(json.loads(receipt['citations']))==1
 case.d.flush();case.e.reply('source-after','test_operator','Откуда ты это знаешь?',source_id='telegram:1:3',channel='bot')
 assert 'Source A —' in case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-citations:source-after'").fetchone()[0]

def test_model_action_rejected_before_task_or_rewrite_outbox_write(case):
 original(case)
 case.s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('GOLDWAY','t','Task','test_operator','step','criterion','confirmed','source',1)")
 before=case.s.rows('SELECT * FROM tasks')
 with pytest.raises(ValueError,match='Conversation intent cannot modify tasks'):rewrite(case,result=response(actions=[dict(op='update',task_id='t',status='completed')],intent='task_action'),task_id='t')
 assert before==case.s.rows('SELECT * FROM tasks') and not case.s.rows("SELECT id FROM outbox WHERE dedupe='answer-rewrite:rewrite'")

@pytest.mark.parametrize('change',['source','newer','business'])
def test_final_guard_after_model(case,change):
 original(case,'ALPHA '+TAIL,grounded=True)
 def mutate(ctx):
  if change=='source':case.e.intelligence.index.revoke_source('source-a')
  elif change=='newer':
   from goldway.answer_citations import begin_answer
   begin_answer(case.s,case.c,'later',dict(sender='test_operator',channel='bot',source_id='telegram:1:99'),NOW)
  else:case.c['business']='OTHER'
 seen,row=rewrite(case,callback=mutate)
 assert len(seen)==1 and (row is None or ('Не могу однозначно' in row['text'] and TAIL not in row['text']))
 if change=='business':assert row is None

def test_actual_confirmation_stays_on_existing_path(case):
 case.s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('GOLDWAY','proposal','Synthetic proposal',NULL,'step','criterion','proposed','source',1)")
 seen=[]
 class Ordinary:
  def propose(self,ctx):
   seen.append(ctx);return response('Подтверждено',actions=[dict(op='update',task_id='proposal',status='confirmed',evidence='Подтверждаю задачу №1')],intent='task_action')
 case.e.model=Ordinary();case.e.reply('confirm','test_operator','Подтверждаю задачу №1',task_id='proposal',source_id='telegram:1:4',channel='bot');case.e.process('confirm')
 assert len(seen)==1 and not seen[0].get('rewrite_only') and case.s.execute("SELECT status FROM tasks WHERE id='proposal'").fetchone()[0]=='confirmed'

@pytest.mark.parametrize('change',['intelligence','business'])
def test_queue_host_drift_rolls_back_success(case,monkeypatch,change):
 original(case);queue=case.s.queue;before=case.s.rows('SELECT * FROM runtime')
 def drift(*a,**kw):
  value=queue(*a,**kw)
  if a[3]=='answer-rewrite:rewrite':
   if change=='business':case.c['business']='OTHER'
   else:case.e.intelligence=None
  return value
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises(ValueError,match='answer_rewrite_unavailable'):rewrite(case)
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='answer-rewrite:rewrite'")
 assert case.s.rows('SELECT * FROM runtime')==before

@pytest.mark.parametrize('extra',[
 {'knowledge_mode':'not_found'}, {'evidence_ids':['invented']},
 {'dialogue_state':dict(topic='',entities=[],pending_offer=dict(kind='confirm_task',text='Confirm'),task_id=None)},
 {'dialogue_state':dict(topic='',entities=[],pending_offer=None,task_id='private-task')},
 {'reply_text':'x'*3001}, {'reply_text':'Источники: invented'}])
def test_untrusted_result_rejected_before_success_write(case,extra):
 original(case)
 with pytest.raises(ValueError):rewrite(case,result=response(**extra))
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='answer-rewrite:rewrite'")


def test_actual_jsonmodel_request_schema_uses_rewrite_contract(case,monkeypatch):
 from goldway.model import JsonModel
 import httpx
 original(case,'ALPHA '+TAIL,grounded=True)
 calls=[]
 class Client:
  def __init__(self,**kw):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def post(self,url,**kw):
   calls.append(kw['json'])
   return SimpleNamespace(raise_for_status=lambda:None,json=lambda:dict(choices=[dict(message=dict(content=json.dumps(response())),finish_reason='stop')],usage=dict(prompt_tokens=10,completion_tokens=5)))
 monkeypatch.setattr(httpx,'Client',Client)
 config=dict(provider='openai',model='synthetic-offline',input_per_million=1,output_per_million=1,daily_budget=1,max_output_tokens=3000,max_context_chars=44000)
 case.e.model=JsonModel(case.s,'GOLDWAY',config,'synthetic-unused',lambda:NOW)
 case.e.reply('json','test_operator','Сократи ответ',source_id='telegram:1:2',channel='bot');case.e.process('json')
 assert len(calls)==1
 schema=calls[0]['response_format']['json_schema']['schema'];props=schema['properties']
 assert props['actions']['maxItems']==0 and props['knowledge_mode']['enum']==['not_needed']
 assert props['reply_text']['maxLength']<3000 and props['evidence_ids']['maxItems']==0
 state=props['dialogue_state']['properties'];assert state['task_id']=={'type':'null'}
 assert 'confirm_task' not in state['pending_offer']['anyOf'][1]['properties']['kind']['enum']
 messages=calls[0]['messages'];assert TAIL in messages[-1]['content'] and 'transform ONLY' in messages[0]['content']
 assert len(case.s.rows('SELECT id FROM usage'))==1
