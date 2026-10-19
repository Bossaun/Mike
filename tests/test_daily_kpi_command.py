"""I55 actual hypothetical daily KPI snapshot via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.daily_kpi_command as command

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


FIRST='Продажи <A>; количество; не меньше; 10; да; 12; 2026-10-20'
SECOND='Расход; деньги RUB; не больше; 100; да; 120; 2026-10-20'
BLOCK=(command.HEADER+'\nНазвание сценария: День <A> & тест\nНачало периода: 2026-10-19\nКонец периода: 2026-10-20\n'
 'Дата наблюдения полноты: 2026-10-20\nПолнота данных: да\nКоличество показателей: 2\nПоказатель 1: '+FIRST+'\nПоказатель 2: '+SECOND+'\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='daily-kpi',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'daily-kpi-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='daily-kpi'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual=command.core.kpi_snapshot;calls=[];results=[]
 def spy(**kw):
  calls.append(kw);result=actual(**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'kpi_snapshot',spy)
 return calls,results


def rows_text(rows,text=BLOCK):
 lines=[line for line in text.splitlines() if not line.startswith('Показатель ')]
 lines=[('Количество показателей: '+str(len(rows))) if line.startswith('Количество показателей: ') else line for line in lines]
 lines[-1:-1]=['Показатель '+str(i)+': '+row for i,row in enumerate(rows,1)]
 return '\n'.join(lines)


def cell(row=FIRST,**changes):
 parts=row.split('; ')
 for k,v in changes.items():parts[int(k)]=v
 return '; '.join(parts)


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_typed_daily_target_states_private_dispatch_no_domain_io(case,monkeypatch,channel):
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
  m.setattr(command.core,'reconcile_forms',lambda *a,**kw:pytest.fail('not old form reconciliation'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(results)==1
 args=calls[0];lines=results[0]
 assert args['require_daily'] is True and len(args['owners'])==1 and args['owners'][0].startswith('scenario-owner:') and args['owners'][0] not in MEMBERS
 assert type(args['coverage']) is command.core.Coverage and args['coverage'].complete
 assert (args['coverage'].period.end-args['coverage'].period.start).days==1
 assert len(args['definitions'])==len(args['observations'])==2
 assert all(type(d) is command.core.MetricDefinition and type(d.target) is Decimal for d in args['definitions'])
 assert args['definitions'][0].unit=='count' and args['definitions'][0].currency is None and args['definitions'][1].unit=='money' and args['definitions'][1].currency=='RUB'
 assert all(type(o) is command.core.MetricObservation and type(o.value) is Decimal for o in args['observations'])
 assert lines[0].status=='known' and lines[0].target_state=='met' and lines[1].status=='known' and lines[1].target_state=='missed'
 body=row['text'];assert 'День &lt;A&gt; &amp; тест' in body and 'Продажи &lt;A&gt;' in body
 assert command._TARGET['met'] in body and command._TARGET['missed'] in body and 'Значение: 12; цель: 10' in body
 assert 'не оценка их работы' in body and 'ежедневный контроль' in body
 for identity in (args['owners'][0],args['definitions'][0].id,args['observations'][0].source_id,args['coverage'].source_id):assert identity not in body
 for raw in ('at_least','missed','provisional','known','target_state','%','рейтинг','рекомендуем'):assert raw not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('direction,value,state',[
 ('не меньше','9','missed'),('не меньше','10','met'),('не меньше','11','met'),
 ('не больше','9','met'),('не больше','10','met'),('не больше','11','missed')])
def test_actual_direction_below_equal_above_not_wrapper_math(case,monkeypatch,direction,value,state):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'2':direction,'5':value})]));line=results[0][0]
 assert line.status=='known' and line.target_state==state and command._TARGET[state] in row['text']
 assert calls[0]['definitions'][0].target==10


@pytest.mark.parametrize('direction',['не меньше','не больше'])
def test_explicit_zero_value_target_not_missing(case,monkeypatch,direction):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'2':direction,'3':'0','5':'0'})]))
 assert calls[0]['definitions'][0].target==calls[0]['observations'][0].value==0
 assert results[0][0].target_state=='met' and 'Значение: 0; цель: 0' in row['text']


@pytest.mark.parametrize('direction',['не меньше','не больше'])
def test_unknown_target_none_known_value_stays_unknown_not_informational(case,monkeypatch,direction):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'2':direction,'3':'неизвестно'})]))
 assert calls[0]['definitions'][0].target is None and results[0][0].status=='known' and results[0][0].target_state=='unknown'
 assert 'цель: неизвестно' in row['text'] and command._TARGET['unknown'] in row['text']


def test_informational_target_explicit_nonapplicable_actual_state(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'2':'справочно','3':'не применяется'})]))
 assert calls[0]['definitions'][0].target is None and calls[0]['definitions'][0].direction=='informational'
 assert results[0][0].status=='known' and results[0][0].target_state=='informational' and 'цель: не применяется' in row['text']


@pytest.mark.parametrize('variant',['absent','value'])
def test_missing_or_none_observation_unknown_not_zero(case,monkeypatch,variant):
 calls,results=spy_core(monkeypatch)
 changes={'4':'нет','5':'не применяется','6':'не применяется'} if variant=='absent' else {'5':'неизвестно'}
 row=submit(case,rows_text([cell(**changes)]));line=results[0][0]
 assert line.value is None and line.status==line.target_state=='unknown' and 'Значение: неизвестно' in row['text']
 if variant=='absent':assert calls[0]['observations']==()
 else:assert len(calls[0]['observations'])==1 and calls[0]['observations'][0].value is None


@pytest.mark.parametrize('complete',['нет','неизвестно'])
def test_incomplete_unknown_coverage_value_provisional_no_target_claim(case,monkeypatch,complete):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Полнота данных':complete}))
 assert calls[0]['coverage'].complete is False and all(line.status=='provisional' and line.target_state=='unknown' for line in results[0])
 assert 'предварительное значение' in row['text'] and command._TARGET['met'] not in row['text'] and command._TARGET['missed'] not in row['text']


def test_actual_observation_before_daily_end_provisional_even_complete_coverage(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'6':'2026-10-19'})]))
 assert results[0][0].status=='provisional' and results[0][0].target_state=='unknown' and row['text']!=command.NOTICE


@pytest.mark.parametrize('changes',[
 {'Конец периода':'2026-10-21','Дата наблюдения полноты':'2026-10-21'},{'Дата наблюдения полноты':'2026-10-19'},{'Конец периода':'2026-10-19'}])
def test_actual_one_day_period_and_complete_before_end_refuse(case,monkeypatch,changes):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**changes))
 assert row['text']==command.NOTICE and results==[]
 if changes.get('Конец периода')=='2026-10-21':assert len(calls)==1 and calls[0]['require_daily'] is True


@pytest.mark.parametrize('day',['2026-10-18','2026-10-21'])
def test_actual_observation_before_period_or_after_declared_coverage_reject(case,monkeypatch,day):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'6':day})]))
 assert row['text']==command.NOTICE and len(calls)==1 and results==[]


@pytest.mark.parametrize('mode',['period','owner','unit','duplicate_observation','duplicate_definition'])
def test_actual_exact_typed_pairing_rejects_inconsistent_data_not_repaired(case,monkeypatch,mode):
 from dataclasses import replace
 actual=command.core.kpi_snapshot;calls=[]
 def snapshot(**kw):
  calls.append(kw)
  if mode=='duplicate_observation':kw['observations']+=(kw['observations'][0],)
  elif mode=='duplicate_definition':kw['definitions']+=(kw['definitions'][0],)
  else:
   observation=kw['observations'][0]
   if mode=='period':observation=replace(observation,period=command.core.Period(command._day('2026-10-18'),command._day('2026-10-19')))
   if mode=='owner':observation=replace(observation,owner='scenario-other')
   if mode=='unit':observation=replace(observation,unit='money',currency='RUB')
   kw['observations']=(observation,)+kw['observations'][1:]
  return actual(**kw)
 monkeypatch.setattr(command.core,'kpi_snapshot',snapshot)
 assert submit(case)['text']==command.NOTICE and len(calls)==1


def test_three_distinct_metric_targets_informational_no_aggregate_score(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([FIRST,SECOND,'Справка; количество; справочно; не применяется; да; 0; 2026-10-20']))
 assert len(calls[0]['definitions'])==len(results[0])==3 and results[0][2].target_state=='informational'
 assert 'Показатель 3' in row['text'] and 'рейтинг' not in row['text']


def test_literal_template_no_guessed_targets_dates_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'kpi_snapshot',lambda **kw:pytest.fail('template core'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_static_fields_no_daily_date_coverage_guess(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'kpi_snapshot',lambda **kw:pytest.fail('missing core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('changes',[
 {'4':'неизвестно'},{'4':'нет'},{'4':'нет','5':'не применяется'},{'5':'не применяется'},{'6':'не применяется'},
 {'3':'-1'},{'5':'-1'},{'3':'NaN'},{'5':'Infinity'},{'3':'1e2'},{'5':'true'},{'3':'1'*25},{'5':'1.123456789'},
 {'2':'справочно'},{'2':'справочно','3':'неизвестно'},{'2':'выше среднего'},{'3':'не применяется'},
 {'1':'количество RUB'},{'1':'деньги rub'},{'1':'деньги'},{'0':'x'*81},{'0':'bad\tlabel'},{'6':'сегодня'},{'6':'2026-10-20T00:00:00Z'}])
def test_closed_target_direction_units_value_presence_date_refuse(case,monkeypatch,changes):
 if changes.get('4')=='неизвестно':monkeypatch.setattr(command.core,'kpi_snapshot',lambda **kw:pytest.fail('unknown presence must clarify'))
 assert submit(case,rows_text([cell(**changes)]))['text']==command.NOTICE


@pytest.mark.parametrize('text',[
 BLOCK.replace('Показатель 2:','Показатель 1:'),BLOCK.replace('Показатель 2:','Показатель 3:'),BLOCK.replace('Количество показателей: 2','Количество показателей: 1'),
 BLOCK.replace('Количество показателей: 2','Количество показателей: 0'),BLOCK.replace('Количество показателей: 2','Количество показателей: 4'),BLOCK.replace('Количество показателей: 2','Количество показателей: 02'),
 BLOCK+'\nСотрудник: real',BLOCK+'\nРейтинг: 10',BLOCK+'\n',rows_text([FIRST]*4)])
def test_exact_count_ordinal_duplicates_fourth_metric_staff_and_rank_fields_refuse(case,text):
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('changes',[
 {'Начало периода':'сегодня'},{'Конец периода':'2026-02-30'},{'Конец периода':'10000-01-01'},{'Начало периода':'9999-12-31'},
 {'Полнота данных':'true'},{'Название сценария':'x'*81},{'Название сценария':'bad\rname'},{'Название сценария':'bad\x7fname'},{'Основание':'проверенная оценка'}])
def test_date_range_controls_closed_coverage_and_basis(case,changes):
 assert submit(case,changed(**changes))['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','count','owner','metric','duplicate','float','nan','negative','target','unit','status','state','provisional_met','source'])
def test_input_output_caps_and_actual_metric_result_invariants_whole_refusal(case,monkeypatch,mode):
 from dataclasses import replace
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*2000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*4000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.kpi_snapshot
  def snapshot(**kw):
   r=actual(**kw);line=r[0]
   if mode=='type':return [*r]
   if mode=='count':return r[:1]
   if mode=='owner':return (replace(line,owner='foreign'),)+r[1:]
   if mode=='metric':return (replace(line,metric_id='foreign'),)+r[1:]
   if mode=='duplicate':return (line,line)
   if mode=='float':return (replace(line,value=12.0),)+r[1:]
   if mode=='nan':return (replace(line,value=Decimal('NaN')),)+r[1:]
   if mode=='negative':return (replace(line,value=Decimal('-1')),)+r[1:]
   if mode=='target':return (replace(line,target=Decimal('11')),)+r[1:]
   if mode=='unit':return (replace(line,unit='money',currency='RUB'),)+r[1:]
   if mode=='status':return (replace(line,status='approved'),)+r[1:]
   if mode=='state':return (replace(line,target_state='assigned'),)+r[1:]
   if mode=='provisional_met':return (replace(line,status='provisional'),)+r[1:]
   if mode=='source':return (replace(line,sources=('foreign',)),)+r[1:]
  monkeypatch.setattr(command.core,'kpi_snapshot',snapshot)
 assert submit(case,text)['text']==command.NOTICE


def test_restart_replay_one_core_call_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0])
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
