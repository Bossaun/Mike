"""New prospective dated-report boundary; real Engine/Store, synthetic delivery."""
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher
import json,re
import goldway.meeting_binding as command
from datetime import date,timedelta
from goldway.meeting_week_adapter import project_week

NOW=1792174600
TEXT='Итоги планёрки.\nПодготовить список товаров.\nКакой образец выбрать?'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
_MISSING=object()


def result(actions=(),questions=_MISSING,intent='meeting',text='Нужно уточнить, какой образец отправить клиенту.'):
 value=dict(actions=list(actions),summary='Synthetic meeting draft',reply_text=text,intent=intent,
  dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 if questions is not _MISSING:value['meeting_question_lines']=questions
 return value


def make_case():
 s=Store(':memory:');clock=[NOW]
 config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,
  allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 engine=Engine(s,config,None,lambda:clock[0]);engine.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute("INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES('GOLDWAY','test_operator','1','1','conn',?)",(NOW,))
 return SimpleNamespace(s=s,e=engine,c=config,clock=clock)


@pytest.fixture
def case(monkeypatch):
 value=make_case()
 import builtins,socket,httpx
 def no_io(*a,**k):pytest.fail('unexpected external I/O')
 monkeypatch.setattr(builtins,'open',no_io);monkeypatch.setattr(socket,'socket',no_io);monkeypatch.setattr(httpx,'Client',no_io)
 yield value
 value.s.db.close()


def submit(case,value,text=TEXT,sender='test_operator',channel='bot',event='meeting',callback=None):
 case.clock[0]+=1;seen=[]
 class Model:
  def propose(self,context):
   seen.append(dict(context))
   if callback:callback(context)
   return value
 case.e.model=Model()
 uid=case.c['employees'][sender]['telegram_user_id']
 source=f'telegram:{uid}:10' if channel=='bot' else f'business:conn:{uid}:10'
 case.e.reply(event,sender,text,source_id=source,channel=channel,message_at=case.clock[0]);case.e.process(event)
 return seen


def outputs(case):return case.s.rows('SELECT * FROM outbox ORDER BY id')


def new_action():
 return dict(op='create',task_id=None,title='Проверить образцы',owner=None,deadline=None,next_step='Сверить образцы',criterion='Готов отчёт проверки',status='proposed',set_fields=[],evidence_lines=[1],owner_lines=[],deadline_lines=[])



class API:
 def __init__(self):self.calls=[];self.n=100
 def call(self,method,**kw):
  if method=='getBusinessConnection':return dict(is_enabled=True,rights=dict(can_reply=True),user=dict(id=99))
  assert method=='sendMessage';self.calls.append(kw);self.n+=1;return dict(message_id=self.n)


def setup(c,*,channel='bot',send=True,questions=True):
 action=dict(op='create',task_id=None,title='Подготовить список товаров',owner=None,deadline=None,next_step='Подготовить список',criterion='Список готов',status='proposed',set_fields=[],evidence_lines=[2],owner_lines=[],deadline_lines=[])
 submit(c,result([action],questions=[[3]] if questions else []),channel=channel)
 rows=c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'meeting-binding-template:%'")
 assert len(rows)==1
 c.code=re.search(r'MB-[0-9a-f]{16}',rows[0]['text'])[0]
 c.api=API();c.d=Dispatcher(c.s,c.c,c.api,lambda:c.clock[0])
 if send:c.d.flush()
 c.task=c.s.rows('SELECT * FROM tasks')[0]
 c.channel=channel
 def fail(*a,**kw):pytest.fail('confirmation called model/refresh/context/search')
 c.e.model=SimpleNamespace(propose=fail);c.e._context=fail;c.e.intelligence.sources.refresh=fail;c.e.intelligence.index.search=fail
 return rows[0]


def block(c,*,day='2026-10-16',until='2026-10-30',selection=None,access='только для меня',consent=None):
 return ('Регистрация планёрки '+c.code+'\nДата планёрки: '+day+'\nДоступ: '+access+'\nДоступ до конца дня: '+until+'\n'+
  (selection or 'Решение: строки 2; задача №'+str(c.task['public_number']))+'\n'+(consent or command._CONSENT))


def confirm(c,text=None,event='confirm',*,send=False,channel=None,at=None):
 channel=channel or c.channel;c.clock[0]+=1
 c.e.reply(event,'test_operator',text or block(c),channel=channel,source_id=('telegram:1:20' if channel=='bot' else 'business:conn:1:20'),message_at=c.clock[0] if at is None else at)
 rows=c.s.rows('SELECT * FROM outbox WHERE dedupe=?',('meeting-binding:'+event,))
 if send:c.d.flush()
 return rows[0] if rows else None


def receipt(c):return json.loads(c.s.rows("SELECT value FROM runtime WHERE key LIKE 'meeting-binding:v1:%'")[0]['value'])


def consumer_payload(c):return dict(sender='test_operator',channel=c.channel,source_id=('telegram:1:99' if c.channel=='bot' else 'business:conn:1:99'))



import goldway.dated_result as reports


def registered(c,channel='bot'):
 setup(c,channel=channel);confirm(c)
 assert receipt(c)['state']=='registered'
 return c


def report_block(c,on='2026-10-16',kind='выполнено',number=None,literal='Подготовлен список товаров.'):
 return ('Отчёт по планёрке '+c.code+'\nЗадача: №'+str(number or c.task['public_number'])+'\nДата результата: '+on+
  '\nСостояние по моему отчёту: '+kind+'\nДоступ: только для меня\nМой отчёт: '+literal+'\n'+reports._CONSENT)


def submit_report(c,text=None,event='report',source=None,channel=None,sender='test_operator'):
 c.clock[0]+=1;channel=channel or c.channel
 source=source or ('telegram:1:'+str(100+c.clock[0]-NOW) if channel=='bot' else 'business:conn:1:'+str(100+c.clock[0]-NOW))
 added=c.e.reply(event,sender,text if text is not None else report_block(c),source_id=source,channel=channel,message_at=c.clock[0])
 rows=c.s.rows('SELECT * FROM outbox WHERE dedupe=?',('dated-result:'+event,))
 return added,rows[0] if rows else None


def report_docs(c):
 return [json.loads(r['value']) for r in c.s.rows("SELECT value FROM runtime WHERE key LIKE 'dated-result:v1:%'") if r['value'].startswith('{')]


def project(c,start=date(2026,10,16),end=date(2026,10,23)):
 parent=command.registered_inputs(c.e,c.code,consumer_payload(c));data=reports.reported_inputs(c.e,c.code,consumer_payload(c))
 return project_week(c.s,c.c,'test_operator',meeting=parent.meeting,start=start,end=end,task_bindings=parent.task_bindings,result_bindings=data.result_bindings,sources=data.sources,document_ids=parent.document_ids,meeting_evidence_ids=parent.meeting_evidence_ids,meeting_anchor_confirmed=True,now=c.clock[0])


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_current_report_producer_typed_i04_preserves_proposed_task(case,channel):
 registered(case,channel);before=case.s.rows('SELECT * FROM tasks');history=case.s.rows('SELECT * FROM task_status_history')
 added,row=submit_report(case)
 assert added and 'По вашему отчёту' in row['text'] and 'не независимая проверка' in row['text']
 assert row['task_id'] is None and row['parse_mode'] is None
 assert case.s.rows('SELECT * FROM tasks')==before and case.s.rows('SELECT * FROM task_status_history')==history
 doc=report_docs(case)[0];assert doc['state']=='reported' and 'Подготовлен список товаров.' not in json.dumps(doc,ensure_ascii=False)
 data=reports.reported_inputs(case.e,case.code,consumer_payload(case))
 assert data.result_bindings[0].outcome=='completed' and data.result_bindings[0].on==date(2026,10,16)
 assert data.result_bindings[0].spans[0].quote=='Мой отчёт: Подготовлен список товаров.'
 assert '\n'.join(data.sources[0].lines)==report_block(case)
 draft=project(case);task=draft.progress[0].tasks[0]
 assert task.recorded_status=='proposed' and task.result_state=='completed'
 assert not case.s.rows('SELECT * FROM knowledge_documents')
 case.d.flush();assert case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='sent'


@pytest.mark.parametrize('change',['missing_date','future','before_meeting','bad_date','kind','unknown_number','access','consent','text','long','extra','duplicate','bad_mb','control','bytes'])
def test_incomplete_unknown_or_invalid_report_never_writes_source_link_or_receipt(case,change):
 registered(case);text=report_block(case)
 if change=='missing_date':text=text.replace('2026-10-16','[уточнить]')
 elif change=='future':text=report_block(case,on='2026-10-17')
 elif change=='before_meeting':text=report_block(case,on='2026-10-15')
 elif change=='bad_date':text=report_block(case,on='2026-02-30')
 elif change=='kind':text=report_block(case,kind='неизвестно')
 elif change=='unknown_number':text=report_block(case,number=999)
 elif change=='access':text=text.replace('только для меня','всей команде')
 elif change=='consent':text=text.replace(reports._CONSENT,'Да')
 elif change=='text':text=report_block(case,literal='[уточнить]')
 elif change=='long':text=report_block(case,literal='x'*301)
 elif change=='extra':text+='\nДругое: поле'
 elif change=='duplicate':text=text.replace('Дата результата:', 'Дата результата: 2026-10-16\nДата результата:')
 elif change=='bad_mb':text=text.replace(case.code,'MB-'+'a'*16)
 elif change=='control':text=report_block(case,literal='a\tb')
 elif change=='bytes':text=report_block(case,literal='😀'*1001)
 before=(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'),case.s.rows('SELECT * FROM tasks'))
 _,row=submit_report(case,text)
 assert row and 'По вашему отчёту' not in row['text'] and not report_docs(case)
 assert before==(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'),case.s.rows('SELECT * FROM tasks'))


def test_fillable_literal_template_roundtrip_never_creates_result(case):
 registered(case);_,row=submit_report(case,'Отчёт по планёрке '+case.code)
 assert row['text']==reports._template(case.code)
 submit_report(case,row['text'],event='template-again')
 assert not report_docs(case)


def test_same_date_conflicts_retained_out_of_window_missing_and_status_not_report(case):
 registered(case)
 assert project(case).progress[0].state=='missing_results'
 case.s.execute("UPDATE tasks SET status='completed',revision=revision+1 WHERE id=?",(case.task['id'],))
 assert project(case).progress[0].state=='missing_results'
 submit_report(case,report_block(case,kind='выполнено'),event='completed')
 submit_report(case,report_block(case,kind='заблокировано',literal='Ожидается образец.'),event='blocked')
 assert len(reports.reported_inputs(case.e,case.code,consumer_payload(case)).result_bindings)==2
 assert project(case).progress[0].state=='conflict'
 assert project(case,date(2026,10,17),date(2026,10,24)).progress[0].state=='missing_results'


def test_replay_restart_and_withdrawal_no_revival(case):
 registered(case);source='telegram:1:12345';text=report_block(case)
 submit_report(case,text,source=source);before=(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'),report_docs(case))
 added,_=submit_report(case,text,source=source)
 assert not added and before==(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'),report_docs(case))
 case.e=Engine(case.s,case.c,None,lambda:case.clock[0])
 assert len(reports.reported_inputs(case.e,case.code,consumer_payload(case)).result_bindings)==1
 rid=reports.reported_inputs(case.e,case.code,consumer_payload(case)).result_bindings[0].id
 _,row=submit_report(case,'Отзови мой отчёт '+rid+' по планёрке '+case.code,event='withdraw')
 assert 'отозван' in row['text'] and not reports.reported_inputs(case.e,case.code,consumer_payload(case)).result_bindings
 submit_report(case,text,event='same-source-again',source=source)
 assert report_docs(case)[0]['state']=='revoked' and not reports.reported_inputs(case.e,case.code,consumer_payload(case)).result_bindings


@pytest.mark.parametrize('change',['parent_expired','parent_revoked','parent_source','report_source','report_deleted','link','doc_acl','role','policy','scope','reader','business'])
def test_current_report_loader_failclosed_no_cached_fallback(case,change):
 registered(case);submit_report(case);doc=report_docs(case)[0]
 if change=='parent_expired':case.clock[0]=receipt(case)['expiry']
 elif change=='parent_revoked':confirm(case,'Отмени регистрацию планёрки '+case.code,event='revoke')
 elif change=='parent_source':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(receipt(case)['source'],))
 elif change=='report_source':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(doc['source'],))
 elif change=='report_deleted':case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',(doc['source'],))
 elif change=='link':case.s.execute('DELETE FROM task_sources WHERE source_id=?',(doc['source'],))
 elif change=='doc_acl':case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?) ON CONFLICT(business,task_id) DO UPDATE SET evidence_ids=excluded.evidence_ids',('GOLDWAY',case.task['id'],json.dumps(['knowledge:revoked'])))
 elif change=='role':case.c['admins']=[]
 elif change=='policy':case.c['employees']['frost']['name']='changed'
 elif change=='business':case.e.b='OTHER'
 payload=consumer_payload(case)
 if change=='scope':payload['channel']='business';payload['source_id']='business:conn:1:20'
 if change=='reader':payload['sender']='frost';payload['source_id']='telegram:2:20'
 with pytest.raises(ValueError):reports.reported_inputs(case.e,case.code,payload)


@pytest.mark.parametrize('phase',['source','link','receipt','queue','protect','drift'])
def test_all_writes_ack_and_event_rollback_on_failure(case,monkeypatch,phase):
 registered(case);before={table:case.s.rows('SELECT * FROM '+table) for table in ['sources','task_sources','tasks','runtime','outbox','task_status_history']}
 if phase in ('source','link'):
  real=case.s.execute
  def execute(sql,*a,**kw):
   if sql.startswith('INSERT INTO '+('sources' if phase=='source' else 'task_sources')):raise RuntimeError('synthetic write failure')
   return real(sql,*a,**kw)
  monkeypatch.setattr(case.s,'execute',execute)
 elif phase=='receipt':
  def fail(*a,**k):raise RuntimeError('synthetic receipt failure')
  monkeypatch.setattr(reports,'_save',fail)
 elif phase in ('queue','protect'):
  def fail(*a,**k):raise RuntimeError('synthetic queue failure')
  monkeypatch.setattr(case.s if phase=='queue' else case.e.intelligence,'queue' if phase=='queue' else 'protect_outbox',fail)
 else:
  real=case.s.queue
  def drift(*a,**k):
   ret=real(*a,**k);case.e.intelligence=None;return ret
  monkeypatch.setattr(case.s,'queue',drift)
 with pytest.raises(Exception):submit_report(case)
 assert before=={table:case.s.rows('SELECT * FROM '+table) for table in before}
 assert not case.s.rows("SELECT id FROM events WHERE id='report'")


@pytest.mark.parametrize('change',['role','scope','source_collision','source_id_length','index_overflow','parent_revoked','source_deleted','acl'])
def test_producer_prerequisites_zero_report_writes(case,change):
 registered(case);source='telegram:1:1234';channel=None
 if change=='role':case.c['admins']=[]
 elif change=='scope':channel='business'
 elif change=='source_collision':case.s.execute('INSERT INTO sources VALUES(?,?,?,?,?)',('GOLDWAY',source,'older immutable input','older',0))
 elif change=='source_id_length':source='business:'+'c'*150+':1:1234';channel='business'
 elif change=='index_overflow':
  scope,_=reports._gate(case.e,consumer_payload(case),case.e.intelligence)
  reports._save(case.e,reports._key(scope,case.code),['MR-'+format(i,'032x') for i in range(33)])
 elif change=='parent_revoked':confirm(case,'Отмени регистрацию планёрки '+case.code,event='revoke')
 elif change=='source_deleted':case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',(receipt(case)['source'],))
 elif change=='acl':case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE task_id=?',(json.dumps(['knowledge:revoked']),case.task['id']))
 before=(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'),case.s.rows('SELECT * FROM tasks'))
 submit_report(case,event='denied',source=source,channel=channel)
 assert not report_docs(case)
 assert before==(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'),case.s.rows('SELECT * FROM tasks'))


def test_loader_does_not_read_report_body_before_current_document_acl(case):
 registered(case);submit_report(case)
 case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=? WHERE task_id=?',(json.dumps(['knowledge:revoked']),case.task['id']))
 trace=[];case.s.db.set_trace_callback(trace.append)
 try:
  with pytest.raises(ValueError):reports.reported_inputs(case.e,case.code,consumer_payload(case))
 finally:case.s.db.set_trace_callback(None)
 assert not any('THEN body END' in sql and 'FROM sources' in sql for sql in trace)


def test_actual_i04_preflight_semantic_failure_rolls_back_candidate_source_and_link(case,monkeypatch):
 registered(case);before=(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'))
 def deny(*a,**kw):raise reports.ReviewError('synthetic typed consumer rejection')
 monkeypatch.setattr(reports,'project_week',deny)
 _,row=submit_report(case)
 assert row['text']==reports.NOTICE and not report_docs(case)
 assert before==(case.s.rows('SELECT * FROM sources'),case.s.rows('SELECT * FROM task_sources'))


def test_html_literal_stays_canonical_plain_input_and_ack_does_not_echo_it(case):
 registered(case);literal='<b>Список & образцы</b>'
 _,row=submit_report(case,report_block(case,literal=literal))
 assert literal not in row['text'] and row['parse_mode'] is None
 data=reports.reported_inputs(case.e,case.code,consumer_payload(case))
 assert data.sources[0].lines[5]=='Мой отчёт: '+literal
 assert data.result_bindings[0].spans[0].quote=='Мой отчёт: '+literal


@pytest.mark.parametrize('phase',['active','withdrawn','expired'])
def test_private_report_namespace_excluded_before_generic_staff_result_body_transfer(case,monkeypatch,phase):
 from goldway.task_result_context import registered_result,PRIVATE_RESULT_SOURCE_PREFIX
 from goldway.work_context import collect_work_context,work_id
 from goldway.answer_citations import _permissions,_cited
 from goldway.monthly_task_history import _Reader
 registered(case);submit_report(case)
 data=reports.reported_inputs(case.e,case.code,consumer_payload(case))
 assert data.sources[0].id.startswith(PRIVATE_RESULT_SOURCE_PREFIX)
 assert project(case).progress[0].tasks[0].result_state=='completed'
 rid=data.result_bindings[0].id
 if phase=='withdrawn':submit_report(case,'Отзови мой отчёт '+rid+' по планёрке '+case.code,event='withdraw')
 elif phase=='expired':case.clock[0]=receipt(case)['expiry']
 case.s.execute("UPDATE tasks SET owner='frost',status='completed',revision=revision+1,evidence=? WHERE id=?",('Подготовить список товаров.',case.task['id']))
 task=case.s.rows('SELECT * FROM tasks')[0];ref=work_id('GOLDWAY',task['id']);trace=[];transferred=[]
 real=case.s.rows
 def rows(sql,*args,**kw):
  result=real(sql,*args,**kw)
  if 'FROM task_sources ts LEFT JOIN sources s' in sql:
   transferred.extend(result)
   assert all(not row['source_id'].startswith(PRIVATE_RESULT_SOURCE_PREFIX) for row in result)
   assert all('Подготовлен список товаров.' not in (row.get('body') or '') for row in result)
  return result
 monkeypatch.setattr(case.s,'rows',rows);case.s.db.set_trace_callback(trace.append)
 try:
  # Existing ordinary public result remains available; no other source omitted.
  assert registered_result(case.s,'GOLDWAY',task['id'],task['revision'])[0]['text']=='Подготовить список товаров.'
  context=collect_work_context(case.s,case.c,'frost',{ref},case.clock[0],'Покажи результат задачи №1')
  assert context['evidence'][0]['registered_result']['text']=='Подготовить список товаров.'
  reader=_Reader(case.s);records,acl,docs=_permissions(reader,case.s,case.c,'frost',[ref],case.clock[0])
  assert _cited(reader,case.s,case.c,'frost',ref,case.clock[0],records,acl,docs)[0]=='task'
  # A text present ONLY in the private report cannot become a generic result.
  case.s.execute('UPDATE tasks SET evidence=? WHERE id=?',('Подготовлен список товаров.',task['id']))
  assert registered_result(case.s,'GOLDWAY',task['id'],task['revision']) is None
  assert not collect_work_context(case.s,case.c,'frost',{ref},case.clock[0],'Покажи результат задачи №1')['evidence']
  with pytest.raises(ValueError):_cited(reader,case.s,case.c,'frost',ref,case.clock[0],records,acl,docs)
 finally:case.s.db.set_trace_callback(None)
 assert transferred
 body_queries=[sql for sql in trace if 'THEN s.body END AS body' in sql]
 assert body_queries and all('substr(ts.source_id,1,' in sql and PRIVATE_RESULT_SOURCE_PREFIX in sql for sql in body_queries)


@pytest.mark.parametrize('field',['title','acl'])
def test_i04_preflight_suppresses_unrelated_oversized_cells_before_transfer(case,monkeypatch,field):
 registered(case)
 task=dict(case.task);task['id']='unrelated';task['public_number']=2;task['status']='confirmed';task['owner']='frost'
 if field=='title':task['title']='x'*100000
 columns=','.join(task);case.s.execute('INSERT INTO tasks('+columns+') VALUES('+','.join('?' for _ in task)+')',tuple(task.values()))
 if field=='acl':case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','unrelated','[]'+' '*17000))
 trace=[];case.s.db.set_trace_callback(trace.append)
 before=(case.s.rows('SELECT id FROM sources'),case.s.rows('SELECT * FROM task_sources'))
 try:_,row=submit_report(case)
 finally:case.s.db.set_trace_callback(None)
 assert 'По вашему отчёту' not in row['text'] and not report_docs(case)
 assert before==(case.s.rows('SELECT id FROM sources'),case.s.rows('SELECT * FROM task_sources'))
 if field=='title':
  queries=[sql for sql in trace if 'FROM tasks WHERE business=' in sql and 'next_step' in sql and sql.startswith('SELECT')]
  assert queries and all('CASE WHEN' in sql and 'THEN title END' in sql for sql in queries)


def test_i36_allowlisted_preflight_cumulative_byte_budget_denies_atomically(case):
 registered(case)
 for n in range(2,129):
  task=dict(case.task);task.update(id='unrelated-'+str(n),public_number=n,status='confirmed',owner='frost',title='t'*3900,next_step='n'*3900,criterion='c'*3900)
  columns=','.join(task);case.s.execute('INSERT INTO tasks('+columns+') VALUES('+','.join('?' for _ in task)+')',tuple(task.values()))
  case.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY',task['id'],'[]'+' '*8000))
 before=(case.s.rows('SELECT id FROM sources'),case.s.rows('SELECT * FROM task_sources'))
 _,row=submit_report(case)
 assert row['text']==reports.NOTICE and not report_docs(case)
 assert before==(case.s.rows('SELECT id FROM sources'),case.s.rows('SELECT * FROM task_sources'))
