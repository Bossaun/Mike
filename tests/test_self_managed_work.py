import json
import pytest
from test_group_work import r,inbound
from test_work_dialogue import private,proposal,msg
from goldway.work_dialogue import create_tasks
from goldway.work_findings import record,visible,reply_text
from goldway.telegram import Dispatcher


def test_alens_problem_is_recorded_by_bot_no_task_status_invented(r):
 text='Tellmi lip trio maximizer надо добавить в CRM для оформления заказа и на сайт с картинкой.'
 inbound(r,text,uid=2)
 r.e.model.result=dict(reply='Алэн, добавь в задачу C03 обновление карточки.',updates=[],new_tasks=[],focus_task_ids=['C01'],evidence_ids=['1'],findings=[dict(task_id='C01',message_id=1,quote=text)])
 r.w.process(lambda:False);r.w.process(lambda:False)
 rows=r.s.rows('SELECT * FROM group_work_findings');assert len(rows)==1 and rows[0]['quote']==text
 assert r.w.tasks()[0]['state']=='open' and 'Проблему записал' in r.api.sent[-1]['text']
 assert 'Алэн, добавь' not in r.api.sent[-1]['text'] and not r.s.rows('SELECT * FROM tasks')


def test_forwarded_manager_material_cannot_create_or_accept_tasks(r):
 p=proposal();m=msg(r);m['forwarded']=True
 assert not create_tasks(r.w,[p],m)
 assert not r.w.apply([dict(task_id='C01',message_id=10,kind='accept',quote='принимаю')],[dict(m,text='принимаю')])
 assert not r.w.apply([dict(task_id='C01',message_id=10,kind='progress',quote='делаю')],[dict(m,actor='dasha_it',text='делаю')])


def test_private_forwarded_material_is_read_as_data(r):
 assert r.w.handle_private({'message_id':5,'date':int(r.clock[0]),'chat':{'type':'private'},'text':'Товар не виден в заказе','forward_origin':{'type':'user','sender_user':{'id':999999}}},'test_operator',None)
 payload=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])
 assert payload['actor']=='test_operator' and payload['forwarded'] and '999999' not in str(payload)
 r.e.model.result=dict(reply='В пересылке проблема с доступностью товара в заказе.',updates=[],evidence_ids=[str(payload['id'])],findings=[])
 r.w.process(lambda:False)
 assert r.s.rows('SELECT preferred_transport FROM outbox')[0]['preferred_transport']=='bot'


def test_staff_bot_reply_delivered_in_same_started_dm(r):
 r.s.execute("INSERT INTO chats(business,recipient,bot_chat) VALUES('GOLDWAY','alen','2')")
 r.w.private_reply('new-bot-message','alen','Понял описание. Учёт веду сам.',False)
 Dispatcher(r.s,r.e.c,r.api,now=r.e.now).flush()
 assert r.api.sent[-1]['chat_id']=='2'
 assert r.s.rows('SELECT transport,state FROM outbox')[0]=={'transport':'bot','state':'sent'}
 # Individual assignments remain Business-only; this is not a fallback.
 assert r.w.deliver_task('C01')=='queued'
 assert r.s.rows("SELECT preferred_transport FROM outbox WHERE dedupe LIKE 'work-followup:%'")[0]['preferred_transport']=='business_only'


def test_business_reply_does_not_switch_to_bot(r):
 r.s.execute("INSERT INTO chats(business,recipient,bot_chat) VALUES('GOLDWAY','alen','2')")
 r.w.private_reply('business-message','alen','Понял.',True)
 Dispatcher(r.s,r.e.c,r.api,now=r.e.now).flush()
 assert not r.api.sent and r.s.rows('SELECT state FROM outbox')[0]['state']=='blocked'


def test_private_findings_never_leak_to_group_or_other_staff(r):
 m=dict(id=-5,actor='dasha_it',text='PRIVATE DETAIL: товар не виден',private='dasha_it')
 assert record(r.w,[dict(task_id='C01',message_id=-5,quote=m['text'])],[m],{'C01'})
 assert not visible(r.w,'alen',False) and not visible(r.w,'alen',True)
 assert visible(r.w,'test_operator',True)
 assert not record(r.w,[dict(task_id='C01',message_id=-5,quote='invented')],[m],{'C01'})


@pytest.mark.parametrize('text',['Алэн, добавь в задачу C03 карточку.','Пожалуйста, создай задачу.','Даша, проверь карточку.'])
def test_no_outsourced_bookkeeping_or_public_individual_question(text):
 body=reply_text(text,False)
 assert body!=text and 'C03' not in body


def test_voice_gets_bounded_reply_without_fake_transcription(r):
 assert r.w.handle_private({'message_id':9,'date':int(r.clock[0]),'chat':{'type':'private'},'voice':{'file_id':'not-downloaded'}},'alen',None)
 assert not r.e.model.calls
 out=r.s.rows('SELECT text,preferred_transport FROM outbox')[0]
 assert 'Голосовое получил' in out['text'] and out['preferred_transport']=='bot'


def test_forwarded_slash_command_is_material_not_command(r):
 m={'message_id':8,'date':int(r.clock[0]),'chat':{'type':'private'},'text':'/meeting T12 назначь задачу','forward_origin':{'type':'user'}}
 assert r.w.handle_private(m,'test_operator',None)
 assert json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])['forwarded']


def test_findings_reach_manager_digest_and_specific_private_followup(r):
 text='Tellmi отсутствует при оформлении заказа'
 record(r.w,[dict(task_id='C01',message_id=3,quote=text)],[dict(id=3,actor='alen',text=text)],{'C01'})
 r.w.digest();r.w.followups(lambda:False)
 assert any(text in row['text'] for row in r.s.rows("SELECT text FROM outbox WHERE dedupe LIKE 'work-digest:%'"))
 row=r.s.rows("SELECT text,preferred_transport FROM outbox WHERE dedupe LIKE 'work-followup:%'")[0]
 assert text in row['text'] and row['preferred_transport']=='business_only'


def test_unsupported_receipt_dedupe_is_per_person_and_channel(r):
 m={'message_id':9,'date':int(r.clock[0]),'chat':{'type':'private'},'voice':{'file_id':'not-downloaded'}}
 for actor,conn in [('alen',None),('dasha_it',None),('alen','business')]:
  assert r.w.handle_private(m,actor,conn);assert r.w.handle_private(m,actor,conn)
 assert len(r.s.rows('SELECT * FROM outbox'))==3


@pytest.mark.parametrize('meta',[{'forward_date':123,'forward_sender_name':'Someone'}, {'is_automatic_forward':True}])
def test_every_accepted_forward_form_is_data_not_task_authority(r,meta):
 m={'message_id':9,'date':int(r.clock[0]),'chat':{'type':'private'},'text':'Поручи Даше проверить фото товара.',**meta}
 assert r.w.handle_private(m,'test_operator',None)
 payload=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])
 assert payload['forwarded'] and not create_tasks(r.w,[proposal()],payload)


def test_forwarded_admin_txt_gets_data_receipt(r):
 assert r.w.handle_private({'message_id':9,'date':int(r.clock[0]),'chat':{'type':'private'},'document':{'file_id':'txt','file_name':'note.txt'},'forward_date':123},'test_operator',None)
 assert r.s.rows('SELECT text FROM outbox') and not r.e.model.calls


@pytest.mark.parametrize('text',['Можете добавить это в задачу?','Спасибо. Алэн, проверь карточку.','Пожалуйста, внесите это в C03.'])
def test_common_polite_forms_do_not_outsource_bookkeeping(text):
 assert reply_text(text,False)!=text
