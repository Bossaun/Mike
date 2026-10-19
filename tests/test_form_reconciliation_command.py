"""I54 actual hypothetical form-account reconciliation via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.form_reconciliation_command as command

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


FIRST='Выручка <A>; деньги RUB; да; 1200; 2026-10-19; 2026-10-19T14:00:00+03:00; да; 1000; 2026-10-19'
SECOND='Продажи; единицы; да; 10; 2026-10-19; 2026-10-19T14:00:00+03:00; да; 10; 2026-10-19'
BLOCK=(command.HEADER+'\nНазвание сценария: Бланк <A> & тест\nНачало периода: 2026-10-12\nКонец периода: 2026-10-19\n'
 'Понедельник сценария: 2026-10-19\nЧасовой пояс IANA: Europe/Moscow\nМомент сценария: 2026-10-19T16:00:00+03:00\n'
 'Дата наблюдения полноты: 2026-10-19\nПолнота данных: да\nКоличество показателей: 2\nПоказатель 1: '+FIRST+'\nПоказатель 2: '+SECOND+'\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='forms',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'form-reconciliation-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='forms'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual=command.core.reconcile_forms;calls=[];results=[]
 def spy(**kw):
  calls.append(kw);result=actual(**kw);results.append(result);return result
 monkeypatch.setattr(command.core,'reconcile_forms',spy)
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


def missing_form(row=FIRST):return cell(row,**{'2':'нет','3':'не применяется','4':'не применяется','5':'не применяется'})
def missing_account(row=FIRST):return cell(row,**{'6':'нет','7':'не применяется','8':'не применяется'})


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_typed_reconciliation_difference_and_question_draft_own_dispatch(case,monkeypatch,channel):
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
  m.setattr(command.core,'kpi_snapshot',lambda *a,**kw:pytest.fail('not invented KPI targets'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(results)==1
 args=calls[0];lines=results[0]
 assert len(args['owners'])==1 and args['owners'][0].startswith('scenario-owner:') and args['owners'][0] not in MEMBERS
 assert type(args['coverage']) is command.core.Coverage and args['coverage'].complete
 assert len(args['definitions'])==len(args['forms'])==len(args['account'])==2
 assert all(type(d) is command.core.MetricDefinition and d.target is None and d.direction=='informational' for d in args['definitions'])
 assert args['definitions'][0].unit=='money' and args['definitions'][0].currency=='RUB' and args['definitions'][1].unit=='units' and args['definitions'][1].currency is None
 assert all(type(f) is command.core.FormValue and type(f.observation) is command.core.MetricObservation and type(f.observation.value) is Decimal for f in args['forms'])
 assert lines[0].difference==200 and lines[0].comparison=='discrepancy' and lines[0].submission_state=='on_time' and lines[0].question
 assert lines[1].difference==0 and lines[1].comparison=='match' and lines[1].question is None
 assert args['due_at'].isoformat()=='2026-10-19T15:00:00+03:00'
 body=row['text'];assert 'Бланк &lt;A&gt; &amp; тест' in body and 'Выручка &lt;A&gt;' in body and 'Разница бланк − учёт: 200' in body
 assert 'Черновик вопроса — не отправлен' in body and command.escape(lines[0].question) in body
 assert 'не проверены независимо' in body and 'Это не вывод о реальном отсутствии' in body and 'вопросы сотрудникам' in body
 for identity in (args['owners'][0],args['definitions'][0].id,args['forms'][0].observation.source_id,args['coverage'].source_id):assert identity not in body
 for raw in ('discrepancy','on_time','provisional','unknown_time','missing_after_cutoff'):assert raw not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('reported,accounted,difference',[('1000','1200','-200'),('0','0','0'),('-5','-2','-3'),('0.30','0.10','0.20')])
def test_actual_signed_reported_minus_accounted_no_manual_aggregation(case,monkeypatch,reported,accounted,difference):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'3':reported,'7':accounted})]))
 assert results[0][0].difference==Decimal(difference) and 'Разница бланк − учёт: '+difference in row['text']


@pytest.mark.parametrize('asof,state,question',[
 ('2026-10-19T14:59:59+03:00','pending',False),('2026-10-19T15:00:00+03:00','missing_after_cutoff',True),
 ('2026-10-19T15:00:01+03:00','missing_after_cutoff',True),('2026-10-19T12:00:00Z','missing_after_cutoff',True)])
def test_actual_missing_form_before_equal_after_cutoff_not_real_absence(case,monkeypatch,asof,state,question):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(rows_text([missing_form()]),**{'Момент сценария':asof}));line=results[0][0]
 assert calls[0]['forms']==() and line.submission_state==state and line.comparison=='missing_form' and line.reported is None and line.difference is None
 assert bool(line.question)==question and ('Черновик вопроса — не отправлен' in row['text'])==question


@pytest.mark.parametrize('submitted,state',[
 ('2026-10-19T15:00:00+03:00','on_time'),('2026-10-19T15:00:01+03:00','late'),('2026-10-19T12:00:00Z','on_time')])
def test_actual_present_form_time_inclusive_equality_and_offset_equivalent(case,monkeypatch,submitted,state):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**{'5':submitted})]))
 assert results[0][0].submission_state==state and row['text']!=command.NOTICE


@pytest.mark.parametrize('variant',['time','form_value','account_value','account_absent'])
def test_unknown_time_value_absent_account_not_zero_or_proven_submission(case,monkeypatch,variant):
 calls,results=spy_core(monkeypatch)
 data=cell(**{'5':'неизвестно'}) if variant=='time' else cell(**{'3':'неизвестно'}) if variant=='form_value' else cell(**{'7':'неизвестно'}) if variant=='account_value' else missing_account()
 row=submit(case,rows_text([data]));line=results[0][0]
 assert line.comparison==('missing_account' if variant=='account_absent' else 'unknown') and line.difference is None and line.question
 assert 'Черновик вопроса — не отправлен' in row['text']
 if variant=='time':assert calls[0]['forms'][0].submitted_at is None and line.reported==1200 and line.submission_state=='unknown_time'
 if variant=='account_absent':assert calls[0]['account']==() and line.accounted is None


@pytest.mark.parametrize('complete',['нет','неизвестно'])
def test_provisional_and_missing_after_cutoff_question_coexist(case,monkeypatch,complete):
 calls,results=spy_core(monkeypatch)
 row=submit(case,changed(rows_text([missing_form()]),**{'Полнота данных':complete}));line=results[0][0]
 assert calls[0]['coverage'].complete is False and line.comparison=='provisional' and line.submission_state=='missing_after_cutoff' and line.difference is None and line.question
 assert 'предварительно' in row['text'] and 'к условному сроку' in row['text'] and 'Черновик вопроса — не отправлен' in row['text']


@pytest.mark.parametrize('changes',[{'4':'2026-10-18'},{'8':'2026-10-18'},{'5':'2026-10-18T14:00:00+03:00'}])
def test_actual_early_observation_or_local_submission_date_provisional_no_final_difference(case,monkeypatch,changes):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**changes)]))
 assert results[0][0].comparison=='provisional' and results[0][0].difference is None and 'предварительно' in row['text']


@pytest.mark.parametrize('monday,start,offset,utc',[('2026-03-23','2026-03-16','+01:00','2026-03-23T14:00:00Z'),('2026-03-30','2026-03-23','+02:00','2026-03-30T13:00:00Z')])
def test_actual_iana_cutoff_dst_transition_and_numeric_offset_instants(case,monkeypatch,monday,start,offset,utc):
 calls,results=spy_core(monkeypatch)
 empty='Тест; единицы; нет; не применяется; не применяется; не применяется; нет; не применяется; не применяется'
 text=changed(rows_text([empty]),**{'Начало периода':start,'Конец периода':monday,'Понедельник сценария':monday,'Часовой пояс IANA':'Europe/Berlin','Момент сценария':utc,'Дата наблюдения полноты':monday})
 row=submit(case,text)
 assert calls[0]['due_at'].isoformat()==monday+'T15:00:00'+offset and results[0][0].submission_state=='missing_after_cutoff'
 assert monday+'T15:00:00'+offset in row['text']


@pytest.mark.parametrize('changes',[
 {'4':'2026-10-20'},{'4':'2026-10-11'},{'8':'2026-10-20'},{'5':'2026-10-19T16:00:01+03:00'}])
def test_actual_future_early_observation_submission_reject_whole(case,monkeypatch,changes):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([cell(**changes)]))
 assert row['text']==command.NOTICE and len(calls)==1 and results==[]


@pytest.mark.parametrize('changes',[{'Дата наблюдения полноты':'2026-10-20'},{'Дата наблюдения полноты':'2026-10-18'}])
def test_actual_future_or_premature_complete_coverage_refuses(case,changes):
 assert submit(case,changed(**changes))['text']==command.NOTICE


@pytest.mark.parametrize('mode',['period','unit','duplicate','foreign_owner'])
def test_actual_typed_metric_pairing_contract_refuses_corrupt_call_not_repaired(case,monkeypatch,mode):
 from dataclasses import replace
 actual=command.core.reconcile_forms;calls=[]
 def reconcile(**kw):
  calls.append(kw)
  if mode=='duplicate':kw['forms']=kw['forms']+(kw['forms'][0],)
  else:
   account=kw['account'][0]
   if mode=='period':account=replace(account,period=command.core.Period(account.period.start,command._day('2026-10-18')))
   if mode=='unit':account=replace(account,unit='units',currency=None)
   if mode=='foreign_owner':account=replace(account,owner='scenario-other')
   kw['account']=(account,)+kw['account'][1:]
  return actual(**kw)
 monkeypatch.setattr(command.core,'reconcile_forms',reconcile)
 assert submit(case)['text']==command.NOTICE and len(calls)==1


def test_three_distinct_informational_metric_cells_no_targets(case,monkeypatch):
 calls,results=spy_core(monkeypatch)
 row=submit(case,rows_text([FIRST,SECOND,'Еще; деньги USD; да; 3; 2026-10-19; неизвестно; нет; не применяется; не применяется']))
 assert len(calls[0]['definitions'])==len(results[0])==3 and results[0][2].comparison=='missing_account'
 assert 'Показатель 3' in row['text'] and all(d.target is None for d in calls[0]['definitions'])


def test_exact_template_no_fake_records_or_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'reconcile_forms',lambda **kw:pytest.fail('template core'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','отправь','согласен'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_static_field_no_dates_timezone_coverage_default(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'reconcile_forms',lambda **kw:pytest.fail('missing field core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('changes',[
 {'2':'неизвестно'},{'6':'неизвестно'},{'2':'нет'},{'6':'нет'}, {'3':'не применяется'},{'7':'не применяется'},
 {'3':'NaN'},{'3':'Infinity'},{'3':'1e2'},{'3':'true'},{'3':'1'*25},{'3':'1.123456789'},
 {'1':'единицы RUB'},{'1':'деньги rub'},{'1':'проценты'},{'0':'x'*81},{'0':'bad\tlabel'},
 {'4':'сегодня'},{'4':'2026-10-19T00:00:00Z'},{'5':'2026-10-19T14:00:00'},{'5':'2026-10-19T14:00:00+24:00'}])
def test_closed_metric_presence_units_values_dates_and_absent_groups(case,monkeypatch,changes):
 if changes.get('2')=='неизвестно' or changes.get('6')=='неизвестно':monkeypatch.setattr(command.core,'reconcile_forms',lambda **kw:pytest.fail('unknown presence must clarify'))
 assert submit(case,rows_text([cell(**changes)]))['text']==command.NOTICE


@pytest.mark.parametrize('text',[
 BLOCK.replace('Показатель 2:','Показатель 1:'),BLOCK.replace('Показатель 2:','Показатель 3:'),BLOCK.replace('Количество показателей: 2','Количество показателей: 1'),
 BLOCK.replace('Количество показателей: 2','Количество показателей: 0'),BLOCK.replace('Количество показателей: 2','Количество показателей: 4'),
 BLOCK+'\nСотрудник: real',BLOCK+'\nЦель: 10',BLOCK+'\n',rows_text([FIRST]*4)])
def test_count_ordinal_duplicates_extra_staff_targets_and_four_metrics_refuse(case,text):
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('changes',[
 {'Часовой пояс IANA':'Not/AZone'},{'Часовой пояс IANA':'../private'},{'Часовой пояс IANA':'x'*65},{'Понедельник сценария':'2026-10-20'},
 {'Момент сценария':'сейчас'},{'Момент сценария':'2026-10-19T16:00:00+03:60'},{'Начало периода':'2026-10-19'},
 {'Конец периода':'2027-10-20'},{'Полнота данных':'true'},{'Основание':'проверено'}, {'Название сценария':'x'*81},{'Название сценария':'bad\rname'},{'Название сценария':'bad\x7fname'}])
def test_static_closed_fields_iana_date_period_and_controls(case,changes):
 assert submit(case,changed(**changes))['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','type','count','foreign_owner','foreign_metric','duplicate','float','nan','comparison','submission','question','source'])
def test_whole_refusal_caps_and_actual_output_invariants(case,monkeypatch,mode):
 from dataclasses import replace
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*2000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*4000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.reconcile_forms
  def reconcile(**kw):
   r=actual(**kw);line=r[0]
   if mode=='type':return [*r]
   if mode=='count':return r[:1]
   if mode=='foreign_owner':return (replace(line,owner='foreign'),)+r[1:]
   if mode=='foreign_metric':return (replace(line,metric_id='foreign'),)+r[1:]
   if mode=='duplicate':return (line,line)
   if mode=='float':return (replace(line,difference=200.0),)+r[1:]
   if mode=='nan':return (replace(line,difference=Decimal('NaN')),)+r[1:]
   if mode=='comparison':return (replace(line,comparison='approved'),)+r[1:]
   if mode=='submission':return (replace(line,submission_state='sent'),)+r[1:]
   if mode=='question':return (replace(line,question='x'*513),)+r[1:]
   if mode=='source':return (replace(line,sources=('foreign',)),)+r[1:]
  monkeypatch.setattr(command.core,'reconcile_forms',reconcile)
 assert submit(case,text)['text']==command.NOTICE


def test_restart_replay_one_core_call_and_queue_no_domain_admission(case,monkeypatch):
 calls,_=spy_core(monkeypatch);row=submit(case)
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0])
 assert submit(case)['id']==row['id'] and len(calls)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
