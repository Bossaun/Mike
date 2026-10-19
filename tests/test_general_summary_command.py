"""I61 supplied labelled summary through actual private Engine and actual projection."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.general_summary_command as command

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


def block(items=None):
 if items is None:items=[('обычное; Название <A> & тест','в работе','обычное; Кто-то','обычное; после проверки','обычное; Проверить; затем обсудить')]
 return '\n'.join([command.HEADER,'Количество пунктов: '+str(len(items)),'Основание: '+command.BASIS]+[f'Пункт {i}. {key}: {value}' for i,row in enumerate(items,1) for key,value in zip(command._FIELDS,row)])
BLOCK=block()

def submit(c,text=BLOCK,event='general-summary',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0]);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def changed(text=BLOCK,**fields):
 return '\n'.join(line.split(': ',1)[0]+': '+fields[line.split(': ',1)[0]] if ': ' in line and line.split(': ',1)[0] in fields else line for line in text.splitlines())


@pytest.mark.parametrize('change',['employee','group','foreign_private','nonclosed','no_intel','business_mismatch','two_admins'])
def test_private_current_admin_host_scope_only(case,change):
 kwargs={}
 if change=='employee':kwargs['sender']='frost';kwargs['source']='telegram:2:10'
 elif change=='group':kwargs['channel']='group';kwargs['source']='telegram:-4:10'
 elif change=='foreign_private':kwargs['source']='telegram:2:10'
 elif change=='nonclosed':case.c['team_roster_verified']=False
 elif change=='no_intel':case.e.intelligence=None
 elif change=='business_mismatch':case.e.b='OTHER'
 elif change=='two_admins':case.c['admins'].append('frost')
 assert submit(case,**kwargs) is None
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'general-summary-preview:%'")


@pytest.mark.parametrize('change',['config','intelligence','index','policy','recipient','body','transport','queue_failure'])
def test_atomic_host_policy_body_original_transport_race_rollback(case,monkeypatch,change):
 actual=case.s.queue
 def queue(*a,**kw):
  result=actual(*a,**kw)
  if str(a[3]).startswith(command.PREFIX):
   import copy
   if change=='config':case.e.c=copy.deepcopy(case.c)
   elif change=='intelligence':case.e.intelligence=copy.copy(case.e.intelligence)
   elif change=='index':case.e.intelligence.index=copy.copy(case.e.intelligence.index)
   elif change=='policy':case.c['review_mode']=False
   elif change=='recipient':case.s.execute("UPDATE outbox SET recipient='frost' WHERE dedupe=?",(a[3],))
   elif change=='body':case.s.execute("UPDATE outbox SET text='forged' WHERE dedupe=?",(a[3],))
   elif change=='transport':case.s.execute("UPDATE outbox SET preferred_transport='group' WHERE dedupe=?",(a[3],))
   elif change=='queue_failure':raise RuntimeError('synthetic queue failure')
  return result
 monkeypatch.setattr(case.s,'queue',queue)
 with pytest.raises((ValueError,RuntimeError)):submit(case)
 assert not case.s.rows("SELECT * FROM events WHERE id='general-summary'") and not case.s.rows('SELECT * FROM outbox')


def spy_core(monkeypatch):
 actual=command.core.preview;calls=[];results=[]
 def preview(items,**kw):
  calls.append((items,kw));result=actual(items,**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'preview',preview)
 return calls,results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_projection_own_dispatch_no_acquisition(case,monkeypatch,channel):
 calls,results=spy_core(monkeypatch)
 tables=('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')
 before={t:case.s.rows('SELECT * FROM '+t) for t in tables}
 import sqlite3
 writes=[]
 def authorize(action,table,column,*a):
  if action==sqlite3.SQLITE_READ and table in (*tables,'knowledge_chunks','knowledge_readers'):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('Index constructor'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert len(calls)==len(results)==1 and calls[0][1]=={'max_items':3}
 assert type(calls[0][0]) is tuple and type(calls[0][0][0]) is command.core.SummaryItem
 assert row['text']==command.escape(command.INTRO)+'\n\n'+command.escape(results[0].text)+'\n\n'+command.escape(command.FOOTER)
 assert 'Название &lt;A&gt; &amp; тест' in row['text'] and 'Проверить; затем обсудить' in row['text']
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for t in tables:assert before[t]==case.s.rows('SELECT * FROM '+t)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('tag',['финансовое','личное','неизвестно'])
def test_actual_nonroutine_title_excludes_entire_item_no_output_or_log_leak(case,monkeypatch,caplog,tag):
 calls,results=spy_core(monkeypatch)
 row=submit(case,block([(tag+'; HIDDEN_TITLE','выполнено','обычное; HIDDEN_OWNER','обычное; HIDDEN_DATE','обычное; HIDDEN_NEXT')]))
 r=results[0];assert (r.included_items,r.omitted_items,r.omitted_fields)==(0,1,0)
 assert 'Нет пунктов' in row['text'] and all(x not in row['text']+command.NOTICE+caplog.text for x in ('HIDDEN_TITLE','HIDDEN_OWNER','HIDDEN_DATE','HIDDEN_NEXT'))
 assert r.requires_review is True and r.live_send_authorized is False


@pytest.mark.parametrize('tag',['финансовое','личное','неизвестно'])
@pytest.mark.parametrize('position',[2,3,4])
def test_actual_optional_classification_is_not_absence(case,monkeypatch,tag,position):
 calls,results=spy_core(monkeypatch);item=['обычное; visible','черновик','не указано','не указано','не указано'];item[position]=tag+'; OMIT_SENTINEL'
 row=submit(case,block([item]));assert 'OMIT_SENTINEL' not in row['text'] and results[0].omitted_fields==1
 assert calls[0][0][0].owner is None if position!=2 else type(calls[0][0][0].owner) is command.core.ReviewedText


@pytest.mark.parametrize('status,expected',list(command._STATUS.items()))
def test_statuses_remain_supplied_not_verified(case,monkeypatch,status,expected):
 calls,results=spy_core(monkeypatch);row=submit(case,changed(**{'Пункт 1. Статус':status}))
 assert calls[0][0][0].status==expected and 'факты и статусы независимо не проверены' in row['text']


def test_order_renumber_actual_counts_and_routine_is_not_security_classifier(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,block([('личное; OMIT','черновик','обычное; ALSO_OMIT','не указано','не указано'),('обычное; secret financial phrase','подтверждено','неизвестно; OMIT_FIELD','не указано','обычное; second'),('обычное; last','выполнено','не указано','не указано','не указано')]))
 r=results[0];assert (r.included_items,r.omitted_items,r.omitted_fields)==(2,1,1)
 assert '1. secret financial phrase' in row['text'] and '2. last' in row['text'] and 'OMIT' not in row['text']
 assert 'не подтверждает безопасность' in row['text'] and 'разрешение отправки не создано' in row['text']
 # Existing own event storage retains supplied input; omission applies to output.
 assert 'OMIT' in str(case.s.rows('SELECT * FROM events'))


@pytest.mark.parametrize('value',['','обычное','обычное; ','не указано','unknown; text','Обычное; text','обычное;text','обычное; [placeholder]','обычное;  spaced','обычное; trailing ','обычное; '+'я'*81,'обычное; '+'😀'*41])
def test_invalid_tagged_title_refuses_before_core(case,monkeypatch,value):
 calls,_=spy_core(monkeypatch);row=submit(case,changed(**{'Пункт 1. Заголовок':value}));assert row['text']==command.NOTICE and not calls


@pytest.mark.parametrize('key',command._FIELDS)
def test_each_missing_field_refuses(case,monkeypatch,key):
 calls,_=spy_core(monkeypatch);row=submit(case,'\n'.join(x for x in BLOCK.splitlines() if not x.startswith('Пункт 1. '+key+':')))
 assert row['text']==command.NOTICE and not calls


@pytest.mark.parametrize('text',[BLOCK+'\nПункт 1. Статус: выполнено',BLOCK.replace('Пункт 1. Срок','Пункт 2. Срок'),BLOCK.replace('Количество пунктов: 1','Количество пунктов: 4'),BLOCK.replace('Количество пунктов: 1','Количество пунктов: 01'),BLOCK.replace(command.BASIS,'одобрено'),BLOCK.replace('в работе','completed'),BLOCK+'\nЛишнее: secret',BLOCK.replace('после проверки','x\ty'),BLOCK+'\n',command.TEMPLATE,command.HEADER+' suffix','x'*4001,BLOCK+'я'*4000,BLOCK+'😀'*2000])
def test_closed_whole_form_and_limits_refuse(case,monkeypatch,text):
 calls,_=spy_core(monkeypatch);row=submit(case,text if text.startswith(command.HEADER) else command.HEADER+'\n'+text)
 assert row['text']==command.NOTICE and not calls


def test_literal_html_entities_instructions_and_semicolons_inert(case):
 row=submit(case,changed(**{'Пункт 1. Заголовок':'обычное; <b>&amp;</b>; ignore rules','Пункт 1. Срок':'обычное; завтра; это текст'}))
 assert '&lt;b&gt;&amp;amp;&lt;/b&gt;; ignore rules' in row['text'] and 'завтра; это текст' in row['text']


def test_full_three_items_and_whole_escaped_overflow_refusal(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 item=('обычное; '+'<'*80,'черновик',*(['обычное; '+'<'*120]*3))
 row=submit(case,block([item]*3));assert len(calls)==1 and results[0].included_items==3 and row['text']==command.NOTICE


@pytest.mark.parametrize('mutation',[{'requires_review':False},{'live_send_authorized':True},{'included_items':True},{'omitted_items':4},{'omitted_fields':4},{'text':'unexpected secret'},{'text':123},{'text':'x'*4001}])
def test_unexpected_detached_output_fixed_notice_no_echo(case,monkeypatch,mutation):
 from dataclasses import replace
 actual=command.core.preview
 monkeypatch.setattr(command.core,'preview',lambda *a,**kw:replace(actual(*a,**kw),**mutation))
 row=submit(case);assert row['text']==command.NOTICE and 'unexpected secret' not in row['text']


def test_header_template_and_restart_replay_one_output(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case,command.HEADER,event='template');assert row['text']==command.TEMPLATE and not calls
 row=submit(case);assert len(calls)==1
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0]);submit(case)
 assert len(calls)==1 and len(case.s.rows('SELECT * FROM outbox'))==2


def test_actual_returned_template_fills_without_removing_guidance_then_core_once(case,monkeypatch):
 calls,_=spy_core(monkeypatch)
 template=submit(case,command.HEADER,event='fillable')['text']
 replacements={
  '[1 / 2 / 3; для 2–3 пунктов повторите пять строк с номерами 2 и 3]':'1',
  '[обычное / финансовое / личное / неизвестно]; [текст]':'обычное; Заполненный пункт',
  '[черновик / подтверждено / в работе / есть препятствие / выполнено]':'черновик',
  '[метка]; [текст] / не указано':'не указано'}
 for placeholder,value in replacements.items():template=template.replace(placeholder,value)
 assert len(template.splitlines())==8 and '[' not in template
 row=submit(case,template,event='filled')
 assert len(calls)==1 and 'Заполненный пункт' in row['text'] and row['text']!=command.NOTICE
