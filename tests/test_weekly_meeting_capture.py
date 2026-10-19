"""I38 actual explicit reported results through existing I36 runtime; synthetic only."""
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher
import json,re
import goldway.meeting_binding as command
from datetime import date,timedelta,datetime,timezone
from zoneinfo import ZoneInfo
from dataclasses import replace,FrozenInstanceError
from goldway import weekly_meeting_capture as capture_module
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


def make_case(path):
 s=Store(str(path));clock=[NOW]
 config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,
  allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 engine=Engine(s,config,None,lambda:clock[0]);engine.intelligence.sources.refresh=lambda *a,**kw:{}
 s.execute("INSERT INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES('GOLDWAY','test_operator','1','1','conn',?)",(NOW,))
 return SimpleNamespace(s=s,e=engine,c=config,clock=clock)


@pytest.fixture
def case(monkeypatch,tmp_path):
 value=make_case(tmp_path/'synthetic.sqlite');value.path=tmp_path/'synthetic.sqlite'
 import builtins,socket,httpx
 def no_io(*a,**k):pytest.fail('unexpected external I/O')
 monkeypatch.setattr(builtins,'open',no_io);monkeypatch.setattr(socket,'socket',no_io);monkeypatch.setattr(httpx,'Client',no_io)
 value.reopen=lambda:None
 def reopen():
  value.s.db.close();value.s=Store(str(value.path));value.e=Engine(value.s,value.c,None,lambda:value.clock[0]);value.e.model=SimpleNamespace(propose=no_io);value.e._context=no_io;value.e.intelligence.sources.refresh=no_io;value.e.intelligence.index.search=no_io
  value.d=Dispatcher(value.s,value.c,value.api,lambda:value.clock[0],weekly_guard=weekly.make_delivery_guard(value.e));value.capture=capture_module.WeeklyMeetingCapture(value.e,enabled=True)
 value.reopen=reopen
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




def init(c,channel='bot'):
 ready(c,channel);c.capture=capture_module.WeeklyMeetingCapture(c.e,enabled=True);return c
def at(c,day,hour=12):c.clock[0]=int(datetime.fromisoformat(day+'T'+str(hour).zfill(2)+':00:00').replace(tzinfo=c.e.tz).timestamp())
def arm(c):
 result=c.capture.arm(c.code,scope_payload=consumer_payload(c));c.watch_key=c.capture._check(c.code,consumer_payload(c))['key'];return result
def due(c):return c.capture.capture_due(c.code,scope_payload=consumer_payload(c))
def current(c):return c.capture.read_current(c.code,scope_payload=consumer_payload(c))
def key(c):return c.watch_key if hasattr(c,'watch_key') else c.capture._check(c.code,consumer_payload(c))['key']
def raw_watch(c):
 r=c.s.execute('SELECT value FROM runtime WHERE key=?',(key(c),)).fetchone();return None if not r else r[0]
def deny(fn):
 with pytest.raises(capture_module.WeeklyCaptureUnavailable,match='^weekly_meeting_capture_unavailable$'):fn()
def test_actual_registered_identity_stable_mutable_tasks_report_and_public_readonly_builder(case):
 init(case);payload=consumer_payload(case);identity=weekly.registered_week_identity(case.e,case.code,payload);before=case.s.db.serialize()
 snapshot=weekly.build_registered_weekly_review(case.e,case.code,payload,start=date(2026,10,16),end=date(2026,10,23))
 assert snapshot.identity==identity and snapshot.pages and snapshot.requires_review and not snapshot.live_authorized and case.s.db.serialize()==before
 assert 'черновик' in '\n'.join(snapshot.pages) and all(not r.startswith('meeting-access:') for r in snapshot.refs)
 case.s.execute("UPDATE tasks SET status='completed',revision=revision+1");second=weekly.build_registered_weekly_review(case.e,case.code,payload,start=date(2026,10,16),end=date(2026,10,23))
 assert second.identity==identity and second.proof!=snapshot.proof and 'датированный результат за период не передан' in '\n'.join(second.pages)
 at(case,'2026-10-22');report(case,on='2026-10-20');third=weekly.build_registered_weekly_review(case.e,case.code,payload,start=date(2026,10,16),end=date(2026,10,23))
 assert third.identity==identity and third.report_proof!=second.report_proof and weekly.ATTRIBUTION in third.pages[0] and 'Готов' in '\n'.join(third.pages)


def test_arm_notdue_correct_task_report_before_due_reopen_captureonce_currentfresh(case,monkeypatch):
 init(case);watch=arm(case);assert not watch.already_armed and watch.due_on==date(2026,10,23);saved=raw_watch(case)
 actual=weekly.build_registered_weekly_review;calls=[]
 def build(*a,**kw):calls.append(1);return actual(*a,**kw)
 monkeypatch.setattr(weekly,'build_registered_weekly_review',build);case.capture=capture_module.WeeklyMeetingCapture(case.e,enabled=True)
 result=due(case);assert not result.due and not result.missed and calls==[] and raw_watch(case)==saved
 case.s.execute("UPDATE tasks SET status='completed',revision=revision+1");at(case,'2026-10-22');report(case,on='2026-10-20');case.reopen();at(case,'2026-10-23')
 baseline={t:case.s.rows('SELECT * FROM '+t) for t in ('tasks','events','outbox','deliveries','sources')};receipt=due(case)
 assert receipt.due and not receipt.already_captured and not receipt.payload_verified and receipt.capture_id and len(calls)==2
 assert all(case.s.rows('SELECT * FROM '+t)==rows for t,rows in baseline.items())
 raw=raw_watch(case);calls.clear();case.clock[0]+=20;replay=due(case);assert replay.already_captured and replay.capture_id==receipt.capture_id and replay.captured_at==receipt.captured_at and not replay.payload_verified and not calls and raw_watch(case)==raw
 case.reopen();calls.clear();reopened=due(case);assert reopened.already_captured and not calls and reopened.capture_id==receipt.capture_id
 fresh=current(case);assert len(calls)==2 and fresh.captured_at==receipt.captured_at and fresh.snapshot.as_of>receipt.captured_at and weekly.ATTRIBUTION in fresh.snapshot.pages[0] and 'выполнении' in '\n'.join(fresh.snapshot.pages)
 with pytest.raises(FrozenInstanceError):fresh.live_authorized=True

@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_manual_handler_compatible_protected_v2_pages_deliveryguards(case,channel):
 init(case,channel);report(case);payload=consumer_payload(case)
 before=weekly.build_registered_weekly_review(case.e,case.code,payload,start=date(2026,10,16),end=date(2026,10,23))
 rows=request(case,start='2026-10-16',end='2026-10-22')
 assert rows and all(r['text']==before.pages[i] for i,r in enumerate(rows))
 for row in rows:
  _,receipt_doc=weekly_receipt(case,row)
  assert receipt_doc['version']==2 and receipt_doc['proof']==before.proof and receipt_doc['report_proof']==before.report_proof and receipt_doc['refs']==list(before.refs)
  assert weekly.make_delivery_guard(case.e)(row)
 case.d.flush();assert all(r['state']=='sent' for r in case.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'weekly-review:%'"))


def test_due_calendar_dst_seven_dates_not_168_hours(case):
 case.c['timezone']='Europe/Berlin';case.e.tz=ZoneInfo('Europe/Berlin');at(case,'2026-10-23');setup(case);confirm(case,block(case,day='2026-10-23',until='2026-11-05'));case.d.flush();case.capture=capture_module.WeeklyMeetingCapture(case.e,enabled=True);watch=arm(case)
 assert watch.due_on==date(2026,10,30)
 before=datetime(2026,10,23,12,tzinfo=case.e.tz).timestamp();at(case,'2026-10-30');assert case.clock[0]-before==169*3600
 assert due(case).due

@pytest.mark.parametrize('change',['wrongactor','wrongtransport','foreigncode','missingcode','extrafield','disabled'])
def test_no_new_scope_or_invented_event_authority(case,change):
 init(case);payload=consumer_payload(case);code=case.code
 if change=='wrongactor':payload['sender']='frost';payload['source_id']='telegram:2:99'
 elif change=='wrongtransport':payload['channel']='group'
 elif change=='foreigncode':code='MB-'+'f'*16
 elif change=='missingcode':code='latest'
 elif change=='extrafield':payload['event_id']='fabricated'
 else:case.capture=capture_module.WeeklyMeetingCapture(case.e,enabled=False)
 before=case.s.db.serialize();deny(lambda:case.capture.arm(code,scope_payload=payload));assert case.s.db.serialize()==before

@pytest.mark.parametrize('change',['missed','futuremeeting','duetoday','expirybeforeday'])
def test_arm_due_access_and_missed_not_catchup(case,change):
 init(case)
 if change=='missed':arm(case);at(case,'2026-10-24');old=raw_watch(case);r=due(case);assert r.missed and not r.due and r.captured_at is None and raw_watch(case)==old;return
 if change=='futuremeeting':at(case,'2026-10-15')
 elif change=='duetoday':at(case,'2026-10-23')
 else:
  scope,_=command._gate(case.e,consumer_payload(case),case.e.intelligence);doc=receipt(case);doc['expiry']=datetime(2026,10,22,23,tzinfo=case.e.tz).timestamp();command._save(case.e,scope,case.code,doc)
 deny(lambda:arm(case))

@pytest.mark.parametrize('change',['taskrevision','taskACL','registration','source','reportwithdraw','newreport','body'])
def test_aftercapture_drift_refuses_before_saved_payload_transfer_no_recapture(case,monkeypatch,change):
 init(case);arm(case);at(case,'2026-10-22');doc=report(case,on='2026-10-20');at(case,'2026-10-23');due(case);raw=raw_watch(case)
 if change=='taskrevision':case.s.execute('UPDATE tasks SET revision=revision+1')
 elif change=='taskACL':case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps(['unreadable']),))
 elif change=='registration':
  scope,_=command._gate(case.e,consumer_payload(case),case.e.intelligence);reg=receipt(case);reg['confirmed_event']='f'*64;command._save(case.e,scope,case.code,reg)
 elif change=='source':case.s.execute('UPDATE sources SET deleted=1 WHERE id=?',(doc['source'],))
 elif change=='reportwithdraw':doc['state']='revoked';set_report_doc(case,doc)
 elif change=='newreport':report(case,event='new-report',on='2026-10-21',literal='Новые переданные данные.')
 else:case.s.execute('UPDATE sources SET body=? WHERE id=?',('tampered body',doc['source']))
 def fail(*a,**kw):pytest.fail('saved payload transferred before fresh source proof')
 monkeypatch.setattr(capture_module._Reader,'payload',fail);deny(lambda:current(case));assert raw_watch(case)==raw

@pytest.mark.parametrize('change',['policy','configidentity','Intelidentity','clockidentity','connection','clockrollback','daycross'])
def test_captured_host_clock_and_policy_drift(case,monkeypatch,change):
 init(case);arm(case);at(case,'2026-10-23');old=case.s.db;actual=weekly.build_registered_weekly_review
 def build(*a,**kw):
  value=actual(*a,**kw)
  if change=='policy':case.c['daily_summary']=True
  elif change=='configidentity':case.e.c=dict(case.c)
  elif change=='Intelidentity':case.e.intelligence=object()
  elif change=='clockidentity':case.e.now=lambda:case.clock[0]
  elif change=='connection':case.s.db=Store(':memory:').db
  elif change=='clockrollback':case.clock[0]-=1
  else:at(case,'2026-10-24')
  return value
 monkeypatch.setattr(weekly,'build_registered_weekly_review',build);case.capture=capture_module.WeeklyMeetingCapture(case.e,enabled=True)
 try:deny(lambda:due(case))
 finally:case.s.db=old
 assert json.loads(raw_watch(case))['capture'] is None

@pytest.mark.parametrize('change',['casrace','sourceafterwrite','writefailure','payloadtamper'])
def test_capture_own_cas_and_source_after_write_atomic_rollback(case,monkeypatch,change):
 init(case);arm(case);at(case,'2026-10-23');watch=raw_watch(case);execute=case.s.execute;hit=[]
 def run(sql,args=()):
  if sql.startswith('UPDATE runtime SET value=') and args[1]==key_value and not hit:
   hit.append(1)
   if change=='casrace':execute('UPDATE runtime SET value=? WHERE key=?',(watch+' ',key_value))
   if change=='writefailure':raise ValueError('synthetic write failure')
   out=execute(sql,args)
   if change=='sourceafterwrite':execute('UPDATE sources SET deleted=1 WHERE id=?',(case.task['source_id'],))
   if change=='payloadtamper':execute('UPDATE runtime SET value=? WHERE key=?',('BROKEN',key_value))
   return out
  return execute(sql,args)
 key_value=key(case);monkeypatch.setattr(case.s,'execute',run);deny(lambda:due(case));assert hit and raw_watch(case)==watch


def test_metadata_only_replay_no_build_or_payload_and_current_read_checks_corruption(case,monkeypatch):
 init(case);arm(case);at(case,'2026-10-23');first=due(case);value=json.loads(raw_watch(case));value['capture']['payload']['pages']=['CORRUPT'];case.s.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(value,ensure_ascii=False,separators=(',',':')),key(case)))
 actual=weekly.build_registered_weekly_review
 def fail(*a,**kw):pytest.fail('alreadycaptured replay built comparison')
 monkeypatch.setattr(weekly,'build_registered_weekly_review',fail);case.capture=capture_module.WeeklyMeetingCapture(case.e,enabled=True);replay=due(case)
 assert replay.already_captured and replay.capture_id==first.capture_id and not replay.payload_verified
 monkeypatch.setattr(weekly,'build_registered_weekly_review',actual);case.capture=capture_module.WeeklyMeetingCapture(case.e,enabled=True);deny(lambda:current(case))

@pytest.mark.parametrize('change',['oversize','malformed','partial','unknown'])
def test_corrupt_or_unsupported_checkpoint_never_repairs_or_recaptures(case,change):
 init(case);arm(case);at(case,'2026-10-23');value=json.loads(raw_watch(case))
 if change=='oversize':encoded='x'*(capture_module.MAX_RECORD_BYTES+1)
 elif change=='malformed':encoded='broken'
 elif change=='partial':value['capture']={'captured_at':case.clock[0]};encoded=json.dumps(value)
 else:value['unknown']='unexpected';encoded=json.dumps(value)
 case.s.execute('UPDATE runtime SET value=? WHERE key=?',(encoded,key(case)));deny(lambda:due(case));assert raw_watch(case)==encoded


def test_reader_counts_build_twoload_manual_and_capture_ceiling(case,monkeypatch):
 init(case);counts=[];actual=weekly._Reader.__init__
 def reader(self,*a,**kw):counts.append(self);return actual(self,*a,**kw)
 monkeypatch.setattr(weekly._Reader,'__init__',reader)
 # Shared class identity is used by actual I35/I37 _PassReader and proof readers.
 payload=consumer_payload(case);weekly.build_registered_weekly_review(case.e,case.code,payload,start=date(2026,10,16),end=date(2026,10,23));assert len(counts)==14
 counts.clear();request(case,start='2026-10-16',end='2026-10-22');assert len(counts)==14
 counts.clear();arm(case);assert len(counts)==6
 counts.clear();at(case,'2026-10-23');due(case);assert len(counts)==28 and all(r.bytes<=4194304 and r.rows<=8192 and r.queries<=2048 for r in counts)


@pytest.mark.parametrize('change',['ACL','link','body','expiry'])
def test_public_identity_final_registered_inputs_rechecks_late_load_mutation(case,monkeypatch,change):
 init(case);actual=command._load;calls=[]
 def load(*a,**kw):
  value=actual(*a,**kw);calls.append(1)
  if len(calls)==3:
   if change=='ACL':case.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps(['unreadable']),))
   elif change=='link':case.s.execute('DELETE FROM task_sources WHERE task_id=?',(case.task['id'],))
   elif change=='body':case.s.execute('UPDATE sources SET body=? WHERE id=?',('tampered',case.task['source_id']))
   else:
    value=dict(value,expiry=case.clock[0]);scope,_=command._gate(case.e,consumer_payload(case),case.e.intelligence);command._save(case.e,scope,case.code,value)
  return value
 monkeypatch.setattr(command,'_load',load)
 with pytest.raises(ValueError):weekly.registered_week_identity(case.e,case.code,consumer_payload(case))
 assert len(calls)>=3


def test_arm_source_revoked_during_insert_rolls_back_watch_and_callback_change(case,monkeypatch):
 init(case);point=key(case);execute=case.s.execute;hit=[]
 def run(sql,args=()):
  out=execute(sql,args)
  if sql.startswith('INSERT INTO runtime') and args[0]==point and not hit:
   hit.append(1);execute('UPDATE sources SET deleted=1 WHERE id=?',(case.task['source_id'],))
  return out
 monkeypatch.setattr(case.s,'execute',run);deny(lambda:arm(case));assert hit and execute('SELECT value FROM runtime WHERE key=?',(point,)).fetchone() is None
 assert execute('SELECT deleted FROM sources WHERE id=?',(case.task['source_id'],)).fetchone()[0]==0


def test_capture_record_whole_overflow_rollback_no_partial_pages(case,monkeypatch):
 init(case);arm(case);at(case,'2026-10-23');old=raw_watch(case)
 monkeypatch.setattr(capture_module,'MAX_RECORD_BYTES',len(old.encode())+100);deny(lambda:due(case));assert raw_watch(case)==old

@pytest.mark.parametrize('change',['draft','pages'])
def test_current_read_checks_full_saved_shape_even_recomputed_local_hash(case,change):
 init(case);arm(case);at(case,'2026-10-23');due(case);doc=json.loads(raw_watch(case));payload=doc['capture']['payload']
 payload['draft']={} if change=='draft' else payload['draft']
 if change=='pages':payload['pages']=['NOT ACTUAL RENDER']
 doc['capture']['body_hash']=capture_module._hash(payload);encoded=capture_module._json(doc);case.s.execute('UPDATE runtime SET value=? WHERE key=?',(encoded,key(case)));deny(lambda:current(case));assert raw_watch(case)==encoded


def test_alreadycaptured_laterday_replay_is_not_missed_or_rebuilt(case,monkeypatch):
 init(case);arm(case);at(case,'2026-10-23');first=due(case);old=raw_watch(case);at(case,'2026-10-24')
 def fail(*a,**kw):pytest.fail('captured replay rebuilt on later day')
 monkeypatch.setattr(weekly,'build_registered_weekly_review',fail);case.capture=capture_module.WeeklyMeetingCapture(case.e,enabled=True)
 replay=due(case);assert replay.already_captured and not replay.missed and not replay.due and replay.capture_id==first.capture_id and raw_watch(case)==old and not replay.payload_verified
