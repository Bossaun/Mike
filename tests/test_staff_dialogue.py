"""Synthetic staff conversation, grounded deadlines and source timestamps."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from goldway.model import ScriptedModel
from goldway.telegram import Updates, Dispatcher
from test_operator_tasks import result
from test_team_tasks import team, create, confirm, process, inbound, update


@pytest.fixture
def assigned(team):
    task=confirm(team,create(team))
    return team,task


def test_staff_general_question_returns_actual_answer_and_does_not_postpone_task(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    clock[0]+=3600
    reply='Приглашаем вас на встречу: обсудим подготовку и список товаров.'
    proposal=result(intent='conversation',reply=reply)
    proposal['summary']='Сотрудник попросил подготовить приглашение.'
    process(team,'Посоветуй, как составить приглашение на встречу.',20,proposal,sender='alen')
    assert store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']==reply
    assert engine.tasks()[0]['last_reply']==task['last_reply']


def test_staff_relative_deadline_is_resolved_without_iso_syntax(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    clock[0]+=86400
    process(team,'Задача №1: новый срок завтра.',20,
            result([update(task,deadline='2099-01-01',deadline_lines=[1])]),sender='alen')
    expected=datetime.fromtimestamp(clock[0],ZoneInfo(config['timezone'])).date()+timedelta(days=1)
    assert engine.tasks()[0]['deadline']==expected.isoformat()


def test_staff_unknown_null_deadline_preserves_existing_fact(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    process(team,'Задача №1: новый срок пока не знаю.',20,
            result([update(task,deadline=None,deadline_lines=[],set_fields=['deadline'])]),sender='alen')
    assert engine.tasks()[0]['deadline']==task['deadline']
    assert 'уточните' in store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text'].casefold()


def test_delayed_telegram_receipt_uses_original_message_date(team):
    engine,store,config,clock,api=team
    original=int(clock[0]);clock[0]+=86400
    text='Создай задачу: подготовить список. Ответственный я.\nСрок завтра.'
    action={'op':'create','title':'Подготовить список','owner':'test_operator','owner_lines':[1],
            'deadline':'2099-01-01','deadline_lines':[2],'next_step':'Составить список',
            'criterion':'Список из трёх пунктов','status':'proposed','evidence_lines':[1]}
    engine.model=ScriptedModel(result([action]))
    Updates(engine,api,900).handle({'update_id':20,'message':{'message_id':20,'date':original,
            'chat':{'id':1,'type':'private'},'from':{'id':1},'text':text}})
    engine.process('update:20')
    expected=datetime.fromtimestamp(original,ZoneInfo(config['timezone'])).date()+timedelta(days=1)
    assert engine.tasks()[0]['deadline']==expected.isoformat()


def add_task(team,owner='alen',confirm_it=True):
    engine,store,config,clock,api=team
    text='Создай задачу: сверить накладные. Ответственный '+config['employees'][owner]['name']+'.'
    action={'op':'create','title':'Сверить накладные','owner':owner,'owner_lines':[1],
            'deadline':None,'next_step':'Сверить три накладные','criterion':'Накладные сверены',
            'status':'proposed','evidence_lines':[1]}
    process(team,text,30,result([action]))
    task=engine.tasks()[-1]
    if confirm_it:
        process(team,'Задача №2: подтверждаю поручение.',31,result([update(task,status='confirmed')]))
    return task


@pytest.mark.parametrize('channel',['bot','business'])
def test_staff_model_context_contains_owned_tasks_and_explicit_role_only(assigned,channel):
    team,task=assigned;engine,store,config,clock,api=team
    add_task(team,owner='frost')
    class Observed:
        def propose(self,context):
            assert context['kind']=='operator_chat' and context['sender_role']=='employee'
            assert context['can_create_tasks'] is False
            assert [t['id'] for t in context['tasks']]==[task['id']]
            assert all(t['owner']=='alen' for t in context['tasks'])
            return result(intent='conversation',reply='Вот предложение для текста приглашения.')
    engine.model=Observed()
    engine.process(inbound(team,'Посоветуй текст приглашения.',20,sender='alen',channel=channel))


def test_staff_general_context_does_not_reveal_unconfirmed_drafts(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    draft=add_task(team,confirm_it=False)
    class Observed:
        def propose(self,context):
            assert draft['id'] not in {t['id'] for t in context['tasks']}
            return result(intent='conversation',reply='Обсудим текущую работу.')
    engine.model=Observed();engine.process(inbound(team,'Расскажи про подготовку встречи.',20,sender='alen'))


@pytest.mark.parametrize('text',['Срок завтра?','Готово ли приглашение на встречу?',
                               'Задача №1: посоветуй, как составить приглашение.',
                               'Задача №1: напиши готовый текст приглашения.'])
def test_question_or_drafting_request_does_not_cancel_reminder(assigned,text):
    team,task=assigned;engine,store,config,clock,api=team;clock[0]+=3600
    store.queue(engine.b,'alen','Какой результат?','reminder:'+task['id']+':test',clock[0]-1,task['id'])
    process(team,text,20,result(intent='conversation',reply='Обсудим вопрос.'),sender='alen',flush=False)
    assert engine.tasks()[0]['last_reply']==task['last_reply']
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")==[{'state':'pending'}]


@pytest.mark.parametrize('text',['Жду документы от поставщика.','Задача №1: жду документы от поставщика.'])
def test_clear_employee_progress_receipt_survives_model_failure(assigned,text):
    team,task=assigned;engine,store,config,clock,api=team;clock[0]+=3600
    store.queue(engine.b,'alen','Какой результат?','reminder:'+task['id']+':test',clock[0]-1,task['id'])
    class Failed:
        def propose(self,context):raise RuntimeError('Synthetic provider failure')
    engine.model=Failed()
    event=inbound(team,text,20,sender='alen')
    assert engine.tasks()[0]['last_reply']==clock[0]
    with pytest.raises(RuntimeError):engine.process(event)
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")==[{'state':'cancelled'}]


@pytest.mark.parametrize('text',[
    'Задача №1: поставщик задерживает документы, список пока не могу сверить.',
    'Задача №1: список подготовлен и сохранён в TEST.txt.',
])
def test_numbered_subject_first_report_receipt_survives_model_failure(assigned,text):
    team,task=assigned;engine,store,config,clock,api=team;clock[0]+=3600
    store.queue(engine.b,'alen','Какой результат?','reminder:'+task['id']+':test',clock[0]-1,task['id'])
    class Failed:
        def propose(self,context):raise RuntimeError('Synthetic provider failure')
    engine.model=Failed()
    event=inbound(team,text,20,sender='alen')
    with pytest.raises(RuntimeError):engine.process(event)
    assert engine.tasks()[0]['last_reply']==clock[0]
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")==[{'state':'cancelled'}]


def test_exact_title_linked_progress_with_multiple_tasks_survives_model_failure(assigned):
    team,task=assigned;engine,store,config,clock,api=team;second=add_task(team)
    clock[0]+=3600
    store.queue(engine.b,'alen','Какой результат?','reminder:'+task['id']+':test',clock[0]-1,task['id'])
    class Failed:
        def propose(self,context):raise RuntimeError('Synthetic provider failure')
    engine.model=Failed()
    text='По задаче «'+task['title']+'»: жду документы от поставщика.'
    event=inbound(team,text,20,sender='alen')
    with pytest.raises(RuntimeError):engine.process(event)
    by_id={t['id']:t for t in engine.tasks()}
    assert by_id[task['id']]['last_reply']==clock[0] and by_id[second['id']]['last_reply'] is None


def test_unlinked_progress_with_multiple_tasks_does_not_guess_receipt(assigned):
    team,task=assigned;engine,store,config,clock,api=team;add_task(team)
    clock[0]+=3600
    event=inbound(team,'Жду документы.',20,sender='alen')
    assert all(t['last_reply'] is None for t in engine.tasks())


def test_completed_staff_result_notifies_manager_with_escaped_source(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    text='Задача №1: список подготовлен и сохранён в TEST.txt, отметка <sample>.'
    process(team,text,20,result([update(task,status='completed')]),sender='alen')
    notice=store.rows("SELECT * FROM outbox WHERE dedupe LIKE 'attention:%'")[0]
    assert notice['recipient']=='test_operator' and notice['state']=='sent' and notice['parse_mode']=='HTML'
    assert 'Выполнено:' in notice['text'] and '&lt;sample&gt;' in notice['text']
    assert '<sample>' not in notice['text'] and engine.tasks()[0]['status']=='completed'


def test_reopened_completed_task_notifies_manager_with_new_obstacle(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    completed='Задача №1: список подготовлен и сохранён в TEST.txt.'
    process(team,completed,20,result([update(task,status='completed')]),sender='alen',flush=False)
    obstacle='Задача №1: итоговый файл повреждён, нужна помощь с восстановлением.'
    process(team,obstacle,21,result([update(engine.tasks()[0],status='blocked')]),sender='alen',flush=False)
    assert engine.tasks()[0]['status']=='blocked' and engine.tasks()[0]['evidence']==obstacle
    alerts=store.rows("SELECT state,text FROM outbox WHERE dedupe LIKE 'attention:%' ORDER BY id")
    assert [r['state'] for r in alerts]==['cancelled','pending']
    assert 'итоговый файл повреждён' in alerts[1]['text'] and 'TEST.txt' not in alerts[1]['text']


def test_blocker_notifies_manager_and_date_update_preserves_obstacle(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    text='Задача №1: поставщик задерживает документы, список пока не могу сверить.'
    process(team,text,20,result([update(task,status='blocked',next_step='Получить документы поставщика')]),sender='alen',flush=False)
    blocked=engine.tasks()[0]
    assert 'Нужно решение:' in store.rows("SELECT text FROM outbox WHERE dedupe LIKE 'attention:%'")[0]['text']
    process(team,'Задача №1: новый срок 20.10.2026.',21,
            result([update(blocked,deadline='2026-10-20',deadline_lines=[1])]),sender='alen',flush=False)
    assert engine.tasks()[0]['evidence']==text
    notices=store.rows("SELECT state,text FROM outbox WHERE dedupe LIKE 'attention:%' ORDER BY id")
    assert [r['state'] for r in notices]==['cancelled','pending']
    assert 'поставщик задерживает' in notices[1]['text'] and '2026-10-20' in notices[1]['text']


def test_new_blocker_evidence_replaces_unsent_attention(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    process(team,'Задача №1: жду документы, нужна помощь.',20,
            result([update(task,status='blocked')]),sender='alen',flush=False)
    process(team,'Задача №1: теперь поставщик сообщил об ошибке в накладной.',21,
            result([update(engine.tasks()[0],status='blocked')]),sender='alen',flush=False)
    assert [r['state'] for r in store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'attention:%' ORDER BY id")]==['cancelled','pending']


def test_resolved_blocker_cancels_unsent_manager_alert(assigned):
    team,task=assigned;engine,store,config,clock,api=team
    process(team,'Задача №1: жду документы, нужна помощь.',20,
            result([update(task,status='blocked')]),sender='alen',flush=False)
    process(team,'Задача №1: документы получил, продолжаю сверку.',21,
            result([update(engine.tasks()[0],status='in_progress')]),sender='alen',flush=False)
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'attention:%'")==[{'state':'cancelled'}]


@pytest.mark.parametrize('state',['sending','unknown','sent'])
def test_attention_with_prior_delivery_outcome_is_preserved(assigned,state):
    team,task=assigned;engine,store,config,clock,api=team
    process(team,'Задача №1: жду документы, нужна помощь.',20,
            result([update(task,status='blocked')]),sender='alen',flush=False)
    store.execute("UPDATE outbox SET state=? WHERE dedupe LIKE 'attention:%'",(state,))
    process(team,'Задача №1: документы получил, продолжаю сверку.',21,
            result([update(engine.tasks()[0],status='in_progress')]),sender='alen',flush=False)
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'attention:%'")==[{'state':state}]


def test_general_question_without_visible_tasks_can_mention_ready_text(team):
    process(team,'Напиши текст приглашения, когда будет готово предложение.',20,
            result(intent='conversation',reply='Вот текст приглашения.'),sender='alen')
    assert team[1].rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']=='Вот текст приглашения.'
    assert not team[0].tasks()


@pytest.mark.parametrize('text',['Задача №1: готово','Задача №1: почти готово, сверяю данные.'])
def test_employee_cannot_close_task_with_a_status_only_or_almost_done(assigned,text):
    team,task=assigned
    with pytest.raises(ValueError,match='concrete result'):
        process(team,text,20,result([update(task,status='completed')]),sender='alen',flush=False)
    assert team[0].tasks()[0]['status']=='confirmed'


def test_employee_unknown_owner_request_preserves_assignee_and_clarifies(assigned):
    team,task=assigned
    process(team,'Задача №1: новым ответственным предлагают Ивана.',20,
            result([update(task,owner=None,owner_lines=[],set_fields=['owner'])]),sender='alen')
    assert team[0].tasks()[0]['owner']=='alen'
    assert 'Ответственный не изменён' in team[1].rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']


def test_masked_literal_deadline_evidence_cannot_set_an_unrequested_date(assigned):
    team,task=assigned
    process(team,'Задача №1: следующий шаг сверить три пункта. Срок завтра.',20,
            result([update(task,next_step='Сверить три пункта',set_fields=['next_step'],deadline_evidence='завтра')]),sender='alen')
    assert team[0].tasks()[0]['deadline']==task['deadline']


def test_explicit_deadline_clear_with_citation_is_supported(assigned):
    team,task=assigned
    process(team,'Задача №1: сними прежний срок.',20,
            result([update(task,deadline=None,deadline_lines=[1],set_fields=['deadline'])]),sender='alen')
    assert team[0].tasks()[0]['deadline'] is None
