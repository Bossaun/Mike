"""New engine rewrite boundary only, actual synthetic dispatcher delivery."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
from goldway.promotion_preview_command import is_promotion_command,build_promotion_preview,PromotionUnavailable
import goldway.promotion_preview_command as command
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

BLOCK=('Расчёт акции\nВалюта: RUB\nВыручка без скидки: 1000\nСебестоимость: 700\n'
       'Скидка %: 10\nКомиссия %: 5\nФиксированная комиссия: 20')

def submit(c,text=BLOCK,event='promotion',sender='test_operator',channel='bot',source=None,task_id=None):
 source=source or ('telegram:1:2' if channel=='bot' else 'business:conn:500:2')
 c.e.reply(event,sender,text,source_id=source,channel=channel,task_id=task_id);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',('GOLDWAY','promotion-preview:'+event)).fetchone()


def prohibit_old_path(c):
 def fail(*a,**kw):pytest.fail('unexpected model/retrieval/task-context path')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail
 c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e.intelligence.memory.get=fail

@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_calls_decimal_core_once_no_model_or_task_write(case,monkeypatch,channel):
 prohibit_old_path(case);calls=[];core=command.preview_promotion
 def spy(**kw):calls.append(kw);return core(**kw)
 monkeypatch.setattr(command,'preview_promotion',spy)
 case.s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('GOLDWAY','t','Task','test_operator','step','criterion','confirmed','source',1)")
 before=case.s.rows('SELECT * FROM tasks');row=submit(case,channel=channel,task_id='t')
 assert len(calls)==1 and calls[0]==dict(currency='RUB',original_revenue='1000',cogs='700',discount_pct='10',fee_pct='5',fixed_fee='20')
 assert 'Выручка после скидки: 900 RUB' in row['text'] and 'Комиссии: 65 RUB' in row['text']
 assert 'Остаток после указанных затрат: 135 RUB' in row['text'] and '15%' in row['text']
 assert 'не чистая прибыль' in row['text'] and 'не финансовый отчёт компании' in row['text']
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None
 assert before==case.s.rows('SELECT * FROM tasks') and not case.s.rows('SELECT id FROM usage')
 assert case.s.execute("SELECT state FROM events WHERE id='promotion'").fetchone()[0]=='done'
 case.d.flush();assert case.s.execute('SELECT state FROM outbox WHERE id=?',(row['id'],)).fetchone()[0]=='sent'


def test_template_request_no_core_call(case,monkeypatch):
 prohibit_old_path(case);monkeypatch.setattr(command,'preview_promotion',lambda **kw:pytest.fail('core without explicit values'))
 row=submit(case,'Рассчитай акцию');assert '[уточнить]' in row['text'] and 'Нули укажите явно' in row['text']
 assert len([line for line in row['text'].splitlines() if ': [уточнить]' in line])==6

@pytest.mark.parametrize('field',['Валюта','Выручка без скидки','Себестоимость','Скидка %','Комиссия %','Фиксированная комиссия'])
def test_each_field_required_zero_not_implied(case,monkeypatch,field):
 prohibit_old_path(case);monkeypatch.setattr(command,'preview_promotion',lambda **kw:pytest.fail('missing input called core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(field+':'))
 row=submit(case,text);assert 'Нужно уточнить:' in row['text'] and field in row['text']

@pytest.mark.parametrize('mutation',[
 lambda b:b+'\nВалюта: USD', lambda b:b.replace('RUB','RUB USD'), lambda b:b.replace('RUB','rub'),
 lambda b:b.replace('700','700 USD'), lambda b:b.replace('700','1e3'), lambda b:b.replace('700','NaN'),
 lambda b:b.replace('700','Infinity'),lambda b:b.replace('700','-1'),lambda b:b.replace('700','+1'),
 lambda b:b.replace('700','True'),lambda b:b.replace('700','1 000'),lambda b:b.replace('700','01'),
 lambda b:b.replace('700','1.1234567'),lambda b:b.replace('700','9'*33),lambda b:b.replace('700','9'*13),
 lambda b:b.replace('Скидка %: 10','Скидка %: 101'),lambda b:b.replace('Комиссия %: 5','Комиссия %: 101'),
 lambda b:b.replace('700','7\t00'),lambda b:b+'\nПрибыль: 500',lambda b:b+'\n\n',
 lambda b:b+'\n'+'x'*2000])
def test_invalid_values_fixed_notice_no_core(case,monkeypatch,mutation):
 prohibit_old_path(case);monkeypatch.setattr(command,'preview_promotion',lambda **kw:pytest.fail('invalid input called core'))
 row=submit(case,mutation(BLOCK));assert 'Расчёт не выполнен.' in row['text'] and '135 RUB' not in row['text']


def test_decimal_commas_and_explicit_zero_fees(case):
 prohibit_old_path(case);text=BLOCK.replace('1000','1000,50').replace('Комиссия %: 5','Комиссия %: 0').replace('Фиксированная комиссия: 20','Фиксированная комиссия: 0')
 row=submit(case,text);assert 'Выручка после скидки: 900.45 RUB' in row['text'] and 'Комиссии: 0 RUB' in row['text']

@pytest.mark.parametrize('text',[BLOCK.replace('Скидка %: 10','Скидка %: 100'),BLOCK.replace('1000','0')])
def test_zero_denominator_unknown_not_zero_margin(case,text):
 prohibit_old_path(case);row=submit(case,text)
 assert 'Доля остатка в выручке после скидки: неизвестна' in row['text'] and '0%' not in row['text'].split('Доля остатка')[1]
 assert 'Остаток после указанных затрат: -720 RUB' in row['text']


def test_negative_contribution_not_relabelled_as_profit(case):
 prohibit_old_path(case);row=submit(case,BLOCK.replace('700','1000'))
 assert 'Остаток после указанных затрат: -165 RUB' in row['text'] and 'не чистая прибыль' in row['text']

@pytest.mark.parametrize('change',['staff','outsider','group','business','intelligence','scope','team'])
def test_unauthorized_no_calculation_or_output(case,monkeypatch,change):
 prohibit_old_path(case);monkeypatch.setattr(command,'preview_promotion',lambda **kw:pytest.fail('unauthorized core'))
 kwargs={}
 if change=='staff':kwargs=dict(sender='frost',source='telegram:2:1')
 elif change=='outsider':kwargs=dict(sender='outsider',source='telegram:9:1')
 elif change=='group':kwargs=dict(channel='group',source='telegram:-5:1')
 elif change=='business':case.c['business']='OTHER'
 elif change=='intelligence':case.e.intelligence.b='OTHER'
 elif change=='scope':kwargs=dict(source='legacy-source')
 else:case.c['team_roster_verified']=False
 row=submit(case,**kwargs);assert row is None and not case.s.rows('SELECT id FROM outbox')


def test_deduped_event_only_one_core_and_outbox(case,monkeypatch):
 prohibit_old_path(case);core=command.preview_promotion;calls=[]
 def spy(**kw):calls.append(kw);return core(**kw)
 monkeypatch.setattr(command,'preview_promotion',spy)
 submit(case);submit(case);assert len(calls)==1 and len(case.s.rows('SELECT id FROM outbox'))==1

@pytest.mark.parametrize('change',['business','intelligence','roster'])
def test_queue_authority_drift_rolls_back(case,monkeypatch,change):
 prohibit_old_path(case);queue=case.s.queue
 def drift(*a,**kw):
  result=queue(*a,**kw)
  if change=='business':case.c['business']='OTHER'
  elif change=='intelligence':case.e.intelligence=None
  else:case.c['team_roster_verified']=False
  return result
 monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises((ValueError,AttributeError)):submit(case)
 assert not case.s.rows('SELECT id FROM outbox') and not case.s.rows('SELECT id FROM events')

@pytest.mark.parametrize('text',['да','Давай','Подтверждаю задачу №2','Сократи ответ','Подробнее о втором бренде','Откуда ты это знаешь?','Рассчитай прибыль акции по данным компании','Создай задачу: рассчитай акцию'])
def test_other_priorities_not_recognized(text):assert not is_promotion_command(text)


def test_following_sources_do_not_fall_back_to_previous_receipt(case):
 class Previous:
  def propose(self,context):return dict(actions=[],summary='Synthetic',reply_text='Prior delivered answer',intent='conversation',dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 case.e.model=Previous();case.e.reply('prior','test_operator','Напиши черновик',source_id='telegram:1:1',channel='bot');case.e.process('prior');case.d.flush()
 assert case.s.execute('SELECT phase FROM answer_citation_turns').fetchone()[0]=='recorded'
 prohibit_old_path(case);submit(case);case.d.flush()
 case.e.reply('source','test_operator','Откуда ты это знаешь?',source_id='telegram:1:3',channel='bot')
 row=case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-citations:source'").fetchone()
 assert 'сейчас недоступны или не подтверждены' in row[0] and 'нет сохранённых корпоративных ссылок' not in row[0]


def test_output_and_literal_input_bounds(case):
 prohibit_old_path(case);text=BLOCK.replace('1000','999999999999.999999').replace('700','0.000001').replace('Скидка %: 10','Скидка %: 0.000001').replace('Комиссия %: 5','Комиссия %: 99.999999')
 row=submit(case,text);assert len(row['text'])<=3000 and len(row['text'].encode('utf-16-le'))//2<=3500


def test_direct_nontext_or_outside_transaction_rejected(case):
 for invalid in [None,True,1,{},[]]:
  assert not is_promotion_command(invalid)
  with pytest.raises(PromotionUnavailable):build_promotion_preview(invalid)
 with pytest.raises(PromotionUnavailable):command.handle_promotion_preview(case.e,'outside',dict(text=BLOCK))


def test_one_terminal_newline_is_harmless_formatting(case):
 prohibit_old_path(case);row=submit(case,BLOCK+'\n')
 assert 'Остаток после указанных затрат: 135 RUB' in row['text']
