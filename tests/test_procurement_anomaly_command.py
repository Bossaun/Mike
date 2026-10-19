"""I59 actual independent batch weight-logistics assessments via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.procurement_anomaly_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: Партия <A> & тест\nКоличество одинаковых единиц: 10\n'
 'Измеренный вес: 120; g; за единицу; нетто\nОриентир веса: 1; kg; за партию; нетто\n'
 'Допуск отклонения веса — коэффициент: 0.2\nЗакупка: 100; RUB; за единицу\nДоставка: 200; RUB; за партию\n'
 'Допуск доставки к закупке — коэффициент: 0.2\nЗакупка и доставка не включают друг друга: да\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='anomaly',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'procurement-anomaly-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='anomaly'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual_weight=command.core.assess_weight;actual_logistics=command.core.assess_logistics
 weights=[];logistics=[];weight_results=[];logistics_results=[]
 def weight(**kw):
  weights.append(kw);result=actual_weight(**kw);weight_results.append(result);return result
 def log(**kw):
  logistics.append(kw);result=actual_logistics(**kw);logistics_results.append(result);return result
 monkeypatch.setattr(command.core,'assess_weight',weight);monkeypatch.setattr(command.core,'assess_logistics',log)
 return weights,logistics,weight_results,logistics_results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_typed_pair_once_independent_results_private_dispatch_no_domain_io(case,monkeypatch,channel):
 weights,logistics,wr,lr=spy_core(monkeypatch)
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
  for name in ('quote_purchase','freight_from_weight','FXRate'):
   m.setattr(command.core,name,lambda *a,**kw:pytest.fail('no quote/tariff/FX recomputation'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(weights)==len(logistics)==len(wr)==len(lr)==1
 w=weights[0];l=logistics[0]
 assert w['quantity']==l['quantity']==10 and type(w['actual']) is type(w['expected']) is command.core.Weight
 assert w['actual'].amount==Decimal('120') and w['actual'].unit=='g' and w['actual'].basis=='per_unit' and w['expected'].basis=='batch'
 assert type(l['purchase']) is type(l['freight']) is command.core.Charge and type(l['purchase'].money) is command.core.Money
 assert l['purchase'].kind=='purchase' and l['freight'].kind=='freight' and l['purchase'].id!=l['freight'].id
 assert l['purchase'].included_cost_ids==l['freight'].included_cost_ids==()
 assert wr[0]['actual_batch_kg']==Decimal('1.200') and wr[0]['expected_batch_kg']==1 and wr[0]['relative_deviation']==Decimal('0.2') and wr[0]['status']=='within_threshold'
 assert lr[0]['ratio']==Decimal('0.2') and lr[0]['status']=='within_threshold'
 body=row['text'];assert 'Партия &lt;A&gt; &amp; тест' in body and 'Относительное отклонение — коэффициент: 0.200' in body
 assert 'Доставка / закупка для партии — коэффициент: 0.2' in body and 'ваше явное условие сценария' in body
 assert 'независимо не проверены' in body and 'не норма товара и не рекомендация покупать' in body
 for identifier in (l['purchase'].id,l['freight'].id):assert identifier not in body
 for raw in ('within_threshold','caller_threshold','flag','total_batch','per_unit','gross','%'):assert raw not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('actual,expected',[
 ('120; g; за единицу; нетто','1.2; kg; за партию; нетто'),
 ('1.2; kg; за партию; нетто','120; g; за единицу; нетто'),
 ('1200; g; за партию; брутто','0.12; kg; за единицу; брутто')])
def test_actual_g_kg_crossed_bases_batch_equivalence_no_double_quantity(case,monkeypatch,actual,expected):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 row=submit(case,changed(**{'Измеренный вес':actual,'Ориентир веса':expected,'Допуск отклонения веса — коэффициент':'0'}))
 assert wr[0]['actual_batch_kg']==wr[0]['expected_batch_kg']==Decimal('1.2') and wr[0]['relative_deviation']==0 and wr[0]['status']=='within_threshold'
 assert row['text']!=command.NOTICE


@pytest.mark.parametrize('actual,state',[('119','within_threshold'),('120','within_threshold'),('121','flag'),('0','flag')])
def test_actual_weight_strict_greater_equality_and_zero_measured(case,monkeypatch,actual,state):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 row=submit(case,changed(**{'Измеренный вес':actual+'; g; за единицу; нетто'}))
 assert wr[0]['status']==state and command._STATUS[state] in row['text']


@pytest.mark.parametrize('updates,reason',[
 ({'Измеренный вес':'неизвестно; g; за единицу; нетто'},'missing_weight_or_threshold'),
 ({'Ориентир веса':'неизвестно; kg; за партию; нетто'},'missing_weight_or_threshold'),
 ({'Допуск отклонения веса — коэффициент':'неизвестно'},'missing_weight_or_threshold'),
 ({'Ориентир веса':'0; kg; за партию; нетто'},'zero_expected_weight'),
 ({'Ориентир веса':'0; kg; за партию; нетто','Допуск отклонения веса — коэффициент':'неизвестно'},'missing_weight_or_threshold'),
 ({'Ориентир веса':'1; kg; за партию; брутто'},'incomparable_net_gross'),
 ({'Измеренный вес':'неизвестно; g; за единицу; нетто','Ориентир веса':'неизвестно; kg; за партию; брутто','Допуск отклонения веса — коэффициент':'неизвестно'},'incomparable_net_gross')])
def test_actual_weight_unknown_precedence_no_fabricated_normalized_masses(case,monkeypatch,updates,reason):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 row=submit(case,changed(**updates))
 assert wr[0]=={'status':'unknown','reason':reason,'relative_deviation':None}
 assert command._WEIGHT_REASONS[reason] in row['text'] and 'Массы партии по правилу' not in row['text']
 assert lr[0]['status']=='within_threshold'


@pytest.mark.parametrize('purchase,freight',[
 ('100; RUB; за единицу','200; RUB; за партию'),('1000; RUB; за партию','20; RUB; за единицу'),
 ('100; RUB; за единицу','20; RUB; за единицу'),('1000; RUB; за партию','200; RUB; за партию')])
def test_actual_logistics_crossed_bases_ratio_no_double_quantity(case,monkeypatch,purchase,freight):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 row=submit(case,changed(**{'Закупка':purchase,'Доставка':freight}))
 assert lr[0]['ratio']==Decimal('0.2') and lr[0]['quantity_basis']=='batch' and row['text']!=command.NOTICE


@pytest.mark.parametrize('freight,state',[('199','within_threshold'),('200','within_threshold'),('201','flag'),('0','within_threshold')])
def test_actual_logistics_strict_greater_equality_and_zero_freight(case,monkeypatch,freight,state):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 row=submit(case,changed(**{'Доставка':freight+'; RUB; за партию'}))
 assert lr[0]['status']==state and command._STATUS[state] in row['text']


@pytest.mark.parametrize('updates,reason',[
 ({'Закупка':'неизвестно; RUB; за единицу'},'missing_amount_or_threshold'),
 ({'Доставка':'неизвестно; RUB; за партию'},'missing_amount_or_threshold'),
 ({'Допуск доставки к закупке — коэффициент':'неизвестно'},'missing_amount_or_threshold'),
 ({'Закупка':'0; RUB; за единицу'},'zero_purchase_baseline'),
 ({'Закупка':'0; RUB; за единицу','Допуск доставки к закупке — коэффициент':'неизвестно'},'missing_amount_or_threshold')])
def test_actual_logistics_unknown_precedence_no_fake_ratio(case,monkeypatch,updates,reason):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 row=submit(case,changed(**updates))
 assert lr[0]=={'status':'unknown','reason':reason,'ratio':None}
 assert command._LOGISTICS_REASONS[reason] in row['text'] and 'Доставка / закупка для партии' not in row['text']
 assert wr[0]['status']=='within_threshold'


@pytest.mark.parametrize('which',['weight_flag','logistics_flag'])
def test_independent_flag_and_unknown_components_no_combined_verdict(case,monkeypatch,which):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 updates={'Измеренный вес':'121; g; за единицу; нетто','Доставка':'неизвестно; RUB; за партию'} if which=='weight_flag' else {'Измеренный вес':'неизвестно; g; за единицу; нетто','Доставка':'201; RUB; за партию'}
 row=submit(case,changed(**updates))
 assert {wr[0]['status'],lr[0]['status']}=={'flag','unknown'}
 assert command._STATUS['flag'] in row['text'] and command._STATUS['unknown'] in row['text']


@pytest.mark.parametrize('declaration',['нет','неизвестно','true'])
def test_independent_costs_require_explicit_yes_no_assumed_empty_overlap_ledger(case,monkeypatch,declaration):
 monkeypatch.setattr(command.core,'assess_weight',lambda **kw:pytest.fail('independence not admitted'))
 monkeypatch.setattr(command.core,'assess_logistics',lambda **kw:pytest.fail('independence not admitted'))
 assert submit(case,changed(**{'Закупка и доставка не включают друг друга':declaration}))['text']==command.NOTICE


def test_mixed_currency_whole_refusal_from_actual_core_no_partial_weight_result(case,monkeypatch):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 row=submit(case,changed(**{'Доставка':'200; USD; за партию'}))
 assert row['text']==command.NOTICE and len(weights)==len(logistics)==1 and len(wr)==1 and lr==[]


@pytest.mark.parametrize('mode',['role','overlap'])
def test_actual_typed_cost_roles_overlap_reject_not_new_ratio_algorithm(case,monkeypatch,mode):
 from dataclasses import replace
 actual=command.core.assess_logistics;calls=[]
 def assess(**kw):
  calls.append(kw)
  if mode=='role':kw['purchase']=replace(kw['purchase'],kind='fee')
  else:kw['freight']=replace(kw['freight'],included_cost_ids=(kw['purchase'].id,))
  return actual(**kw)
 monkeypatch.setattr(command.core,'assess_logistics',assess)
 assert submit(case)['text']==command.NOTICE and len(calls)==1


def test_largest_quantity_still_count_of_identical_units_actual_normalization(case,monkeypatch):
 weights,logistics,wr,lr=spy_core(monkeypatch)
 text=changed(**{'Количество одинаковых единиц':'1000000000','Измеренный вес':'1; g; за единицу; нетто','Ориентир веса':'1000000; kg; за партию; нетто'})
 row=submit(case,text)
 assert weights[0]['quantity']==10**9 and wr[0]['relative_deviation']==0 and wr[0]['actual_batch_kg']==1000000
 assert row['text']!=command.NOTICE


def test_literal_template_no_defaults_or_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'assess_weight',lambda **kw:pytest.fail('template core'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','согласен','закажи'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_field_no_basis_currency_threshold_guess(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'assess_weight',lambda **kw:pytest.fail('missing input core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Количество одинаковых единиц':'0'},{'Количество одинаковых единиц':'01'},{'Количество одинаковых единиц':'1.0'},{'Количество одинаковых единиц':'1000000001'},
 {'Измеренный вес':'120; lb; за единицу; нетто'},{'Измеренный вес':'120; g; неизвестно; нетто'},{'Ориентир веса':'1; kg; за партию; неизвестно'},
 {'Измеренный вес':'-1; g; за единицу; нетто'},{'Измеренный вес':'NaN; g; за единицу; нетто'},{'Измеренный вес':'1e2; g; за единицу; нетто'},
 {'Допуск отклонения веса — коэффициент':'-0.1'},{'Допуск доставки к закупке — коэффициент':'10%'},
 {'Закупка':'100; rub; за единицу'},{'Доставка':'200; RUB; неизвестно'},{'Закупка':'100; RUB'},
 {'Допуск отклонения веса — коэффициент':'1.123456789'},{'Доставка':'неизвестно; неизвестно; за партию'},
 {'Название сценария':'x'*81},{'Название сценария':'bad\rname'},{'Название сценария':'bad\tname'},{'Название сценария':'bad\x7fname'},{'Основание':'проверенные реальные сведения'}])
def test_closed_quantity_units_bases_kinds_money_coefficients_literals(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('suffix',['\n','\nЗакупка: 0; RUB; за партию','\nПоставщик: real','\nНорма отрасли: 0.1','x'*4000])
def test_no_extra_claims_identity_fields_duplicates_or_truncation(case,suffix):
 assert submit(case,BLOCK+suffix)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','weight_type','weight_extra','weight_float','weight_reason','weight_threshold','weight_unknown_extra','log_type','log_missing','log_nan','log_quantity','log_currency','log_basis','log_threshold'])
def test_whole_refusal_closed_actual_output_shapes_no_fabricated_optional_keys(case,monkeypatch,mode):
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*2000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*4000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 elif mode.startswith('weight'):
  actual=command.core.assess_weight
  def assess(**kw):
   r=actual(**kw)
   if mode=='weight_type':return SimpleNamespace()
   if mode=='weight_extra':r['fake_mass']=Decimal('0')
   if mode=='weight_float':r['relative_deviation']=0.2
   if mode=='weight_reason':r['reason']='real_fraud'
   if mode=='weight_threshold':r['max_relative_deviation']=Decimal('0.3')
   if mode=='weight_unknown_extra':return {'status':'unknown','reason':'missing_weight_or_threshold','relative_deviation':None,'actual_batch_kg':Decimal('0')}
   return r
  monkeypatch.setattr(command.core,'assess_weight',assess)
 else:
  actual=command.core.assess_logistics
  def assess(**kw):
   r=actual(**kw)
   if mode=='log_type':return SimpleNamespace()
   if mode=='log_missing':del r['quantity_basis']
   if mode=='log_nan':r['ratio']=Decimal('NaN')
   if mode=='log_quantity':r['quantity']=True
   if mode=='log_currency':r['currency']='USD'
   if mode=='log_basis':r['quantity_basis']='per_unit'
   if mode=='log_threshold':r['max_freight_to_purchase_ratio']=Decimal('0.3')
   return r
  monkeypatch.setattr(command.core,'assess_logistics',assess)
 assert submit(case,text)['text']==command.NOTICE


def test_restart_replay_one_call_each_no_domain_admission(case,monkeypatch):
 weights,logistics,wr,lr=spy_core(monkeypatch);row=submit(case)
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0])
 assert submit(case)['id']==row['id'] and len(weights)==len(logistics)==len(wr)==len(lr)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
