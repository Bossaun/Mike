import json
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from goldway.store import Store
from goldway.group_daily import GroupDaily
from goldway.group_work import GroupWork,deadline,start
from goldway.delivery_notices import DeliveryNotices
from goldway.telegram import DeliveryError
from goldway.model import response_schema

class API:
 def __init__(self):self.sent=[];self.role='administrator';self.error=None
 def call(self,method,**kw):
  if method=='getMe':return {'id':99,'username':'TEST_BOT'}
  if method=='getChat':return {'id':-123,'type':'group'}
  if method=='getChatMember':return {'status':self.role}
  assert method=='sendMessage';self.sent.append(kw)
  if self.error:raise self.error
  return {'message_id':1000+len(self.sent)}

@pytest.fixture
def r(tmp_path):
 clock=[datetime(2026,10,29,8,tzinfo=timezone.utc).timestamp()]
 s=Store(tmp_path/'db');api=API()
 c={'business':'GOLDWAY','live_send':True,'team_intake':True,'admins':['test_operator'],'bot_username':'TEST_BOT','employees':{'test_operator':{'name':'Майк','telegram_user_id':1},'alen':{'name':'Алэн','telegram_user_id':2},'dasha_it':{'name':'Даша','telegram_user_id':3}}}
 class Model:
  def __init__(self):self.calls=[];self.hook=None;self.result={'reply':'Даша, покажите проверенную карточку.','updates':[],'evidence_ids':['seed']}
  def propose(self,ctx):
   self.calls.append(ctx)
   if self.hook:self.hook()
   return self.result
 e=SimpleNamespace(s=s,c=c,b='GOLDWAY',now=lambda:clock[0],model=Model())
 gc=dict(enabled=True,verified=True,chat_id=-123,title='Test',timezone='Europe/Moscow',hour=10,minute=0,started_at=int(clock[0]-1),seed_context='Проверка карточки.')
 gp=tmp_path/'g.json';gp.write_text(json.dumps(gc));d=GroupDaily(e,api,{'id':99},gp)
 task=dict(id='C01',owner='dasha_it',title='Карточка',ask='Покажите проверку карточки.',criterion='Алэн сверил фото и остаток.',reviewer='alen',source='Synthetic meeting',active=True,daily=False)
 cfg=dict(enabled=True,verified=True,chat_id=-123,started_at=int(clock[0]-1),tasks=[task],participants={k:{'name':v['name'],'user_id':v['telegram_user_id']} for k,v in c['employees'].items()},kickoff=True)
 p=tmp_path/'w.json';p.write_text(json.dumps(cfg))
 np=tmp_path/'n.json';np.write_text(json.dumps(dict(enabled=True,manager='test_operator',started_at=clock[0]-1,transport='business_or_bot')))
 notices=DeliveryNotices(s,c,e.now,np);w=GroupWork(d,notices,p)
 yield SimpleNamespace(s=s,e=e,d=d,w=w,p=p,cfg=cfg,gp=gp,api=api,clock=clock,np=np,n=notices)
 s.db.close()

def inbound(r,text,uid=3,mid=1,**kw):
 m=dict(message_id=mid,date=int(r.clock[0]),chat={'id':-123,'type':'group'},**{'from':{'id':uid}},text=text);m.update(kw)
 u={'update_id':mid,'message':m};r.d.handle(u);r.w.handle(u);return u

def task(r):return r.s.rows('SELECT * FROM group_work_tasks')[0]

def update(r,kind,quote,uid=3,mid=1):
 m={'id':mid,'actor':{1:'test_operator',2:'alen',3:'dasha_it'}.get(uid),'text':quote}
 return r.w.apply([dict(task_id='C01',message_id=mid,kind=kind,quote=quote)],[m])

@pytest.mark.parametrize('text,parent',[('@TEST_BOT проверь доступ',{}),('Валли, что делать?',{}),('Помоги',{'reply_to_message':{'from':{'id':99},'message_id':77}})])
def test_addressed_message_replies_once_and_notifies_manager(r,text,parent):
 inbound(r,text,**parent);r.w.tick();r.w.tick()
 replies=[x for x in r.api.sent if 'reply_parameters' in x];assert len(replies)==1
 assert r.e.model.calls[0]['live_group_access']['can_read_all'] is True
 assert r.e.model.calls[0]['current_messages'][0]['addressed']
 assert len(r.s.rows('SELECT * FROM outbox'))==5
 GroupWork(r.d,r.n,r.p).tick();assert len(r.api.sent)==2
 assert len(r.s.rows('SELECT * FROM outbox'))==5

@pytest.mark.parametrize('kw',[{'chat':{'id':-99,'type':'group'}},{'date':1},{'from':{'id':99,'is_bot':True}},{'forward_origin':{}},{'sender_chat':{'id':-3}}])
def test_scope_rejects_old_forwarded_and_bot_messages(r,kw):
 inbound(r,'@TEST_BOT привет',**kw);assert not r.s.rows('SELECT * FROM group_work_events')

def test_edits_never_repeat_actions(r):
 u=inbound(r,'Валли, привет');r.w.tick();before=len(r.api.sent)
 r.w.handle({'update_id':2,'edited_message':u['message']});r.w.tick();assert len(r.api.sent)==before

def test_unaddressed_normal_chat_can_be_quiet(r):
 r.e.model.result={'reply':'','updates':[],'evidence_ids':[]}
 inbound(r,'Спасибо');r.w.tick();assert not [x for x in r.api.sent if 'reply_parameters' in x]

def test_minimal_privacy_still_answers_direct_mentions_but_no_daily(r):
 r.api.role='member';inbound(r,'@TEST_BOT привет');r.w.tick()
 assert len(r.api.sent)==1 and 'reply_parameters' in r.api.sent[0]
 assert not r.e.model.calls[0]['live_group_access']['can_read_all']

def test_owner_result_requires_independent_acceptance(r):
 assert not update(r,'result','Карточку исправил, фото проверил.',uid=2)
 assert update(r,'result','Карточку исправила, фото проверила.')
 assert task(r)['state']=='awaiting_acceptance'
 assert not update(r,'accept','Проверила, принимаю.',uid=3,mid=2)
 assert not update(r,'accept','Не принимаю.',uid=2,mid=2)
 assert update(r,'accept','Проверил фото и остаток, принимаю.',uid=2,mid=3)
 assert task(r)['state']=='done'
 assert r.s.rows('SELECT * FROM tasks')==[]

def test_fabricated_evidence_unknown_task_and_promises_cannot_complete(r):
 assert not update(r,'result','Сделаю завтра.')
 assert not update(r,'result','Не готово.')
 assert not r.w.apply([dict(task_id='C01',message_id=1,kind='result',quote='Готово')],[dict(id=1,actor='dasha_it',text='Когда будет готово?')])
 assert not r.w.apply([dict(task_id='C99',message_id=1,kind='result',quote='Готово')],[dict(id=1,actor='dasha_it',text='Готово')])
 assert task(r)['state']=='open'

def test_deadline_from_owner_literal_and_no_early_reminder(r):
 assert not update(r,'deadline','Возможно завтра в 15:00')
 assert not update(r,'deadline','Сделаю завтра в 15:00',uid=2)
 notes=update(r,'deadline','Сделаю 30.10.2026 в 15:00');assert notes and '30.10.2026 в 15:00' in notes[0]
 r.w.tick();assert len(r.api.sent)==1 # activation only
 r.clock[0]=task(r)['due']+1;r.w.tick();assert len(r.api.sent)==1
 assert len(r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'"))==1
 r.w.tick();assert len(r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'"))==1

def test_unagreed_date_is_not_invented(r):
 assert deadline('скоро',r.clock[0],'Europe/Moscow') is None
 assert deadline('сделаю 31.02.2026 15:00',r.clock[0],'Europe/Moscow') is None
 assert deadline('сегодня 09:00',r.clock[0],'Europe/Moscow') is None
 update(r,'progress','Работаю над фото.');assert task(r)['state']=='in_progress'
 assert task(r)['due'] is None

def test_unknown_send_holds_all_later_group_posts_and_no_manager_false_receipt(r):
 r.api.error=DeliveryError('unknown','timeout');r.w.tick();assert len(r.api.sent)==1
 assert not r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'delivery-notice:%'")
 r.api.error=None;r.clock[0]+=86400;GroupWork(r.d,r.n,r.p).tick();assert len(r.api.sent)==1

def test_revocation_during_model_prevents_update_and_send(r):
 inbound(r,'Валли, готово');r.e.model.hook=lambda:r.p.write_text('{"enabled":false}')
 r.e.model.result=dict(reply='Готово',updates=[dict(task_id='C01',message_id=1,kind='result',quote='готово')],evidence_ids=['1'])
 r.w.process(lambda:False);assert not r.api.sent and task(r)['state']=='open'

def test_link_policy_allows_only_own_address(r):
 r.w.send('links','group','Обращайтесь @TEST_BOT. https://example.com @someone Даша, проверьте фото.')
 text=r.api.sent[-1]['text'];assert '@TEST_BOT' in text and 'example.com' not in text and '@someone' not in text

def test_model_failure_is_visible_not_infinite_retry(r):
 inbound(r,'Валли, ответь');r.e.model.result={};r.w.process(lambda:False);r.w.process(lambda:False)
 assert len(r.e.model.calls)==1 and len(r.api.sent)==1 and 'не смог' in r.api.sent[0]['text']

def test_one_daily_followup_per_owner_survives_restart(r):
 r.w.tick();assert len(r.api.sent)==1
 assert len(r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'"))==1
 GroupWork(r.d,r.n,r.p).tick();assert len(r.api.sent)==1
 r.s.execute("UPDATE outbox SET state='sent',message_id='111' WHERE dedupe LIKE 'work-followup:%'")
 r.clock[0]+=86400;GroupWork(r.d,r.n,r.p).tick();assert len(r.api.sent)==1
 assert len(r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'"))==2


def test_notice_only_actual_new_delivery_and_not_recursive(r):
 s=r.s
 s.queue('GOLDWAY','alen','Проверьте остаток','staff1',r.clock[0])
 oid=s.rows('SELECT * FROM outbox')[0]['id']
 r.n.tick();assert len(s.rows('SELECT * FROM outbox'))==1
 s.execute("UPDATE outbox SET state='sent',message_id='111' WHERE id=?",(oid,))
 s.execute("INSERT INTO deliveries(outbox_id,at,state,detail) VALUES(?,?,'sent','111')",(oid,r.clock[0]))
 r.n.tick();r.n.tick();assert len(s.rows('SELECT * FROM outbox'))==2
 notice=s.rows('SELECT * FROM outbox')[1];assert notice['recipient']=='test_operator' and 'Алэн' in notice['text']
 s.execute("UPDATE outbox SET state='sent',message_id='112' WHERE id=?",(notice['id'],))
 s.execute("INSERT INTO deliveries(outbox_id,at,state,detail) VALUES(?,?,'sent','112')",(notice['id'],r.clock[0]))
 r.n.tick();assert len(s.rows('SELECT * FROM outbox'))==2

def test_notice_cutoff_unknown_revoked_and_nonadmin(r):
 r.n.record('old','alen','old',3,r.clock[0]-100);r.n.record('x','alen','x',None,r.clock[0]);assert not r.s.rows('SELECT * FROM outbox')
 cfg=json.loads(r.np.read_text());cfg['manager']='alen';r.np.write_text(json.dumps(cfg))
 r.n.record('new','alen','new',3,r.clock[0]);assert not r.s.rows('SELECT * FROM outbox')

def test_group_model_schema_is_scoped(r):
 assert 'updates' in response_schema({'kind':'group_work'})['properties']
 assert 'updates' not in response_schema({'kind':'group_daily'})['properties']

def test_invalid_optional_config_does_not_stop_private_worker(r):
 r.p.write_text('{bad');assert start(r.d,r.n,r.p) is None

def test_same_owner_report_is_idempotent(r):
 update(r,'result','Готово.');update(r,'result','Готово.')
 assert len(r.s.rows('SELECT * FROM group_work_changes'))==1


def test_negated_acceptance_substring_cannot_finish(r):
 update(r,'result','Карточка готова.')
 r.w.apply([dict(task_id='C01',message_id=2,kind='accept',quote='принимаю')],
           [dict(id=2,actor='alen',text='C01 не принимаю, фото ещё неверное.')])
 assert task(r)['state']=='awaiting_acceptance'


def test_negative_private_id_distinguishes_bot_business_and_config_order(r):
 m={'message_id':1,'date':int(r.clock[0]),'chat':{'id':3,'type':'private'},'text':'C01 делаю'}
 assert r.w.handle_private(m,'dasha_it',None)
 assert r.w.handle_private(m,'dasha_it','connection')
 assert len(r.s.rows('SELECT * FROM group_work_events'))==2
 r.cfg['participants']=dict(reversed(list(r.cfg['participants'].items())));r.p.write_text(json.dumps(r.cfg))
 w=GroupWork(r.d,r.n,r.p);assert w.handle_private(m,'dasha_it','connection')
 assert len(r.s.rows('SELECT * FROM group_work_events'))==2


def test_changed_assignment_and_inactive_config_are_respected_after_restart(r):
 update(r,'progress','Делаю.')
 r.cfg['tasks'][0]['owner']='alen';r.cfg['tasks'][0]['active']=False;r.p.write_text(json.dumps(r.cfg))
 w=GroupWork(r.d,r.n,r.p);assert w.tasks()[0]['owner']=='alen' and not w.tasks()[0]['active']
 assert w.tasks()[0]['state']=='open'
 w.tick();assert not [x for x in r.api.sent if 'Покажите проверку карточки' in x['text']]


def test_group_only_manager_route_never_receives_private_notice(r):
 from goldway.telegram import Dispatcher
 r.s.execute("INSERT INTO chats(business,recipient,group_chat) VALUES('GOLDWAY','test_operator','-123')")
 r.n.record('staff','alen','PRIVATE STAFF TEXT',123,r.clock[0])
 Dispatcher(r.s,r.e.c,r.api,now=r.e.now).flush()
 assert not r.api.sent
 assert r.s.rows('SELECT state FROM outbox')[0]['state']=='blocked'


def test_queued_work_and_notices_are_revoked_before_delivery(r):
 from goldway.telegram import Dispatcher
 r.s.execute("INSERT INTO chats(business,recipient,bot_chat) VALUES('GOLDWAY','test_operator','1')")
 r.s.execute("INSERT INTO chats(business,recipient,bot_chat) VALUES('GOLDWAY','dasha_it','3')")
 r.w.private_reply('1','dasha_it','reply',False)
 r.n.record('staff','alen','PRIVATE STAFF TEXT',123,r.clock[0])
 r.p.write_text('{"enabled":false}');r.np.write_text('{"enabled":false}')
 Dispatcher(r.s,r.e.c,r.api,now=r.e.now).flush()
 assert not r.api.sent
 assert all(x['state']=='cancelled' for x in r.s.rows('SELECT state FROM outbox'))


def test_business_only_cannot_fallback_to_bot(r):
 from goldway.telegram import Dispatcher
 r.e.c['business_bot_fallback']=True
 r.s.execute("INSERT INTO chats(business,recipient,bot_chat) VALUES('GOLDWAY','alen','2')")
 with pytest.raises(DeliveryError):Dispatcher(r.s,r.e.c,r.api,now=r.e.now).route('alen','business_only')


def test_addressed_event_precedes_normal_backlog(r):
 for i in range(1,15):inbound(r,'Обычное сообщение',mid=i)
 inbound(r,'Валли, проверь доступ',mid=15)
 assert r.w.pending();r.w.process(lambda:False)
 assert r.e.model.calls[0]['current_messages'][0]['id']==15


def test_private_report_does_not_appear_in_group_model_context(r):
 r.w.apply([dict(task_id='C01',message_id=-1,kind='result',quote='PRIVATE RESULT')],
           [dict(id=-1,actor='dasha_it',text='PRIVATE RESULT',private='dasha_it',focus=['C01'])])
 inbound(r,'Валли, покажи задачи');r.w.process(lambda:False)
 assert 'PRIVATE RESULT' not in json.dumps(r.e.model.calls[-1])


def test_private_yes_is_in_progress_only_for_single_task_focus(r):
 m=dict(id=-1,actor='dasha_it',text='Да',private='dasha_it',focus=['C01'])
 r.w.apply([dict(task_id='C01',message_id=-1,kind='progress',quote='Да')],[m]);assert task(r)['state']=='in_progress'
 r.s.execute("UPDATE group_work_tasks SET state='open'")
 m['id']=-2;m['focus']=['C01','C02']
 r.w.apply([dict(task_id='C01',message_id=-2,kind='progress',quote='Да')],[m]);assert task(r)['state']=='open'


def test_daily_touch_has_separate_reply_anchor(r):
 daily=dict(r.cfg['tasks'][0],id='C08',daily=True,owner='alen',title='Повторное касание',ask='Сделайте повторное касание.')
 r.cfg['tasks'].append(daily);r.p.write_text(json.dumps(r.cfg));w=GroupWork(r.d,r.n,r.p);w.tick()
 touch=[x for x in r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'") if 'Сделайте повторное касание' in x['text']];assert len(touch)==1 and 'Покажите проверку карточки' not in touch[0]['text']


def test_daily_digest_has_real_states_and_dedupe(r):
 update(r,'progress','Делаю.');r.w.digest();r.w.digest()
 rows=r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-digest:%'")
 assert len(rows)==2 and 'В работе по ответу исполнителя:' in rows[0]['text']
 assert 'C01' not in rows[0]['text'] and 'C01' in rows[1]['text']
 assert rows[1]['dedupe']==rows[0]['dedupe']+':details:1'
 assert rows[0]['preferred_transport']=='private_only'


def test_inspection_is_not_acceptance_and_staff_never_fallback(r):
 update(r,'result','Карточка готова.')
 assert not update(r,'accept','Проверил C01: фото неверное, остаток ошибочный.',uid=2,mid=2)
 assert task(r)['state']=='awaiting_acceptance'
 r.w.private_reply('staff','dasha_it','C01 в работе',False)
 assert r.s.rows('SELECT preferred_transport FROM outbox')[0]['preferred_transport']=='bot'


@pytest.mark.parametrize('author',[{'id':88,'is_bot':True},{'id':2}])
@pytest.mark.parametrize('media',[{}, {'photo':[{'file_id':'photo','width':100,'height':100}]}, {'voice':{'file_id':'voice','duration':5,'file_size':100}}])
def test_foreign_reply_is_passive_even_with_media(r,author,media):
 inbound(r,'не нужно',uid=1,reply_to_message={'message_id':77,'from':author},**media)
 assert not r.s.rows('SELECT * FROM group_work_events')
 r.w.process(lambda:False)
 assert not r.e.model.calls
 assert not r.api.sent


def test_explicit_mention_overrides_foreign_reply(r):
 inbound(r,'@TEST_BOT помоги',reply_to_message={'message_id':77,'from':{'id':88,'is_bot':True}})
 r.w.process(lambda:False)
 assert len(r.e.model.calls)==1
 assert len(r.api.sent)==1


def test_unaddressed_model_reply_never_interrupts_group(r):
 inbound(r,'Спасибо')
 r.w.process(lambda:False)
 assert len(r.e.model.calls)==1
 assert not r.api.sent
 assert r.s.rows('SELECT state FROM group_work_events')[0]['state']=='done'
