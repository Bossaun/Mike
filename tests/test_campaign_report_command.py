"""I44 actual supplied campaign report core through private Engine; synthetic only."""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
import json
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher
import goldway.campaign_report_command as command

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


BLOCK=('Подведи итоги акции\nНазвание сценария: Акция <A> & тест\nНачало периода: 2026-10-01T09:00:00+03:00\n'
 'Конец периода: 2026-10-08T09:00:00+03:00\nСрез сценария: 2026-10-08T10:00:00+03:00\n'
 'Наблюдалось: 2026-10-08T09:30:00+03:00\nПокрытие переданных значений: полное\n'
 'Показы: 100\nОбращения: 10\nЗаказы: 0\nОснование: '+command.BASIS)


def submit(c,text=BLOCK,event='campaign',sender='test_operator',channel='bot',source=None):
 c.e.reply(event,sender,text,channel=channel,source_id=source or ('telegram:1:10' if channel=='bot' else 'business:conn:1:10'),message_at=c.clock[0]);c.e.process(event)
 return c.s.execute('SELECT * FROM outbox WHERE business=? AND dedupe=?',(c.c['business'],command.PREFIX+command._hash(event))).fetchone()


def spy_core(monkeypatch):
 actual=command.core.campaign_report;render=command.core.render_campaign_report;calls=[];reports=[];drafts=[]
 def spy(campaign,observations,**kw):
  calls.append((campaign,observations,kw));result=actual(campaign,observations,**kw);reports.append(result);return result
 def renderer(report):
  result=render(report);drafts.append(result);return result
 monkeypatch.setattr(command.core,'campaign_report',spy);monkeypatch.setattr(command.core,'render_campaign_report',renderer)
 return calls,reports,drafts


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_engine_core_typed_report_renderer_and_own_dispatch(case,monkeypatch,channel):
 calls,reports,drafts=spy_core(monkeypatch)
 snapshots={table:case.s.rows('SELECT * FROM '+table) for table in ('tasks','sources','task_sources','knowledge_documents','runtime','usage','task_status_history')}
 import sqlite3
 reads=[];writes=[]
 def authorize(action,table,column,*args):
  if action==sqlite3.SQLITE_READ:
   reads.append(table)
   if table in snapshots or table in ('knowledge_chunks','knowledge_readers'):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize)
 from goldway.knowledge import KnowledgeIndex
 with monkeypatch.context() as m:
  m.setattr(KnowledgeIndex,'__init__',lambda *a,**kw:pytest.fail('new command constructed Index'))
  row=submit(case,channel=channel)
 case.s.db.set_authorizer(None)
 assert row and len(calls)==len(reports)==len(drafts)==1
 campaign,observations,kw=calls[0]
 assert type(campaign) is command.core.Campaign and len(campaign.metrics)==3
 assert all(type(d) is command.core.MetricDefinition for d in campaign.metrics)
 assert len(observations)==3 and all(type(o) is command.core.MetricObservation for o in observations)
 assert all(o.start==campaign.start and o.end==campaign.end and o.campaign_id==campaign.id and o.unit=='шт.' for o in observations)
 assert [r.value for r in reports[0].rows]==[Decimal('100'),Decimal('10'),Decimal('0')]
 assert reports[0].period_state=='closed' and reports[0].review_required is True and reports[0].live_authorized is False
 assert drafts[0].review_required is True and drafts[0].live_authorized is False
 body=row['text'];assert 'Акция &lt;A&gt; &amp; тест' in body and 'Показы: 100 шт.; покрытие: полное по вашему сообщению.' in body and 'Заказы: 0 шт.' in body
 assert 'независимо не проверены' in body
 assert 'Это не проверка реальной акции или аккаунта' in body and 'редактируемый черновик' in body
 assert 'ROI' not in body and command.SOURCE in body and campaign.id not in body
 assert not any(code in body for code in ('before_start','in_progress','closed','complete','partial','unknown','missing'))
 assert row['recipient']=='test_operator' and row['preferred_transport']==channel and row['task_id'] is None and row['parse_mode']=='HTML'
 assert set(writes)<={'events','answer_citation_turns','outbox'}
 for table,before in snapshots.items():assert before==case.s.rows('SELECT * FROM '+table)
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')
 case.d.flush();assert len(case.api.calls)==1 and case.api.calls[0]['chat_id']=='1'
 if channel=='business':assert case.api.calls[0]['business_connection_id']=='conn'


@pytest.mark.parametrize('label',['Показы','Обращения','Заказы','all'])
def test_actual_core_missing_not_zero_and_definitions_preserved(case,monkeypatch,label):
 calls,reports,drafts=spy_core(monkeypatch);text=BLOCK
 for name,value in [('Показы','100'),('Обращения','10'),('Заказы','0')]:
  if label in (name,'all'):text=text.replace(name+': '+value,name+': неизвестно')
 if label=='all':text=text.replace('Наблюдалось: 2026-10-08T09:30:00+03:00','Наблюдалось: неизвестно')
 row=submit(case,text);assert row and len(calls)==1 and len(reports[0].rows)==3
 for r in reports[0].rows:
  if label in (r.definition.label,'all'):
   assert r.value is None and r.coverage=='missing' and r.source_id is None
   assert r.definition.label+': [нет наблюдения] шт.; покрытие: наблюдение не передано.' in row['text']
  else:assert r.value is not None
 assert len(calls[0][1])==(0 if label=='all' else 2)
 if label=='all':assert drafts[0].source_ids==()


@pytest.mark.parametrize('coverage',['частичное','неизвестно'])
def test_known_partial_unknown_coverage_remains_reported_value_not_complete_total(case,monkeypatch,coverage):
 calls,reports,_=spy_core(monkeypatch)
 row=submit(case,BLOCK.replace('Покрытие переданных значений: полное','Покрытие переданных значений: '+coverage))
 expected=command._COVERAGES[coverage]
 assert [r.coverage for r in reports[0].rows]==[expected]*3 and reports[0].rows[0].value==100
 assert 'Показы: 100 шт.; покрытие: '+coverage in row['text']
 assert 'не доказывает полный итог' in row['text']


@pytest.mark.parametrize('period_state',['before_start','in_progress','closed'])
def test_actual_core_period_state_and_supplied_slice_not_server_clock(case,monkeypatch,period_state):
 calls,reports,_=spy_core(monkeypatch);case.clock[0]+=500*86400
 if period_state=='before_start':
  text=BLOCK.replace('Срез сценария: 2026-10-08T10:00:00+03:00','Срез сценария: 2026-10-01T08:59:59+03:00').replace('Наблюдалось: 2026-10-08T09:30:00+03:00','Наблюдалось: неизвестно')
  for a,b in [('Показы: 100','Показы: неизвестно'),('Обращения: 10','Обращения: неизвестно'),('Заказы: 0','Заказы: неизвестно')]:text=text.replace(a,b)
 elif period_state=='in_progress':
  text=BLOCK.replace('2026-10-08T10:00:00+03:00','2026-10-08T08:59:59+03:00').replace('2026-10-08T09:30:00+03:00','2026-10-08T08:59:59+03:00').replace('Покрытие переданных значений: полное','Покрытие переданных значений: частичное')
 else:text=BLOCK.replace('2026-10-08T10:00:00+03:00','2026-10-08T09:00:00+03:00').replace('2026-10-08T09:30:00+03:00','2026-10-08T09:00:00+03:00')
 row=submit(case,text);assert row and reports[0].period_state==period_state
 assert 'Состояние периода: '+{'before_start':'ещё не начался','in_progress':'ещё идёт','closed':'закончился по указанному срезу'}[period_state] in row['text']
 assert period_state not in row['text']
 assert calls[0][2]['now'].timestamp()!=case.clock[0]


def test_actual_renderer_output_is_used_and_escaped(case,monkeypatch):
 render=command.core.render_campaign_report
 def renderer(report):
  value=render(report)
  return command.core.EditableDraft(value.text+'\nLiteral <CORE> & report',value.source_ids)
 monkeypatch.setattr(command.core,'render_campaign_report',renderer)
 assert 'Literal &lt;CORE&gt; &amp; report' in submit(case)['text']


def test_explicit_timezone_window_and_largest_counter(case,monkeypatch):
 calls,reports,_=spy_core(monkeypatch)
 text=BLOCK.replace('2026-10-01T09:00:00+03:00','2026-10-01T06:00:00Z').replace('2026-10-08T09:00:00+03:00','2026-10-08T06:00:00Z').replace('Показы: 100','Показы: 999999999')
 row=submit(case,text);assert row and reports[0].rows[0].value==Decimal('999999999')
 assert calls[0][0].end-calls[0][0].start==command.timedelta(days=7)


def test_exact_template_roundtrip_core_not_called_until_fields_supplied(case,monkeypatch):
 monkeypatch.setattr(command.core,'campaign_report',lambda *a,**kw:pytest.fail('template core call'))
 row=submit(case,command.HEADER);assert row['text']==command.TEMPLATE and len(row['text'].splitlines())==11
 assert submit(case,row['text'],event='unfilled')['text']==command.NOTICE


@pytest.mark.parametrize('label',command._LABELS)
def test_missing_each_fixed_field_no_default_or_core_call(case,monkeypatch,label):
 monkeypatch.setattr(command.core,'campaign_report',lambda *a,**kw:pytest.fail('missing input core'))
 text='\n'.join(line for line in BLOCK.splitlines() if not line.startswith(label+': '))
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('mutation',[
 lambda b:b+'\n',lambda b:b+'\nПоказы: 1',lambda b:b+'\nВыручка: 999',
 lambda b:b.replace(command.BASIS,'проверенные факты компании'),lambda b:b.replace('Покрытие переданных значений: полное','Покрытие переданных значений: возможно'),
 lambda b:b.replace('Показы: 100','Показы: -1'),lambda b:b.replace('Показы: 100','Показы: +1'),
 lambda b:b.replace('Показы: 100','Показы: 1.5'),lambda b:b.replace('Показы: 100','Показы: 1,5'),
 lambda b:b.replace('Показы: 100','Показы: 1e2'),lambda b:b.replace('Показы: 100','Показы: 01'),
 lambda b:b.replace('Показы: 100','Показы: 1000000000'),lambda b:b.replace('Показы: 100','Показы: NaN'),
 lambda b:b.replace('Показы: 100','Показы: 1 000'),lambda b:b.replace('Показы: 100','Показы: true'),
 lambda b:b.replace('Наблюдалось: 2026-10-08T09:30:00+03:00','Наблюдалось: неизвестно'),
 lambda b:b.replace('2026-10-08T09:30:00+03:00','2026-10-08T10:01:00+03:00'),
 lambda b:b.replace('2026-10-08T09:30:00+03:00','2026-10-01T08:59:59+03:00'),
 lambda b:b.replace('2026-10-08T09:30:00+03:00','2026-10-08T08:59:59+03:00'),
 lambda b:b.replace('2026-10-01T09:00:00+03:00','2026-10-01T09:00:00'),
 lambda b:b.replace('2026-10-08T09:00:00+03:00','2026-10-07T09:00:00+03:00'),
 lambda b:b.replace('2026-10-08T09:00:00+03:00','2026-10-09T09:00:00+03:00'),
 lambda b:b.replace('2026-10-08T09:00:00+03:00','2026-10-01T09:00:00+03:00'),
 lambda b:b.replace('2026-10-01T09:00:00+03:00','2026-02-30T09:00:00+03:00'),
 lambda b:b.replace('2026-10-08T10:00:00+03:00','сегодня'),lambda b:b.replace('2026-10-08T10:00:00+03:00','20261008T100000+0300'),
 lambda b:b.replace('2026-10-08T10:00:00+03:00','2026-10-08T10:00:00+03:60'),
 lambda b:b.replace('2026-10-08T10:00:00+03:00','2026-10-08T10:00:00+24:00'),
 lambda b:b.replace('Акция <A> & тест','x'*101),lambda b:b.replace('Акция <A> & тест','bad\tname'),
 lambda b:b.replace('Акция <A> & тест','bad\rname'),lambda b:b.replace('Акция <A> & тест','bad\x7fname'),
 lambda b:b+'x'*4096])
def test_invalid_fields_dates_counts_and_scope_fixed_notice(case,mutation):
 assert submit(case,mutation(BLOCK))['text']==command.NOTICE


def test_all_unknown_still_validates_explicit_observation_chronology(case):
 text=BLOCK.replace('Показы: 100','Показы: неизвестно').replace('Обращения: 10','Обращения: неизвестно').replace('Заказы: 0','Заказы: неизвестно')
 text=text.replace('2026-10-08T09:30:00+03:00','2026-10-08T10:01:00+03:00')
 assert submit(case,text)['text']==command.NOTICE


@pytest.mark.parametrize('mode',['utf8','utf16','renderer_overflow','report_flag','draft_flag','wrong_report'])
def test_whole_refusal_limits_and_actual_review_live_flags(case,monkeypatch,mode):
 text=BLOCK
 if mode=='utf8':text=text.replace('Акция <A> & тест','😀'*1000)
 elif mode=='utf16':text=text.replace('Акция <A> & тест','Ж'*1900)
 elif mode=='renderer_overflow':monkeypatch.setattr(command.core,'render_campaign_report',lambda report:command.core.EditableDraft('&'*1000,(command.SOURCE,)))
 elif mode=='draft_flag':
  actual=command.core.render_campaign_report
  def renderer(report):
   result=actual(report);object.__setattr__(result,'live_authorized',True);return result
  monkeypatch.setattr(command.core,'render_campaign_report',renderer)
 elif mode in ('report_flag','wrong_report'):
  actual=command.core.campaign_report
  def report(*a,**kw):
   if mode=='wrong_report':return SimpleNamespace(rows=[])
   result=actual(*a,**kw);object.__setattr__(result,'review_required',False);return result
  monkeypatch.setattr(command.core,'campaign_report',report)
 assert submit(case,text)['text']==command.NOTICE


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
 assert not case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'campaign-report-preview:%'")


@pytest.mark.parametrize('change',['config','intelligence','index','policy','recipient','body','transport','queue_failure'])
def test_atomic_host_policy_exact_body_and_original_transport_queue_race(case,monkeypatch,change):
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
 assert not case.s.rows("SELECT * FROM events WHERE id='campaign'") and not case.s.rows('SELECT * FROM outbox')


def test_replay_once_and_short_consents_never_enter_campaign_route(case,monkeypatch):
 calls,_,_=spy_core(monkeypatch)
 row=submit(case);assert submit(case)['id']==row['id'] and len(calls)==1
 for text in ('да','нет','согласен','отправь'):
  assert command.is_command(text) is False
 assert not case.s.rows('SELECT * FROM tasks')
