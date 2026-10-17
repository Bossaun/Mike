"""New engine rewrite boundary only, actual synthetic dispatcher delivery."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
from goldway.sales_cost_check_command import is_sales_cost_check_command,build_sales_cost_check,SalesCheckUnavailable
import goldway.sales_cost_check_command as command
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

BLOCK=('Проверь продажи\nВалюта: RUB\n2026-10-16; 900; 1000; нет\n'
       '2026-10-16; 500; [неизвестно]; нет\n2026-10-16; 0; 700; да')


def submit(c,text=BLOCK,event='sales',sender='test_operator',channel='bot',source=None,task_id=None):
 c.e.reply(event,sender,text,source_id=source or ('telegram:1:2' if channel=='bot' else 'business:conn:500:2'),channel=channel,task_id=task_id);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',('GOLDWAY','sales-cost-check:'+event)).fetchone()


def prohibit_old_path(c):
 def fail(*a,**kw):pytest.fail('unexpected model/retrieval/task-context path')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail
 c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e.intelligence.memory.get=fail

@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_calls_core_once_strict_rows_and_delivery(case,monkeypatch,channel):
 prohibit_old_path(case);core=command.cost_flags;calls=[]
 def spy(records,**kw):calls.append((records,kw));return core(records,**kw)
 monkeypatch.setattr(command,'cost_flags',spy)
 case.s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('GOLDWAY','t','Task','test_operator','step','criterion','confirmed','source',1)")
 before=case.s.rows('SELECT * FROM tasks');row=submit(case,channel=channel,task_id='t')
 assert len(calls)==1 and calls[0][1]==dict(currency='RUB')
 records=calls[0][0];assert len(records)==3 and [r.id for r in records]==['row:1','row:2','row:3']
 assert records[0].date.isoformat()=='2026-10-16' and records[1].cogs is None and records[2].barter is True and records[2].revenue==0
 text=row['text'];assert 'Строка 1: выручка ниже указанной себестоимости.' in text
 assert 'Строка 2: себестоимость неизвестна' in text and 'Строка 3: бартер заявлен' in text
 assert 'не проверка всех продаж компании' in text and 'независимо не проверены' in text
 assert 'row:' not in text and '900' not in text and '1000' not in text
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None
 assert before==case.s.rows('SELECT * FROM tasks') and not case.s.rows('SELECT id FROM usage')
 case.d.flush();assert case.s.execute('SELECT state FROM outbox WHERE id=?',(row['id'],)).fetchone()[0]=='sent'


def test_fieldless_template_zero_core(case,monkeypatch):
 prohibit_old_path(case);monkeypatch.setattr(command,'cost_flags',lambda *a,**kw:pytest.fail('template core call'))
 row=submit(case,'Проверь продажи');assert 'Валюта: [уточнить]' in row['text'] and '[бартер да/нет]' in row['text']

@pytest.mark.parametrize('revenue,cogs,barter,expected',[
 ('0','[неизвестно]','нет','себестоимость неизвестна'),('0','0','нет','флагов для переданной'),
 ('0','1','нет','выручка ниже указанной'),('0','1','да','бартер заявлен'),
 ('0','[неизвестно]','да','себестоимость неизвестна'),('10','20','да','бартер заявлен'),
 ('1','0','нет','флагов для переданной'),('1','1','нет','флагов для переданной')])
def test_missing_zero_and_explicit_barter_distinct(case,revenue,cogs,barter,expected):
 prohibit_old_path(case);row=submit(case,'Проверь продажи\nВалюта: RUB\n2026-10-16; '+revenue+'; '+cogs+'; '+barter)
 assert 'Строка 1: '+expected in row['text']
 if barter=='да':assert 'выручка ниже указанной' not in row['text']


def test_identical_rows_are_distinct_declared_operations(case,monkeypatch):
 prohibit_old_path(case);calls=[];core=command.cost_flags
 def spy(records,**kw):calls.append(records);return core(records,**kw)
 monkeypatch.setattr(command,'cost_flags',spy)
 row=submit(case,'Проверь продажи\nВалюта: RUB\n'+'\n'.join(['2026-10-16; 1; 2; нет']*2))
 assert len(calls)==1 and len(calls[0])==2 and calls[0][0].id!=calls[0][1].id
 assert row['text'].count('выручка ниже указанной')==2 and 'одинаковые строки не объединены' in row['text']

@pytest.mark.parametrize('bad',[
 '2026-02-30; 1; 2; нет','2026-2-1; 1; 2; нет','сегодня; 1; 2; нет','2026-10-16; 1; 2',
 '2026-10-16; 1; 2; ','2026-10-16; 1; 2; возможно','2026-10-16; 1; 2; false',
 '2026-10-16; 1; ; нет','2026-10-16; ; 1; нет','2026-10-16; 1; неизвестно; нет',
 '2026-10-16; -1; 2; нет','2026-10-16; 1; -2; нет','2026-10-16; +1; 2; нет',
 '2026-10-16; 1e3; 2; нет','2026-10-16; NaN; 2; нет','2026-10-16; Infinity; 2; нет',
 '2026-10-16; 1; 2 USD; нет','2026-10-16; 1 RUB; 2; нет','2026-10-16; 01; 2; нет',
 '2026-10-16; 1 000; 2; нет','2026-10-16; 1.1234567; 2; нет',
 '2026-10-16; 9999999999999; 2; нет','2026-10-16; 1\t0; 2; нет',
 '2026-10-16; 1; 2; нет; private-order-id','\n','PRIVATE ORDER TEXT',
 '2026-10-16; 1; 2; нет'+'x'*128])
def test_whole_block_invalid_no_silent_valid_row_drop(case,monkeypatch,bad):
 prohibit_old_path(case);monkeypatch.setattr(command,'cost_flags',lambda *a,**kw:pytest.fail('partially valid core input'))
 text='Проверь продажи\nВалюта: RUB\n2026-10-16; 1; 2; нет\n'+bad
 row=submit(case,text);assert 'Проверка не выполнена' in row['text'] and 'Строка 1: выручка' not in row['text'] and 'PRIVATE' not in row['text']

@pytest.mark.parametrize('text',[
 'Проверь продажи\n2026-10-16; 1; 2; нет','Проверь продажи\nВалюта: rub\n2026-10-16; 1; 2; нет',
 'Проверь продажи\nВалюта: RUB USD\n2026-10-16; 1; 2; нет','Проверь продажи\nВалюта: RUB',
 'Проверь продажи\nВалюта: RUB\nВалюта: USD\n2026-10-16; 1; 2; нет',
 'Проверь продажи\nВалюта: RUB\n'+'\n'.join(['2026-10-16; 1; 2; нет']*13),
 'Проверь продажи\nВалюта: RUB\n'+'x'*2500])
def test_missing_or_conflicting_currency_extra_rows_and_size_denied(case,monkeypatch,text):
 prohibit_old_path(case);monkeypatch.setattr(command,'cost_flags',lambda *a,**kw:pytest.fail('invalid block core'))
 row=submit(case,text);assert 'Проверка не выполнена' in row['text']


def test_decimal_comma_and_valid_single_terminal_newline(case):
 prohibit_old_path(case);row=submit(case,'Проверь продажи\nВалюта: RUB\n2026-10-16; 1,25; 1,26; нет\n')
 assert 'выручка ниже указанной' in row['text']


def test_maximum_12_rows_amounts_and_output_bound(case):
 prohibit_old_path(case);text='Проверь продажи\nВалюта: RUB\n'+'\n'.join(['2026-10-16; 999999999999.999999; [неизвестно]; да']*12)
 row=submit(case,text);assert 'Строка 12: себестоимость неизвестна' in row['text']
 assert len(row['text'])<=3000 and len(row['text'].encode('utf-16-le'))//2<=3500

@pytest.mark.parametrize('change',['staff','outsider','group','business','intelligence','scope','team'])
def test_unauthorized_before_core_and_no_output(case,monkeypatch,change):
 prohibit_old_path(case);monkeypatch.setattr(command,'cost_flags',lambda *a,**kw:pytest.fail('unauthorized core'))
 kwargs={}
 if change=='staff':kwargs=dict(sender='frost',source='telegram:2:1')
 elif change=='outsider':kwargs=dict(sender='outsider',source='telegram:9:1')
 elif change=='group':kwargs=dict(channel='group',source='telegram:-5:1')
 elif change=='business':case.c['business']='OTHER'
 elif change=='intelligence':case.e.intelligence.b='OTHER'
 elif change=='scope':kwargs=dict(source='legacy-source')
 else:case.c['team_roster_verified']=False
 assert submit(case,**kwargs) is None and not case.s.rows('SELECT id FROM outbox')


def test_replay_no_second_core_or_outbox(case,monkeypatch):
 prohibit_old_path(case);calls=[];core=command.cost_flags
 def spy(records,**kw):calls.append(records);return core(records,**kw)
 monkeypatch.setattr(command,'cost_flags',spy)
 submit(case);submit(case);assert len(calls)==1 and len(case.s.rows('SELECT id FROM outbox'))==1

@pytest.mark.parametrize('change',['business','intelligence','roster'])
def test_queue_drift_rolls_back(case,monkeypatch,change):
 prohibit_old_path(case);queue=case.s.queue
 def drift(*a,**kw):
  result=queue(*a,**kw)
  if change=='business':case.c['business']='OTHER'
  elif change=='intelligence':case.e.intelligence=None
  else:case.c['team_roster_verified']=False
  return result
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises(ValueError):submit(case)
 assert not case.s.rows('SELECT id FROM outbox') and not case.s.rows('SELECT id FROM events')

@pytest.mark.parametrize('text',['да','Давай','Подтверждаю задачу №2','Сократи ответ','Подробнее о втором бренде','Откуда ты это знаешь?','Рассчитай акцию','Рассчитай закупку','Создай задачу: проверь продажи','Проверь продажи за неделю в нашей компании'])
def test_other_routes_not_intercepted(text):assert not is_sales_cost_check_command(text)


def test_source_followup_does_not_explain_old_answer(case):
 class Previous:
  def propose(self,context):return dict(actions=[],summary='Synthetic',reply_text='Prior answer',intent='conversation',dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 case.e.model=Previous();case.e.reply('prior','test_operator','Напиши черновик',source_id='telegram:1:1',channel='bot');case.e.process('prior');case.d.flush()
 assert case.s.execute('SELECT phase FROM answer_citation_turns').fetchone()[0]=='recorded'
 prohibit_old_path(case);submit(case);case.d.flush();case.e.reply('source','test_operator','Откуда ты это знаешь?',source_id='telegram:1:3',channel='bot')
 assert 'сейчас недоступны или не подтверждены' in case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-citations:source'").fetchone()[0]


def test_no_flags_claim_scoped_to_rows_not_company(case):
 prohibit_old_path(case);row=submit(case,'Проверь продажи\nВалюта: RUB\n2026-10-16; 2; 1; нет')
 assert 'флагов для переданной выручки и известной себестоимости нет' in row['text'] and 'не проверка всех продаж компании' in row['text']


def test_nontext_and_no_transaction_rejected(case):
 for invalid in [None,True,1,{},[]]:
  assert not is_sales_cost_check_command(invalid)
  with pytest.raises(SalesCheckUnavailable):build_sales_cost_check(invalid)
 with pytest.raises(SalesCheckUnavailable):command.handle_sales_cost_check(case.e,'outside',dict(text=BLOCK))
