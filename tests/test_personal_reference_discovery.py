"""I40 bounded own-reference metadata listing: actual Engine, synthetic only."""
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from datetime import datetime,timezone
import json,re
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher
import goldway.personal_reference as personal
import goldway.personal_reference_discovery as listing

NOW=1792174600
TEXT='Итоги обсуждения.\nКакой образец отправить клиенту?\nРешение пока не принимали.'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
_MISSING=object()


def result(actions=(),questions=_MISSING,intent='meeting',text='Нужно уточнить, какой образец отправить клиенту.'):
 value=dict(actions=list(actions),summary='Synthetic meeting draft',reply_text=text,intent=intent,
  dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 if questions is not _MISSING:value['meeting_question_lines']=questions
 return value


def make_case():
 s=Store(':memory:');clock=[NOW]
 config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,
  allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 engine=Engine(s,config,None,lambda:clock[0]);engine.intelligence.sources.refresh=lambda *a,**kw:{}
 return SimpleNamespace(s=s,e=engine,c=config,clock=clock)


@pytest.fixture
def case(monkeypatch):
 value=make_case()
 import builtins,socket,httpx
 def no_io(*a,**k):pytest.fail('unexpected external I/O')
 monkeypatch.setattr(builtins,'open',no_io);monkeypatch.setattr(socket,'socket',no_io);monkeypatch.setattr(httpx,'Client',no_io)
 yield value
 value.s.db.close()



def block(body='Личный образец ALPHA. Утверждение отправителя.',expiry=NOW+86400,title='Личная справка',url='https://example.invalid/reference',memo=None,revision=1):
 command='Сохрани справку для меня' if memo is None else f'Замени мою справку {memo}\nВерсия: {revision}'
 stamp=datetime.fromtimestamp(expiry,timezone.utc).isoformat()
 return f'{command}\nНазвание: {title}\nСсылка: {url}\nДействует до: {stamp}\nПодтверждаю: непроверенная личная справка\nТекст:\n{body}'


def send(case,text,*,sender='test_operator',channel='bot',event=None,at='current',source=None):
 case.clock[0]+=1;event=event or 'request-'+str(case.clock[0])
 class NoModel:
  def propose(self,*a):pytest.fail('personal route called model')
 case.e.model=NoModel();uid=case.c['employees'].get(sender,{}).get('telegram_user_id',999)
 source=source or (f'telegram:{uid}:10' if channel=='bot' else f'business:conn:{uid}:10')
 return case.e.reply(event,sender,text,source_id=source,channel=channel,message_at=case.clock[0] if at=='current' else at)


def rows(case):return case.s.rows('SELECT * FROM outbox ORDER BY id')


def memo_id(case):
 values=[re.search(r'R[0-9a-f]{32}',r['text']) for r in rows(case)]
 return next(x.group() for x in values if x)


def documents(case):return case.s.rows('SELECT * FROM knowledge_documents')


def search(case,query='образец',sender='test_operator'):
 from goldway.intelligence import reader_key
 return case.e.intelligence.index.search(query,reader_key(case.e.b,sender),now=case.clock[0],max_chars=6000)




class API:
 def __init__(self):self.calls=[]
 def call(self,method,**kw):
  if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
  assert method=='sendMessage';self.calls.append(kw);return dict(message_id=len(self.calls)+100)


def ready(c,channel='bot',count=1):
 c.channel=channel;c.api=API()
 c.s.execute("INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)",(c.e.b,'test_operator','1','1','conn',c.clock[0]))
 c.d=Dispatcher(c.s,c.c,c.api,lambda:c.clock[0],personal_list_guard=listing.make_delivery_guard(c.e))
 for i in range(count):send(c,block(title='Название '+str(i).zfill(2),body='PRIVATE BODY '+str(i)),channel=channel)
 c.d.flush();c.d.flush();c.api.calls.clear()
 def fail(*a,**kw):pytest.fail('unexpected model/search/refresh/context')
 c.e.intelligence.index.search=fail;c.e.intelligence.sources.refresh=fail;c.e._context=fail


def heads(c):
 return [json.loads(r['value']) for r in c.s.rows("SELECT value FROM runtime WHERE key LIKE 'personal-reference:v1:%head:%'")]


def listed(c,text='Покажи мои справки',event=None,**kw):
 send(c,text,channel=kw.pop('channel',c.channel),event=event,**kw)
 return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'personal-reference-list:%' ORDER BY id")


def update_head(c,head):
 prefix=personal._prefix(c.e,'test_operator')
 c.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(head),prefix+'head:'+head['memo']))


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_i34_admission_list_metadata_dispatch_and_exact_code_view(case,monkeypatch,channel):
 ready(case,channel,count=2)
 before=case.s.rows('SELECT * FROM knowledge_documents');tasks=case.s.rows('SELECT * FROM tasks')
 import sqlite3
 reads=[];writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ:
   reads.append((table,column))
   if (table,column) in [('knowledge_chunks','text'),('knowledge_documents','url')]:return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  def no_constructor(*a,**k):pytest.fail('catalogue constructed Index')
  m.setattr(KnowledgeIndex,'__init__',no_constructor)
  output=listed(case)
  assert len(output)==1
  assert listing.make_delivery_guard(case.e)(output[0]) is True
 case.s.db.set_authorizer(None)
 assert ('knowledge_chunks','text') not in reads and ('knowledge_documents','url') not in reads
 assert set(writes)<={'events','answer_citation_turns','outbox','runtime','intelligence_outbox_acl'}
 body=output[0]['text'];assert 'Ваши непроверенные личные справки' in body and 'Страница 1 из 1' in body
 assert 'PRIVATE BODY' not in body and 'http' not in body and 'Название 00' in body and 'версия 1' in body
 assert before==case.s.rows('SELECT * FROM knowledge_documents') and tasks==case.s.rows('SELECT * FROM tasks')
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(output[0]['id'],))[0]['evidence_ids'])
 assert len(refs)==2 and all(r.startswith('knowledge:') for r in refs)
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['text']==body
 memo=re.search('R[0-9a-f]{32}',body).group()
 send(case,'Покажи мою справку '+memo,channel=channel)
 assert any('PRIVATE BODY' in r['text'] for r in rows(case)[-2:])


def test_same_titles_explicit_pages_64_heads_no_hidden_subset(case):
 ready(case,count=64)
 first=listed(case)[0]['text'];assert 'Страница 1 из 8' in first and 'Далее: Покажи мои справки страница 2' in first
 last=listed(case,'Покажи мои справки страница 8')[-1]['text'];assert 'Страница 8 из 8' in last and 'Название 63' in last
 assert 'Название 00' not in last and 'Название 63' not in first
 assert len(set(re.findall('R[0-9a-f]{32}',last)))==8
 assert listing.personal._units(last)<=3500


def test_same_titles_distinguishable_by_exact_public_code(case):
 ready(case,count=0)
 send(case,block(title='Одинаковое',body='one'));send(case,block(title='Одинаковое',body='two'))
 output=listed(case)[0]['text'];assert output.count('Одинаковое')==2
 assert len(set(re.findall('R[0-9a-f]{32}',output)))==2


@pytest.mark.parametrize('mode',['empty','expired','revoked','extra_reader','missing_doc','watermark','future'])
def test_unavailable_current_references_omit_title_no_old_fallback(case,mode):
 ready(case,count=0 if mode=='empty' else 1)
 if mode!='empty':
  head=heads(case)[0];key=personal._doc_key(head['source'])
  if mode=='expired':case.clock[0]+=86401
  elif mode=='revoked':send(case,'Отзови мою справку '+head['memo']+' версия 1')
  elif mode=='extra_reader':case.s.execute('INSERT INTO knowledge_readers VALUES(?,?)',(key,'another-reader'))
  elif mode=='missing_doc':case.e.intelligence.index.revoke_source(head['source'])
  elif mode=='watermark':case.s.execute('INSERT INTO knowledge_source_revocations VALUES(?,?,1)',(head['source'],head['observed']))
  elif mode=='future':head['observed']=case.clock[0]+100;head['expiry']=case.clock[0]+1000;update_head(case,head)
 output=listed(case)[0]
 assert 'Сейчас нет доступных личных справок' in output['text'] and 'Название' not in output['text']
 case.d.flush();assert any('Сейчас нет доступных личных справок' in c['text'] for c in case.api.calls)


@pytest.mark.parametrize('text',['Покажи мои справки страница 0','Покажи мои справки страница 9','Покажи мои справки страница 01','Покажи мои справки\nещё','Покажи мои справки страница 2'])
def test_invalid_or_absent_page_never_defaults_or_calls_model(case,text):
 ready(case);assert listed(case,text)==[]
 assert rows(case)[-1]['text']==listing.NOTICE


@pytest.mark.parametrize('kwargs',[dict(sender='frost'),dict(channel='group',source='telegram:-1:10'),dict(source='telegram:2:10')])
def test_wrong_owner_group_and_actual_private_scope_never_list(case,kwargs):
 ready(case);assert listed(case,**kwargs)==[]


@pytest.mark.parametrize('change',['head','source','key','title','confirmed','observed','chunks','chunk_blob','reader_count','oversized_value','heads65'])
def test_malformed_or_overlimit_metadata_refuses_whole_catalogue(case,change):
 ready(case,count=2);head=heads(case)[0];key=personal._doc_key(head['source'])
 if change=='head':head['revision']='1';update_head(case,head)
 elif change=='source':head['source']='forged';update_head(case,head)
 elif change=='key':
  prefix=personal._prefix(case.e,'test_operator');case.s.execute('INSERT INTO runtime VALUES(?,?)',(prefix+'head:bad',json.dumps(head)))
 elif change=='title':case.s.execute('UPDATE knowledge_documents SET title=? WHERE id=?',('Ж'*241,key))
 elif change=='confirmed':case.s.execute('UPDATE knowledge_documents SET confirmed=1 WHERE id=?',(key,))
 elif change=='observed':case.s.execute('UPDATE knowledge_document_observations SET observed_at=observed_at+1 WHERE document_key=?',(key,))
 elif change=='chunks':case.s.execute('DELETE FROM knowledge_chunks WHERE document_key=?',(key,))
 elif change=='chunk_blob':case.s.execute('UPDATE knowledge_chunks SET position=? WHERE document_key=?',(b'bad',key))
 elif change=='reader_count':
  for i in range(3):case.s.execute('INSERT INTO knowledge_readers VALUES(?,?)',(key,'extra'+str(i)))
 elif change=='oversized_value':case.s.execute('UPDATE runtime SET value=? WHERE key=?',('x'*4097,personal._prefix(case.e,'test_operator')+'head:'+head['memo']))
 elif change=='heads65':
  prefix=personal._prefix(case.e,'test_operator')
  for i in range(63):
   memo='R'+format(i,'032x');h=dict(head,memo=memo,source=personal._source(case.e,'test_operator',memo));case.s.execute('INSERT INTO runtime VALUES(?,?)',(prefix+'head:'+memo,json.dumps(h)))
 assert listed(case)==[] and rows(case)[-1]['text']==listing.NOTICE


@pytest.mark.parametrize('change',['revoke','expire','replace','title','reader','chunk_id','head','policy','body','refs','receipt','destination','watermark','new_entry'])
def test_pending_catalogue_current_drift_cancels(case,change):
 ready(case);head=heads(case)[0];key=personal._doc_key(head['source']);row=listed(case)[0]
 if change=='revoke':send(case,'Отзови мою справку '+head['memo']+' версия 1')
 elif change=='expire':case.clock[0]+=86401
 elif change=='replace':send(case,block(title='Новое название',body='replacement',memo=head['memo'],revision=1))
 elif change=='title':case.s.execute("UPDATE knowledge_documents SET title='Edited' WHERE id=?",(key,))
 elif change=='reader':case.s.execute('DELETE FROM knowledge_readers WHERE document_key=?',(key,))
 elif change=='chunk_id':case.s.execute('UPDATE knowledge_chunks SET id=? WHERE document_key=?',('knowledge:'+'0'*32,key))
 elif change=='head':head['provenance_hash']='0'*64;update_head(case,head)
 elif change=='policy':case.c['review_mode']=False
 elif change=='body':case.s.execute("UPDATE outbox SET text='forged' WHERE id=?",(row['id'],))
 elif change=='refs':case.s.execute("UPDATE intelligence_outbox_acl SET evidence_ids='[]' WHERE outbox_id=?",(row['id'],))
 elif change=='receipt':case.s.execute('DELETE FROM runtime WHERE key=?',(listing._key(case.e.b,row['id']),))
 elif change=='destination':case.s.execute("UPDATE chats SET bot_chat='2'")
 elif change=='watermark':case.s.execute('INSERT INTO knowledge_source_revocations VALUES(?,?,1)',(head['source'],head['observed']))
 elif change=='new_entry':send(case,block(title='Another',body='new reference'))
 case.d.flush();assert not any('Ваши непроверенные личные справки' in c['text'] for c in case.api.calls)
 current=case.s.rows('SELECT state,error FROM outbox WHERE id=?',(row['id'],))[0]
 assert current==dict(state='cancelled',error=listing.CANCEL)


@pytest.mark.parametrize('change',['revoke','replace','title','expire','fallback','generic'])
def test_route_callback_revalidates_inside_preclaim_and_keeps_generic_acl(case,monkeypatch,change):
 ready(case,'business');head=heads(case)[0];row=listed(case)[0];actual=case.api.call;generic=[True]
 if change=='generic':monkeypatch.setattr('goldway.intelligence.delivery_permitted',lambda *a,**kw:generic[0])
 def call(method,**kw):
  if method=='getBusinessConnection':
   if change=='revoke':send(case,'Отзови мою справку '+head['memo']+' версия 1',channel='business')
   elif change=='replace':send(case,block(title='new',body='new',memo=head['memo'],revision=1),channel='business')
   elif change=='title':case.s.execute("UPDATE knowledge_documents SET title='changed'")
   elif change=='expire':case.clock[0]+=86401
   elif change=='generic':generic[0]=False
   elif change=='fallback':return dict(is_enabled=False)
  return actual(method,**kw)
 if change=='fallback':case.c['business_bot_fallback']=True
 case.api.call=call;case.d.flush()
 assert case.api.calls==[] and case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='cancelled'


def test_restart_persisted_receipt_defaultdeny_namespace_only_and_unknown_no_replay(case):
 ready(case);row=listed(case)[0]
 fresh=Engine(case.s,case.c,None,lambda:case.clock[0]);d=Dispatcher(case.s,case.c,case.api,lambda:case.clock[0],personal_list_guard=listing.make_delivery_guard(fresh));d.flush()
 assert len(case.api.calls)==1
 case.api.calls.clear();row=listed(case,event='second')[-1]
 Dispatcher(case.s,case.c,case.api,lambda:case.clock[0]).flush()
 assert case.api.calls==[] and case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='cancelled'
 case.s.queue(case.e.b,'test_operator','ordinary','ordinary',case.clock[0],preferred_transport='bot');Dispatcher(case.s,case.c,case.api,lambda:case.clock[0]).flush();assert case.api.calls[-1]['text']=='ordinary'
 row=listed(case,event='third')[-1];case.s.execute("UPDATE outbox SET state='sending' WHERE id=?",(row['id'],));d.recover();d.flush()
 assert case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='unknown'


@pytest.mark.parametrize('change',['queue','protect','config','intelligence','receipt','title'])
def test_atomic_output_metadata_and_captured_host_races(case,monkeypatch,change):
 ready(case)
 if change=='queue':
  actual=case.s.queue
  def queue(*a,**kw):
   result=actual(*a,**kw)
   if str(a[3]).startswith(listing.PREFIX):raise ValueError('synthetic queue error')
   return result
  monkeypatch.setattr(case.s,'queue',queue)
 else:
  actual=case.e.intelligence.protect_outbox
  def protect(identifier,refs):
   actual(identifier,refs)
   if change=='protect':raise ValueError('synthetic protection error')
   elif change=='config':
    import copy
    case.e.c=copy.deepcopy(case.e.c)
   elif change=='intelligence':
    import copy
    case.e.intelligence=copy.copy(case.e.intelligence)
   elif change=='title':case.s.execute("UPDATE knowledge_documents SET title='changed'")
   elif change=='receipt':case.s.execute("UPDATE outbox SET text='changed' WHERE id=?",(identifier,))
  monkeypatch.setattr(case.e.intelligence,'protect_outbox',protect)
 assert listed(case)==[]
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'personal-reference-list:%'")
 assert not case.s.rows('SELECT a.* FROM intelligence_outbox_acl a LEFT JOIN outbox o ON o.id=a.outbox_id WHERE o.id IS NULL')


def test_empty_output_still_needs_current_host_proof(case):
 ready(case,count=0);row=listed(case)[0]
 assert json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],))[0]['evidence_ids'])==[]
 case.c['admins']=['frost'];case.d.flush()
 assert case.api.calls==[] and case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='cancelled'


def test_actual_cumulative_reader_caps_two_build_passes_and_one_each_guard(case,monkeypatch):
 ready(case,count=64)
 actual=listing._Reader;readers=[]
 class Counted(actual):
  def __init__(self,*a,**kw):super().__init__(*a,**kw);readers.append(self)
 monkeypatch.setattr(listing,'_Reader',Counted)
 assert len(listed(case))==1 and len(readers)==1
 assert all(r.bytes<=4*1024*1024 and r.rows<=8192 and r.queries<=2048 for r in readers)
 readers.clear();case.d.flush();assert len(case.api.calls)==1 and len(readers)==2
 assert all(r.bytes<=4*1024*1024 and r.rows<=8192 and r.queries<=2048 for r in readers)



def test_intelligence_disabled_factory_is_safe_ordinary_routes_stay_available(case):
 ready(case);row=listed(case)[0];case.e.intelligence=None
 guard=listing.make_delivery_guard(case.e)
 d=Dispatcher(case.s,case.c,case.api,lambda:case.clock[0],personal_list_guard=guard)
 d.flush();assert case.api.calls==[] and case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='cancelled'
 assert listed(case,event='disabled')==[dict(row,state='cancelled',error=listing.CANCEL)]
 case.s.queue(case.e.b,'test_operator','ordinary','ordinary-no-intelligence',case.clock[0],preferred_transport='bot')
 d.flush();assert case.api.calls[-1]['text']=='ordinary'


def test_final_escaped_title_page_overflow_refuses_whole_catalogue(case):
 ready(case,count=0)
 for i in range(8):send(case,block(title='<'*120,body='body'+str(i)))
 assert listed(case)==[] and rows(case)[-1]['text']==listing.NOTICE
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'personal-reference-list:%'")
