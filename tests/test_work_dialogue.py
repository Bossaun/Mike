import json
import pytest
from test_group_work import r,inbound
from goldway.group_work import GroupWork,deadline
from goldway.work_dialogue import create_tasks,visible,conversation,reference_tasks


def private(r,text,actor='test_operator',mid=10):
 return r.w.handle_private({'message_id':mid,'date':int(r.clock[0]),'chat':{'type':'private','id':1},'text':text},actor,None)

def msg(r,text='Поручи Даше проверить фото товара.',actor='test_operator',private=True,id=10):
 return dict(id=id,text=text,actor=actor,at=r.clock[0],**({'private':actor} if private else {}))

def proposal():return dict(owner='dasha_it',title='Фото товара',ask='Проверь фото товара.',criterion='Фото соответствует упаковке.',quote='Поручи Даше проверить фото товара.')


def test_manager_question_without_id_uses_sent_reports(r):
 r.s.queue('GOLDWAY','test_operator','Я отправил Даше: проверь фото.','delivery-notice:example',r.clock[0]-20);r.s.execute("UPDATE outbox SET state='sent',message_id='10'")
 r.s.queue('GOLDWAY','test_operator','NOT SENT','work-reply:unsent',r.clock[0]-10)
 assert private(r,'Что от меня конкретно требуется?')
 r.e.model.result=dict(reply='Сейчас ничего срочного: Даша проверяет фото. Я сообщу, когда потребуется ваше решение.',updates=[],new_tasks=[],focus_task_ids=[],evidence_ids=['seed'])
 r.w.process(lambda:False);c=r.e.model.calls[0]
 assert c['actor_admin'] and len(c['tasks'])==1
 assert 'Я отправил Даше' in str(c['conversation']) and 'NOT SENT' not in str(c['conversation'])
 out=r.s.rows("SELECT text FROM outbox WHERE dedupe LIKE 'work-reply:reply:%'");assert len(out)==1 and 'Сейчас ничего' in out[0]['text']


def test_staff_freeform_progress_no_number(r):
 assert private(r,'Занимаюсь фотографией карточки.','dasha_it')
 m=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])
 r.e.model.result=dict(reply='Понял. Когда получится показать?',updates=[dict(task_id='C01',message_id=m['id'],kind='progress',quote=m['text'])],evidence_ids=[str(m['id'])],focus_task_ids=['C01'],new_tasks=[])
 r.w.process(lambda:False);assert r.w.tasks()[0]['state']=='in_progress'
 assert r.s.rows("SELECT preferred_transport FROM outbox WHERE dedupe LIKE 'work-reply:%'")[0]['preferred_transport']=='bot'


def test_private_history_is_recipient_specific(r):
 private(r,'PRIVATE MANAGER');r.w.process(lambda:False)
 assert 'PRIVATE MANAGER' not in str(conversation(r.w,msg(r,'other',actor='dasha_it')))


@pytest.mark.parametrize('sent',[True,False])
def test_plain_yes_requires_one_sent_question(r,sent):
 private(r,'Фото сейчас проверю.','dasha_it',1)
 r.e.model.result=dict(reply='Берёте проверку фото в работу?',updates=[],evidence_ids=['seed'],focus_task_ids=['C01'],new_tasks=[]);r.w.process(lambda:False)
 if sent:r.s.execute("UPDATE outbox SET state='sent',message_id='100' WHERE dedupe LIKE 'work-reply:%'")
 assert private(r,'Да','dasha_it',2)
 m=json.loads(r.s.rows('SELECT payload FROM group_work_events ORDER BY rowid DESC LIMIT 1')[0]['payload']);assert m['focus']==(['C01'] if sent else [])
 notes=r.w.apply([dict(task_id='C01',message_id=m['id'],kind='progress',quote='Да')],[m]);assert bool(notes)==sent


def test_private_owner_task_persists_without_group_leak(r):
 notes=create_tasks(r.w,[proposal()],msg(r));assert notes and 'Передаю' in notes[0]
 assert len(r.w.tasks())==2 and not r.api.sent
 assert len(visible(r.w,'dasha_it',False))==1 and len(visible(r.w,'alen',True))==1
 assert len(visible(r.w,'test_operator',True))==2
 assert not create_tasks(r.w,[proposal()],msg(r))
 w=GroupWork(r.d,r.n,r.p);assert len(w.tasks())==2;w.followups(lambda:False)
 assert all('Фото товара' not in x['text'] for x in r.api.sent)


def test_group_owner_task_queues_only_private_assignment(r):
 notes=create_tasks(r.w,[proposal()],msg(r,private=False));assert 'Передаю' in notes[0]
 assert not r.api.sent
 r.w.followups(lambda:False)
 rows=r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'")
 assert len(rows)==1 and rows[0]['preferred_transport']=='business_only'


@pytest.mark.parametrize('actor,text,owner',[('dasha_it','Поручи Даше проверить фото товара.','dasha_it'),('test_operator','Не ставь задачу. Поручи Даше проверить фото товара.','dasha_it'),('test_operator','Например: Поручи Даше проверить фото товара.','dasha_it'),('test_operator','Поручи Даше проверить фото товара.','vlad'),('test_operator','Ничего не поручал','dasha_it')])
def test_invalid_task_proposals_do_not_create(r,actor,text,owner):
 p=proposal();p['owner']=owner;assert not create_tasks(r.w,[p],msg(r,text,actor));assert len(r.w.tasks())==1 and not r.api.sent


def test_forward_or_command_not_swallowed_by_new_intake(r):
 assert r.w.handle_private({'message_id':1,'date':r.clock[0],'chat':{'type':'private'},'text':'Поручи Даше','forward_origin':{}},'test_operator',None)
 assert not private(r,'/summary')


def test_new_private_task_cannot_be_changed_in_group(r):
 create_tasks(r.w,[proposal()],msg(r))
 assert not r.w.apply([dict(task_id='C02',message_id=11,kind='progress',quote='Делаю фото')],[dict(id=11,actor='dasha_it',text='Делаю фото')]);assert r.w.tasks()[1]['state']=='open'


def test_relative_deadline_without_format(r):
 text='Завтра вечером закончу фото'
 r.w.apply([dict(task_id='C01',message_id=1,kind='deadline',quote=text)],[dict(id=1,actor='dasha_it',text=text,at=r.clock[0])])
 t=r.w.tasks()[0];assert t['deadline_text']==text and t['due']>r.clock[0]+86400
 r.w.followups(lambda:False);assert not r.api.sent
 assert deadline('завтра к 15',r.clock[0],'Europe/Moscow') is not None


def test_natural_acceptance_still_needs_result(r):
 assert not r.w.apply([dict(task_id='C01',message_id=1,kind='accept',quote='Всё верно')],[dict(id=1,actor='alen',text='Всё верно')])
 r.w.apply([dict(task_id='C01',message_id=2,kind='result',quote='Фото готово')],[dict(id=2,actor='dasha_it',text='Фото готово')])
 assert r.w.apply([dict(task_id='C01',message_id=3,kind='accept',quote='Всё верно')],[dict(id=3,actor='alen',text='Всё верно')]);assert r.w.tasks()[0]['state']=='done'


def test_followup_no_mandatory_number_or_date_template(r):
 r.w.followups(lambda:False);text=r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'")[0]['text']
 assert 'номером пункта' not in text and 'C01' not in text
 assert len(text)<=450 and not r.api.sent


def test_model_cannot_change_assignee_or_make_task_from_question(r):
 p=proposal();p['quote']='Поручи Алэну проверить фото.'
 assert not create_tasks(r.w,[p],msg(r,p['quote']))
 p['quote']='требуется';assert not create_tasks(r.w,[p],msg(r,'Что от меня требуется?'))
 assert len(r.w.tasks())==1


def test_private_deadline_never_enters_group_context(r):
 text='Завтра вечером, PRIVATE_REASON'
 r.w.apply([dict(task_id='C01',message_id=1,kind='deadline',quote=text)],[dict(id=1,actor='dasha_it',text=text,private='dasha_it',at=r.clock[0])])
 inbound(r,'Валли, как дела?',mid=2);r.w.process(lambda:False)
 assert 'PRIVATE_REASON' not in str(r.e.model.calls[0])
 assert r.e.model.calls[0]['tasks'][0]['due'] is None


def test_yes_cannot_update_a_different_task(r):
 create_tasks(r.w,[proposal()],msg(r))
 assert not r.w.apply([dict(task_id='C02',message_id=11,kind='progress',quote='Да')],[dict(id=11,actor='dasha_it',text='Да',private='dasha_it',focus=['C01'])])
 assert r.w.tasks()[1]['state']=='open'


def test_sent_assignment_anchors_plain_yes(r):
 create_tasks(r.w,[proposal()],msg(r))
 r.s.execute("UPDATE outbox SET state='sent',message_id='555' WHERE dedupe LIKE 'work-followup:%'")
 private(r,'Да','dasha_it',11)
 m=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])
 assert m['focus']==['C02']


def test_unknown_assignment_no_retry_or_group_fallback_next_day(r):
 create_tasks(r.w,[proposal()],msg(r,private=False));r.s.execute("UPDATE outbox SET state='unknown' WHERE dedupe LIKE 'work-followup:%'")
 r.clock[0]+=86400;r.w.followups(lambda:False)
 assert not r.api.sent and len(r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'"))==1


def test_missing_business_route_keeps_one_question_and_never_group(r):
 from goldway.telegram import Dispatcher
 r.w.followups(lambda:False);Dispatcher(r.s,r.e.c,r.api,now=r.e.now).flush()
 assert not r.api.sent
 rows=r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'");assert len(rows)==1 and rows[0]['state']=='blocked'
 r.clock[0]+=86400;r.w.followups(lambda:False)
 assert len(r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'"))==1

@pytest.mark.parametrize('text',[
 'Что нужно Даше сделать?', 'Нужно Даше проверить фото?',
 'Алэн попросил Дашу проверить фото.',
 'Даша написала, что нужно проверить фото.',
 'Алэн сказал поручить Даше проверить фото.',
])
def test_questions_and_third_party_requests_never_create_tasks(r,text):
 p=proposal();p['quote']=text
 assert not create_tasks(r.w,[p],msg(r,text))
 assert len(r.w.tasks())==1 and not r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'")

@pytest.mark.parametrize('text',[
 'Поручи Даше проверить фото.', 'Нужно Даше проверить фото.',
 'Можешь попросить Дашу проверить фото?', 'Давай попросим Дашу проверить фото.',
])
def test_natural_direct_requests_remain_supported(r,text):
 p=proposal();p['quote']=text
 assert create_tasks(r.w,[p],msg(r,text))

@pytest.mark.parametrize('text',[
 'Не нужно Даше проверять фото.', 'Не надо поручать Даше фото.',
 'Почему ты сказал Даше: сделай фото?', 'Даша написала: поручите мне фото.',
 'Не хочу, чтобы Даша проверяла фото.',
])
def test_negated_or_reported_requests_cannot_create_tasks(r,text):
 p=proposal();p['quote']=text
 assert not create_tasks(r.w,[p],msg(r,text))
 assert len(r.w.tasks())==1


def test_new_assignment_does_not_stack_on_pending_private_question(r):
 r.w.private_reply('question','dasha_it','Когда сможете показать фото?',False)
 notes=create_tasks(r.w,[proposal()],msg(r))
 assert notes and 'после текущей' in notes[0]
 assert not r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'")


def test_followup_waits_after_private_dialogue_reply(r):
 r.w.private_reply('question','dasha_it','Когда сможете показать фото?',False)
 r.s.execute("UPDATE outbox SET state='sent' WHERE dedupe LIKE 'work-reply:%'")
 r.w.followups(lambda:False)
 assert not r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'")
 r.clock[0]+=86400;r.w.followups(lambda:False)
 assert len(r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'"))==1


def test_delayed_delivery_waits_from_receipt_not_queue_date(r):
 r.w.private_reply('question','dasha_it','Когда сможете показать фото?',False)
 r.s.execute("UPDATE outbox SET state='sent',created=created-86400")
 oid=r.s.rows('SELECT id FROM outbox')[0]['id']
 r.s.execute("INSERT INTO deliveries(outbox_id,at,state,detail) VALUES(?,?,'sent','123')",(oid,r.clock[0]))
 r.w.followups(lambda:False)
 assert not r.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'work-followup:%'")
