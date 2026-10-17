"""New engine rewrite boundary only, actual synthetic dispatcher delivery."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
from goldway.purchase_quote_command import is_purchase_quote_command,build_purchase_quote,PurchaseQuoteUnavailable
import goldway.purchase_quote_command as command
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

BLOCK=('Расчёт закупки\nКоличество: 10\nВалюта итога: RUB\nЗакупка: 100 RUB; за штуку\n'
       'Доставка: 300 RUB; за партию\nКомиссия: 0 RUB; за партию\nПрочие сборы: 50 RUB; за партию\n'
       'Все сборы перечислены: да\nЗатраты не включают друг друга: да\nКурсы: нет\nДата курсов: не применяется')
FOREIGN=BLOCK.replace('100 RUB','100 CNY').replace('Курсы: нет','Курсы: CNY->RUB=12').replace('Дата курсов: не применяется','Дата курсов: 2026-10-16')


def submit(c,text=BLOCK,event='purchase',sender='test_operator',channel='bot',source=None,task_id=None):
 c.e.reply(event,sender,text,source_id=source or ('telegram:1:2' if channel=='bot' else 'business:conn:500:2'),channel=channel,task_id=task_id);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',('GOLDWAY','purchase-quote:'+event)).fetchone()


def prohibit_old_path(c):
 def fail(*a,**kw):pytest.fail('unexpected model/retrieval/task-context path')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail
 c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e.intelligence.memory.get=fail

@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_decimal_core_once_and_delivery(case,monkeypatch,channel):
 prohibit_old_path(case);core=command.quote_purchase;calls=[]
 def spy(**kw):calls.append(kw);return core(**kw)
 monkeypatch.setattr(command,'quote_purchase',spy)
 case.s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('GOLDWAY','t','Task','test_operator','step','criterion','confirmed','source',1)")
 before=case.s.rows('SELECT * FROM tasks');row=submit(case,FOREIGN,channel=channel,task_id='t')
 assert len(calls)==1 and calls[0]['quantity']==10 and calls[0]['fees_complete'] is True and calls[0]['transaction_kind']=='purchase'
 charges=calls[0]['charges'];assert len(charges)==4 and charges[0].money.amount==100 and charges[0].money.currency=='CNY' and charges[0].basis=='per_unit'
 assert len(calls[0]['fx_rates'])==1 and calls[0]['fx_rates'][0].target_per_source_unit==12
 text=row['text'];assert 'Закупка: 100 CNY, за штуку → 12000 RUB за партию' in text
 assert 'Итого по указанным затратам за партию: 12350 RUB' in text and 'На одну одинаковую штуку: 1235 RUB' in text
 assert 'Курсы по сообщению на 2026-10-16: 1 CNY = 12 RUB' in text
 assert 'Все сборы перечислены по сообщению: да' in text and 'Раздельность затрат заявлена вами' in text
 assert 'не финансовый отчёт' not in text or 'по переданным' in text
 assert 'независимо не проверены' in text and 'не оформленная закупка' in text
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None
 assert before==case.s.rows('SELECT * FROM tasks') and not case.s.rows('SELECT id FROM usage')
 case.d.flush();assert case.s.execute('SELECT state FROM outbox WHERE id=?',(row['id'],)).fetchone()[0]=='sent'


def test_same_currency_no_hidden_conversion(case):
 prohibit_old_path(case);row=submit(case)
 assert '1350 RUB' in row['text'] and '135 RUB' in row['text'] and 'Пересчёт валют не применяется' in row['text']


def test_mixed_explicit_per_unit_batch_and_direct_rates(case):
 prohibit_old_path(case)
 text=FOREIGN.replace('300 RUB; за партию','2 USD; за штуку').replace('CNY->RUB=12','CNY->RUB=12; USD->RUB=90')
 row=submit(case,text);assert 'Доставка: 2 USD, за штуку → 1800 RUB за партию' in row['text'] and '13850 RUB' in row['text'] and '1385 RUB' in row['text']


def test_exact_template_has_no_currency_or_basis_defaults(case,monkeypatch):
 prohibit_old_path(case);monkeypatch.setattr(command,'quote_purchase',lambda **kw:pytest.fail('template must not calculate'))
 row=submit(case,'Рассчитай закупку');assert '[валюта]; [база]' in row['text'] and 'Нули не подставлены' in row['text']

@pytest.mark.parametrize('label',list(command._LABELS))
def test_missing_each_field_no_silent_default(case,monkeypatch,label):
 prohibit_old_path(case);monkeypatch.setattr(command,'quote_purchase',lambda **kw:pytest.fail('missing field core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.casefold().startswith(label+':'))
 row=submit(case,text);assert 'Нужно уточнить:' in row['text'] and 'Итого по указанным затратам' not in row['text']

@pytest.mark.parametrize('kind,label',[('purchase','Закупка'),('freight','Доставка'),('commission','Комиссия'),('fee','Прочие сборы')])
def test_explicit_unknown_amount_remains_unknown(case,monkeypatch,kind,label):
 prohibit_old_path(case);calls=[];core=command.quote_purchase
 def spy(**kw):calls.append(kw);return core(**kw)
 monkeypatch.setattr(command,'quote_purchase',spy)
 text='\n'.join(label+': [уточнить] RUB; за партию' if line.startswith(label+':') else line for line in BLOCK.splitlines())
 row=submit(case,text)
 assert len(calls)==1 and next(c for c in calls[0]['charges'] if c.kind==kind).money.amount is None
 assert 'Сумма известных компонентов (не итог закупки)' in row['text'] and 'Итог партии и стоимость штуки: неизвестны' in row['text']
 assert 'Итого по указанным затратам' not in row['text'] and 'сумма '+label.lower() in row['text']


def test_incomplete_fees_explicit_subtotal_only(case):
 prohibit_old_path(case);row=submit(case,BLOCK.replace('Все сборы перечислены: да','Все сборы перечислены: нет'))
 assert 'Все сборы перечислены по сообщению: нет' in row['text']
 assert 'Сумма известных компонентов (не итог закупки): 1350 RUB' in row['text'] and 'полнота перечисления сборов' in row['text']
 assert 'Итого по указанным затратам' not in row['text']

@pytest.mark.parametrize('mutation',[
 lambda b:b+'\nЗакупка: 1 RUB; за штуку',lambda b:b.replace('Количество: 10','Количество: 0'),
 lambda b:b.replace('Количество: 10','Количество: 10.0'),lambda b:b.replace('Количество: 10','Количество: 1000000001'),
 lambda b:b.replace('Количество: 10','Количество: true'),lambda b:b.replace('100 RUB','100 rub'),
 lambda b:b.replace('100 RUB','100 USD RUB'),lambda b:b.replace('100 RUB','NaN RUB'),
 lambda b:b.replace('100 RUB','Infinity RUB'),lambda b:b.replace('100 RUB','-1 RUB'),
 lambda b:b.replace('100 RUB','+1 RUB'),lambda b:b.replace('100 RUB','1e2 RUB'),
 lambda b:b.replace('100 RUB','1 000 RUB'),lambda b:b.replace('100 RUB','01 RUB'),
 lambda b:b.replace('100 RUB','1.1234567 RUB'),lambda b:b.replace('100 RUB','9999999999999 RUB'),
 lambda b:b.replace('100 RUB','1\t00 RUB'),lambda b:b.replace('за штуку','за кг'),
 lambda b:b.replace('за штуку','за упаковку'),lambda b:b.replace('0 RUB; за партию','0%; за партию'),
 lambda b:b.replace('Затраты не включают друг друга: да','Затраты не включают друг друга: нет'),
 lambda b:b.replace('Все сборы перечислены: да','Все сборы перечислены: возможно'),
 lambda b:b.replace('Курсы: нет','Курсы: RUB->RUB=1'),
 lambda b:b.replace('Дата курсов: не применяется','Дата курсов: 2026-10-16'),
 lambda b:b+'\nНалог: 0',lambda b:b+'\n'+'X'*3000])
def test_invalid_or_ambiguous_inputs_no_core(case,monkeypatch,mutation):
 prohibit_old_path(case);monkeypatch.setattr(command,'quote_purchase',lambda **kw:pytest.fail('invalid core call'))
 row=submit(case,mutation(BLOCK));assert 'Расчёт не подготовлен.' in row['text'] and 'Итого по указанным затратам' not in row['text']

@pytest.mark.parametrize('replacement',[
 'RUB->CNY=0.1','CNY->USD=1; USD->RUB=90','CNY->RUB=12; CNY->RUB=13',
 'CNY->RUB=0','CNY->RUB=-1','CNY->RUB=NaN','CNY->RUB=1e1',
 'CNY->RUB=12; USD->RUB=90','нет','CNY->RUB=1.1234567'])
def test_fx_missing_inverse_triangulated_duplicate_extra_or_invalid_denied(case,monkeypatch,replacement):
 prohibit_old_path(case);monkeypatch.setattr(command,'quote_purchase',lambda **kw:pytest.fail('unsupported FX core call'))
 row=submit(case,FOREIGN.replace('CNY->RUB=12',replacement));assert 'Расчёт не подготовлен.' in row['text'] and '12350' not in row['text']

@pytest.mark.parametrize('day',['2026-02-30','2026-2-1','сегодня','не применяется','[уточнить]'])
def test_no_invented_fx_date(case,monkeypatch,day):
 prohibit_old_path(case);monkeypatch.setattr(command,'quote_purchase',lambda **kw:pytest.fail('invalid date core call'))
 row=submit(case,FOREIGN.replace('2026-10-16',day));assert 'Итого по указанным затратам' not in row['text']


def test_comma_literals_explicit_zeros_not_unknown(case):
 prohibit_old_path(case);text=BLOCK.replace('100 RUB','0,5 RUB').replace('300 RUB','0 RUB').replace('50 RUB','0 RUB')
 row=submit(case,text);assert 'Итого по указанным затратам за партию: 5 RUB' in row['text'] and 'На одну одинаковую штуку: 0.5 RUB' in row['text']

@pytest.mark.parametrize('change',['staff','outsider','group','business','intelligence','scope','team'])
def test_authority_scope_before_core_or_output(case,monkeypatch,change):
 prohibit_old_path(case);monkeypatch.setattr(command,'quote_purchase',lambda **kw:pytest.fail('unauthorized core'))
 kwargs={}
 if change=='staff':kwargs=dict(sender='frost',source='telegram:2:1')
 elif change=='outsider':kwargs=dict(sender='outsider',source='telegram:9:1')
 elif change=='group':kwargs=dict(channel='group',source='telegram:-5:1')
 elif change=='business':case.c['business']='OTHER'
 elif change=='intelligence':case.e.intelligence.b='OTHER'
 elif change=='scope':kwargs=dict(source='legacy-source')
 else:case.c['team_roster_verified']=False
 assert submit(case,**kwargs) is None and not case.s.rows('SELECT id FROM outbox')


def test_replay_no_duplicate_core_or_answer(case,monkeypatch):
 prohibit_old_path(case);calls=[];core=command.quote_purchase
 def spy(**kw):calls.append(kw);return core(**kw)
 monkeypatch.setattr(command,'quote_purchase',spy)
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
 with pytest.raises(ValueError):submit(case)
 assert not case.s.rows('SELECT id FROM outbox') and not case.s.rows('SELECT id FROM events')

@pytest.mark.parametrize('text',['да','Давай','Подтверждаю задачу №2','Сократи ответ','Подробнее о втором бренде','Откуда ты это знаешь?','Рассчитай акцию','Создай задачу: рассчитай закупку','Рассчитай закупку по данным компании'])
def test_other_routes_not_intercepted(text):assert not is_purchase_quote_command(text)


def test_maximum_literal_quantity_and_repeating_per_unit_are_bounded(case):
 prohibit_old_path(case);text=FOREIGN.replace('Количество: 10','Количество: 1000000000').replace('100 CNY','999999999999.999999 CNY').replace('CNY->RUB=12','CNY->RUB=999999999999.999999')
 row=submit(case,text);assert 'Итого по указанным затратам' in row['text'] and len(row['text'])<=3000 and len(row['text'].encode('utf-16-le'))//2<=3500
 row=submit(case,BLOCK.replace('Количество: 10','Количество: 3'),event='repeating');assert 'Итого по указанным затратам' in row['text'] and len(row['text'])<=3000


def test_after_purchase_sources_do_not_select_old_receipt(case):
 class Previous:
  def propose(self,context):return dict(actions=[],summary='Synthetic',reply_text='Prior answer',intent='conversation',dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 case.e.model=Previous();case.e.reply('prior','test_operator','Напиши черновик',source_id='telegram:1:1',channel='bot');case.e.process('prior');case.d.flush()
 assert case.s.execute('SELECT phase FROM answer_citation_turns').fetchone()[0]=='recorded'
 prohibit_old_path(case);submit(case);case.d.flush();case.e.reply('source','test_operator','Откуда ты это знаешь?',source_id='telegram:1:3',channel='bot')
 assert 'сейчас недоступны или не подтверждены' in case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-citations:source'").fetchone()[0]


def test_nontext_or_outside_transaction_rejected(case):
 for invalid in [None,True,1,{},[]]:
  assert not is_purchase_quote_command(invalid)
  with pytest.raises(PurchaseQuoteUnavailable):build_purchase_quote(invalid)
 with pytest.raises(PurchaseQuoteUnavailable):command.handle_purchase_quote(case.e,'outside',dict(text=BLOCK))


def test_all_amounts_unknown_not_empty_sum_zero(case):
 prohibit_old_path(case)
 text='\n'.join(line.split(':',1)[0]+': [уточнить] RUB; за партию'
                if line.split(':',1)[0] in ('Закупка','Доставка','Комиссия','Прочие сборы') else line
                for line in BLOCK.splitlines())
 row=submit(case,text)
 assert 'Сумма известных компонентов: неизвестна — ни одна сумма не указана' in row['text']
 assert 'Итог партии и стоимость штуки: неизвестны' in row['text'] and ': 0 RUB' not in row['text']
