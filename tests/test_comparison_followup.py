"""New comparison Engine/receipt/provider boundary only; synthetic dispatch."""
import json
import sqlite3
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key, delivery_permitted
from goldway.comparison_followup import is_comparison_request, _NOTICE
from goldway.model import response_schema
MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600;TAIL='ESSENTIAL-FINAL-CONDITION'

@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:NOW);e.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',NOW))
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
 c.e.model=Model();source='telegram:1:1' if channel=='bot' else 'business:conn:1:1'
 c.e.reply(event,'test_operator','Расскажи об ALPHA' if grounded else 'Напиши черновик письма',source_id=source,channel=channel);c.e.process(event)
 if send:c.d.flush()
 return c.s.execute('SELECT text FROM outbox WHERE dedupe=?',('clarify:'+event,)).fetchone()[0]

def compare(c,text='Сравни пункты 1 и 2',event='rewrite',result=None,channel='bot',source=None,task_id=None,callback=None):
 seen=[]
 class Model:
  def propose(self,ctx):
   seen.append(ctx)
   if callback:callback(ctx)
   return result or response()
 c.e.model=Model()
 def fail(*a,**kw):pytest.fail('comparison reached memory/refresh/context/retrieval')
 c.e.intelligence.memory.get=fail;c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e._context=fail
 c.e.reply(event,'test_operator',text,source_id=source or ('telegram:1:2' if channel=='bot' else 'business:conn:1:2'),channel=channel,task_id=task_id);c.e.process(event)
 return seen,c.s.execute('SELECT * FROM outbox WHERE dedupe=?',('comparison-followup:'+event,)).fetchone()


LIST='Бренды:\n1. Alpha — FIRST stated condition.\n  FIRST retained limitation.\n2. Beta — SECOND stated condition.\n  SECOND retained limitation.\n3. Gamma — THIRD MUST NOT ENTER COMPARISON.'


@pytest.mark.parametrize('text',['Сравни пункты 1 и 2','Сравни первый и второй пункты','  СРАВНИ   ПУНКТЫ 1 И 2! '])
@pytest.mark.parametrize('channel',['bot','business'])
def test_exact_runtime_pair_uses_complete_selected_blocks_only(case,text,channel):
 original(case,LIST,channel=channel,entities=['Gamma','invented'])
 seen,row=compare(case,text,channel=channel)
 assert len(seen)==1
 ctx=seen[0];hint=ctx['followup']
 assert ctx['rewrite_only'] and ctx['comparison_only'] and ctx['ordinal_only'] is False
 assert hint['kind']=='compare_numbered_items' and hint['selected_numbers']==[1,2]
 assert hint['selected_names']==['Alpha','Beta'] and hint['numbered_count']==3
 assert 'FIRST retained limitation' in hint['target_answer'] and 'SECOND retained limitation' in hint['target_answer']
 assert 'Gamma' not in hint['target_answer'] and 'THIRD' not in hint['target_answer']
 assert ctx['tasks']==[] and ctx['employees']=={} and ctx['capabilities']['task_mutations'] is False
 assert row['task_id'] is None and row['recipient']=='test_operator' and row['preferred_transport']==channel
 assert row['text'].startswith('Кратко')
 assert not case.s.rows('SELECT * FROM tasks')
 case.d.flush()
 assert case.s.execute('SELECT state,transport FROM outbox WHERE id=?',(row['id'],)).fetchone()['state']=='sent'


@pytest.mark.parametrize('text',['Сравни их','Сравни первый и второй','Сравни пункты 2 и 1','Сравни пункты 1 и 1',
 'Сравни пункты 01 и 2','Сравни пункты 1 и 3','Сравни пункты 1 и 2 и создай задачу','Не сравни пункты 1 и 2',
 '«Сравни пункты 1 и 2»','Что значит сравни пункты 1 и 2?','Сравни первый и второй бренды','Пожалуйста, сравни пункты 1 и 2'])
def test_no_substrings_other_pairs_or_implicit_pronouns(text):
 assert not is_comparison_request(text)


@pytest.mark.parametrize('body',['1. Alpha — only one', 'Alpha and Beta without numbering',
 '1. Alpha — first\n1. Beta — duplicate', '1. Alpha — first\n3. Beta — gap',
 '1. Alpha — first\n2) Beta — mixed', '1. Alpha — first\n2. Alpha — duplicate name',
 '1. Alpha — first\n  - nested\n2. Beta — second', '1. Alpha — first\n2. Beta — second\nUnbound outro',
 '1. Alpha — first\n2. Beta — second\n\nИсточники:\n1. Malicious footer item',
 '\n'.join(str(i)+'. Item'+str(i)+' — body' for i in range(1,14))])
def test_ambiguous_list_clarifies_without_model_or_older_fallback(case,body):
 original(case,LIST,event='older')
 original(case,body,event='latest')
 seen,row=compare(case)
 assert not seen and row['text']==_NOTICE
 assert not case.s.rows('SELECT * FROM tasks')


@pytest.mark.parametrize('state',['pending','unknown','sending','blocked','retry','cancelled'])
def test_unsent_latest_is_barrier(case,state):
 original(case,LIST,event='older')
 original(case,LIST,event='latest',send=False)
 case.s.execute('UPDATE outbox SET state=? WHERE dedupe=?',(state,'clarify:latest'))
 seen,row=compare(case)
 assert not seen and row['text']==_NOTICE


def test_accepted_pending_turn_is_barrier_even_if_old_pair_delivered(case):
 original(case,LIST)
 case.e.reply('newer','test_operator','Новый вопрос',source_id='telegram:1:15',channel='bot')
 seen,row=compare(case)
 assert not seen and row['text']==_NOTICE


def test_unavailable_turn_not_skipped(case):
 original(case,LIST)
 case.e.reply('unsupported','test_operator','Подготовь ТЗ дизайнеру',source_id='telegram:1:15',channel='bot')
 # Deterministic command leaves unsupported pending provenance barrier.
 seen,row=compare(case)
 assert not seen and row['text']==_NOTICE


def test_no_registered_receipt_does_not_import_memory_or_text(case):
 seen,row=compare(case)
 assert not seen and row['text']==_NOTICE


@pytest.mark.parametrize('change',['different_channel','different_connection','different_chat','different_sender'])
def test_exact_private_scope_no_fallback(case,change):
 original(case,LIST)
 if change=='different_channel':seen,row=compare(case,channel='business')
 elif change=='different_connection':seen,row=compare(case,channel='business',source='business:other:1:2')
 elif change=='different_chat':seen,row=compare(case,source='telegram:2:2')
 else:
  # Real Engine sender gate accepts a registered other member; no their receipt.
  case.e.model=type('Fail',(),{'propose':lambda *a:pytest.fail('other sender used model')})()
  case.e.reply('rewrite','frost','Сравни пункты 1 и 2',source_id='telegram:2:2',channel='bot');case.e.process('rewrite')
  row=case.s.execute("SELECT * FROM outbox WHERE dedupe='comparison-followup:rewrite'").fetchone();seen=[]
 assert not seen and row['text']==_NOTICE


@pytest.mark.parametrize('change',['revoke','version','outbox_changed','not_sent','future_document'])
def test_current_source_and_original_content_guard_before_model(case,change):
 original(case,LIST,grounded=True)
 if change=='revoke':case.e.intelligence.index.revoke_source('source-a',observed_at=NOW)
 elif change=='version':case.s.execute("UPDATE knowledge_chunks SET text='Changed version'")
 elif change=='outbox_changed':case.s.execute("UPDATE outbox SET text='Changed delivered answer'")
 elif change=='not_sent':case.s.execute("UPDATE outbox SET state='unknown'")
 else:case.s.execute('UPDATE knowledge_documents SET observed_at=?',(NOW+1,))
 seen,row=compare(case)
 assert not seen and row['text']==_NOTICE


def test_citation_footer_authority_derived_from_original_receipt_only(case):
 original(case,LIST,grounded=True)
 seen,row=compare(case,result=response('Общее: обе записи имеют условия. Различия: FIRST и SECOND. Не указано: цена.'))
 assert len(seen)==1 and 'Источники исходного ответа:' in row['text']
 assert 'https://example.invalid/A' in row['text']
 case.d.flush()
 # A future source-followup can resolve this derivative's own actual receipt.
 case.e.reply('source','test_operator','Покажи источники',source_id='telegram:1:3',channel='bot')
 out=case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-citations:source'").fetchone()
 assert out is not None and 'https://example.invalid/A' in out['text']


@pytest.mark.parametrize('mutation',[dict(actions=[dict(op='create')]),dict(intent='task_action'),
 dict(knowledge_mode='grounded'),dict(evidence_ids=['invented']),dict(reply_text='Источники: invented'),
 dict(reply_text='Answer\n\nИсточники исходного ответа:\nInvented'),
 dict(dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id='invented')),
 dict(dialogue_state=dict(topic='',entities=[],pending_offer=dict(kind='confirm_task',text='Confirm'),task_id=None))])
def test_generated_authority_or_source_invention_rejected(case,mutation):
 original(case,LIST)
 with pytest.raises(ValueError):compare(case,result=response(**mutation))
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='comparison-followup:rewrite'")
 assert not case.s.rows('SELECT * FROM tasks')


@pytest.mark.parametrize('change',['source','new_inbound','parent_text','intel_business'])
def test_fresh_final_snapshot_blocks_changed_parent(case,change):
 original(case,LIST,grounded=True)
 def mutate(ctx):
  if change=='source':case.e.intelligence.index.revoke_source('source-a',observed_at=NOW)
  elif change=='new_inbound':case.e.reply('late','test_operator','Сравни первый и второй пункты',source_id='telegram:1:99',channel='bot')
  elif change=='parent_text':case.s.execute("UPDATE outbox SET text='Changed' WHERE dedupe='clarify:original'")
  else:case.e.intelligence.b='OTHER'
 seen,row=compare(case,callback=mutate)
 assert len(seen)==1
 if change=='intel_business':assert row is None
 else:assert row['text']==_NOTICE
 assert not case.s.rows('SELECT * FROM tasks')


def test_dedupe_and_reprocess_do_not_add_model_or_synthetic_send(case):
 original(case,LIST)
 seen,row=compare(case);case.d.flush()
 count=len(case.s.rows('SELECT * FROM outbox'))
 assert case.e.reply('rewrite','test_operator','Сравни пункты 1 и 2',source_id='telegram:1:2',channel='bot') is False
 case.e.process('rewrite');case.d.flush()
 assert len(seen)==1 and len(case.s.rows('SELECT * FROM outbox'))==count


def test_queue_guard_drift_rolls_back_derivative(case,monkeypatch):
 original(case,LIST)
 original_runtime=case.s.rows('SELECT * FROM runtime')
 real=case.s.queue
 def drift(*a,**kw):
  out=real(*a,**kw)
  if a[3]=='comparison-followup:rewrite':case.e.intelligence.b='OTHER'
  return out
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises(ValueError):compare(case)
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='comparison-followup:rewrite'")
 assert case.s.rows('SELECT * FROM runtime')==original_runtime


def test_projected_pair_budget_denies_without_truncating_conditions(case):
 # A long shared preamble is retained in both literal item projections, so
 # expanded projection exceeds3000 even though complete parent fits.
 body='A'*240+':\n1. Alpha — '+'a'*1300+'\n2. Beta — '+'b'*1300
 assert len(body)<3000
 original(case,body)
 seen,row=compare(case)
 assert not seen and row['text']==_NOTICE


def test_utf16_output_guard_rejects_oversize_no_partial_queue(case):
 original(case,LIST)
 with pytest.raises(ValueError):compare(case,result=response('😀'*1800))
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='comparison-followup:rewrite'")


def test_actual_offline_provider_contract_two_blocks_readonly_budget_once(case,monkeypatch):
 from goldway.model import JsonModel
 import httpx
 original(case,LIST,grounded=True)
 calls=[]
 class Client:
  def __init__(self,**kw):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def post(self,url,**kw):
   calls.append(kw['json'])
   return SimpleNamespace(raise_for_status=lambda:None,json=lambda:dict(choices=[dict(message=dict(content=json.dumps(response('Общее: есть условия. Различия: FIRST и SECOND. Цена не указана.'))),finish_reason='stop')],usage=dict(prompt_tokens=10,completion_tokens=5)))
 monkeypatch.setattr(httpx,'Client',Client)
 case.e.model=JsonModel(case.s,'GOLDWAY',dict(provider='openai',model='synthetic-offline',input_per_million=1,
  output_per_million=1,daily_budget=1,max_output_tokens=3000,max_context_chars=44000),'synthetic-unused',lambda:NOW)
 # Existing ordinary retrieval/context must remain untouched by comparison.
 def forbidden(*a,**k):pytest.fail('comparison used ordinary context or refresh')
 case.e._context=forbidden;case.e.intelligence.sources.refresh=forbidden
 case.e.reply('json','test_operator','Сравни пункты 1 и 2',source_id='telegram:1:2',channel='bot');case.e.process('json')
 assert len(calls)==1 and len(case.s.rows('SELECT id FROM usage'))==1
 schema=calls[0]['response_format']['json_schema']['schema']['properties']
 assert schema['actions']['maxItems']==0 and schema['evidence_ids']['maxItems']==0
 assert schema['knowledge_mode']['enum']==['not_needed'] and schema['dialogue_state']['properties']['task_id']=={'type':'null'}
 system=calls[0]['messages'][0]['content'];payload=calls[0]['messages'][-1]['content']
 assert 'comparison_only=true' in system and 'Do not infer missing' in system
 assert 'FIRST retained limitation' in payload and 'SECOND retained limitation' in payload and 'THIRD' not in payload
 assert not case.s.rows('SELECT * FROM tasks')


def test_current_staff_same_private_scope_comparison_is_readonly(case):
 case.s.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?)',('GOLDWAY','frost','2'))
 class Original:
  def propose(self,ctx):return response(LIST,intent='conversation')
 case.e.model=Original();case.e.reply('staff-parent','frost','Напиши синтетический список',source_id='telegram:2:1',channel='bot')
 case.e.process('staff-parent');case.d.flush()
 seen=[]
 class Compare:
  def propose(self,ctx):seen.append(ctx);return response('Общее и различия только из FIRST и SECOND.')
 case.e.model=Compare()
 def forbidden(*a,**k):pytest.fail('staff comparison used ordinary context/refresh')
 case.e._context=forbidden;case.e.intelligence.sources.refresh=forbidden
 case.e.reply('staff-compare','frost','Сравни пункты 1 и 2',source_id='telegram:2:2',channel='bot');case.e.process('staff-compare')
 assert len(seen)==1 and seen[0]['tasks']==[] and seen[0]['comparison_only']
 row=case.s.execute("SELECT * FROM outbox WHERE dedupe='comparison-followup:staff-compare'").fetchone()
 assert row['recipient']=='frost' and row['task_id'] is None and not case.s.rows('SELECT * FROM tasks')


def test_membership_removed_after_accept_before_processing_is_not_authorized(case):
 original(case,LIST)
 case.e.reply('blocked','test_operator','Сравни пункты 1 и 2',source_id='telegram:1:2',channel='bot')
 case.c['employees'].pop('test_operator')
 class Fail:
  def propose(self,*a):pytest.fail('removed member used model')
 case.e.model=Fail();case.e.process('blocked')
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='comparison-followup:blocked'")
 assert case.s.rows("SELECT state FROM events WHERE id='blocked'")==[dict(state='done')]


def test_group_channel_cannot_receive_comparison(case):
 original(case,LIST)
 class Fail:
  def propose(self,*a):pytest.fail('group used comparison model')
 case.e.model=Fail()
 case.e.reply('group','test_operator','Сравни пункты 1 и 2',source_id='telegram:-10:2',channel='group')
 case.e.process('group')
 assert not case.s.rows("SELECT id FROM outbox WHERE dedupe='comparison-followup:group'")
