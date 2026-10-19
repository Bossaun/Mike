"""I53 actual supplied two-week sales sample comparison via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.weekly_sales_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: Недели <A> & тест\nПонедельник текущей недели: 2026-10-12\nВалюта расчета: RUB\nКоличество строк: 6\n'
 'Строка 1: 2026-10-05; 100; 60; нет; RUB\nСтрока 2: 2026-10-06; 200; неизвестно; нет; RUB\n'
 'Строка 3: 2026-10-12; 200; 100; нет; RUB\nСтрока 4: 2026-10-13; 100; 50; нет; RUB\n'
 'Строка 5: 2026-10-07; 999; 30; да; RUB\nСтрока 6: 2026-10-14; 888; неизвестно; да; RUB\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='sales-weeks',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'weekly-sales-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='sales-weeks'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual=command.core.compare_weeks;calls=[];results=[]
 def spy(records,**kw):
  calls.append((records,kw));result=actual(records,**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'compare_weeks',spy)
 return calls,results


def rows_text(rows,text=BLOCK):
 lines=[line for line in text.splitlines() if not line.startswith('Строка ')]
 lines=[('Количество строк: '+str(len(rows))) if line.startswith('Количество строк: ') else line for line in lines]
 lines[-1:-1]=['Строка '+str(i)+': '+row for i,row in enumerate(rows,1)]
 return '\n'.join(lines)


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_sale_compare_sample_financials_private_dispatch_no_domain_io(case,monkeypatch,channel):
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
  m.setattr(command.core,'cost_flags',lambda *a,**kw:pytest.fail('not old cost preview'))
  m.setattr(command.core,'preview_promotion',lambda *a,**kw:pytest.fail('not old promotion calculator'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(results)==1
 records,args=calls[0];r=results[0]
 assert len(records)==6 and all(type(s) is command.core.Sale and type(s.revenue) is Decimal for s in records)
 assert args['week_start'].isoformat()=='2026-10-12' and args['currency']=='RUB'
 assert r.current.revenue==r.prior.revenue==Decimal('300')
 assert r.current.known_cost_revenue==Decimal('300') and r.prior.known_cost_revenue==Decimal('100')
 assert r.current.gross_profit==Decimal('150') and r.prior.gross_profit==Decimal('40')
 assert r.current.cost_coverage==1 and r.prior.cost_coverage==Decimal('0.5')
 assert r.revenue_delta==r.revenue_change_pct==0 and r.known_scope_gross_profit_delta==110 and r.gross_margin_change_pp==10
 assert r.prior.known_barter_cost==30 and r.current.known_barter_cost==0 and r.current.unknown_barter_cost_count==1
 body=row['text'];assert 'Недели &lt;A&gt; &amp; тест' in body and '10 п.п.' in body
 assert 'Себестоимость известна для 1 из 2' in body and 'Доля по количеству: 0.5' in body
 assert 'Выручка строк с известной себестоимостью: 100 RUB' in body and 'Прибыль только этих строк: 40 RUB' in body
 assert 'Охваты двух недель могут различаться' in body and 'Полнота реальных продаж не установлена' in body
 assert 'известный подытог их себестоимости: 0 RUB; с неизвестной себестоимостью: 1' in body
 assert 'Бартер исключён из обычной выручки' in body and 'Возвраты этим расчетом не учитываются' in body
 for s in records:assert s.id not in body
 assert not any(raw in body for raw in ('WeekMetrics','gross_profit','gross_margin_change_pp','рекомендуем'))
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


def test_unknown_cogs_not_zero_differing_known_scopes_and_none_deltas(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-05; 100; 60; нет; RUB','2026-10-12; 200; неизвестно; нет; RUB']))
 r=results[0]
 assert calls[0][0][1].cogs is None and r.current.revenue==200 and r.current.known_cost_revenue==0
 assert r.current.cost_coverage==0 and r.current.unknown_cost_count==1 and r.current.gross_profit is None and r.current.gross_margin_pct is None
 assert r.known_scope_gross_profit_delta is None and r.gross_margin_change_pp is None and r.revenue_change_pct==100
 assert 'Прибыль только этих строк: неизвестно' in row['text'] and 'Маржа того же известного охвата: не определено' in row['text']


def test_known_zero_cost_is_not_unknown_cost(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-12; 100; 0; нет; RUB']))
 assert calls[0][0][0].cogs==0 and results[0].current.gross_profit==100 and results[0].current.gross_margin_pct==100
 assert 'Прибыль только этих строк: 100 RUB' in row['text']


def test_zero_revenue_stays_ordinary_zero_denominator_and_empty_prior_not_actual_absence(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-12; 0; 0; нет; RUB']));r=results[0]
 assert calls[0][0][0].barter is False and r.current.sale_count==1 and r.current.barter_count==0 and r.current.gross_profit==0
 assert r.current.gross_margin_pct is None and r.prior.sale_count==0 and r.prior.cost_coverage is None and r.prior.gross_profit is None
 assert r.revenue_change_pct is r.known_scope_gross_profit_delta is r.gross_margin_change_pp is None
 assert 'изменение: не определено' in row['text'] and 'Пустая неделя выборки не доказывает отсутствие реальных продаж' in row['text']


def test_explicit_positive_revenue_barter_excluded_and_known_unknown_cost_subtotals(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-05; 1000; 30; да; RUB','2026-10-12; 2000; неизвестно; да; RUB']))
 r=results[0]
 assert r.current.revenue==r.prior.revenue==0 and r.current.sale_count==r.prior.sale_count==0
 assert r.prior.known_barter_cost==30 and r.current.unknown_barter_cost_count==1 and r.current.gross_profit is None
 assert 'не полный итог при неизвестных затратах' in row['text']


def test_actual_negative_gross_profit_and_signed_pp_delta_no_advice(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-05; 100; 50; нет; RUB','2026-10-12; 100; 150; нет; RUB']))
 r=results[0]
 assert r.current.gross_profit==-50 and r.current.gross_margin_pct==-50 and r.known_scope_gross_profit_delta==-100 and r.gross_margin_change_pp==-100
 assert '-100 п.п.' in row['text'] and not any(word in row['text'] for word in ('выгодно','снизьте','увеличьте'))


@pytest.mark.parametrize('day,scope',[('2026-10-05','prior'),('2026-10-11','prior'),('2026-10-12','current'),('2026-10-18','current')])
def test_actual_date_only_two_week_halfopen_boundaries_no_wallclock_default(case,monkeypatch,day,scope):
 calls,results=spy_core(monkeypatch);case.clock[0]+=500*86400
 row=submit(case,rows_text([day+'; 100; 60; нет; RUB']))
 assert getattr(results[0],scope).sale_count==1 and getattr(results[0],scope).revenue==100 and row['text']!=command.NOTICE
 assert type(calls[0][0][0].date).__name__=='date'


@pytest.mark.parametrize('day',['2026-10-04','2026-10-19'])
def test_outside_two_weeks_refuses_entire_sample_not_silent_discard(case,monkeypatch,day):
 monkeypatch.setattr(command.core,'compare_weeks',lambda *a,**kw:pytest.fail('out of window called core'))
 text=rows_text(['2026-10-12; 100; 60; нет; RUB',day+'; 200; 100; нет; RUB'])
 assert submit(case,text)['text']==command.NOTICE


def test_twelve_distinct_equal_valued_operations_not_deduplicated(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-12; 0.30; 0.10; нет; RUB']*12));records=calls[0][0]
 assert len(set(s.id for s in records))==12 and results[0].current.sale_count==12 and results[0].current.revenue==Decimal('3.60') and results[0].current.gross_profit==Decimal('2.40')
 assert '3.60 RUB' in row['text'] and '2.40 RUB' in row['text']


def test_mixed_row_currency_rejected_by_actual_core_not_converted_or_filtered(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text(['2026-10-12; 100; 60; нет; RUB','2026-10-13; 200; 100; нет; USD']))
 assert row['text']==command.NOTICE and len(calls)==1 and results==[] and len(calls[0][0])==2


def test_template_sample_only_no_completeness_or_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'compare_weeks',lambda *a,**kw:pytest.fail('template core'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert not command.is_command(text)
 assert 'Полнота' not in command.TEMPLATE


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_static_field_no_defaults(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'compare_weeks',lambda *a,**kw:pytest.fail('missing field core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('text',[
 BLOCK.replace('Строка 6:','Строка 1:'),BLOCK.replace('Строка 6:','Строка 7:'),BLOCK.replace('Строка 1:','Строка 01:'),
 BLOCK.replace('Количество строк: 6','Количество строк: 0'),BLOCK.replace('Количество строк: 6','Количество строк: 5'),BLOCK.replace('Количество строк: 6','Количество строк: 7'),
 BLOCK.replace('Количество строк: 6','Количество строк: 13'),BLOCK.replace('Количество строк: 6','Количество строк: 06'),
 BLOCK+'\nПолнота: полно',BLOCK+'\nБизнес: real',BLOCK+'\nВалюта расчета: RUB',BLOCK+'\n',rows_text(['2026-10-12; 100; 60; нет; RUB']*13)])
def test_exact_ordinal_count_missing_duplicate_extra_claims_thirteen_rows_refuse(case,text):
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('record',[
 '2026-10-12; -1; 0; нет; RUB','2026-10-12; 1; -1; нет; RUB','2026-10-12; неизвестно; 0; нет; RUB',
 '2026-10-12; 1; 0; неизвестно; RUB','2026-10-12; 1; 0; true; RUB','2026-10-12; 1; 0; нет',
 '2026-10-12; NaN; 0; нет; RUB','2026-10-12; Infinity; 0; нет; RUB','2026-10-12; 1e2; 0; нет; RUB','2026-10-12; True; 0; нет; RUB',
 '2026-10-12; 1.123456789; 0; нет; RUB','2026-10-12; '+'1'*25+'; 0; нет; RUB','2026-10-12; 01; 0; нет; RUB',
 'сегодня; 1; 0; нет; RUB','2026-10-12T00:00:00+03:00; 1; 0; нет; RUB','2026-02-30; 1; 0; нет; RUB','2026-10-12; 1; 0; нет; rub',
 '2026-10-12; 1; 0; нет; RUB; возврат'])
def test_closed_whole_row_date_nonnegative_money_barter_currency(case,record):
 assert submit(case,rows_text([record]))['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Понедельник текущей недели':'2026-10-13'},{'Понедельник текущей недели':'сегодня'},{'Понедельник текущей недели':'0001-01-01'},
 {'Понедельник текущей недели':'9999-12-27'},{'Валюта расчета':'rub'},{'Название сценария':'x'*81},
 {'Название сценария':'bad\rname'},{'Название сценария':'bad\tname'},{'Название сценария':'bad\x7fname'},{'Основание':'проверенная полнота'}])
def test_explicit_monday_date_range_currency_literal_caps(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','week_type','date','currency','count','coverage','float','nan','negative_revenue','scope','missing_profit','delta_type'])
def test_input_output_caps_and_actual_result_invariants_whole_refuse(case,monkeypatch,mode):
 from dataclasses import replace
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*2000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*4000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.compare_weeks
  def compare(*a,**kw):
   r=actual(*a,**kw);w=r.current
   if mode=='type':return SimpleNamespace()
   if mode=='week_type':return replace(r,current=SimpleNamespace())
   if mode=='date':return replace(r,current=replace(w,start=r.prior.start))
   if mode=='currency':return replace(r,current=replace(w,currency='USD'))
   if mode=='count':return replace(r,current=replace(w,sale_count=True))
   if mode=='coverage':return replace(r,current=replace(w,cost_coverage=Decimal('2')))
   if mode=='float':return replace(r,current=replace(w,revenue=300.0))
   if mode=='nan':return replace(r,current=replace(w,revenue=Decimal('NaN')))
   if mode=='negative_revenue':return replace(r,current=replace(w,revenue=Decimal('-1')))
   if mode=='scope':return replace(r,current=replace(w,unknown_cost_count=3))
   if mode=='missing_profit':return replace(r,current=replace(w,gross_profit=None))
   if mode=='delta_type':return replace(r,gross_margin_change_pp='10')
  monkeypatch.setattr(command.core,'compare_weeks',compare)
 assert submit(case,text)['text']==command.NOTICE


def test_replay_one_actual_core_call_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
