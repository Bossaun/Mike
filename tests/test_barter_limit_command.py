"""I52 actual hypothetical barter limit projection via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.barter_limit_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: Лимит <A> & тест\nНачало периода: 2026-10-09T09:00:00+03:00\nКонец периода: 2026-10-17T09:00:00+03:00\n'
 'Валюта расчета: RUB\nПолнота сведений: полно\nУсловный лимит: 100 RUB\nКоличество отгрузок: 2\n'
 'Отгрузка 1: 2026-10-10T10:00:00+03:00; да; да; 40 RUB\n'
 'Отгрузка 2: 2026-10-11T10:00:00+03:00; да; да; 60 RUB\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='limit',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'barter-limit-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='limit'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual=command.core.evaluate;calls=[];results=[]
 def spy(**kw):
  calls.append(kw);result=actual(**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'evaluate',spy)
 return calls,results


def rows_text(rows,text=BLOCK):
 lines=[line for line in text.splitlines() if not line.startswith('Отгрузка ')]
 lines=[('Количество отгрузок: '+str(len(rows))) if line.startswith('Количество отгрузок: ') else line for line in lines]
 lines[-1:-1]=['Отгрузка '+str(i)+': '+row for i,row in enumerate(rows,1)]
 return '\n'.join(lines)


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_multishipment_cost_limit_private_dispatch_no_domain_io(case,monkeypatch,channel):
 calls,results=spy_core(monkeypatch)
 snapshots={t:case.s.rows('SELECT * FROM '+t) for t in ('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')}
 import sqlite3
 writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ and (table in snapshots or table in ('knowledge_chunks','knowledge_readers')):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('new command constructed Index'))
  m.setattr(command.core,'report_due_candidates',lambda *a,**kw:pytest.fail('no timers'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(results)==1
 args=calls[0];r=results[0]
 assert args['records_complete'] is True and type(args['barter_limit']) is command.core.Money and args['barter_limit'].amount==Decimal('100')
 assert args['orders']==args['clicks']==args['refunds']==args['reports']==args['promos']==()
 assert len(args['shipments'])==len(args['aliases'])==2
 assert all(type(s) is command.core.Shipment and type(s.barter_cost.amount) is Decimal for s in args['shipments'])
 assert all(type(b) is command.core.Binding and b.confirmed and b.influencer_id==args['influencer_id'] for b in args['aliases'])
 assert r.financials.barter_cost==Decimal('100') and r.limit_state=='within_limit'
 body=row['text'];assert 'Лимит &lt;A&gt; &amp; тест' in body and 'учтённая правилом: 100 RUB' in body and 'в пределах переданного лимита' in body
 assert 'одного анонимного участника' in body and 'не общий итог компании' in body and 'не согласованный реальный лимит' in body
 assert 'не разрешает расходы или отправку товара' in body
 for identity in (args['influencer_id'],args['shipments'][0].id,args['aliases'][0].id,args['shipments'][1].source_id):assert identity not in body
 for raw in ('ROI','выручка','маржа','within_limit','exceeded','Financials','остаток','рекомендуем'):assert raw not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('limit,state',[('101 RUB','within_limit'),('100 RUB','within_limit'),('99 RUB','exceeded'),('0 RUB','exceeded')])
def test_actual_aggregate_not_per_row_below_equal_above(case,monkeypatch,limit,state):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Условный лимит':limit}))
 assert results[0].financials.barter_cost==Decimal('100') and results[0].limit_state==state
 assert ('переданный лимит превышен' if state=='exceeded' else 'в пределах переданного лимита') in row['text']
 assert len(calls[0]['shipments'])==2


def test_zero_cost_zero_limit_within_independent_of_undefined_roi(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 text=rows_text(['2026-10-10T10:00:00+03:00; да; да; 0 RUB','2026-10-11T10:00:00+03:00; да; нет; неизвестно USD'])
 row=submit(case,changed(text,**{'Условный лимит':'0 RUB'}))
 r=results[0]
 assert r.financials.barter_cost==0 and r.financials.state=='zero_denominator' and r.financials.roi is None and r.limit_state=='within_limit'
 assert 'учтённая правилом: 0 RUB' in row['text'] and 'в пределах переданного лимита' in row['text'] and 'ROI' not in row['text']


@pytest.mark.parametrize('complete',['неполно','неизвестно'])
def test_actual_incomplete_unknown_not_zero_or_within(case,monkeypatch,complete):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Полнота сведений':complete}))
 assert calls[0]['records_complete'] is False and results[0].financials.barter_cost is None and results[0].limit_state=='unknown'
 assert 'сведения неполны' in row['text'] and 'Стоимость бартера, учтённая правилом: неизвестно' in row['text']


@pytest.mark.parametrize('record,reason',[
 ('2026-10-10T10:00:00+03:00; да; да; неизвестно RUB','missing_amount'),
 ('2026-10-10T10:00:00+03:00; да; неизвестно; 40 RUB','unknown_barter'),
 ('2026-10-10T10:00:00+03:00; нет; да; 40 RUB','unresolved_attribution'),
 ('2026-10-10T10:00:00+03:00; неизвестно; да; 40 RUB','unresolved_attribution'),
 ('2026-10-10T10:00:00+03:00; да; да; 40 USD','currency_conflict')])
def test_actual_selected_unknown_cost_barter_binding_and_currency(case,monkeypatch,record,reason):
 calls,results=spy_core(monkeypatch)
 text=rows_text([record,'2026-10-11T10:00:00+03:00; да; да; 60 RUB'])
 row=submit(case,text);r=results[0]
 assert r.financials.barter_cost is None and r.limit_state=='unknown' and reason in r.financials.reasons
 assert command._REASONS[reason] in row['text']
 if reason=='unknown_barter':assert calls[0]['shipments'][0].barter_confirmed is None
 if reason=='unresolved_attribution':assert not calls[0]['aliases'][0].confirmed and r.unattributed_shipment_ids


@pytest.mark.parametrize('limit,reason',[('неизвестно RUB','Сумма условного лимита неизвестна'),('100 USD','Валюта условного лимита отличается'),('неизвестно USD','Валюта условного лимита отличается')])
def test_actual_known_cost_unknown_or_currency_mismatch_limit_not_unknown_cost(case,monkeypatch,limit,reason):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Условный лимит':limit}));r=results[0]
 assert r.financials.barter_cost==Decimal('100') and r.limit_state=='unknown'
 assert 'учтённая правилом: 100 RUB' in row['text'] and 'Сравнение по сценарию: не определено' in row['text'] and reason in row['text']
 assert 'Почему стоимость не определена' not in row['text']


@pytest.mark.parametrize('at,known',[
 ('2026-10-09T09:00:00+03:00',False),('2026-10-17T09:00:00+03:00',True),
 ('2026-10-09T08:59:59+03:00',True),('2026-10-17T06:00:00Z',True)])
def test_actual_halfopen_unknown_outside_window_does_not_contaminate_selected_cost(case,monkeypatch,at,known):
 calls,results=spy_core(monkeypatch)
 text=rows_text(['2026-10-10T10:00:00+03:00; да; да; 40 RUB',at+'; неизвестно; неизвестно; неизвестно USD'])
 row=submit(case,text);r=results[0]
 assert (r.financials.barter_cost is not None)==known
 if known:assert r.financials.barter_cost==Decimal('40') and r.limit_state=='within_limit' and len(r.shipment_ids)==1
 else:assert r.financials.barter_cost is None and r.limit_state=='unknown'


@pytest.mark.parametrize('amount',['0 RUB','неизвестно RUB'])
def test_explicit_nonbarter_zero_unknown_cost_not_inferred_barter(case,monkeypatch,amount):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-10T10:00:00+03:00; да; нет; '+amount]))
 assert calls[0]['shipments'][0].barter_confirmed is False and results[0].financials.barter_cost==0 and results[0].limit_state=='within_limit'
 assert row['text']!=command.NOTICE


def test_twelve_real_typed_rows_and_exact_decimal_sum(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 text=rows_text(['2026-10-10T10:00:00+03:00; да; да; 0.10 RUB']*12)
 row=submit(case,changed(text,**{'Условный лимит':'1.20 RUB'}));r=results[0]
 assert len(calls[0]['shipments'])==len(calls[0]['aliases'])==12 and r.financials.barter_cost==Decimal('1.20') and r.limit_state=='within_limit'
 assert len(set(s.id for s in calls[0]['shipments']))==12 and 'Передано отгрузок: 12' in row['text'] and '1.20 RUB' in row['text']


def test_template_no_guessed_currency_limit_count_or_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'evaluate',lambda **kw:pytest.fail('template core'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_static_field_no_defaults(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'evaluate',lambda **kw:pytest.fail('missing core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('text',[
 BLOCK.replace('Отгрузка 2:','Отгрузка 1:'),BLOCK.replace('Отгрузка 2:','Отгрузка 3:'),BLOCK.replace('Отгрузка 1:','Отгрузка 01:'),
 BLOCK.replace('Отгрузка 2:','Отгрузка 13:'),BLOCK.replace('Количество отгрузок: 2','Количество отгрузок: 1'),BLOCK.replace('Количество отгрузок: 2','Количество отгрузок: 3'),
 BLOCK.replace('Количество отгрузок: 2','Количество отгрузок: 0'),BLOCK.replace('Количество отгрузок: 2','Количество отгрузок: 13'),BLOCK.replace('Количество отгрузок: 2','Количество отгрузок: 02'),
 BLOCK+'\nБлогер: real',BLOCK+'\nУсловный лимит: 10 RUB',BLOCK+'\n',rows_text(['2026-10-10T10:00:00+03:00; да; да; 1 RUB']*13)])
def test_declared_ordinal_count_duplicates_gaps_unknown_fields_and_thirteen_rows_refuse(case,text):
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('record',[
 '2026-10-10T10:00:00+03:00; да; да', '2026-10-10T10:00:00+03:00; да; да; 1 RUB; да',
 '2026-10-10T10:00:00+03:00; true; да; 1 RUB','2026-10-10T10:00:00+03:00; да; возможно; 1 RUB',
 'сегодня; да; да; 1 RUB','2026-10-10T10:00:00; да; да; 1 RUB','2026-10-10T10:00:00+24:00; да; да; 1 RUB','2026-10-10T10:00:00+03:60; да; да; 1 RUB',
 '2026-10-10T10:00:00+03:00; да; нет; 1 RUB','2026-10-10T10:00:00+03:00; да; да; -1 RUB',
 '2026-10-10T10:00:00+03:00; да; да; NaN RUB','2026-10-10T10:00:00+03:00; да; да; 1e2 RUB','2026-10-10T10:00:00+03:00; да; да; неизвестно',
 '2026-10-10T10:00:00+03:00; да; да; 1 rub','2026-10-10T10:00:00+03:00; да; да; 1.123456789 RUB',
 '2026-10-10T10:00:00+03:00; да; да; '+'1'*25+' RUB'])
def test_closed_whole_row_date_flags_decimal_currency_constructor_contradiction(case,record):
 assert submit(case,rows_text([record]))['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Условный лимит':'-1 RUB'},{'Условный лимит':'NaN RUB'},{'Условный лимит':'Infinity RUB'},{'Условный лимит':'True RUB'},{'Условный лимит':'100'},
 {'Валюта расчета':'rub'},{'Полнота сведений':'true'},{'Название сценария':'x'*81},{'Название сценария':'bad\rname'},{'Название сценария':'bad\tname'},{'Название сценария':'bad\x7fname'},
 {'Основание':'согласовано'},{'Конец периода':'2026-10-09T09:00:00+03:00'},{'Конец периода':'2027-10-19T09:00:00+03:00'}])
def test_closed_static_fields_limit_dates_and_literals(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','financial_type','state','cost_float','cost_nan','cost_negative','unknown_cost_known_limit','reason','currency','foreign_row','duplicate_row','source'])
def test_input_output_caps_and_core_projection_invariants_whole_refusal(case,monkeypatch,mode):
 from dataclasses import replace
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*2000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*4000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.evaluate
  def evaluate(**kw):
   r=actual(**kw);f=r.financials
   if mode=='type':return SimpleNamespace()
   if mode=='financial_type':return replace(r,financials=SimpleNamespace())
   if mode=='state':return replace(r,limit_state='approved')
   if mode=='cost_float':return replace(r,financials=replace(f,barter_cost=100.0))
   if mode=='cost_nan':return replace(r,financials=replace(f,barter_cost=Decimal('NaN')))
   if mode=='cost_negative':return replace(r,financials=replace(f,barter_cost=Decimal('-1')))
   if mode=='unknown_cost_known_limit':return replace(r,financials=replace(f,barter_cost=None))
   if mode=='reason':return replace(r,financials=replace(f,reasons=('invented',)))
   if mode=='currency':return replace(r,financials=replace(f,currency='USD'))
   if mode=='foreign_row':return replace(r,shipment_ids=('foreign',))
   if mode=='duplicate_row':return replace(r,shipment_ids=(r.shipment_ids[0],r.shipment_ids[0]))
   if mode=='source':return replace(r,source_ids=('invented',))
  monkeypatch.setattr(command.core,'evaluate',evaluate)
 assert submit(case,text)['text']==command.NOTICE


def test_replay_one_core_call_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
