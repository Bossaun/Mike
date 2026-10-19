"""I51 actual hypothetical influencer ROI projection via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.influencer_roi_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: Расчет <A> & тест\nНачало периода: 2026-10-09T09:00:00+03:00\nКонец периода: 2026-10-17T09:00:00+03:00\n'
 'Валюта расчета: RUB\nПолнота сведений: полно\nСвязь условного участника подтверждена: да\nБартер подтвержден: да\n'
 'Дата отгрузки: 2026-10-09T10:00:00+03:00\nСтоимость бартера: 100 RUB\nЗаказ передан: да\nДата заказа: 2026-10-10T10:00:00+03:00\n'
 'Выручка заказа до возвратов: 1000 RUB\nМаржа заказа до возвратов и бартера: 400 RUB\nВозврат передан: да\nДата возврата: 2026-10-11T10:00:00+03:00\n'
 'Уменьшение выручки при возврате: 100 RUB\nУменьшение маржи при возврате: 40 RUB\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='roi',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'influencer-roi-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='roi'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual=command.core.evaluate;calls=[];results=[]
 def spy(**kw):
  calls.append(kw);result=actual(**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'evaluate',spy)
 return calls,results


def absent(order='нет',refund='нет'):
 return {'Заказ передан':order,'Дата заказа':'не применяется','Выручка заказа до возвратов':'не применяется','Маржа заказа до возвратов и бартера':'не применяется',
  'Возврат передан':refund,'Дата возврата':'не применяется','Уменьшение выручки при возврате':'не применяется','Уменьшение маржи при возврате':'не применяется'}


def no_refund(presence='нет'):
 return {'Возврат передан':presence,'Дата возврата':'не применяется','Уменьшение выручки при возврате':'не применяется','Уменьшение маржи при возврате':'не применяется'}


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_typed_financials_coefficient_private_dispatch_no_domain_io(case,monkeypatch,channel):
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
 args=calls[0];r=results[0];f=r.financials
 assert args['records_complete'] is True and args['barter_limit'] is None and args['clicks']==args['reports']==()
 assert len(args['shipments'])==len(args['orders'])==len(args['refunds'])==1
 order=args['orders'][0];ship=args['shipments'][0];refund=args['refunds'][0]
 assert type(ship) is command.core.Shipment and type(order) is command.core.Order and type(refund) is command.core.Refund
 assert type(order.revenue_before_refunds) is command.core.Money and type(order.revenue_before_refunds.amount) is Decimal
 assert args['aliases'][0].influencer_id==args['promos'][0].influencer_id==args['influencer_id']
 assert order.promo_codes==(args['promos'][0].id,) and order.referral_alias==args['aliases'][0].id and refund.order_id==order.id
 assert f.state=='known' and (f.net_revenue,f.margin_after_refunds,f.barter_cost,f.net_after_barter,f.roi)==tuple(map(Decimal,('900','360','100','260','2.6')))
 assert r.limit_state=='unknown'
 body=row['text'];assert 'Расчет &lt;A&gt; &amp; тест' in body and 'ROI — коэффициент: 2.6' in body
 assert 'до бартера: 360 RUB' in body and 'и бартера: 260 RUB' in body and 'Выручка после учтённых возвратов: 900 RUB' in body
 assert 'из переданных предположений' in body and 'независимо не проверены' in body and 'не проверенная прибыль' in body
 for identity in (ship.id,order.id,refund.id,args['influencer_id'],order.source_id):assert identity not in body
 for raw in ('known','Financials','within_limit','incomplete_observations','%'):assert raw not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('updates,reason',[
 ({'Полнота сведений':'неполно'},'incomplete_observations'),({'Полнота сведений':'неизвестно'},'incomplete_observations'),
 ({'Связь условного участника подтверждена':'нет'},'unresolved_attribution'),({'Связь условного участника подтверждена':'неизвестно'},'unresolved_attribution'),
 ({'Бартер подтвержден':'неизвестно'},'unknown_barter'),({'Стоимость бартера':'неизвестно RUB'},'missing_amount'),
 ({'Выручка заказа до возвратов':'неизвестно RUB'},'missing_amount'),({'Маржа заказа до возвратов и бартера':'неизвестно RUB'},'missing_amount'),
 ({'Уменьшение выручки при возврате':'неизвестно RUB'},'missing_amount'),({'Уменьшение маржи при возврате':'неизвестно RUB'},'missing_amount'),
 ({'Выручка заказа до возвратов':'1000 USD'},'currency_conflict'),({'Стоимость бартера':'100 USD'},'currency_conflict'),
 ({'Уменьшение маржи при возврате':'40 USD'},'currency_conflict'),({'Валюта расчета':'USD'},'currency_conflict')])
def test_actual_unknown_reasons_preserve_partial_known_cost_no_zero_invention(case,monkeypatch,updates,reason):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**updates));f=results[0].financials
 assert f.state=='unknown' and reason in f.reasons and f.roi is f.net_revenue is f.net_after_barter is f.margin_after_refunds is None
 assert 'недостаточно определённых сведений' in row['text'] and command._REASONS[reason] in row['text'] and 'ROI — коэффициент: не определён' in row['text']
 if updates=={'Выручка заказа до возвратов':'неизвестно RUB'}:
  assert f.barter_cost==Decimal('100') and 'Стоимость бартера, учтённая правилом: 100 RUB' in row['text']
 if updates=={'Бартер подтвержден':'неизвестно'}:assert calls[0]['shipments'][0].barter_confirmed is None
 if 'Связь условного участника подтверждена' in updates:
  assert not calls[0]['aliases'][0].confirmed and not calls[0]['promos'][0].confirmed and results[0].unattributed_order_ids


@pytest.mark.parametrize('barter',['да','нет'])
def test_actual_zero_denominator_undefined_not_zero_or_infinity(case,monkeypatch,barter):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Стоимость бартера':'0 RUB','Бартер подтвержден':barter}));f=results[0].financials
 assert f.state=='zero_denominator' and f.barter_cost==0 and f.roi is None and f.net_after_barter==Decimal('360')
 assert 'ROI — коэффициент: не определён' in row['text'] and 'коэффициент не определён' in row['text']
 assert calls[0]['shipments'][0].barter_confirmed==(barter=='да')


def test_actual_negative_margin_and_roi_not_rated_or_recommended(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Маржа заказа до возвратов и бартера':'-50 RUB'}));f=results[0].financials
 assert f.margin_after_refunds==Decimal('-90') and f.net_after_barter==Decimal('-190') and f.roi==Decimal('-1.9')
 assert 'ROI — коэффициент: -1.9' in row['text'] and not any(word in row['text'] for word in ('рекомендуем','выгодно','увеличьте','снизьте'))


def test_actual_older_order_current_refund_no_repeated_revenue(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Дата заказа':'2026-09-01T10:00:00+03:00'}));f=results[0].financials
 assert not results[0].order_ids and results[0].refund_ids
 assert (f.net_revenue,f.margin_after_refunds,f.net_after_barter,f.roi)==tuple(map(Decimal,('-100','-40','-140','-1.4')))
 assert 'Выручка после учтённых возвратов: -100 RUB' in row['text']


@pytest.mark.parametrize('at,expected',[
 ('2026-10-17T09:00:00+03:00',('1000','400','300','3')),('2026-10-09T09:00:00+03:00',('900','360','260','2.6'))])
def test_actual_refund_halfopen_period_and_before_order_rejection(case,monkeypatch,at,expected):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Дата заказа':'2026-10-09T09:00:00+03:00','Дата возврата':at}))
 f=results[0].financials
 assert (f.net_revenue,f.margin_after_refunds,f.net_after_barter,f.roi)==tuple(map(Decimal,expected))
 assert row['text']!=command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Дата возврата':'2026-10-10T09:59:59+03:00'}, {'Уменьшение выручки при возврате':'1001 RUB'},
 {'Стоимость бартера':'-1 RUB'}, {'Выручка заказа до возвратов':'-1 RUB'}, {'Уменьшение выручки при возврате':'-1 RUB'}, {'Уменьшение маржи при возврате':'-1 RUB'},
 {'Бартер подтвержден':'нет'}, {'Уменьшение выручки при возврате':'1001 RUB','Дата возврата':'2026-10-18T10:00:00+03:00'}])
def test_actual_typed_money_and_refund_integrity_whole_refusal(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('order,refund',[('неизвестно','нет'),('нет','неизвестно'),('неизвестно','неизвестно')])
def test_unknown_presence_requires_incomplete_and_absent_records_not_zero_proof(case,monkeypatch,order,refund):
 calls,results=spy_core(monkeypatch)
 updates=absent(order,refund);updates['Полнота сведений']='неполно'
 row=submit(case,changed(**updates))
 assert calls[0]['orders']==calls[0]['refunds']==() and calls[0]['records_complete'] is False
 assert results[0].financials.state=='unknown' and 'неизвестно' in row['text']
 assert submit(case,changed(**absent(order,refund)),event='complete-contradiction')['text']==command.NOTICE


def test_explicit_complete_no_orders_is_supplied_dataset_not_inferred_zero(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**absent()));f=results[0].financials
 assert calls[0]['orders']==calls[0]['refunds']==() and f.state=='known' and f.net_revenue==0 and f.roi==Decimal('-1')
 assert 'из переданных предположений' in row['text']


def test_zero_order_revenue_does_not_confirm_barter(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 updates={**no_refund(),'Выручка заказа до возвратов':'0 RUB','Маржа заказа до возвратов и бартера':'0 RUB','Бартер подтвержден':'неизвестно'}
 submit(case,changed(**updates))
 assert calls[0]['shipments'][0].barter_confirmed is None and results[0].financials.state=='unknown'


def test_exact_decimal_precision_not_float_rounding(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 updates={**no_refund(),'Выручка заказа до возвратов':'0.30 RUB','Маржа заказа до возвратов и бартера':'0.30 RUB','Стоимость бартера':'0.10 RUB'}
 row=submit(case,changed(**updates));f=results[0].financials
 assert calls[0]['orders'][0].revenue_before_refunds.amount==Decimal('0.30') and f.net_after_barter==Decimal('0.20') and f.roi==Decimal('2')
 assert '0.20 RUB' in row['text'] and 'ROI — коэффициент: 2' in row['text']


def test_template_all_fields_no_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'evaluate',lambda **kw:pytest.fail('template core'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and len(row['text'].splitlines())==19 and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_field_no_default_money_date_currency_complete(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'evaluate',lambda **kw:pytest.fail('missing input core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Стоимость бартера':'NaN RUB'},{'Стоимость бартера':'Infinity RUB'},{'Стоимость бартера':'1e2 RUB'},{'Стоимость бартера':'True RUB'},{'Стоимость бартера':'100'},{'Стоимость бартера':'100 rub'},
 {'Стоимость бартера':'1'*25+' RUB'},{'Стоимость бартера':'1.123456789 RUB'},{'Стоимость бартера':'01 RUB'},{'Стоимость бартера':'+1 RUB'},
 {'Валюта расчета':'rub'},{'Название сценария':'x'*81},{'Название сценария':'bad\rname'},{'Название сценария':'bad\tname'},{'Название сценария':'bad\x7fname'},
 {'Основание':'проверено'},{'Полнота сведений':'true'},{'Связь условного участника подтверждена':'true'},{'Бартер подтвержден':'возможно'},
 {'Начало периода':'сегодня'},{'Конец периода':'2026-10-09T09:00:00+03:00'},{'Конец периода':'2027-10-19T09:00:00+03:00'},
 {'Дата отгрузки':'2026-10-09T10:00:00'},{'Дата заказа':'2026-10-10T10:00:00+24:00'},{'Дата возврата':'2026-10-11T10:00:00+03:60'},
 {'Заказ передан':'нет'},{'Возврат передан':'нет'},{'Заказ передан':'неизвестно'},{'Возврат передан':'неизвестно'}])
def test_closed_form_bounded_money_presence_literals_dates(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('suffix',['\n','\nВалюта расчета: RUB','\nБлогер: real','\nЛимит: 100 RUB','x'*4000])
def test_no_extra_identity_limit_records_or_input_truncation(case,suffix):
 assert submit(case,BLOCK+suffix)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','financial_type','state','reason','currency','float','nonfinite','unknown_with_total','known_missing_roi','zero_with_roi','foreign_source','limit'])
def test_whole_refusal_caps_and_actual_financial_output_invariants(case,monkeypatch,mode):
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
   if mode=='state':return replace(r,financials=replace(f,state='approved'))
   if mode=='reason':return replace(r,financials=replace(f,reasons=('invented',)))
   if mode=='currency':return replace(r,financials=replace(f,currency='USD'))
   if mode=='float':return replace(r,financials=replace(f,roi=2.6))
   if mode=='nonfinite':return replace(r,financials=replace(f,roi=Decimal('NaN')))
   if mode=='unknown_with_total':return replace(r,financials=replace(f,state='unknown',reasons=('missing_amount',)))
   if mode=='known_missing_roi':return replace(r,financials=replace(f,roi=None))
   if mode=='zero_with_roi':return replace(r,financials=replace(f,state='zero_denominator',barter_cost=Decimal('0')))
   if mode=='foreign_source':return replace(r,source_ids=('invented',))
   if mode=='limit':return replace(r,limit_state='within_limit')
  monkeypatch.setattr(command.core,'evaluate',evaluate)
 assert submit(case,text)['text']==command.NOTICE


def test_replay_one_evaluate_call_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
