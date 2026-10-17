"""New Engine boundary for explicit supplied coverage only; no warehouse I/O."""
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal, localcontext
import sqlite3
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
import goldway.stock_coverage_command as command
from goldway.inventory_metrics import UNKNOWN, StockKind
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']


class NoExternal:
    def propose(self, *a, **k):
        pytest.fail('coverage preview called model')
    def call(self, *a, **k):
        pytest.fail('coverage preview called Telegram/provider')


@pytest.fixture
def case(monkeypatch):
    config = {'business': 'GOLDWAY', 'timezone': 'Europe/Moscow',
        'employees': {x: {'name': 'Synthetic '+x, 'telegram_user_id': i+1} for i, x in enumerate(MEMBERS)},
        'admins': ['test_operator'], 'business_account_user_id': 99,
        'team_intake': {'recipients': MEMBERS[:], 'started_at': 100},
        'allowed_live_recipients': MEMBERS[:], 'team_roster_verified': True, 'recipients_verified': True,
        'live_send': False, 'review_mode': True, 'sheets_write': False, 'daily_summary': False,
        'allow_onboarding_replies': False, 'allow_registered_replies': False,
        'allowed_group_ids': [], 'group_destinations': {}, 'meeting_recipient': None,
        'bot_user_id': 100, 'bot_user_id_verified': True,
        'intelligence': {'enabled': True, 'sources': []}}
    clock = [1792174600.0]
    store = Store(':memory:')
    engine = Engine(store, config, NoExternal(), lambda: clock[0])
    def forbidden(*a, **k):
        pytest.fail('coverage preview reached context/refresh/external I/O/constructor')
    engine._context = forbidden
    engine.intelligence.sources.refresh = forbidden
    import builtins, socket, httpx
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(KnowledgeIndex, '__init__', forbidden)
    yield engine, store, clock
    store.db.close()


DEFAULTS={'Товар':'SKU-SYNTHETIC','Вариант':'100 мл','Склад':'Склад сценария','Партия':'Партия сценария',
 'Единица':'шт','Дата наблюдения':'2026-10-16','Свободный остаток':'120','Продано за период':'60',
 'Длительность периода, дней':'30','Дней в расчётном месяце':'30',
 'Низкий запас, месяцев':'3','Затоваривание, месяцев':'6','Без продаж, дней':'30'}


def block(**changes):
 values=dict(DEFAULTS);values.update(changes)
 return 'Расчёт запаса\n'+'\n'.join(k+': '+v for k,v in values.items() if v is not None)


def reply(case,text=None,*,event='coverage',sender='test_operator',channel='bot',source=None):
 added=case[0].reply(event,sender,block() if text is None else text,channel=channel,
  source_id=source or ('business:conn:1:20' if channel=='business' else 'telegram:1:20'))
 rows=case[1].rows('SELECT * FROM outbox WHERE dedupe=?',('stock-coverage:'+event,))
 return added,rows[0] if rows else None


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_calls_real_stock_flags_for_explicit_scope(case,channel,monkeypatch):
 real=command.stock_flags;seen=[]
 def observe(stock,sales,**kwargs):
  seen.append((stock,sales,kwargs));return real(stock,sales,**kwargs)
 monkeypatch.setattr(command,'stock_flags',observe)
 added,out=reply(case,channel=channel)
 assert added and len(seen)==1
 stock,sales,kwargs=seen[0]
 assert stock.kind is StockKind.FREE and stock.units==Decimal('120') and sales.sold_units==Decimal('60')
 assert stock.key==sales.key and stock.unit==sales.unit=='шт' and stock.observed_on==sales.observed_on==date(2026,10,16)
 assert kwargs['month_days']==Decimal('30') and kwargs['thresholds'].low_months==Decimal('3')
 assert stock.source_id.startswith('supplied:free:') and sales.source_id.startswith('supplied:sales:')
 assert 'Расчётный запас: 2 мес.' in out['text'] and 'низкий запас — да; затоваривание — нет' in out['text']
 assert 'Только сценарий' in out['text'] and 'данные склада не проверены' in out['text']
 assert out['task_id'] is None and out['parse_mode'] is None and out['preferred_transport']==channel
 assert 'supplied:' not in out['text'] and not case[1].rows('SELECT * FROM tasks')
 assert case[1].rows('SELECT state,error FROM events')==[dict(state='done',error=None)]
 case[0].process('coverage')  # already done, never a model event.


def test_exact_natural_request_and_literal_template_roundtrip(case,monkeypatch):
 def forbidden(*a,**k):pytest.fail('template or incomplete fields fabricated core inputs')
 monkeypatch.setattr(command,'coverage',forbidden);monkeypatch.setattr(command,'stock_flags',forbidden)
 _,out=reply(case,'Рассчитай запас товара')
 assert out['text']==command._TEMPLATE
 _,again=reply(case,out['text'],event='roundtrip')
 assert 'Запас в месяцах: неизвестен' in again['text'] and 'Нужно уточнить:' in again['text']
 assert 'Расчётный запас: 0' not in again['text'] and '2026-' not in again['text']


@pytest.mark.parametrize('text',['Рассчитай запас товара завтра','Покажи запас','Сверь пересчёт товара',
 'Расчёт запаса и измени остаток','Что значит рассчитай запас товара?','Не рассчитай запас товара'])
def test_exact_recognizer_not_recount_or_substrings(text):
 assert not command.is_stock_coverage_command(text)


def test_no_thresholds_calls_real_coverage_without_threshold_invention(case,monkeypatch):
 seen=[];real=command.coverage
 def observe(*a,**k):seen.append((a,k));return real(*a,**k)
 def forbidden(*a,**k):pytest.fail('missing thresholds got inferred')
 monkeypatch.setattr(command,'coverage',observe);monkeypatch.setattr(command,'stock_flags',forbidden)
 _,out=reply(case,block(**{'Низкий запас, месяцев':None,'Затоваривание, месяцев':None,'Без продаж, дней':None}))
 assert len(seen)==1 and 'Расчётный запас: 2 мес.' in out['text']
 assert 'не рассчитаны' in out['text'] and 'Не заданы пороги' in out['text']


@pytest.mark.parametrize('key',['Товар','Единица','Дата наблюдения','Длительность периода, дней','Дней в расчётном месяце'])
def test_no_core_constructors_with_missing_explicit_essential_fields(case,key,monkeypatch):
 def forbidden(*a,**k):pytest.fail('missing structural input was invented')
 monkeypatch.setattr(command,'StockSnapshot',forbidden);monkeypatch.setattr(command,'SalesWindow',forbidden)
 _,out=reply(case,block(**{key:'[уточнить]'}))
 assert 'Запас в месяцах: неизвестен' in out['text'] and key in out['text']
 assert 'Расчётный запас:' not in out['text']


@pytest.mark.parametrize('key',['Свободный остаток','Продано за период','Вариант','Склад','Партия'])
def test_supported_unknown_reaches_real_core_unknown_not_zero(case,key,monkeypatch):
 real=command.stock_flags;seen=[]
 def observe(*a,**k):seen.append((a,k));return real(*a,**k)
 monkeypatch.setattr(command,'stock_flags',observe)
 _,out=reply(case,block(**{key:'неизвестно'}))
 assert len(seen)==1 and 'Запас в месяцах: неизвестен' in out['text']
 stock,sales=seen[0][0]
 if key=='Свободный остаток':assert stock.units is UNKNOWN
 elif key=='Продано за период':assert sales.sold_units is UNKNOWN
 else:assert UNKNOWN in (stock.key.item.variant,stock.key.location,stock.key.batch)
 assert 'Расчётный запас: 0' not in out['text']


@pytest.mark.parametrize('stock,sold,expected',[('0','60','Расчётный запас: 0 мес.'),
 ('0','0','запас в месяцах неизвестен (0/0)'),('120','0','срок исчерпания не определяется')])
def test_literal_zero_and_zero_sales_labels(case,stock,sold,expected):
 _,out=reply(case,block(**{'Свободный остаток':stock,'Продано за период':sold}))
 assert expected in out['text']
 assert 'Свободный остаток: '+stock in out['text'] and 'Продано за период: '+sold in out['text']
 if sold=='0' and stock!='0':assert 'не доказательство бесконечного реального запаса' in out['text']


@pytest.mark.parametrize('days,expected',[('29','отсутствие продаж — неизвестно'),('30','отсутствие продаж — да')])
def test_no_sales_flag_respects_explicit_observation_duration(case,days,expected):
 _,out=reply(case,block(**{'Продано за период':'0','Длительность периода, дней':days}))
 assert expected in out['text']


def test_supplied_month_convention_and_period_drive_ratio(case):
 _,out=reply(case,block(**{'Длительность периода, дней':'60','Дней в расчётном месяце':'20'}))
 assert 'Расчётный запас: 6 мес.' in out['text']
 assert 'затоваривание — нет' in out['text']  # exact over=6 uses strict >.


@pytest.mark.parametrize('value',['-1','+1','1e3','NaN','Infinity','true','1 000','1_000','1.2.3','01','1.0000001','1000000000000'])
def test_bad_quantity_never_falls_back_to_zero_or_model(case,value):
 _,out=reply(case,block(**{'Свободный остаток':value,'Дата наблюдения':'[уточнить]'}))
 assert out['text']==command._NOTICE


@pytest.mark.parametrize('field,value',[('Длительность периода, дней','0'),('Длительность периода, дней','1.5'),
 ('Длительность периода, дней','36601'),('Без продаж, дней','0'),('Дней в расчётном месяце','0'),
 ('Дней в расчётном месяце','36600.000001'),('Низкий запас, месяцев','6'),
 ('Дата наблюдения','завтра'),('Дата наблюдения','2026-02-30'),('Дата наблюдения','16.10.26'),
 ('Дата наблюдения','2026-10-16T10:00Z')])
def test_invalid_supplied_days_date_or_threshold_order_fixed_notice(case,field,value):
 _,out=reply(case,block(**{field:value}))
 assert out['text']==command._NOTICE


@pytest.mark.parametrize('suffix',['\nНеизвестное поле: 1','\nСвободный остаток: 5','\n свОбодный ОСТАТОК : 5',
 '\nЕдиница продаж: кг','\nДата продаж: 2026-10-15','\nСрок заказа: завтра'])
def test_duplicate_unknown_or_mixed_scope_fields_failclosed(case,suffix):
 _,out=reply(case,block()+suffix)
 assert out['text']==command._NOTICE


@pytest.mark.parametrize('change',['item','variant','unit'])
def test_per_field_caps(case,change):
 label={'item':'Товар','variant':'Вариант','unit':'Единица'}[change]
 cap={'item':160,'variant':120,'unit':40}[change]
 _,out=reply(case,block(**{label:'x'*(cap+1)}))
 assert out['text']==command._NOTICE


def test_size_control_and_literal_html_plaintext(case):
 _,out=reply(case,block(**{'Товар':'<b>Сценарий & товар</b>'}))
 assert '<b>Сценарий & товар</b>' in out['text'] and out['parse_mode'] is None
 _,oversize=reply(case,'Расчёт запаса\nТовар: '+'x'*4000,event='oversize')
 assert oversize['text']==command._NOTICE


def test_decimal_comma_and_explicit_full_date(case):
 _,out=reply(case,block(**{'Свободный остаток':'12,5','Продано за период':'2,5','Дата наблюдения':'16.10.2026'}))
 assert 'Расчётный запас: 5 мес.' in out['text'] and 'Дата наблюдения: 2026-10-16' in out['text']


def test_no_storage_sources_warehouse_model_or_history_reads_or_writes(case):
 prohibited={'tasks','sources','task_sources','knowledge_documents','knowledge_chunks','knowledge_readers',
  'intelligence_task_acl','usage','task_status_history','c07_checks','c07_series','sheet_snapshots'}
 def auth(action,table,*_):
  if table in prohibited and action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
  return sqlite3.SQLITE_OK
 case[1].db.set_authorizer(auth)
 try:assert 'Расчётный запас: 2 мес.' in reply(case)[1]['text']
 finally:case[1].db.set_authorizer(None)


@pytest.mark.parametrize('sender,channel',[('alen','bot'),('test_operator','group'),('test_operator',None)])
def test_existing_private_admin_only_no_recipient_expansion(case,sender,channel):
 _,out=reply(case,sender=sender,channel=channel)
 assert out is None and not case[1].rows('SELECT * FROM outbox')


def test_outsider_early_rejected(case):
 added,out=reply(case,sender='outsider')
 assert added is False and out is None and not case[1].rows('SELECT * FROM events')


def test_dedupe_same_event_one_queue_and_no_process_model(case):
 added,out=reply(case)
 assert added
 assert reply(case)[0] is False
 case[0].process('coverage')
 assert case[1].rows('SELECT * FROM outbox')==[out]


@pytest.mark.parametrize('field',['b','c','s'])
def test_intelligence_identity_drift_denied(case,field):
 setattr(case[0].intelligence,field,object())
 _,out=reply(case)
 assert out is None


@pytest.mark.parametrize('change',['role','scope','intelligence'])
def test_queue_authority_drift_rolls_back(case,monkeypatch,change):
 real=case[1].queue
 def drift(*a,**k):
  out=real(*a,**k)
  if change=='role':case[0].c['admins']=[]
  elif change=='scope':case[0].c['business']='OTHER'
  else:case[0].intelligence=None
  return out
 monkeypatch.setattr(case[1],'queue',drift)
 with pytest.raises((command.StockCoverageUnavailable,ValueError)):reply(case)
 assert not case[1].rows('SELECT * FROM outbox') and not case[1].rows('SELECT * FROM events')


def test_before_queue_scope_drift_rolls_back(case,monkeypatch):
 real=command.build_stock_coverage_preview
 def mutate(*a,**k):
  out=real(*a,**k);case[0].c['admins']=[];return out
 monkeypatch.setattr(command,'build_stock_coverage_preview',mutate)
 with pytest.raises((command.StockCoverageUnavailable,ValueError)):reply(case)
 assert not case[1].rows('SELECT * FROM outbox')


def test_input_maximum_astral_plaintext_and_preview_immutable(case):
 text=block(**{'Товар':'😀'*160,'Вариант':'😀'*120,'Склад':'😀'*120,'Партия':'😀'*120,'Единица':'😀'*40})
 draft=command.build_stock_coverage_preview(text,business='GOLDWAY',event_id='synthetic')
 assert draft.review_required and not draft.live_authorized and len(draft.text.encode('utf-16-le'))//2<=3500
 with pytest.raises(FrozenInstanceError):draft.text='changed'
 assert 'supplied:' not in draft.text


def test_output_guard_and_surrogate_pure_boundary(case,monkeypatch):
 monkeypatch.setattr(command,'MAX_OUTPUT_UNITS',50)
 with pytest.raises(command.StockCoverageUnavailable):command.build_stock_coverage_preview(block(),business='GOLDWAY',event_id='synthetic')
 with pytest.raises(command.StockCoverageUnavailable):command.build_stock_coverage_preview(block(**{'Товар':'\ud800'}),business='GOLDWAY',event_id='synthetic')


@pytest.mark.parametrize('key',['Низкий запас, месяцев','Затоваривание, месяцев','Без продаж, дней'])
def test_partial_thresholds_leave_flags_unknown_no_default(case,key,monkeypatch):
 def forbidden(*a,**k):pytest.fail('partial threshold set was silently completed')
 monkeypatch.setattr(command,'stock_flags',forbidden)
 _,out=reply(case,block(**{key:'[уточнить]'}))
 assert 'Расчётный запас: 2 мес.' in out['text'] and 'Флаги низкого запаса' in out['text']
 assert 'не рассчитаны' in out['text'] and key in out['text']
