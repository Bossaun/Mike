import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Updates,Dispatcher
from goldway.model import ScriptedModel,response_schema
from test_telegram import API
from test_personal_intake import profile


@pytest.fixture
def personal(env):
    e,s,c,clock=env;profile(c,clock)
    c['live_send']=True;c['recipients_verified']=True
    return e,s,c,clock


def result(actions=None,intent='task_action',reply='Уточню детали задачи.'):
    return {'actions':actions or [],'intent':intent,'summary':'Тестовый итог','reply_text':reply}


def creation(text,owner='test_operator',deadline=None):
    return {'op':'create','title':'Подготовить список товаров','owner':owner,'owner_lines':[1] if owner else [],
            'deadline':deadline,'deadline_lines':[2] if len(text.splitlines())>1 else [],
            'next_step':'Составить список','criterion':'Список из трёх пунктов','status':'confirmed','evidence_lines':[1]}


def message(clock,text,id):
    return {'update_id':id,'message':{'message_id':id,'date':int(clock[0]),'from':{'id':77,'is_bot':False},
                                   'chat':{'id':77,'type':'private'},'text':text}}


def deliver(e,s,c,clock,api,text,id,model_result):
    e.model=ScriptedModel(model_result)
    Updates(e,api,999).handle(message(clock,text,id))
    ev=s.rows("SELECT id FROM events WHERE state='pending'")
    for row in ev:e.process(row['id'])
    Dispatcher(s,c,api,lambda:clock[0]).flush()


def test_plain_instruction_to_confirmation_result_and_restart(personal,tmp_path):
    e,s,c,clock=personal;api=API()
    text='Создай задачу: подготовить список товаров, ответственный я.\nСрок завтра.'
    deliver(e,s,c,clock,api,text,10,result([creation(text)]))
    task=e.tasks()[0]
    assert task['status']=='proposed' and task['owner']=='test_operator'
    reference=datetime.fromtimestamp(clock[0],ZoneInfo(c['timezone'])).date()
    from datetime import timedelta
    assert task['deadline']==(reference+timedelta(days=1)).isoformat()
    deliver(e,s,c,clock,api,text,11,result([creation(text)]))
    assert len(e.tasks())==1
    assert len(s.rows('SELECT * FROM outbox'))==1
    confirm='Задача №1: подтверждаю поручение.'
    deliver(e,s,c,clock,api,confirm,12,result([{'op':'update','task_id':task['id'],'status':'confirmed','evidence':confirm}]))
    assert e.tasks()[0]['status']=='confirmed'
    almost='Задача №1: почти готово, проверяю последние пункты.'
    deliver(e,s,c,clock,api,almost,13,result([],intent='conversation',reply='Сообщите конкретный результат, когда закончите.'))
    assert e.tasks()[0]['status']=='confirmed'
    reopened=Store(str(tmp_path/'state.sqlite'));next_e=Engine(reopened,c,e.model,lambda:clock[0])
    done='Задача №1: список из трёх пунктов подготовлен и сохранён в TEST.txt.'
    deliver(next_e,reopened,c,clock,api,done,14,result([{'op':'update','task_id':task['id'],'status':'completed','evidence':done}]))
    assert next_e.tasks()[0]['id']==task['id'] and next_e.tasks()[0]['status']=='completed'
    assert next_e.tasks()[0]['public_number']==1
    assert all(r['recipient']=='test_operator' and r['transport']=='bot' for r in reopened.rows("SELECT * FROM outbox WHERE state='sent'"))
    assert not any('business_connection_id' in args for method,args in api.calls if method=='sendMessage')


def test_unknown_fields_are_questions_not_facts(personal):
    e,s,c,clock=personal;text='Создай задачу: подготовить список товаров.'
    e.model=ScriptedModel(result([creation(text,owner=None)]));e.reply('new','test_operator',text,channel='bot');e.process('new')
    task=e.tasks()[0];out=s.rows('SELECT * FROM outbox')[0]
    assert task['owner'] is None and task['deadline'] is None and task['status']=='proposed'
    assert 'ответственного' in out['text'] and 'срок' in out['text']


def test_general_question_can_use_model_without_creating_task(personal):
    e,s,c,clock=personal;e.model=ScriptedModel(result(intent='conversation',reply='Вот план: сначала собрать данные, затем сверить их.'))
    e.reply('question','test_operator','Напиши план подготовки к встрече',channel='bot');e.process('question')
    assert not e.tasks()
    assert 'Вот план:' in s.rows('SELECT * FROM outbox')[0]['text']


@pytest.mark.parametrize('change',[{'intent':'conversation'},{'intent':None},{'reply_text':None}])
def test_invalid_intent_or_reply_never_mutates_tasks(personal,change):
    e,s,c,clock=personal;text='Создай задачу, ответственный я.'
    value=result([creation(text)]);value.update(change);e.model=ScriptedModel(value)
    e.reply('bad','test_operator',text,channel='bot')
    with pytest.raises(ValueError):e.process('bad')
    assert not e.tasks()


def test_personal_chat_cannot_create_without_matching_private_channel(personal):
    e,s,c,clock=personal;text='Создай задачу, ответственный я.'
    e.model=ScriptedModel(result([creation(text)]));e.reply('bad','test_operator',text,channel='group')
    with pytest.raises(ValueError):e.process('bad')
    assert not e.tasks()


def test_relative_date_uses_event_day_not_delayed_processing(personal):
    e,s,c,clock=personal;text='Создай задачу, ответственный я.\nСрок завтра.'
    before=datetime.fromtimestamp(clock[0],ZoneInfo(c['timezone'])).date()
    e.model=ScriptedModel(result([creation(text)]));e.reply('new','test_operator',text,channel='bot')
    clock[0]+=86400;e.process('new')
    from datetime import timedelta
    assert e.tasks()[0]['deadline']==(before+timedelta(days=1)).isoformat()


def test_uncertain_date_stays_unknown(personal):
    e,s,c,clock=personal;text='Создай задачу, ответственный я.\nСрок наверное завтра.'
    a=creation(text,deadline='2099-01-01');e.model=ScriptedModel(result([a]))
    e.reply('new','test_operator',text,channel='bot');e.process('new')
    assert e.tasks()[0]['deadline'] is None


def test_general_chat_does_not_postpone_existing_task(personal):
    e,s,c,clock=personal;text='Создай задачу, ответственный я.'
    e.model=ScriptedModel(result([creation(text)]));e.reply('new','test_operator',text,channel='bot');e.process('new')
    e.model=ScriptedModel(result(intent='conversation',reply='Вот текст объявления.'))
    e.reply('chat','test_operator','Напиши текст объявления',channel='bot');e.process('chat')
    assert e.tasks()[0]['last_reply'] is None


def test_schema_routes_conversation_and_actions_separately():
    schema=response_schema({'kind':'operator_chat','employees':{'test_operator':'Synthetic'}})
    assert set(schema['required'])=={'actions','summary','reply_text','intent'}
    assert schema['properties']['actions']['maxItems']==20
    assert response_schema({'kind':'conversation'})['properties']['actions']['maxItems']==0


def make_task(personal):
    e,s,c,clock=personal;text='Создай задачу: подготовить список товаров, ответственный я.\nСрок завтра.'
    e.model=ScriptedModel(result([creation(text)]));e.reply('create','test_operator',text,channel='bot');e.process('create')
    return e.tasks()[0]


def test_missing_confirmation_cannot_confirm_draft(personal):
    e,s,c,clock=personal;task=make_task(personal);text='Задача №1: расскажи про неё.'
    e.model=ScriptedModel(result([{'op':'update','task_id':task['id'],'status':'confirmed','evidence':text}]))
    e.reply('bad','test_operator',text,channel='bot')
    with pytest.raises(ValueError,match='explicit confirmation'):e.process('bad')
    assert e.tasks()[0]['status']=='proposed'


def test_bare_done_is_not_concrete_completion(personal):
    e,s,c,clock=personal;task=make_task(personal);text='Задача №1: готово'
    e.model=ScriptedModel(result([{'op':'update','task_id':task['id'],'status':'completed','evidence':text}]))
    e.reply('bad','test_operator',text,channel='bot')
    with pytest.raises(ValueError,match='concrete result'):e.process('bad')
    assert e.tasks()[0]['status']=='proposed'


def test_unknown_task_number_never_changes_existing_task(personal):
    e,s,c,clock=personal;task=make_task(personal)
    class NoModel:
        def propose(self,context):raise AssertionError('Unknown number should clarify without model')
    e.model=NoModel();e.reply('unknown','test_operator','Задача №999: готово',channel='bot');e.process('unknown')
    assert e.tasks()[0]['status']=='proposed'


def test_ambiguous_new_deadline_preserves_existing_fact(personal):
    e,s,c,clock=personal;task=make_task(personal)
    text='Задача №1: перенеси срок на пятницу.'
    e.model=ScriptedModel(result([{'op':'update','task_id':task['id'],'deadline':'2099-01-01','deadline_lines':[1],'evidence_lines':[1]}]))
    e.reply('change','test_operator',text,channel='bot');e.process('change')
    assert e.tasks()[0]['deadline']==task['deadline']
    assert 'Срок не изменён' in s.rows("SELECT text FROM outbox ORDER BY id DESC LIMIT 1")[0]['text']


def test_negated_owner_is_not_assigned_as_fact(personal):
    e,s,c,clock=personal;text='Создай задачу: подготовить список. Ответственный не я.'
    e.model=ScriptedModel(result([creation(text)]));e.reply('new','test_operator',text,channel='bot');e.process('new')
    assert e.tasks()[0]['owner'] is None


def test_completed_result_survives_unclear_deadline_update(personal):
    e,s,c,clock=personal;task=make_task(personal)
    done='Задача №1: список из трёх пунктов подготовлен в TEST.txt.'
    e.model=ScriptedModel(result([{'op':'update','task_id':task['id'],'status':'completed','evidence':done}]))
    e.reply('done','test_operator',done,channel='bot');e.process('done')
    text='Задача №1: перенеси срок на пятницу.'
    e.model=ScriptedModel(result([{'op':'update','task_id':task['id'],'deadline':'2099-01-01','deadline_lines':[1],'evidence_lines':[1]}]))
    e.reply('ambiguous','test_operator',text,channel='bot');e.process('ambiguous')
    assert e.tasks()[0]['evidence']==done and e.tasks()[0]['deadline']==task['deadline']
    assert 'Срок не изменён' in s.rows("SELECT text FROM outbox ORDER BY id DESC LIMIT 1")[0]['text']


def test_unconfigured_business_profile_cannot_open_operator_actions(env):
    e,s,c,clock=env;c['task_actions_enabled']=True;c['review_mode']=True
    text='Создай задачу, ответственный я.'
    a=creation(text,owner='boss');e.model=ScriptedModel(result([a]));e.reply('closed','boss',text,channel='business')
    with pytest.raises(ValueError):e.process('closed')
    assert not e.tasks()


def test_employee_cannot_use_operator_action_endpoint(env):
    e,s,c,clock=env;c['task_actions_enabled']=True;text='Создай задачу, ответственный я.'
    e.model=ScriptedModel(result([creation(text,owner='alen')]))
    e.reply('employee','alen',text,channel='bot')
    with pytest.raises(ValueError):e.process('employee')
    assert not e.tasks()


@pytest.mark.parametrize('text',['Задача №1: вся работа выполнена','Задача №1: всё полностью готово'])
def test_status_only_completion_requires_real_detail(personal,text):
    e,s,c,clock=personal;task=make_task(personal)
    e.model=ScriptedModel(result([{'op':'update','task_id':task['id'],'status':'completed','evidence':text}]))
    e.reply('bad','test_operator',text,channel='bot')
    with pytest.raises(ValueError,match='concrete result'):e.process('bad')
    assert e.tasks()[0]['status']=='proposed'
    assert 'Что именно сделано?' in s.rows("SELECT text FROM outbox WHERE dedupe='error:bad'")[0]['text']


def test_ambiguous_task_update_clarifies_without_guessing(personal):
    e,s,c,clock=personal;first=make_task(personal)
    text='Создай задачу: подготовить второй список, ответственный я.'
    second=creation(text);second['title']='Подготовить второй список'
    e.model=ScriptedModel(result([second]));e.reply('second','test_operator',text,channel='bot');e.process('second')
    request='Перенеси срок на завтра.'
    e.model=ScriptedModel(result([{'op':'update','task_id':first['id'],'deadline':'2099-01-01','deadline_lines':[1],'evidence_lines':[1]}]))
    e.reply('ambiguous','test_operator',request,channel='bot')
    with pytest.raises(ValueError,match='несколько задач'):e.process('ambiguous')
    assert e.tasks()[0]['deadline']==first['deadline']
    assert 'Уточните номер задачи' in s.rows("SELECT text FROM outbox WHERE dedupe='error:ambiguous'")[0]['text']


def test_instruction_with_general_chat_words_still_creates_draft(personal):
    e,s,c,clock=personal
    text='Создай задачу: общаться с поставщиком по списку товаров. Ответственный я.'
    e.model=ScriptedModel(result([creation(text)]))
    e.reply('new','test_operator',text,channel='bot');e.process('new')
    assert e.tasks()[0]['status']=='proposed'


def test_explicit_creation_with_ready_criterion_creates_literal_draft(personal):
    e,s,c,clock=personal
    text='Создай тестовую задачу „Проверка ALPHA-1431“: я сделаю контрольный список завтра. Критерий завершения — готов список из трёх пунктов: А, Б, В.'
    action=creation(text)
    action.update(title='Проверка ALPHA-1431',next_step='Составить контрольный список',
                  criterion='Готов список из трёх пунктов: А, Б, В.',deadline_lines=[1])
    class CheckModel:
        calls=0
        def propose(self,context):
            self.calls+=1
            assert context['kind']=='operator_chat' and not context['tasks']
            return result([action])
    model=CheckModel();e.model=model;api=API()
    Updates(e,api,999).handle(message(clock,text,1431));e.process('update:1431')
    assert len(e.tasks())==1
    task=e.tasks()[0]
    from datetime import timedelta
    tomorrow=datetime.fromtimestamp(clock[0],ZoneInfo(c['timezone'])).date()+timedelta(days=1)
    assert model.calls==1 and task['status']=='proposed'
    assert task['title']=='Проверка ALPHA-1431' and task['owner']=='test_operator'
    assert task['deadline']==tomorrow.isoformat() and task['evidence']==text
    assert task['criterion']==action['criterion'] and task['public_number']==1
    assert 'Уточните задачу' not in s.rows('SELECT text FROM outbox')[0]['text']
    assert not api.calls


@pytest.mark.parametrize('text,task_id',[
    ('Создай задачу №999: готов список из трёх пунктов.',None),
    ('Создай тестовую задачу: готов список из трёх пунктов.','missing'),
    ('Готов список из трёх пунктов.',None),
])
def test_creation_exception_keeps_unknown_references_and_bare_completion_closed(personal,text,task_id):
    e,s,c,clock=personal
    class NoModel:
        def propose(self,context):raise AssertionError('Ambiguous reference must not reach the model')
    e.model=NoModel();e.reply('ambiguous','test_operator',text,task_id=task_id,channel='bot');e.process('ambiguous')
    assert not e.tasks()
    assert s.rows('SELECT text FROM outbox')[0]['text'].startswith('Уточните задачу:')


def test_employee_creation_with_ready_criterion_does_not_open_operator_exception(env):
    e,s,c,clock=env
    class NoModel:
        def propose(self,context):raise AssertionError('Unverified role must not open creation exception')
    e.model=NoModel()
    e.reply('employee','alen','Создай тестовую задачу: готов список из трёх пунктов.',channel='bot');e.process('employee')
    assert not e.tasks()
    assert s.rows('SELECT text FROM outbox')[0]['text'].startswith('Уточните задачу:')


def test_creation_exception_does_not_accept_invented_evidence(personal):
    e,s,c,clock=personal;text='Создай тестовую задачу: готов список из трёх пунктов. Ответственный я.'
    action=creation(text);action.pop('evidence_lines');action['evidence']='Invented synthetic evidence'
    e.model=ScriptedModel(result([action]));e.reply('invented','test_operator',text,channel='bot')
    with pytest.raises(ValueError,match='Missing literal evidence'):e.process('invented')
    assert not e.tasks()


def test_long_private_text_is_not_mistaken_for_a_meeting(personal):
    e,s,c,clock=personal
    text='Напиши текст приглашения на встречу. '+('Нужно обсудить подготовку и сверить список. '*12)
    assert len(text)>400
    class CheckContext:
        def propose(self,context):
            assert context['kind']=='operator_chat'
            return result(intent='conversation',reply='Приглашаем вас обсудить подготовку и сверить список.')
    e.model=CheckContext()
    Updates(e,API(),999).handle(message(clock,text,101))
    assert s.rows('SELECT kind FROM events')==[{'kind':'reply'}]
    e.process('update:101')
    assert not e.tasks() and 'Приглашаем' in s.rows('SELECT text FROM outbox')[0]['text']


def test_explicit_personal_meeting_keeps_transcript_path(personal):
    e,s,c,clock=personal
    Updates(e,API(),999).handle(message(clock,'/meeting Обсудили список товаров.',102))
    assert s.rows('SELECT kind FROM events')==[{'kind':'transcript'}]


@pytest.mark.parametrize('text',['Задача №1: я не сделал список товаров.','Задача №1: список почти готов, сверяю данные.'])
def test_negative_result_cannot_close_task_and_gets_human_clarification(personal,text):
    e,s,c,clock=personal;task=make_task(personal)
    e.model=ScriptedModel(result([{'op':'update','task_id':task['id'],'status':'completed','evidence':text}]))
    e.reply('not-done','test_operator',text,channel='bot')
    with pytest.raises(ValueError,match='concrete result'):e.process('not-done')
    assert e.tasks()[0]['status']=='proposed'
    notice=s.rows("SELECT text FROM outbox WHERE dedupe='error:not-done'")[0]['text']
    assert notice.startswith('Что именно сделано?') and 'Completion requires' not in notice


def test_referenced_response_is_received_before_model_failure_even_with_general_words(personal):
    e,s,c,clock=personal;task=make_task(personal)
    clock[0]+=3600
    s.queue(e.b,'test_operator','Есть результат?',f"reminder:{task['id']}:test",clock[0]-1,task['id'])
    class FailedModel:
        def propose(self,context):raise RuntimeError('Synthetic model failure')
    e.model=FailedModel()
    e.reply('answer','test_operator','Задача №1: нужно общаться с поставщиком, жду его ответа.',channel='bot')
    with pytest.raises(RuntimeError):e.process('answer')
    assert e.tasks()[0]['last_reply']==clock[0]
    assert s.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")==[{'state':'cancelled'}]
