"""I58-B actual readonly typed baseline conflict consumer via private Engine."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.table_conflict_command as command

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


BASE_ONE='1; 10; r1; текст «  Old <A>  »; десятичное 2.00'
BASE_TWO='2; 20; r1; текст «Other»; десятичное 3.00'
BLOCK=(command.HEADER+'\nНазвание сценария: Конфликт <A> & тест\nИсходных строк: 2\nПроверяемых строк: 2\nКоличество колонок: 2\n'
 'Выбранный ключ: 1\nКоличество изменений: 1\nИсходная версия книги: v1\nИсходная версия листа: v1\nПроверяемая версия книги: v1\nПроверяемая версия листа: v1\n'
 'Колонка 1: Название <A>\nКолонка 2: Неизменяемое планом\nИсходная строка 1: '+BASE_ONE+'\nИсходная строка 2: '+BASE_TWO+'\n'
 'Проверяемая строка 1: '+BASE_ONE+'\nПроверяемая строка 2: '+BASE_TWO+'\nИзменение 1: 1; текст «New & B»\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='table-conflict',sender='test_operator',channel='bot',source=None):
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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'table-conflict-preview:%'")


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
 assert not case.s.rows("SELECT * FROM events WHERE id='table-conflict'") and not case.s.rows('SELECT * FROM outbox')




def spy_core(monkeypatch):
 actual_build=command.core.build_plan;actual_check=command.core.check_plan_baseline
 builds=[];checks=[];outcomes=[]
 def build(book,**kw):
  plan=actual_build(book,**kw);builds.append((book,kw,plan));return plan
 def check(book,plan):
  checks.append((book,plan))
  try:row=actual_check(book,plan)
  except command.core.ConflictError as error:outcomes.append(str(error));raise
  outcomes.append(row);return row
 monkeypatch.setattr(command.core,'build_plan',build);monkeypatch.setattr(command.core,'check_plan_baseline',check)
 return builds,checks,outcomes


def current_rows(rows,text=BLOCK):
 lines=[line for line in text.splitlines() if not line.startswith('Проверяемая строка ')]
 lines=[('Проверяемых строк: '+str(len(rows))) if line.startswith('Проверяемых строк: ') else line for line in lines]
 lines[-1:-1]=['Проверяемая строка '+str(i)+': '+row for i,row in enumerate(rows,1)]
 return '\n'.join(lines)


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_build_and_checker_once_immutable_compatible_no_approval_apply_io(case,monkeypatch,channel):
 builds,checks,outcomes=spy_core(monkeypatch)
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
  for name in ('issue_local_approval','validate_approval','apply_to_fake_workbook','ApprovalToken','_key','preview'):
   m.setattr(command.core,name,lambda *a,**kw:pytest.fail('no approval/key/token/validate/apply/preview path'))
  m.setattr(command.core.hmac,'new',lambda *a,**kw:pytest.fail('no signing'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(builds)==len(checks)==len(outcomes)==1
 baseline,args,plan=builds[0];current,checked_plan=checks[0]
 assert checked_plan is plan and type(plan) is command.core.ChangePlan
 assert type(baseline) is type(current) is command.core.Workbook and baseline is not current
 assert outcomes[0] is current.sheets[0].rows[0] and plan.baseline is baseline.sheets[0].rows[0]
 assert plan.baseline.position==10 and plan.baseline.revision=='scenario-version:r1'
 assert plan.baseline.cells[2].value==current.sheets[0].rows[0].cells[2].value==Decimal('2.00')
 assert plan.changes[0].before=='  Old <A>  ' and plan.changes[0].after=='New & B'
 assert baseline.revision==current.revision=='scenario-version:v1' and len(plan.changes)==1
 body=row['text'];assert 'Конфликт &lt;A&gt; &amp; тест' in body and 'Было: текст «  Old &lt;A&gt;  »' in body and 'Предлагается: текст «New &amp; B»' in body
 assert 'Совместимо с переданным исходным снимком' in body and 'не создаёт одобрение или право записи' in body
 assert 'Реальная таблица не читалась' in body and 'не проверка её актуальности' in body
 for identifier in (baseline.id,baseline.sheets[0].id,plan.identity.value,command.KEY_COLUMN,plan.baseline.revision):assert identifier not in body
 assert 'Неизменяемое планом' not in body and 'причина' not in body
 assert row['recipient']=='test_operator' and row['task_id'] is None and row['preferred_transport']==channel and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('updates',[
 {'Проверяемая версия книги':'v2'},{'Проверяемая версия листа':'v2'},
 {'Проверяемая строка 1':'1; 10; r2; текст «  Old <A>  »; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 11; r1; текст «  Old <A>  »; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 10; r1; текст «manual»; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 10; r1; текст «New & B»; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 10; r1; текст «  Old <A>  »; десятичное 4.00'}])
def test_actual_checker_revision_move_planned_and_unplanned_cell_conflicts_broad_only(case,monkeypatch,updates):
 builds,checks,outcomes=spy_core(monkeypatch)
 row=submit(case,changed(**updates))
 assert len(builds)==len(checks)==1 and outcomes==['Baseline changed: manual edit, row move or revision conflict']
 assert 'Конфликт: снимок изменился: версия, положение или содержимое' in row['text']
 assert 'отдельная причина этим результатом не устанавливается' in row['text'] and 'Совместимо' not in row['text']
 assert builds[0][0].sheets[0].rows[0].position==10 and builds[0][2].baseline.cells[2].value==Decimal('2.00')


@pytest.mark.parametrize('before,current',[
 ('десятичное 2.00','десятичное 2.000'),('логическое нет','целое 0'),('пусто','текст «»'),('текст «  »','текст « »')])
def test_actual_fullrow_type_scale_null_spaces_preserved_by_frozen_scalar_helper(case,monkeypatch,before,current):
 builds,checks,outcomes=spy_core(monkeypatch)
 text=changed(**{'Исходная строка 1':'1; 10; r1; текст «  Old <A>  »; '+before,'Проверяемая строка 1':'1; 10; r1; текст «  Old <A>  »; '+current})
 row=submit(case,text)
 assert len(checks)==1 and type(outcomes[0]) is str and 'снимок изменился' in row['text']
 assert type(builds[0][0].sheets[0].rows[0].cells[2].value) is type(command._scalar(before))


@pytest.mark.parametrize('rows',[
 [],[BASE_TWO],['1; 10; r1; текст «  Old <A>  »; десятичное 2.00','1; 30; r1; текст «  Old <A>  »; десятичное 2.00']])
def test_actual_missing_or_duplicate_key_different_positions_reaches_checker_no_first_fallback(case,monkeypatch,rows):
 builds,checks,outcomes=spy_core(monkeypatch)
 row=submit(case,current_rows(rows))
 assert len(builds)==len(checks)==1 and outcomes==['Target workbook/sheet/row is missing or ambiguous']
 assert 'книга, лист или строка отсутствуют либо неоднозначны' in row['text'] and 'Совместимо' not in row['text']


def test_key_independent_of_physical_position_and_supplied_row_order(case,monkeypatch):
 builds,checks,outcomes=spy_core(monkeypatch)
 text=changed(**{'Исходная строка 1':BASE_TWO,'Исходная строка 2':BASE_ONE,'Проверяемая строка 1':BASE_TWO,'Проверяемая строка 2':BASE_ONE})
 row=submit(case,text)
 assert builds[0][2].baseline.position==10 and outcomes[0] is checks[0][0].sheets[0].rows[1] and 'Совместимо' in row['text']


def test_other_row_change_does_not_become_selected_row_conflict(case,monkeypatch):
 builds,checks,outcomes=spy_core(monkeypatch)
 row=submit(case,changed(**{'Проверяемая строка 2':'2; 90; r9; текст «other manual»; десятичное 9.00'}))
 assert type(outcomes[0]) is command.core.Row and 'Совместимо' in row['text']


@pytest.mark.parametrize('mode',['workbook','sheet'])
def test_actual_checker_missing_workbook_or_sheet_maps_only_supported_broad_class(case,monkeypatch,mode):
 from dataclasses import replace
 actual=command.core.check_plan_baseline;calls=[]
 def check(book,plan):
  calls.append(book)
  if mode=='workbook':book=replace(book,id='scenario-other')
  else:book=replace(book,sheets=(replace(book.sheets[0],id='scenario-other'),))
  return actual(book,plan)
 monkeypatch.setattr(command.core,'check_plan_baseline',check)
 row=submit(case)
 assert len(calls)==1 and 'отсутствуют либо неоднозначны' in row['text']


@pytest.mark.parametrize('updates',[
 {'Выбранный ключ':'3'}, {'Исходная строка 2':'1; 20; r1; текст «Other»; десятичное 3.00'},
 {'Изменение 1':'1; текст «  Old <A>  »'}, {'Изменение 1':'3; целое 1'},
 {'Проверяемая строка 2':'2; 10; r1; текст «Other»; десятичное 3.00'}])
def test_invalid_baseline_noop_unknown_column_or_duplicate_positions_clarify_before_checker(case,monkeypatch,updates):
 builds,checks,outcomes=spy_core(monkeypatch)
 row=submit(case,changed(**updates))
 assert row['text']==command.NOTICE and checks==outcomes==[]


def test_duplicate_desired_columns_refuse_whole_not_subset(case,monkeypatch):
 builds,checks,outcomes=spy_core(monkeypatch)
 text=changed(**{'Количество изменений':'2'})+'\nИзменение 2: 1; текст «Other change»'
 assert submit(case,text)['text']==command.NOTICE and checks==[]


def test_max_three_snapshots_columns_changes_and_inert_formula(case,monkeypatch):
 builds,checks,outcomes=spy_core(monkeypatch)
 text=changed(**{'Исходных строк':'3','Проверяемых строк':'3','Количество колонок':'3','Количество изменений':'3',
 'Исходная строка 1':BASE_ONE+'; пусто','Исходная строка 2':BASE_TWO+'; логическое нет',
 'Проверяемая строка 1':BASE_ONE+'; пусто','Проверяемая строка 2':BASE_TWO+'; логическое нет'})
 text+='\nКолонка 3: Формула\nИсходная строка 3: 3; 30; r1; текст «Third»; целое 0; пусто\nПроверяемая строка 3: 3; 30; r1; текст «Third»; целое 0; пусто\nИзменение 2: 2; десятичное 2.000\nИзменение 3: 3; текст «=SUM(A1:A3)»'
 row=submit(case,text)
 assert len(builds[0][0].sheets[0].rows)==3 and len(builds[0][2].changes)==3 and len(checks)==1
 assert 'Совместимо' in row['text'] and '=SUM(A1:A3)' in row['text'] and 'десятичное 2.000' in row['text']


def test_literal_template_no_approval_offer_or_short_consent(case,monkeypatch):
 monkeypatch.setattr(command.core,'check_plan_baseline',lambda *a,**kw:pytest.fail('template checker'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and command._units(row['text'])<3500
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE
 for text in ('да','нет','согласен','одобряю','запиши'):assert not command.is_command(text)


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_field_no_version_snapshot_or_key_guess(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'check_plan_baseline',lambda *a,**kw:pytest.fail('missing input checker'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('updates',[
 {'Исходных строк':'0'},{'Проверяемых строк':'4'},{'Количество колонок':'0'},{'Количество изменений':'4'},{'Выбранный ключ':'real'},
 {'Исходная версия книги':'неизвестно'},{'Проверяемая версия листа':'https://real'},{'Проверяемая версия книги':'x'*17},
 {'Проверяемая строка 1':'4; 10; r1; текст «  Old <A>  »; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 0; r1; текст «  Old <A>  »; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 01; r1; текст «  Old <A>  »; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 10000001; r1; текст «  Old <A>  »; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 10; неизвестно; текст «  Old <A>  »; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 10; r1; неизвестно; десятичное 2.00'},
 {'Проверяемая строка 1':'1; 10; r1; текст «  Old <A>  »; десятичное NaN'},
 {'Изменение 1':'__scenario_row_key; целое 1'},{'Название сценария':'x'*81},{'Колонка 1':'x'*81},
 {'Название сценария':'bad\rname'},{'Колонка 1':'bad\tname'},{'Основание':'одобрено и записано'}])
def test_closed_versions_positions_keys_types_labels_and_caps(case,updates):
 assert submit(case,changed(**updates))['text']==command.NOTICE


@pytest.mark.parametrize('text',[
 BLOCK.replace('Проверяемая строка 2:','Проверяемая строка 1:'),BLOCK.replace('Исходная строка 2:','Исходная строка 3:'),
 BLOCK.replace('Проверяемых строк: 2','Проверяемых строк: 0'),BLOCK.replace('Исходных строк: 2','Исходных строк: 3'),
 BLOCK+'\nОдобрение: да',BLOCK+'\nWorkbook: real',BLOCK+'\n',BLOCK.replace('Колонка 2: Неизменяемое планом','Колонка 2: Название <A>'),
 BLOCK.replace('Проверяемая строка 1: '+BASE_ONE,'Проверяемая строка 1: 1; 10; r1; пусто')])
def test_count_ordinals_unknown_fields_partial_rows_and_actual_ids_refuse(case,text):
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['bytes','characters','output','unexpected_conflict','wrong_error','none','foreign_row','other_row'])
def test_whole_refusal_limits_and_checker_result_contract_no_fine_diagnostics(case,monkeypatch,mode):
 text=BLOCK
 if mode=='bytes':text=changed(**{'Название сценария':'😀'*2000})
 elif mode=='characters':text=changed(**{'Название сценария':'Ж'*4000})
 elif mode=='output':monkeypatch.setattr(command,'escape',lambda *a,**kw:'&'*4000)
 else:
  actual=command.core.check_plan_baseline
  def check(book,plan):
   row=actual(book,plan)
   if mode=='unexpected_conflict':raise command.core.ConflictError('unsupported specific cause')
   if mode=='wrong_error':raise ValueError('malformed snapshot')
   if mode=='none':return None
   if mode=='foreign_row':
    from dataclasses import replace
    return replace(row)
   if mode=='other_row':return book.sheets[0].rows[1]
  monkeypatch.setattr(command.core,'check_plan_baseline',check)
 assert submit(case,text)['text']==command.NOTICE


def test_restart_replay_one_build_checker_no_domain_admission(case,monkeypatch):
 builds,checks,outcomes=spy_core(monkeypatch);row=submit(case)
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0])
 assert submit(case)['id']==row['id'] and len(builds)==len(checks)==len(outcomes)==1
 assert not case.s.rows('SELECT * FROM sources') and not case.s.rows('SELECT * FROM tasks')
