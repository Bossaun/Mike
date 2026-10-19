"""I50 fixed-interval hypothetical placement report due preview via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.placement_report_due_command as command

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


BLOCK=(command.HEADER+'\nНазвание сценария: Отчет <A> & тест\nДата отгрузки: 2026-10-09T12:00:00+03:00\n'
 'Момент сценария: 2026-10-16T12:00:00+03:00\nИнтервал сценария, часов: 168\nСтоимость и статус бартера: неизвестны\n'
 'Валюта неизвестной стоимости: RUB\nДата наблюдения отчета A: неизвестно\nРезультат в отчете A: неизвестно\n'
 'Дата наблюдения отчета B: неизвестно\nРезультат в отчете B: неизвестно\n'
 'Дата наблюдения отчета C: неизвестно\nРезультат в отчете C: неизвестно\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='report-due',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'placement-report-due-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='report-due'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual=command.core.report_due_candidates;calls=[];results=[]
 def spy(shipments,reports,**kw):
  calls.append((shipments,reports,kw));result=actual(shipments,reports,**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'report_due_candidates',spy)
 return calls,results


def reported(slot,at,mark):return {'Дата наблюдения отчета '+slot:at,'Результат в отчете '+slot:mark}


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_core_due_date_private_dispatch_no_domain_io(case,monkeypatch,channel):
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
  m.setattr(command.core,'evaluate',lambda *a,**kw:pytest.fail('no I49/financial evaluator'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(results)==1
 ships,reports,kw=calls[0];ship=ships[0];candidate=results[0][0]
 assert len(ships)==1 and reports==() and type(ship) is command.core.Shipment
 assert ship.barter_confirmed is None and type(ship.barter_cost) is command.core.Money and ship.barter_cost.amount is None and ship.barter_cost.currency=='RUB'
 assert type(candidate) is command.core.DueCandidate and candidate.shipment_id==ship.id and candidate.source_id==ship.source_id
 assert candidate.due_at==datetime(2026,10,16,9,tzinfo=timezone.utc)
 body=row['text'];assert 'Отчет &lt;A&gt; &amp; тест' in body and candidate.due_at.isoformat() in body
 assert 'В этом сценарии наступил срок проверки' in body and 'не доказывает отсутствие реального отчета' in body
 assert 'не согласование реального графика' in body and 'Таймер, запрос отчета блогеру' in body
 assert 'Стоимость и статус бартера неизвестны (RUB)' in body
 for identity in (ship.id,ship.recipient_alias,ship.source_id):assert identity not in body
 assert not any(raw in body for raw in ('DueCandidate','shipment_id','ROI','confirmed'))
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('asof,due',[
 ('2026-10-16T11:59:59+03:00',False),('2026-10-16T12:00:00+03:00',True),('2026-10-16T12:00:01+03:00',True),
 ('2026-10-09T11:59:59+03:00',False),('2026-10-16T09:00:00Z',True)])
def test_actual_168hours_boundary_inclusive_no_false_empty_completion(case,monkeypatch,asof,due):
 calls,results=spy_core(monkeypatch);case.clock[0]+=500*86400
 row=submit(case,changed(**{'Момент сценария':asof}))
 assert len(calls)==1 and bool(results[0])==due
 assert calls[0][2]['now'].timestamp()!=case.clock[0]
 if not due:
  assert 'Это не подтверждает реальную доставку отчета, публикацию или завершение работы' in row['text']
  assert 'Дата по правилу' not in row['text']


@pytest.mark.parametrize('ship,asof,expected',[
 ('2026-10-24T10:00:00+02:00','2026-10-31T09:00:00+01:00','2026-10-31T08:00:00+00:00'),
 ('2026-03-21T10:00:00+01:00','2026-03-28T11:00:00+02:00','2026-03-28T09:00:00+00:00')])
def test_actual_elapsed_utc_rule_offsets_across_clock_change_dates(case,monkeypatch,ship,asof,expected):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{'Дата отгрузки':ship,'Момент сценария':asof}))
 assert results[0][0].due_at.isoformat()==expected and expected in row['text']


@pytest.mark.parametrize('at,mark,due',[
 ('2026-10-16T11:59:59+03:00','да',False),('2026-10-16T12:00:00+03:00','да',False),
 ('2026-10-16T12:00:01+03:00','да',True),('2026-10-16T11:00:00+03:00','нет',True),
 ('2026-10-16T12:00:00+03:00','нет',True),('2026-10-17T12:00:00+03:00','нет',True)])
def test_actual_report_cutoff_inclusive_future_ignored_not_filtered_by_adapter(case,monkeypatch,at,mark,due):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**reported('A',at,mark)))
 report=calls[0][1][0]
 assert type(report) is command.core.PlacementReport and report.result_present==(mark=='да')
 assert bool(results[0])==due and 'включая точное совпадение' in row['text']


@pytest.mark.parametrize('marks,dates,due',[
 (('да','нет'),('2026-10-14T12:00:00+03:00','2026-10-15T12:00:00+03:00'),True),
 (('нет','да'),('2026-10-14T12:00:00+03:00','2026-10-15T12:00:00+03:00'),False),
 (('нет','да'),('2026-10-15T12:00:00+03:00','2026-10-14T12:00:00+03:00'),True),
 (('да','нет'),('2026-10-14T12:00:00+03:00','2026-10-16T12:00:01+03:00'),False),
 (('нет','да'),('2026-10-14T12:00:00+03:00','2026-10-16T12:00:01+03:00'),True),
 (('да','да'),('2026-10-15T12:00:00+03:00','2026-10-15T12:00:00+03:00'),False)])
def test_actual_latest_observation_newer_false_reopens_and_slot_order_not_priority(case,monkeypatch,marks,dates,due):
 calls,results=spy_core(monkeypatch)
 updates={**reported('A',dates[0],marks[0]),**reported('B',dates[1],marks[1])}
 row=submit(case,changed(**updates))
 assert len(calls[0][1])==2 and bool(results[0])==due and row['text']!=command.NOTICE


def test_three_actual_observations_latest_known_and_ordinal_display(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 updates={**reported('A','2026-10-15T12:00:00+03:00','нет'),**reported('B','2026-10-14T12:00:00+03:00','да'),**reported('C','2026-10-16T12:00:01+03:00','да')}
 row=submit(case,changed(**updates))
 assert len(calls[0][1])==3 and results[0]
 assert all('№'+str(i)+':' in row['text'] for i in (1,2,3))
 for report in calls[0][1]:assert report.id not in row['text'] and report.source_id not in row['text']


@pytest.mark.parametrize('at',['2026-10-15T12:00:00+03:00','2026-10-16T12:00:00+03:00'])
def test_actual_conflicting_simultaneous_known_observations_whole_reject(case,monkeypatch,at):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**{**reported('A',at,'да'),**reported('B',at,'нет')}))
 assert row['text']==command.NOTICE and len(calls)==1 and results==[]


def test_actual_future_conflicts_are_not_known_yet(case,monkeypatch):
 calls,results=spy_core(monkeypatch);at='2026-10-16T12:00:01+03:00'
 row=submit(case,changed(**{**reported('A',at,'да'),**reported('B',at,'нет')}))
 assert row['text']!=command.NOTICE and results[0] and len(calls[0][1])==2


def test_actual_report_before_shipment_reject_even_at_future_slice(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**reported('A','2026-10-09T11:59:59+03:00','да')))
 assert row['text']==command.NOTICE and len(calls)==1 and results==[]


def test_unknown_report_pairs_are_absent_objects_not_negative_reports(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(**reported('C','2026-10-15T12:00:00+03:00','нет')))
 assert len(calls[0][1])==1 and calls[0][1][0].result_present is False and '\n№3:' in row['text'] and '\n№1:' not in row['text']


def test_exact_template_requires_explicit_interval_and_no_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'report_due_candidates',lambda *a,**kw:pytest.fail('template core call'))
 row=submit(case,command.HEADER)
 assert row['text']==command.TEMPLATE and len(row['text'].splitlines())==14
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_each_missing_field_no_default_interval_currency_date(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'report_due_candidates',lambda *a,**kw:pytest.fail('missing input core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Интервал сценария, часов':'неизвестно'},{'Интервал сценария, часов':'24'},{'Интервал сценария, часов':'167'},{'Интервал сценария, часов':'169'},{'Интервал сценария, часов':'168.0'},{'Интервал сценария, часов':'0168'},
 {'Стоимость и статус бартера':'0'},{'Стоимость и статус бартера':'подтверждены'},{'Валюта неизвестной стоимости':'rub'},{'Валюта неизвестной стоимости':'₽'},
 {'Название сценария':'x'*81},{'Название сценария':'bad\rname'},{'Название сценария':'bad\tname'},{'Название сценария':'bad\x7fname'}, {'Основание':'проверено'},
 {'Дата отгрузки':'сегодня'},{'Дата отгрузки':'2026-10-09T12:00:00'},{'Момент сценария':'2026-10-16T12:00:00+24:00'},{'Момент сценария':'2026-10-16T12:00:00+03:60'},
 {'Дата отгрузки':'2026-02-30T12:00:00+03:00'}, {'Дата отгрузки':'9999-12-31T12:00:00+03:00'},
 {'Результат в отчете A':'да'}, {'Результат в отчете B':'нет'}, {'Дата наблюдения отчета C':'2026-10-15T12:00:00+03:00'},
 {'Дата наблюдения отчета A':'2026-10-15T12:00:00+03:00','Результат в отчете A':'true'}])
def test_closed_form_unknown_pairs_supported_interval_date_range_refuse(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('suffix',['\n','\nИнтервал сценария, часов: 168','\nБлогер: real','\nДата наблюдения отчета D: неизвестно','x'*2000])
def test_no_extra_recipients_records_partial_subset_or_truncation(case,suffix):
 assert submit(case,BLOCK+suffix)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','list','many','foreign_shipment','foreign_alias','foreign_source','naive_due','invalid_due'])
def test_input_output_caps_and_actual_candidate_invariants_whole_refuse(case,monkeypatch,mode):
 from dataclasses import replace
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*1000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*2000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.report_due_candidates
  def due(*a,**kw):
   result=actual(*a,**kw);candidate=result[0]
   if mode=='type':return (SimpleNamespace(),)
   if mode=='list':return list(result)
   if mode=='many':return (candidate,candidate)
   if mode=='foreign_shipment':return (replace(candidate,shipment_id='foreign'),)
   if mode=='foreign_alias':return (replace(candidate,recipient_alias='foreign'),)
   if mode=='foreign_source':return (replace(candidate,source_id='foreign'),)
   if mode=='naive_due':return (replace(candidate,due_at=datetime(2026,10,16)),)
   if mode=='invalid_due':return (replace(candidate,due_at='today'),)
  monkeypatch.setattr(command.core,'report_due_candidates',due)
 assert submit(case,text)['text']==command.NOTICE


def test_replay_one_actual_core_call_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')


@pytest.mark.parametrize('order',__import__('itertools').permutations(range(3)))
@pytest.mark.parametrize('future',[False,True])
def test_all_slot_permutations_preserve_older_conflict_and_future_cutoff(case,monkeypatch,order,future):
 calls,results=spy_core(monkeypatch)
 if future:
  # Known positive survives; contradictory future pair remains unknown.
  records=[('2026-10-17T12:00:00+03:00','да'),('2026-10-17T12:00:00+03:00','нет'),('2026-10-15T12:00:00+03:00','да')]
 else:
  # A newer known positive cannot hide an older known contradiction.
  records=[('2026-10-14T12:00:00+03:00','да'),('2026-10-14T12:00:00+03:00','нет'),('2026-10-15T12:00:00+03:00','да')]
 updates={}
 for slot,index in zip(('A','B','C'),order):updates.update(reported(slot,*records[index]))
 row=submit(case,changed(**updates))
 assert len(calls)==1 and len(calls[0][1])==3
 observed=[report.observed_at for report in calls[0][1]]
 assert observed==sorted(observed)
 if future:assert results==[()] and row['text']!=command.NOTICE
 else:assert results==[] and row['text']==command.NOTICE
