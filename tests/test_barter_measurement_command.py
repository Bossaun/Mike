"""I49 supplied hypothetical barter measurement via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.barter_measurement_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: Бартер <A> & тест\nНачало периода: 2026-10-16T09:00:00+03:00\n'
 'Конец периода: 2026-10-17T09:00:00+03:00\nДата отгрузки: 2026-10-16T10:00:00+03:00\n'
 'Связь получателя подтверждена по сценарию: да\nСвязь промокода подтверждена по сценарию: нет\n'
 'Бартер подтвержден по сценарию: да\nСтоимость бартера: неизвестно\nВалюта неизвестной стоимости: RUB\n'
 'Дата наблюдения отчета: неизвестно\nРезультат в отчете: неизвестно\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='measurement',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'barter-measurement-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='measurement'") and not case.s.rows('SELECT * FROM outbox')



def spy_core(monkeypatch):
 actual=command.core.evaluate;calls=[];results=[]
 def spy(**kw):
  calls.append(kw);result=actual(**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'evaluate',spy)
 return calls,results


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_evaluate_missing_measurement_private_dispatch_no_domain_io(case,monkeypatch,channel):
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
 args=calls[0];result=results[0];shipment=args['shipments'][0]
 assert type(shipment) is command.core.Shipment and type(shipment.barter_cost) is command.core.Money
 assert shipment.barter_cost.amount is None and shipment.barter_cost.currency=='RUB'
 assert args['records_complete'] is False and args['barter_limit'] is None
 assert args['orders']==args['clicks']==args['refunds']==args['reports']==()
 assert len(args['aliases'])==len(args['promos'])==1 and type(args['aliases'][0]) is command.core.Binding
 assert args['aliases'][0].confirmed is True and args['promos'][0].confirmed is False
 assert result.shipment_ids==result.unmeasurable_barter_ids==(shipment.id,) and not result.unattributed_shipment_ids
 assert result.barter_states==((shipment.id,command.core.BarterState.CONFIRMED),)
 assert result.financials.state=='unknown' and result.limit_state=='unknown'
 assert all(value is None for value in (result.financials.net_revenue,result.financials.margin_after_refunds,result.financials.barter_cost,result.financials.net_after_barter,result.financials.roi))
 body=row['text'];assert 'Бартер &lt;A&gt; &amp; тест' in body
 assert 'нет подтверждённого способа измерения' in body and 'не проверены независимо' in body
 assert 'Стоимость явно неизвестна (RUB)' in body and 'неполны' in body
 for identity in (shipment.id,shipment.recipient_alias,args['influencer_id'],shipment.source_id,args['promos'][0].id):assert identity not in body
 for raw in ('unmeasurable_barter_ids','unknown','confirmed','ROI','within_limit','net_revenue'):assert raw not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('alias',['да','нет','неизвестно'])
@pytest.mark.parametrize('promo',['да','нет','неизвестно'])
@pytest.mark.parametrize('barter',['да','нет','неизвестно'])
def test_actual_classification_exact_confirmed_alias_promo_unknown_barter(case,monkeypatch,alias,promo,barter):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Связь получателя подтверждена по сценарию':alias,'Связь промокода подтверждена по сценарию':promo,'Бартер подтвержден по сценарию':barter}))
 args=calls[0];result=results[0];ship=args['shipments'][0]
 assert ship.barter_confirmed=={'да':True,'нет':False,'неизвестно':None}[barter]
 assert args['aliases'][0].confirmed==(alias=='да') and args['promos'][0].confirmed==(promo=='да')
 assert bool(result.unmeasurable_barter_ids)==(alias=='да' and promo!='да' and barter=='да')
 if alias!='да':
  assert result.unattributed_shipment_ids==(ship.id,) and not result.shipment_ids and not result.barter_states
  assert 'не отнесена к условному участнику' in row['text'] and 'Классификация отгрузки' not in row['text']
 else:
  assert result.shipment_ids==(ship.id,) and result.barter_states[0][1] is {'да':command.core.BarterState.CONFIRMED,'нет':command.core.BarterState.NOT_BARTER,'неизвестно':command.core.BarterState.UNKNOWN}[barter]
  if barter=='неизвестно':assert 'неизвестно, является ли отгрузка бартером' in row['text']
  if not result.unmeasurable_barter_ids:assert 'Это не доказывает эффективность' in row['text']


@pytest.mark.parametrize('at,mark,missing',[
 ('2026-10-16T10:00:00+03:00','да',False),('2026-10-17T08:59:59+03:00','да',False),
 ('2026-10-17T09:00:00+03:00','да',True),('2026-10-18T10:00:00+03:00','да',True),
 ('2026-10-16T11:00:00+03:00','нет',True)])
def test_actual_report_observation_cutoff_not_publication_or_efficacy(case,monkeypatch,at,mark,missing):
 calls,results=spy_core(monkeypatch);case.clock[0]+=500*86400
 row=submit(case,changed(**{'Дата наблюдения отчета':at,'Результат в отчете':mark}))
 report=calls[0]['reports'][0]
 assert type(report) is command.core.PlacementReport and report.result_present==(mark=='да')
 assert bool(results[0].unmeasurable_barter_ids)==missing
 assert 'Наблюдения на конце периода и позже не учитываются' in row['text']
 assert 'не проверяет его содержание' in row['text']


def test_actual_core_report_before_shipment_refuses_whole(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Дата наблюдения отчета':'2026-10-16T09:59:59+03:00','Результат в отчете':'да'}))
 assert row['text']==command.NOTICE and len(calls)==1 and results==[]


@pytest.mark.parametrize('at',['2026-10-16T09:00:00+03:00','2026-10-17T08:59:59+03:00','2026-10-16T06:00:00Z'])
def test_halfopen_shipment_inclusion_and_aware_offset(case,monkeypatch,at):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Дата отгрузки':at}))
 assert row['text']!=command.NOTICE and len(calls)==1 and results[0].shipment_ids


@pytest.mark.parametrize('at',['2026-10-16T08:59:59+03:00','2026-10-17T09:00:00+03:00'])
def test_halfopen_shipment_outside_refuses_not_empty_success(case,monkeypatch,at):
 monkeypatch.setattr(command.core,'evaluate',lambda **kw:pytest.fail('invalid period input called core'))
 assert submit(case,changed(**{'Дата отгрузки':at}))['text']==command.NOTICE


def test_template_no_defaults_or_short_consent_action(case,monkeypatch):
 monkeypatch.setattr(command.core,'evaluate',lambda **kw:pytest.fail('template core call'))
 row=submit(case,command.HEADER)
 assert row['text']==command.TEMPLATE and len(row['text'].splitlines())==13
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_each_missing_field_refuses_without_default(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'evaluate',lambda **kw:pytest.fail('missing input core call'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Основание':'проверено'}, {'Название сценария':'x'*81}, {'Название сценария':'bad\rname'}, {'Название сценария':'bad\tname'}, {'Название сценария':'bad\x7fname'},
 {'Стоимость бартера':'0'}, {'Стоимость бартера':'100'}, {'Валюта неизвестной стоимости':'rub'}, {'Валюта неизвестной стоимости':'₽'}, {'Валюта неизвестной стоимости':'RUB USD'},
 {'Связь получателя подтверждена по сценарию':'true'}, {'Связь промокода подтверждена по сценарию':'возможно'}, {'Бартер подтвержден по сценарию':'бартер'},
 {'Дата наблюдения отчета':'неизвестно','Результат в отчете':'да'}, {'Дата наблюдения отчета':'неизвестно','Результат в отчете':'нет'},
 {'Дата наблюдения отчета':'2026-10-16T11:00:00+03:00','Результат в отчете':'неизвестно'},
 {'Дата наблюдения отчета':'2026-10-16T11:00:00+03:00','Результат в отчете':'true'},
 {'Дата отгрузки':'сегодня'}, {'Дата отгрузки':'2026-10-16T10:00:00'}, {'Дата отгрузки':'2026-10-16T10:00:00+24:00'}, {'Дата отгрузки':'2026-10-16T10:00:00+03:60'},
 {'Дата отгрузки':'2026-02-30T10:00:00+03:00'}, {'Конец периода':'2026-10-16T09:00:00+03:00'}, {'Конец периода':'2027-10-18T09:00:00+03:00'}])
def test_closed_form_unknown_money_and_partial_report_claims_refuse(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('suffix',['\n','\nСтоимость бартера: неизвестно','\nПолучатель: real','\nПолнота заказов: да','x'*2000])
def test_no_extra_fields_real_id_claims_partial_subset_or_input_truncation(case,suffix):
 assert submit(case,BLOCK+suffix)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','tuple','foreign','duplicate','overlap','missing_partition','states','state_type','financial','limit','financial_amount','sources'])
def test_whole_refusal_budgets_and_unexpected_core_projection(case,monkeypatch,mode):
 from dataclasses import replace
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*1000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*2000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.evaluate
  def evaluate(**kw):
   r=actual(**kw);ship=r.shipment_ids[0]
   if mode=='type':return SimpleNamespace()
   if mode=='tuple':return replace(r,shipment_ids=[ship])
   if mode=='foreign':return replace(r,shipment_ids=('foreign',))
   if mode=='duplicate':return replace(r,shipment_ids=(ship,ship))
   if mode=='overlap':return replace(r,unattributed_shipment_ids=(ship,))
   if mode=='missing_partition':return replace(r,shipment_ids=(),unmeasurable_barter_ids=(),barter_states=())
   if mode=='states':return replace(r,barter_states=())
   if mode=='state_type':return replace(r,barter_states=((ship,'confirmed'),))
   if mode=='financial':return replace(r,financials=replace(r.financials,state='known'))
   if mode=='limit':return replace(r,limit_state='within_limit')
   if mode=='financial_amount':return replace(r,financials=replace(r.financials,barter_cost=Decimal('0')))
   if mode=='sources':return replace(r,source_ids=('invented',))
  monkeypatch.setattr(command.core,'evaluate',evaluate)
 assert submit(case,text)['text']==command.NOTICE


def test_replay_exactly_one_actual_evaluator_call_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
