"""New engine rewrite boundary only, actual synthetic dispatcher delivery."""
import json
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
from goldway.intelligence import reader_key,delivery_permitted
from goldway.purchase_stock_preview_command import is_purchase_stock_command,build_purchase_stock_preview,PurchaseStockUnavailable
import goldway.purchase_stock_preview_command as command
from goldway.inventory_metrics import UNKNOWN, StockKind
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

BLOCK=('План закупки\nТовар: ALPHA\nВариант: S\nСклад: L\nПартия: B\nЕдиница: шт\n'
       'Дата наблюдения: 2026-10-16\nСвободный остаток: 30\nПлановое количество: 60\n'
       'Продано за период: 30\nДлительность периода, дней: 30\nДней в расчётном месяце: 30\n'
       'Низкий запас, месяцев: 0.5\nЗатоваривание, месяцев: 2\nБез продаж, дней: 30')


def submit(c,text=BLOCK,event='plan',sender='test_operator',channel='bot',source=None,task_id=None):
 c.e.reply(event,sender,text,source_id=source or ('telegram:1:2' if channel=='bot' else 'business:conn:500:2'),channel=channel,task_id=task_id);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',('GOLDWAY','purchase-stock:'+event)).fetchone()


def prohibit_old_path(c):
 def fail(*a,**kw):pytest.fail('unexpected model/retrieval/task-context path')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail
 c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail;c.e.intelligence.memory.get=fail

@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_core_plan_and_two_sides_threshold_crossing_delivery(case,monkeypatch,channel):
 prohibit_old_path(case);preview=command.procurement_preview;flags=command.stock_flags;plans=[];sides=[]
 def spy_plan(stock,plan):plans.append((stock,plan));return preview(stock,plan)
 def spy_flags(stock,sales,**kw):sides.append((stock,sales,kw));return flags(stock,sales,**kw)
 monkeypatch.setattr(command,'procurement_preview',spy_plan);monkeypatch.setattr(command,'stock_flags',spy_flags)
 monkeypatch.setattr(command,'coverage',lambda *a,**kw:pytest.fail('duplicate outside stock_flags coverage'))
 case.s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('GOLDWAY','t','Task','test_operator','step','criterion','confirmed','source',1)")
 before=case.s.rows('SELECT * FROM tasks');row=submit(case,channel=channel,task_id='t')
 assert len(plans)==1 and len(sides)==2 and plans[0][1].units==60 and sides[0][0].units==30 and sides[1][0].units==90
 stock,plan=plans[0];assert stock.kind is StockKind.FREE and stock.key==plan.key==sides[0][1].key==sides[1][0].key
 assert stock.observed_on==plan.observed_on==sides[1][0].observed_on and stock.unit==plan.unit=='шт'
 assert sides[1][0].source_id.startswith('supplied:hypothetical:')
 text=row['text'];assert 'Условный остаток после добавления: 90' in text and 'Покрытие до: 1 мес.' in text and 'Покрытие после: 3 мес.' in text
 assert 'По заданным порогам до: низкий запас — нет; затоваривание — нет' in text
 assert 'По заданным порогам после: низкий запас — нет; затоваривание — да' in text
 assert 'не прогноз спроса' in text and 'не рекомендация закупать' in text and 'их фактическое отсутствие не подтверждено' in text
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None
 assert before==case.s.rows('SELECT * FROM tasks') and not case.s.rows('SELECT id FROM usage')
 case.d.flush();assert case.s.execute('SELECT state FROM outbox WHERE id=?',(row['id'],)).fetchone()[0]=='sent'


def test_plan_zero_keeps_before_after_equal(case):
 prohibit_old_path(case);row=submit(case,BLOCK.replace('Плановое количество: 60','Плановое количество: 0'))
 assert 'Условный остаток после добавления: 30' in row['text'] and 'Покрытие до: 1 мес.' in row['text'] and 'Покрытие после: 1 мес.' in row['text']


def test_plan_unknown_preserves_honest_before_and_unknown_after(case):
 prohibit_old_path(case);row=submit(case,BLOCK.replace('Плановое количество: 60','Плановое количество: [уточнить]'))
 assert 'Условный остаток после добавления: [неизвестно]' in row['text'] and 'Покрытие до: 1 мес.' in row['text'] and 'Покрытие после: неизвестно' in row['text']

@pytest.mark.parametrize('label',['Свободный остаток','Вариант','Склад','Партия'])
def test_unknown_stock_or_dimensions_never_zero_or_synthetic_all(case,monkeypatch,label):
 prohibit_old_path(case);preview=command.procurement_preview;seen=[]
 def spy(stock,plan):seen.append((stock,plan));return preview(stock,plan)
 monkeypatch.setattr(command,'procurement_preview',spy)
 text='\n'.join(label+': [уточнить]' if line.startswith(label+':') else line for line in BLOCK.splitlines())
 row=submit(case,text);assert len(seen)==1 and 'Условный остаток после добавления: [неизвестно]' in row['text']
 assert 'Покрытие до: неизвестно' in row['text'] and 'Покрытие после: неизвестно' in row['text']
 stock=seen[0][0]
 value={'Свободный остаток':stock.units,'Вариант':stock.key.item.variant,'Склад':stock.key.location,'Партия':stock.key.batch}[label]
 assert value is UNKNOWN


def test_unknown_sales_keeps_expected_amount_but_both_coverages_unknown(case):
 prohibit_old_path(case);row=submit(case,BLOCK.replace('Продано за период: 30','Продано за период: [неизвестно]'))
 assert 'Условный остаток после добавления: 90' in row['text'] and 'Покрытие до: неизвестно' in row['text'] and 'Покрытие после: неизвестно' in row['text']

@pytest.mark.parametrize('label',['Низкий запас, месяцев','Затоваривание, месяцев','Без продаж, дней'])
def test_partial_thresholds_only_two_actual_coverages_no_flags(case,monkeypatch,label):
 prohibit_old_path(case);cover=command.coverage;seen=[]
 def spy(*a,**kw):seen.append(a[0].units);return cover(*a,**kw)
 monkeypatch.setattr(command,'coverage',spy);monkeypatch.setattr(command,'stock_flags',lambda *a,**kw:pytest.fail('invented thresholds'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+':'))
 row=submit(case,text);assert seen==[30,90] and 'Флаги до/после не рассчитаны' in row['text']
 assert 'Покрытие до: 1 мес.' in row['text'] and 'Покрытие после: 3 мес.' in row['text'] and label in row['text']

@pytest.mark.parametrize('stock,plan,before,after',[
 ('30','60','положительный остаток','положительный остаток'),
 ('0','60','неизвестно — остаток0/продажи0','положительный остаток'),
 ('0','0','неизвестно — остаток0/продажи0','неизвестно — остаток0/продажи0'),
 ('[уточнить]','60','неизвестно — нет достаточных','неизвестно — нет достаточных')])
def test_zero_sales_truthful_without_future_prediction(case,stock,plan,before,after):
 prohibit_old_path(case);text=BLOCK.replace('Свободный остаток: 30','Свободный остаток: '+stock).replace('Плановое количество: 60','Плановое количество: '+plan).replace('Продано за период: 30','Продано за период: 0')
 row=submit(case,text);assert 'Покрытие до: '+before in row['text'] and 'Покрытие после: '+after in row['text']
 assert 'не прогноз спроса' in row['text']

@pytest.mark.parametrize('label',['Товар','Единица','Дата наблюдения','Длительность периода, дней','Дней в расчётном месяце'])
def test_missing_required_constructors_no_fake_defaults(case,monkeypatch,label):
 prohibit_old_path(case);monkeypatch.setattr(command,'procurement_preview',lambda *a,**kw:pytest.fail('missing structural field core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+':'))
 row=submit(case,text);assert 'Покрытие до: неизвестно' in row['text'] and 'Нужно уточнить:' in row['text'] and label in row['text']

@pytest.mark.parametrize('mutation',[
 lambda b:b.replace('Плановое количество: 60','Плановое количество: -1'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: NaN'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: Infinity'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: 1e2'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: 1 000'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: 01'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: 1.1234567'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: 9999999999999'),
 lambda b:b.replace('Плановое количество: 60','Плановое количество: 6\t0'),
 lambda b:b.replace('Дата наблюдения: 2026-10-16','Дата наблюдения: 2026-02-30'),
 lambda b:b.replace('Дата наблюдения: 2026-10-16','Дата наблюдения: сегодня'),
 lambda b:b.replace('Длительность периода, дней: 30','Длительность периода, дней: 0'),
 lambda b:b.replace('Длительность периода, дней: 30','Длительность периода, дней: 30.5'),
 lambda b:b.replace('Дней в расчётном месяце: 30','Дней в расчётном месяце: 0'),
 lambda b:b.replace('Дней в расчётном месяце: 30','Дней в расчётном месяце: 36601'),
 lambda b:b.replace('Без продаж, дней: 30','Без продаж, дней: 0'),
 lambda b:b.replace('Низкий запас, месяцев: 0.5','Низкий запас, месяцев: 2'),
 lambda b:b+'\nПлановое количество: 10',lambda b:b+'\nЕдиница плана: кг',
 lambda b:b+'\nДата поставки: 2026-12-01',lambda b:b+'\nРезерв: 10',
 lambda b:b.replace('Товар: ALPHA','Товар: '+'X'*161),lambda b:b+'\n'+'x'*4000])
def test_invalid_whole_scenario_no_partial_core(case,monkeypatch,mutation):
 prohibit_old_path(case);monkeypatch.setattr(command,'procurement_preview',lambda *a,**kw:pytest.fail('invalid core'))
 row=submit(case,mutation(BLOCK));assert 'Сценарий не рассчитан.' in row['text'] and 'Условный остаток после добавления: 90' not in row['text']


def test_template_no_amount_unit_month_or_threshold_default(case,monkeypatch):
 prohibit_old_path(case);monkeypatch.setattr(command,'procurement_preview',lambda *a,**kw:pytest.fail('template core'))
 row=submit(case,'Проверь план закупки');assert row['text'].count(': [уточнить]')==14 and '30 дн.' not in row['text']

@pytest.mark.parametrize('change',['staff','outsider','group','business','intelligence','scope','team'])
def test_unauthorized_no_core_or_output(case,monkeypatch,change):
 prohibit_old_path(case);monkeypatch.setattr(command,'procurement_preview',lambda *a,**kw:pytest.fail('unauthorized core'))
 kwargs={}
 if change=='staff':kwargs=dict(sender='frost',source='telegram:2:1')
 elif change=='outsider':kwargs=dict(sender='outsider',source='telegram:9:1')
 elif change=='group':kwargs=dict(channel='group',source='telegram:-5:1')
 elif change=='business':case.c['business']='OTHER'
 elif change=='intelligence':case.e.intelligence.b='OTHER'
 elif change=='scope':kwargs=dict(source='legacy-source')
 else:case.c['team_roster_verified']=False
 assert submit(case,**kwargs) is None and not case.s.rows('SELECT id FROM outbox')


def test_replay_core_once_no_duplicate_queue(case,monkeypatch):
 prohibit_old_path(case);core=command.procurement_preview;seen=[]
 def spy(*a,**kw):seen.append(a);return core(*a,**kw)
 monkeypatch.setattr(command,'procurement_preview',spy)
 submit(case);submit(case);assert len(seen)==1 and len(case.s.rows('SELECT id FROM outbox'))==1

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

@pytest.mark.parametrize('text',['да','Давай','Подтверждаю задачу №2','Сократи ответ','Подробнее о втором бренде','Откуда ты это знаешь?','Рассчитай акцию','Рассчитай закупку','Рассчитай запас товара','Создай задачу: проверь план закупки'])
def test_other_routes_not_intercepted(text):assert not is_purchase_stock_command(text)


def test_maximum_bounded_labels_and_numeric_output(case):
 prohibit_old_path(case);text=BLOCK.replace('Товар: ALPHA','Товар: '+'Т'*160).replace('Вариант: S','Вариант: '+'В'*120).replace('Склад: L','Склад: '+'С'*120).replace('Партия: B','Партия: '+'П'*120).replace('Свободный остаток: 30','Свободный остаток: 999999999999.999999').replace('Плановое количество: 60','Плановое количество: 999999999999.999999').replace('Продано за период: 30','Продано за период: 0.000001')
 row=submit(case,text);assert 'Условный остаток после добавления: 1999999999999.999998' in row['text'] and len(row['text'])<=3000 and len(row['text'].encode('utf-16-le'))//2<=3500


def test_existing_i23_and_i22_recognizers_remain_disjoint():
 from goldway.stock_coverage_command import is_stock_coverage_command
 from goldway.purchase_quote_command import is_purchase_quote_command
 assert is_stock_coverage_command('Рассчитай запас товара') and not is_stock_coverage_command('Проверь план закупки')
 assert is_purchase_quote_command('Рассчитай закупку') and not is_purchase_quote_command('Проверь план закупки')


def test_following_sources_do_not_fall_back_to_old_receipt(case):
 class Previous:
  def propose(self,context):return dict(actions=[],summary='Synthetic',reply_text='Prior answer',intent='conversation',dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 case.e.model=Previous();case.e.reply('prior','test_operator','Напиши черновик',source_id='telegram:1:1',channel='bot');case.e.process('prior');case.d.flush()
 assert case.s.execute('SELECT phase FROM answer_citation_turns').fetchone()[0]=='recorded'
 prohibit_old_path(case);submit(case);case.d.flush();case.e.reply('source','test_operator','Откуда ты это знаешь?',source_id='telegram:1:3',channel='bot')
 assert 'сейчас недоступны или не подтверждены' in case.s.execute("SELECT text FROM outbox WHERE dedupe='answer-citations:source'").fetchone()[0]


def test_nontext_and_transaction_boundary(case):
 for invalid in [None,True,1,{},[]]:
  assert not is_purchase_stock_command(invalid)
  with pytest.raises(PurchaseStockUnavailable):build_purchase_stock_preview(invalid,business='GOLDWAY',event_id='e')
 with pytest.raises(PurchaseStockUnavailable):command.handle_purchase_stock(case.e,'outside',dict(text=BLOCK))


def test_returned_template_can_be_filled_and_sent_as_actual_next_turn(case,monkeypatch):
 prohibit_old_path(case);preview=command.procurement_preview;seen=[]
 def spy(*a,**kw):seen.append(a);return preview(*a,**kw)
 monkeypatch.setattr(command,'procurement_preview',spy)
 row=submit(case,'Проверь план закупки',event='template-turn')
 assert not seen
 replacements={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in BLOCK.splitlines()[1:]}
 filled='\n'.join(line.split(':',1)[0]+': '+replacements[line.split(':',1)[0]] if ': [уточнить]' in line else line for line in row['text'].splitlines())
 row=submit(case,filled,event='filled-template')
 assert len(seen)==1 and 'Условный остаток после добавления: 90' in row['text'] and 'Покрытие после: 3 мес.' in row['text']
