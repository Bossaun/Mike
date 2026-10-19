"""I38 actual explicit reported results through existing I36 runtime; synthetic only."""
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
import goldway.weekly_review_command as weekly

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
 c.api=API();c.d=Dispatcher(c.s,c.c,c.api,lambda:c.clock[0],weekly_guard=weekly.make_delivery_guard(c.e))
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



def ready(c,channel='bot'):
 registered(c,channel);c.d.flush();c.api.calls.clear()


def report(c,*,kind='выполнено',on='2026-10-16',event='report',literal='Готов <образец> & список.'):
 _,row=submit_report(c,report_block(c,on=on,kind=kind,literal=literal),event=event)
 assert row and 'По вашему отчёту' in row['text']
 c.d.flush();c.api.calls.clear()
 return report_docs(c)[-1]


def request(c,event='weekly',start='2026-10-12',end='2026-10-18'):
 c.clock[0]+=1
 c.e.reply(event,'test_operator','Сверь планёрку '+c.code+' за '+start+' — '+end,channel=c.channel,
  source_id=('telegram:1:900' if c.channel=='bot' else 'business:conn:1:900'),message_at=c.clock[0])
 return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'weekly-review:%' ORDER BY id")


def weekly_receipt(c,row):
 key=weekly._receipt_key(c.e.b,row['id'])
 return key,json.loads(c.s.rows('SELECT value FROM runtime WHERE key=?',(key,))[0]['value'])


def set_report_doc(c,doc):
 scope,_=command._gate(c.e,consumer_payload(c),c.e.intelligence)
 rid=next(r for r in reports._ids(weekly._Reader(c.s),scope,c.code) if reports._read(weekly._Reader(c.s),reports._key(scope,c.code,r))['source']==doc['source'])
 reports._save(c.e,reports._key(scope,c.code,rid),doc)


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_i37_to_existing_i36_i04_i07_dispatch_no_task_writes(case,monkeypatch,channel):
 ready(case,channel);report(case)
 calls=[];actual=weekly.adapter.project_week
 def spy(*a,**kw):calls.append(kw);return actual(*a,**kw)
 monkeypatch.setattr(weekly.adapter,'project_week',spy)
 before=case.s.rows('SELECT * FROM tasks');history=case.s.rows('SELECT * FROM task_status_history')
 import sqlite3
 writes=[]
 def authorize(action,table,*args):
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):writes.append(table)
  return sqlite3.SQLITE_OK
 case.s.db.set_authorizer(authorize);rows=request(case);case.s.db.set_authorizer(None)
 assert len(rows)==1
 assert set(writes)<={'events','answer_citation_turns','outbox','runtime','intelligence_outbox_acl'}
 assert calls[0]['start']==date(2026,10,12) and calls[0]['end']==date(2026,10,19)
 assert len(calls[0]['result_bindings'])==1 and len(calls[0]['sources'])==1
 body=rows[0]['text']
 assert body.startswith(weekly.ATTRIBUTION)
 assert 'Статус переданной записи: черновик.' in body
 assert 'За период: зарегистрировано сообщение о выполнении.' in body
 assert 'Готов &lt;образец&gt; &amp; список.' in body and 'независимой проверки нет' in body
 assert case.task['id'] not in body and case.task['source_id'] not in body
 assert not re.search('MR-[0-9a-f]{32}',body)
 assert before==case.s.rows('SELECT * FROM tasks') and history==case.s.rows('SELECT * FROM task_status_history')
 refs=json.loads(case.s.rows('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(rows[0]['id'],))[0]['evidence_ids'])
 assert refs and all(r.startswith('work-task:') for r in refs)
 assert not case.s.rows('SELECT * FROM knowledge_documents')
 case.d.flush();assert len(case.api.calls)==1


@pytest.mark.parametrize('mode',['no_index','revoked','completed_record'])
def test_no_current_report_preserves_literal_i36_body(case,mode):
 ready(case)
 if mode=='revoked':
  doc=report(case);doc['state']='revoked';set_report_doc(case,doc)
 if mode=='completed_record':case.s.execute("UPDATE tasks SET status='completed',revision=revision+1")
 parent=command.registered_inputs(case.e,case.code,consumer_payload(case))
 draft=weekly.adapter.project_week(weekly._ReadView(case.s),case.c,'test_operator',meeting=parent.meeting,
  start=date(2026,10,12),end=date(2026,10,19),task_bindings=parent.task_bindings,result_bindings=(),sources=(),
  document_ids=parent.document_ids,meeting_evidence_ids=parent.meeting_evidence_ids,meeting_anchor_confirmed=True,now=case.clock[0]+1)
 expected=weekly.render.render_weekly_draft(draft)
 rows=request(case);assert [r['text'] for r in rows]==expected
 assert 'датированный результат за период не передан' in rows[0]['text']
 case.d.flush();assert len(case.api.calls)==1


@pytest.mark.parametrize('kind,label',[('выполнено','о выполнении'),('в работе','о ходе работы'),('заблокировано','о препятствии')])
def test_closed_reported_kinds_are_current_reported_states(case,kind,label):
 ready(case);report(case,kind=kind)
 assert label in request(case)[0]['text']


def test_exact_window_latest_report_and_same_day_conflict(case):
 ready(case);case.clock[0]+=2*86400
 report(case,kind='заблокировано',on='2026-10-16',event='r1')
 report(case,kind='выполнено',on='2026-10-17',event='r2')
 report(case,kind='в работе',on='2026-10-17',event='r3')
 row=request(case)[0]
 assert 'зарегистрированные результаты противоречат друг другу' in row['text']
 # Latest explicit dated report wins; no receipt/created-time ranking.
 report(case,kind='выполнено',on='2026-10-18',event='r4')
 rows=request(case,event='weekly2');body=rows[-1]['text']
 assert 'За период: зарегистрировано сообщение о выполнении' in body
 assert 'зарегистрированные результаты противоречат друг другу' not in body
 assert 'датированный результат за период не передан' in request(case,event='outside',start='2026-10-19',end='2026-10-25')[-1]['text']


@pytest.mark.parametrize('change',['withdraw','body','deleted','link','index_remove','receipt_extra','date','outcome','provenance','expired','parent_revoke','task_revision','acl','index_empty_to_absent','outside_add'])
def test_pending_report_or_metadata_drift_cancels_without_rebuild(case,change):
 ready(case);doc=report(case);row=request(case)[0]
 scope,_=command._gate(case.e,consumer_payload(case),case.e.intelligence)
 if change=='withdraw':doc['state']='revoked';set_report_doc(case,doc)
 elif change=='body':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(doc['source'],))
 elif change=='deleted':case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',(doc['source'],))
 elif change=='link':case.s.execute('DELETE FROM task_sources WHERE source_id=?',(doc['source'],))
 elif change=='index_remove':reports._save(case.e,reports._key(scope,case.code),[])
 elif change=='receipt_extra':doc['additional_metadata']='changed';set_report_doc(case,doc)
 elif change=='date':doc['on']='2026-10-15';set_report_doc(case,doc)
 elif change=='outcome':doc['outcome']='blocked';set_report_doc(case,doc)
 elif change=='provenance':doc['origin_event']='0'*64;set_report_doc(case,doc)
 elif change=='expired':case.clock[0]+=31*86400
 elif change=='parent_revoke':
  value=receipt(case);value['state']='revoked';case.s.execute("UPDATE runtime SET value=? WHERE key LIKE 'meeting-binding:v1:%'",(json.dumps(value),))
 elif change=='task_revision':case.s.execute('UPDATE tasks SET revision=revision+1')
 elif change=='acl':case.s.execute("UPDATE intelligence_task_acl SET evidence_ids='[\"denied\"]'")
 elif change=='index_empty_to_absent':
  # Begin a separate no-active-report output then remove its exact empty index.
  doc['state']='revoked';set_report_doc(case,doc);reports._save(case.e,reports._key(scope,case.code),[])
  case.s.execute("UPDATE outbox SET state='cancelled' WHERE id=?",(row['id'],))
  row=request(case,event='empty_index')[-1]
  case.s.execute('DELETE FROM runtime WHERE key=?',(reports._key(scope,case.code),))
 elif change=='outside_add':
  case.clock[0]+=4*86400
  # Register without flushing the pending weekly output.
  added,ack=submit_report(case,report_block(case,on='2026-10-19'),event='outside_report')
  assert added and ack
 case.d.flush();assert not any('недельный разбор' in c['text'] for c in case.api.calls)
 current=case.s.rows('SELECT state,error FROM outbox WHERE id=?',(row['id'],))[0]
 assert current==dict(state='cancelled',error=weekly.CANCEL)


@pytest.mark.parametrize('change',['withdraw','date','source','index','new_report'])
def test_report_drift_inside_business_route_callback_is_caught_preclaim(case,change):
 ready(case,'business');doc=report(case);row=request(case)[0]
 actual=case.api.call
 def call(method,**kw):
  if method=='getBusinessConnection':
   if change=='withdraw':doc['state']='revoked';set_report_doc(case,doc)
   elif change=='date':doc['on']='2026-10-15';set_report_doc(case,doc)
   elif change=='source':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(doc['source'],))
   elif change=='index':
    scope,_=command._gate(case.e,consumer_payload(case),case.e.intelligence);reports._save(case.e,reports._key(scope,case.code),[])
   elif change=='new_report':submit_report(case,report_block(case,kind='заблокировано'),event='route_new_report')
  return actual(method,**kw)
 case.api.call=call;case.d.flush()
 assert case.api.calls==[] and case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='cancelled'


def test_report_digest_persists_restart_and_old_receipt_is_not_rebuilt(case):
 ready(case);report(case);row=request(case)[0]
 key,doc=weekly_receipt(case,row);assert doc['version']==2 and re.fullmatch('[0-9a-f]{64}',doc['report_proof'])
 fresh=Engine(case.s,case.c,None,lambda:case.clock[0]);d=Dispatcher(case.s,case.c,case.api,lambda:case.clock[0],weekly_guard=weekly.make_delivery_guard(fresh))
 d.recover();d.flush();assert len(case.api.calls)==1
 case.api.calls.clear();row=request(case,event='old_receipt')[-1];key,doc=weekly_receipt(case,row)
 doc['version']=1;doc.pop('report_proof');case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(doc),key))
 d.flush();assert case.api.calls==[] and case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='cancelled'


@pytest.mark.parametrize('bad',['corrupt_index','oversized_record','invalid_digest','withdrawn_metadata','unknown_kind'])
def test_bad_report_metadata_never_falls_back_to_missing(case,bad):
 ready(case);doc=report(case)
 scope,_=command._gate(case.e,consumer_payload(case),case.e.intelligence)
 if bad=='corrupt_index':reports._save(case.e,reports._key(scope,case.code),['bad-id'])
 elif bad=='oversized_record':
  rid=reports._ids(weekly._Reader(case.s),scope,case.code)[0]
  case.s.execute('UPDATE runtime SET value=? WHERE key=?',('x'*12001,reports._key(scope,case.code,rid)))
 elif bad=='unknown_kind':doc['outcome']='unknown';set_report_doc(case,doc)
 elif bad=='withdrawn_metadata':
  doc['state']='revoked';set_report_doc(case,doc)
  row=request(case)[0];doc['origin_event']='different';set_report_doc(case,doc)
  case.d.flush();assert case.api.calls==[] and case.s.rows('SELECT state FROM outbox WHERE id=?',(row['id'],))[0]['state']=='cancelled';return
 elif bad=='invalid_digest':
  row=request(case)[0];key,value=weekly_receipt(case,row);value['report_proof']='bad';case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(value),key))
  case.d.flush();assert case.api.calls==[];return
 assert request(case)==[]
 assert case.s.rows("SELECT text FROM outbox WHERE dedupe LIKE 'weekly-review-notice:%'")[-1]['text']==weekly.NOTICE


@pytest.mark.parametrize('change',['withdraw','source','receipt','queue','protect','config'])
def test_multi_page_report_build_races_roll_back_every_page_and_receipt(case,monkeypatch,change):
 ready(case);doc=report(case)
 monkeypatch.setattr(weekly.render,'render_weekly_draft',lambda *a:['first','second'])
 actual=case.e.intelligence.protect_outbox;seen=[]
 def protect(identifier,refs):
  actual(identifier,refs);seen.append(identifier)
  if len(seen)==2:
   if change=='withdraw':doc['state']='revoked';set_report_doc(case,doc)
   elif change=='source':case.s.execute("UPDATE sources SET body='changed' WHERE id=?",(doc['source'],))
   elif change=='receipt':doc['origin_event']='changed';set_report_doc(case,doc)
   elif change=='protect':raise ValueError('synthetic protect race')
   elif change=='config':
    import copy
    case.e.c=copy.deepcopy(case.e.c)
 monkeypatch.setattr(case.e.intelligence,'protect_outbox',protect)
 if change=='queue':
  original=case.s.queue
  def queue(*a,**kw):
   if len(seen)==1 and str(a[3]).startswith(weekly.PREFIX):raise ValueError('synthetic queue race')
   return original(*a,**kw)
  monkeypatch.setattr(case.s,'queue',queue)
 assert request(case)==[]
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'weekly-review:%'")
 assert not case.s.rows("SELECT a.* FROM intelligence_outbox_acl a LEFT JOIN outbox o ON o.id=a.outbox_id WHERE o.id IS NULL")


def test_actual_fixed_reader_passes_14_build_7_per_guard(case,monkeypatch):
 ready(case);report(case)
 actual=weekly._Reader;readers=[];calls=[];load=reports.reported_inputs
 class Counted(actual):
  def __init__(self,*a,**kw):super().__init__(*a,**kw);readers.append(self)
 def loader(*a,**kw):calls.append(1);return load(*a,**kw)
 monkeypatch.setattr(weekly,'_Reader',Counted);monkeypatch.setattr('goldway.own_task_overview._Reader',Counted);monkeypatch.setattr(reports,'reported_inputs',loader)
 assert len(request(case))==1 and len(calls)==2 and len(readers)==14
 assert sum(r.bytes for r in readers)<=56*1024*1024
 assert all(r.queries<=2048 and r.rows<=8192 and r.bytes<=4*1024*1024 for r in readers)
 readers.clear();calls.clear();case.d.flush()
 assert len(case.api.calls)==1 and len(calls)==2 and len(readers)==14
 assert sum(r.bytes for r in readers)<=56*1024*1024 # two guard checks, 28MiB each


@pytest.mark.parametrize('overflow',['index33','pages9','total24000','single3500','draft16000'])
def test_hard_caps_refuse_whole_report_not_subset(case,monkeypatch,overflow):
 ready(case);report(case)
 if overflow=='index33':
  scope,_=command._gate(case.e,consumer_payload(case),case.e.intelligence)
  reports._save(case.e,reports._key(scope,case.code),['MR-'+format(i,'032x') for i in range(33)])
 elif overflow=='pages9':monkeypatch.setattr(weekly.render,'render_weekly_draft',lambda *a:['x']*9)
 elif overflow=='total24000':monkeypatch.setattr(weekly.render,'render_weekly_draft',lambda *a:['x'*3000]*8)
 elif overflow=='single3500':monkeypatch.setattr(weekly.render,'render_weekly_draft',lambda *a:['x'*3500])
 elif overflow=='draft16000':
  # 32 maximal reports on one date are valid I37 input; actual I04 draft
  # serialization must reject overflow rather than hiding some same-date inputs.
  for i in range(31):report(case,event='max'+str(i),literal='Ж'*295)
 assert request(case)==[]
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'weekly-review:%'")


def test_max32_reports_33_sources_reach_i04_then_final_page_caps_refuse_whole(case,monkeypatch):
 ready(case)
 for i in range(32):report(case,event='small'+str(i),literal='Готово.')
 seen=[];actual=weekly.adapter.project_week
 def spy(*a,**kw):
  seen.append((len(kw['result_bindings']),len(kw['sources'])+len(kw['meeting'].sources)))
  return actual(*a,**kw)
 monkeypatch.setattr(weekly.adapter,'project_week',spy)
 rows=request(case)
 assert seen==[(32,33)]
 # I07 accepts the complete typed draft; fixed attribution can take its
 # fullest page beyond3500, so refuse ALL pages without trimming reports.
 assert rows==[]
 assert not case.s.rows("SELECT * FROM runtime WHERE key LIKE 'weekly-review:%'")
