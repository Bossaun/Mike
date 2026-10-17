"""New actual Engine -> supplied typed catalog completeness boundary only."""
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
import sqlite3
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
from goldway.inventory_metrics import UNKNOWN
import goldway.catalog_completeness_command as command
MEMBERS = ['test_operator', 'frost', 'alen', 'nastya', 'dasha_it']


class NoExternal:
    def propose(self, *a, **k):
        pytest.fail('catalog completeness preview called model')
    def call(self, *a, **k):
        pytest.fail('catalog completeness preview called Telegram/provider')


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
        pytest.fail('catalog completeness preview reached context/refresh/external I/O/constructor')
    engine._context = forbidden
    engine.intelligence.sources.refresh = forbidden
    import builtins, socket, httpx
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(KnowledgeIndex, '__init__', forbidden)
    yield engine, store, clock
    store.db.close()



def line(item='SKU-A',variant='100 мл',day='2026-10-16',photo='да',price='100'):
 return f'Товар: {item}; Вариант: {variant}; Дата наблюдения: {day}; Фото: {photo}; Цена: {price}'


def block(*rows,currency='RUB'):
 return 'Проверка карточек товаров\nВалюта цен: '+currency+'\n'+'\n'.join(rows or [line()])


def reply(case,text=None,*,event='catalog',sender='test_operator',channel='bot',source=None):
 added=case[0].reply(event,sender,block() if text is None else text,channel=channel,
  source_id=source or ('business:conn:1:20' if channel=='business' else 'telegram:1:20'))
 rows=case[1].rows('SELECT * FROM outbox WHERE dedupe=?',('catalog-completeness:'+event,))
 return added,rows[0] if rows else None


@pytest.mark.parametrize('channel',['bot','business'])
def test_real_whole_sample_core_once_and_missing_unknown_zero_distinct(case,monkeypatch,channel):
 real=command.completeness;seen=[]
 def observe(rows):seen.append(rows);return real(rows)
 monkeypatch.setattr(command,'completeness',observe)
 text=block(line('A',photo='нет',price='отсутствует'),line('B',photo='неизвестно',price='неизвестно'),
  line('C',photo='да',price='0'),line('D',photo='да',price='12,50'))
 added,out=reply(case,text,channel=channel)
 assert added and len(seen)==1 and len(seen[0])==4
 rows=seen[0]
 assert rows[0].photo_present is False and rows[0].price is None
 assert rows[1].photo_present is UNKNOWN and rows[1].price is UNKNOWN
 assert rows[2].photo_present is True and type(rows[2].price) is Decimal and rows[2].price==0
 assert rows[3].price==Decimal('12.50')
 assert all(row.observed_on==date(2026,10,16) and row.source_id.startswith('supplied:catalog:') for row in rows)
 assert 'Фото по сообщению: есть 2, отсутствует 1, неизвестно 1.' in out['text']
 assert 'Цена по сообщению: числовая 2 (из них 0: 1), отсутствует 1, неизвестна 1.' in out['text']
 assert 'Без фото (1): A — 100 мл.' in out['text'] and 'Фото неизвестно (1): B — 100 мл.' in out['text']
 assert 'Без цены (1): A — 100 мл.' in out['text'] and 'Цена неизвестна (1): B — 100 мл.' in out['text']
 assert 'Цена 0 RUB (1): C — 100 мл.' in out['text']
 assert 'supplied:' not in out['text'] and 'выборка из 4 строк' in out['text']
 assert 'Это не проверка всего каталога' in out['text']
 assert out['recipient']=='test_operator' and out['task_id'] is None and out['parse_mode'] is None
 assert out['preferred_transport']==channel and not case[1].rows('SELECT * FROM tasks')
 case[0].process('catalog')


def test_exact_template_roundtrip_no_fake_price_or_date_constructors(case,monkeypatch):
 def forbidden(*a,**k):pytest.fail('template fabricated observations/zero prices')
 monkeypatch.setattr(command,'CatalogObservation',forbidden);monkeypatch.setattr(command,'completeness',forbidden)
 _,out=reply(case,'Проверь карточки товаров')
 assert out['text']==command._TEMPLATE and 'Цена: неизвестно' in out['text']
 _,again=reply(case,out['text'],event='again')
 assert 'Итоги выборки не рассчитаны' in again['text'] and 'Нужно уточнить' in again['text']
 assert 'Дата наблюдения: 2026-' not in again['text'] and 'числовая 0' not in again['text']


@pytest.mark.parametrize('text',['Проверь карточки товаров завтра','Проверь карточки','Проверка карточек товаров и заказов',
 'Не проверь карточки товаров','Что значит проверь карточки товаров?','Сверь пересчёт товара','Рассчитай запас товара'])
def test_no_substrings_or_existing_preview_collision(text):
 assert not command.is_catalog_completeness_command(text)


@pytest.mark.parametrize('currency',['[уточнить]','неизвестно',''])
def test_no_currency_required_when_all_prices_missing_or_unknown(case,monkeypatch,currency):
 real=command.completeness;seen=[]
 def observe(rows):seen.append(rows);return real(rows)
 monkeypatch.setattr(command,'completeness',observe)
 _,out=reply(case,block(line('A',price='отсутствует'),line('B',price='неизвестно'),currency=currency))
 assert len(seen)==1 and 'Валюта не указана; числовые цены не переданы.' in out['text']
 assert 'отсутствует 1, неизвестна 1' in out['text'] and 'Цена 0 (0)' in out['text']


@pytest.mark.parametrize('price',['0','0.000000','1','0,5'])
def test_every_numeric_price_including_zero_requires_explicit_currency_before_core(case,monkeypatch,price):
 def forbidden(*a,**k):pytest.fail('numeric price bypassed missing currency via fake value')
 monkeypatch.setattr(command,'CatalogObservation',forbidden);monkeypatch.setattr(command,'completeness',forbidden)
 _,out=reply(case,block(line(price=price),currency='[уточнить]'))
 assert 'Нужно уточнить: Валюта цен.' in out['text'] and 'Итоги выборки не рассчитаны' in out['text']
 assert 'Цена по сообщению' not in out['text']


@pytest.mark.parametrize('currency',['rub','RUB USD','₽','US','RUB; USD','123'])
def test_supplied_invalid_or_mixed_currency_rejected(case,currency):
 _,out=reply(case,block(currency=currency))
 assert out['text']==command._NOTICE


@pytest.mark.parametrize('value',['NaN','Infinity','true','false','-1','+1','-0','1e3','1 000','1_000','01','1.0000001','1000000000000','100 RUB','нет'])
def test_invalid_price_rejects_whole_block_before_core_even_after_valid_rows(case,monkeypatch,value):
 def forbidden(*a,**k):pytest.fail('partial valid rows reached completeness')
 monkeypatch.setattr(command,'CatalogObservation',forbidden);monkeypatch.setattr(command,'completeness',forbidden)
 _,out=reply(case,block(line('A'),line('B',price=value)))
 assert out['text']==command._NOTICE


@pytest.mark.parametrize('value',['есть','1','true','https://example.invalid/photo.jpg','нет фото','отсутствует'])
def test_photo_requires_explicit_flag_not_url_or_truthiness(case,value):
 _,out=reply(case,block(line(photo=value)))
 assert out['text']==command._NOTICE


@pytest.mark.parametrize('value',['завтра','16.10.26','2026-02-30','2026-10-16T10:00Z','0000-01-01'])
def test_explicit_calendar_dates_only(case,value):
 _,out=reply(case,block(line(day=value)))
 assert out['text']==command._NOTICE


def test_same_date_different_full_format_accepted(case):
 _,out=reply(case,block(line('A',day='16.10.2026'),line('B',day='2026-10-16')))
 assert 'выборка из 2 строк' in out['text'] and 'Дата наблюдения по сообщению: 2026-10-16.' in out['text']


@pytest.mark.parametrize('text',[block(line('A'),line('A')),block(line('A'),line('B',day='2026-10-17'))])
def test_duplicate_identity_or_mixed_observation_date_reject_before_core(case,monkeypatch,text):
 def forbidden(*a,**k):pytest.fail('duplicate/mixed-date block reached core')
 monkeypatch.setattr(command,'CatalogObservation',forbidden);monkeypatch.setattr(command,'completeness',forbidden)
 _,out=reply(case,text)
 assert out['text']==command._NOTICE


def test_distinct_explicit_variants_are_distinct_rows(case):
 _,out=reply(case,block(line('A','100 мл',photo='нет'),line('A','200 мл',price='отсутствует')))
 assert 'выборка из 2 строк' in out['text'] and 'Без фото (1): A — 100 мл' in out['text']
 assert 'Без цены (1): A — 200 мл' in out['text']


@pytest.mark.parametrize('field,value',[('item','[уточнить]'),('variant',''),('variant','неизвестно'),('day','[неизвестно]')])
def test_missing_identity_or_date_no_core_until_whole_block_known(case,monkeypatch,field,value):
 def forbidden(*a,**k):pytest.fail('required identity/date was invented')
 monkeypatch.setattr(command,'CatalogObservation',forbidden);monkeypatch.setattr(command,'completeness',forbidden)
 _,out=reply(case,block(line('A'),line(**{field:value})))
 assert 'Итоги выборки не рассчитаны' in out['text'] and 'строка 2' in out['text']
 assert 'выборка из' not in out['text']


@pytest.mark.parametrize('suffix',['; Фото: нет','; URL: https://example.invalid','; Валюта: USD'])
def test_extra_or_repeated_row_fields_failclosed(case,suffix):
 _,out=reply(case,block(line()+suffix))
 assert out['text']==command._NOTICE


def test_repeated_label_within_exact_five_fields_rejected(case):
 text='Проверка карточек товаров\nВалюта цен: RUB\nТовар: A; Вариант: 1; Дата наблюдения: 2026-10-16; Фото: да; фото: нет'
 _,out=reply(case,text)
 assert out['text']==command._NOTICE


@pytest.mark.parametrize('count',[0,13])
def test_sample_count_cap_no_partial_result(case,count):
 text='Проверка карточек товаров\nВалюта цен: RUB\n'+'\n'.join(line(str(i)) for i in range(count))
 _,out=reply(case,text)
 assert out['text']==command._NOTICE


def test_twelve_rows_all_lists_full_no_truncation_and_utf16_budget(case,monkeypatch):
 # Each label appears twice at most; strict UTF16 field bounds keep whole lists.
 rows=[line('😀'*23+chr(65+i),'💧'*16,photo='неизвестно',price='неизвестно') for i in range(12)]
 real=command.completeness;seen=[]
 def observe(obs):seen.append(obs);return real(obs)
 monkeypatch.setattr(command,'completeness',observe)
 _,out=reply(case,block(*rows,currency='[уточнить]'))
 assert len(seen)==1 and len(seen[0])==12 and 'Фото неизвестно (12)' in out['text'] and 'Цена неизвестна (12)' in out['text']
 assert len(out['text'].encode('utf-16-le'))//2<=3500 and '…' not in out['text']
 assert all('😀'*23+chr(65+i) in out['text'] for i in range(12))


@pytest.mark.parametrize('kwargs',[dict(item='x'*49),dict(variant='x'*33),dict(item='😀'*25),dict(variant='😀'*17)])
def test_identity_caps_reject_not_truncate(case,kwargs):
 _,out=reply(case,block(line(**kwargs)))
 assert out['text']==command._NOTICE


def test_plaintext_html_and_global_input_cap(case):
 _,out=reply(case,block(line('<b>A&B</b>','<i>one</i>',photo='нет',price='отсутствует')))
 assert '<b>A&B</b> — <i>one</i>' in out['text'] and out['parse_mode'] is None
 _,out=reply(case,'Проверка карточек товаров\nВалюта цен: RUB\n'+'x'*4000,event='oversize')
 assert out['text']==command._NOTICE


def test_runtime_denies_catalog_sources_photos_task_model_history_usage_reads_and_writes(case):
 prohibited={'tasks','sources','task_sources','knowledge_documents','knowledge_chunks','knowledge_readers',
  'intelligence_task_acl','usage','task_status_history','c07_checks','c07_series','sheet_snapshots'}
 def auth(action,table,*_):
  if table in prohibited and action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
  return sqlite3.SQLITE_OK
 case[1].db.set_authorizer(auth)
 try:assert 'выборка из 1 строк' in reply(case)[1]['text']
 finally:case[1].db.set_authorizer(None)


@pytest.mark.parametrize('sender,channel',[('alen','bot'),('test_operator','group'),('test_operator',None)])
def test_current_private_admin_only(case,sender,channel):
 _,out=reply(case,sender=sender,channel=channel)
 assert out is None and not case[1].rows('SELECT * FROM outbox')


def test_outsider_rejected_before_event(case):
 added,out=reply(case,sender='outsider')
 assert not added and out is None and not case[1].rows('SELECT * FROM events')


@pytest.mark.parametrize('field',['b','s','c'])
def test_intelligence_identity_gate(case,field):
 setattr(case[0].intelligence,field,object())
 assert reply(case)[1] is None


def test_duplicate_event_done_no_reprocessing_model(case):
 _,out=reply(case)
 assert reply(case)[0] is False
 case[0].process('catalog')
 assert case[1].rows('SELECT * FROM outbox')==[out]


@pytest.mark.parametrize('change',['role','business','intelligence'])
def test_after_queue_gate_drift_rolls_back(case,monkeypatch,change):
 real=case[1].queue
 def drift(*a,**k):
  out=real(*a,**k)
  if change=='role':case[0].c['admins']=[]
  elif change=='business':case[0].c['business']='OTHER'
  else:case[0].intelligence=None
  return out
 monkeypatch.setattr(case[1],'queue',drift)
 with pytest.raises(ValueError):reply(case)
 assert not case[1].rows('SELECT * FROM outbox') and not case[1].rows('SELECT * FROM events')


def test_before_queue_gate_drift_rolls_back(case,monkeypatch):
 real=command.build_catalog_completeness_preview
 def drift(*a,**k):
  out=real(*a,**k);case[0].c['admins']=[];return out
 monkeypatch.setattr(command,'build_catalog_completeness_preview',drift)
 with pytest.raises(ValueError):reply(case)
 assert not case[1].rows('SELECT * FROM outbox')


def test_frozen_draft_flags_and_pure_surrogate_output_guard(case,monkeypatch):
 draft=command.build_catalog_completeness_preview(block(),business='GOLDWAY',event_id='synthetic')
 assert draft.review_required and not draft.live_authorized
 with pytest.raises(FrozenInstanceError):draft.text='changed'
 with pytest.raises(command.CatalogCompletenessUnavailable):command.build_catalog_completeness_preview(block(line(item='\ud800')),business='GOLDWAY',event_id='synthetic')
 monkeypatch.setattr(command,'MAX_OUTPUT_UNITS',20)
 with pytest.raises(command.CatalogCompletenessUnavailable):command.build_catalog_completeness_preview(block(),business='GOLDWAY',event_id='synthetic')
