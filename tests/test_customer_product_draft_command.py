"""Actual private supplied-product scenario through accepted core, no old suites."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.customer_product_draft_command as command

MEMBERS=['test_operator','frost','alen','nastya','dasha_it'];NOW=1792174600


@pytest.fixture
def case(monkeypatch):
 s=Store(':memory:');clock=[NOW]
 c=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,
  sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 e=Engine(s,c,None,lambda:clock[0]);s.execute('INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',(c['business'],'test_operator','1','1','conn',NOW))
 class API:
  def __init__(self):self.calls=[]
  def call(self,method,**kw):
   if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
   assert method=='sendMessage';self.calls.append(kw);return dict(message_id=100+len(self.calls))
 api=API();d=Dispatcher(s,c,api,lambda:clock[0])
 def fail(*a,**kw):pytest.fail('unexpected model/source/context/constructor/network call')
 e.model=SimpleNamespace(propose=fail);e._context=fail;e.intelligence.sources.refresh=fail;e.intelligence.index.search=fail;e.intelligence.memory.get=fail
 import builtins,socket,httpx
 monkeypatch.setattr(builtins,'open',fail);monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail)
 yield SimpleNamespace(s=s,c=c,e=e,d=d,api=api,clock=clock)
 s.db.close()


BLOCK=('Подготовь ответ о товаре\nНазвание для сценария: Образец <A>\nНаличие: да\nЦена: 120.50\nВалюта: RUB\n'
 'Доставка: По указанному сценарию <два дня> & проверка\nДанные действуют с: 2026-10-16T09:00:00+03:00\n'
 'Данные действуют до: 2026-10-17T09:00:00+03:00\nСрез сценария: 2026-10-16T12:00:00+03:00\n'
 'Актуальность заявлена мной: да\nОснование: значения из этого сообщения, без независимой проверки')


def submit(c,text=BLOCK,event='scenario',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0])
 c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],'product-reply-preview:'+command._hash(event))).fetchone()


def spy_core(monkeypatch):
 actual=command.core.product_draft;calls=[];results=[]
 def spy(**kw):calls.append(kw);value=actual(**kw);results.append(value);return value
 monkeypatch.setattr(command.core,'product_draft',spy)
 return calls,results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_immutable_core_positive_missing_and_own_dispatch(case,monkeypatch,channel):
 calls,results=spy_core(monkeypatch)
 snapshots={table:case.s.rows('SELECT * FROM '+table) for table in ('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')}
 import sqlite3
 reads=[];writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ:
   reads.append(table)
   if table in ('tasks','sources','task_sources','knowledge_documents','knowledge_chunks','knowledge_readers','runtime','usage','task_status_history'):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('new command constructed Index'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==1 and len(results)==1
 supplied=calls[0];evidence=supplied['evidence']
 assert type(evidence) is command.core.ProductEvidence and type(evidence.window) is command.core.Window
 assert type(evidence.conversation) is command.core.Conversation
 assert evidence.price==Decimal('120.50') and evidence.available is True and evidence.currency=='RUB' and evidence.confirmed is True
 assert results[0].draft_only is True and results[0].missing==()
 assert 'Наличие: в наличии' in row['text'] and 'Цена: 120.50 RUB' in row['text']
 assert 'Образец &lt;A&gt;' in row['text'] and '&lt;два дня&gt; &amp; проверка' in row['text']
 assert 'непроверенный сценарий' in row['text'] and 'независимо не проверены' in row['text'] and 'Клиенту не отправлен' in row['text']
 assert supplied['product_id'] not in row['text'] and evidence.source_id not in row['text']
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('old,new,missing',[
 ('Наличие: да','Наличие: нет',()),
 ('Наличие: да','Наличие: неизвестно',('availability',)),
 ('Цена: 120.50','Цена: неизвестно',('price',)),
 ('Валюта: RUB','Валюта: неизвестно',('price',)),
 ('Доставка: По указанному сценарию <два дня> & проверка','Доставка: неизвестно',('delivery',)),
 ('Актуальность заявлена мной: да','Актуальность заявлена мной: нет',('availability','price','delivery')),
 ('Актуальность заявлена мной: да','Актуальность заявлена мной: неизвестно',('availability','price','delivery'))])
def test_actual_core_missing_tuple_and_unconfirmed_values_drive_output(case,monkeypatch,old,new,missing):
 calls,results=spy_core(monkeypatch);row=submit(case,BLOCK.replace(old,new))
 assert len(calls)==1 and results[0].missing==missing
 for key in missing:assert command._MISSING[key] in row['text']
 if missing==('availability','price','delivery'):
  assert row['text'].count('уточняется')==3 and 'Цена: 120.50 RUB' not in row['text']
  assert calls[0]['evidence'].confirmed is False
 if new=='Наличие: нет':assert 'Наличие: нет в наличии' in row['text']


@pytest.mark.parametrize('price',['0','0.00','0,50','999999999.99'])
def test_bounded_explicit_decimal_and_zero_are_not_missing(case,monkeypatch,price):
 calls,results=spy_core(monkeypatch);row=submit(case,BLOCK.replace('Цена: 120.50','Цена: '+price))
 assert len(calls)==1 and calls[0]['evidence'].price==Decimal(price.replace(',','.')) and 'price' not in results[0].missing
 assert 'Цена: '+str(Decimal(price.replace(',','.')))+' RUB' in row['text']


@pytest.mark.parametrize('at,unknown',[
 ('2026-10-16T09:00:00+03:00',False),('2026-10-17T08:59:59+03:00',False),
 ('2026-10-17T09:00:00+03:00',True),('2026-10-16T08:59:59+03:00',True),
 ('2026-10-18T00:00:00Z',True),('2026-10-16T06:00:00Z',False)])
def test_supplied_window_boundaries_not_server_clock(case,monkeypatch,at,unknown):
 calls,results=spy_core(monkeypatch);case.clock[0]+=100*86400
 row=submit(case,BLOCK.replace('Срез сценария: 2026-10-16T12:00:00+03:00','Срез сценария: '+at))
 assert calls[0]['now']==datetime.fromisoformat(at.replace('Z','+00:00'))
 assert bool(results[0].missing)==unknown
 assert row['text'].count('уточняется')==(3 if unknown else 0)


def test_actual_missing_output_not_wrapper_recalculation(case,monkeypatch):
 actual=command.core.product_draft
 def core(**kw):
  reply=actual(**kw)
  return command.core.ReplyDraft(reply.conversation,reply.product_id,reply.text,('price',),reply.source_ids)
 monkeypatch.setattr(command.core,'product_draft',core)
 row=submit(case);assert 'Нужно уточнить: цена и валюта.' in row['text']


def test_exact_template_roundtrip_has_ten_fields_and_no_date_defaults(case,monkeypatch):
 monkeypatch.setattr(command.core,'product_draft',lambda **kw:pytest.fail('template/missing fields must not call core'))
 row=submit(case,command.HEADER)
 assert row['text']==command.TEMPLATE and len(row['text'].splitlines())==11
 assert '[YYYY-MM-DDTHH:MM:SS+HH:MM]' in row['text']
 row=submit(case,row['text'],event='unfilled');assert row['text']==command.NOTICE


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_label_never_defaults_or_calls_core(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'product_draft',lambda **kw:pytest.fail('missing input core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('mutation',[
 lambda b:b+'\nНаличие: да',lambda b:b+'\n',lambda b:b+'\nПолучатель: customer',
 lambda b:b.replace('Основание: значения из этого сообщения, без независимой проверки','Основание: факты компании проверены'),
 lambda b:b.replace('Наличие: да','Наличие: возможно'),lambda b:b.replace('Наличие: да','Наличие: true'),
 lambda b:b.replace('Актуальность заявлена мной: да','Актуальность заявлена мной: подтверждено'),
 lambda b:b.replace('Цена: 120.50','Цена: NaN'),lambda b:b.replace('Цена: 120.50','Цена: Infinity'),
 lambda b:b.replace('Цена: 120.50','Цена: -1'),lambda b:b.replace('Цена: 120.50','Цена: +1'),
 lambda b:b.replace('Цена: 120.50','Цена: 1e2'),lambda b:b.replace('Цена: 120.50','Цена: 001'),
 lambda b:b.replace('Цена: 120.50','Цена: 1.123'),lambda b:b.replace('Цена: 120.50','Цена: 1000000000'),
 lambda b:b.replace('Цена: 120.50','Цена: 1 000'),lambda b:b.replace('Валюта: RUB','Валюта: rub'),
 lambda b:b.replace('Валюта: RUB','Валюта: USD RUB'),lambda b:b.replace('Название для сценария: Образец <A>','Название для сценария: '+'x'*101),
 lambda b:b.replace('Доставка: По указанному сценарию <два дня> & проверка','Доставка: '+'x'*401),
 lambda b:b.replace('2026-10-16T09:00:00+03:00','2026-10-16T09:00:00'),
 lambda b:b.replace('2026-10-16T09:00:00+03:00','2026-02-30T09:00:00+03:00'),
 lambda b:b.replace('2026-10-17T09:00:00+03:00','2026-10-16T09:00:00+03:00'),
 lambda b:b.replace('2026-10-17T09:00:00+03:00','2026-10-15T09:00:00+03:00'),
 lambda b:b.replace('Срез сценария: 2026-10-16T12:00:00+03:00','Срез сценария: сегодня'),
 lambda b:b.replace('Срез сценария: 2026-10-16T12:00:00+03:00','Срез сценария: 20261016T120000+03:00'),
 lambda b:b.replace('Образец <A>','x\tfoo'),lambda b:b.replace('Образец <A>','x\rfoo'),lambda b:b.replace('Образец <A>','x\x7ffoo'),
 lambda b:b+'x'*4096])
def test_invalid_or_ambiguous_input_fixed_notice_no_core(case,monkeypatch,mutation):
 monkeypatch.setattr(command.core,'product_draft',lambda **kw:pytest.fail('invalid input called core'))
 assert submit(case,mutation(BLOCK))['text']==command.NOTICE


@pytest.mark.parametrize('which',['utf16','utf8','output'])
def test_actual_hard_input_and_final_html_bounds(case,which):
 if which=='utf16':text=BLOCK.replace('Образец <A>','Ж'*1800)
 elif which=='utf8':text=BLOCK.replace('Образец <A>','😀'*1000)
 else:
  text=BLOCK.replace('Образец <A>','"'*100).replace('По указанному сценарию <два дня> & проверка','"'*400)
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('value',['wrong_type','draft_flag'])
def test_unexpected_core_result_is_not_a_customer_draft(case,monkeypatch,value):
 actual=command.core.product_draft
 def result(**kw):
  if value=='wrong_type':return SimpleNamespace(text='forged')
  reply=actual(**kw);object.__setattr__(reply,'draft_only',False);return reply
 monkeypatch.setattr(command.core,'product_draft',result)
 assert submit(case)['text']==command.NOTICE


@pytest.mark.parametrize('change',['employee','group','foreign_private','nonclosed','no_intel','business_mismatch','two_admins'])
def test_privileged_scenario_route_denies_unsupported_actor_or_host(case,change):
 kwargs={}
 if change=='employee':kwargs['sender']='frost';kwargs['source']='telegram:2:10'
 elif change=='group':kwargs['channel']='group';kwargs['source']='telegram:-4:10'
 elif change=='foreign_private':kwargs['source']='telegram:2:10'
 elif change=='nonclosed':case.c['team_roster_verified']=False
 elif change=='no_intel':case.e.intelligence=None
 elif change=='business_mismatch':case.e.b='OTHER'
 elif change=='two_admins':case.c['admins'].append('frost')
 assert submit(case,**kwargs) is None
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'product-reply-preview:%'")


@pytest.mark.parametrize('change',['config','intelligence','index','policy','recipient','body','queue_failure'])
def test_captured_host_queue_races_rollback_all_outbox_and_event(case,monkeypatch,change):
 actual=case.s.queue
 def queue(*a,**kw):
  result=actual(*a,**kw)
  if str(a[3]).startswith('product-reply-preview:'):
   import copy
   if change=='config':case.e.c=copy.deepcopy(case.c)
   elif change=='intelligence':case.e.intelligence=copy.copy(case.e.intelligence)
   elif change=='index':case.e.intelligence.index=copy.copy(case.e.intelligence.index)
   elif change=='policy':case.c['review_mode']=False
   elif change=='recipient':case.s.execute("UPDATE outbox SET recipient='frost' WHERE dedupe=?",(a[3],))
   elif change=='body':case.s.execute("UPDATE outbox SET text='forged' WHERE dedupe=?",(a[3],))
   elif change=='queue_failure':raise RuntimeError('synthetic queue failure')
  return result
 monkeypatch.setattr(case.s,'queue',queue)
 with pytest.raises((ValueError,RuntimeError)):submit(case)
 assert not case.s.rows("SELECT * FROM events WHERE id='scenario'")
 assert not case.s.rows('SELECT * FROM outbox')


def test_replay_creates_one_core_result_and_one_own_output(case,monkeypatch):
 calls,_=spy_core(monkeypatch)
 first=submit(case);second=submit(case)
 assert first['id']==second['id'] and len(calls)==1 and len(case.s.rows('SELECT * FROM outbox'))==1
