"""Closed-team task delegation using synthetic identities and external APIs."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from goldway.engine import Engine
from goldway.model import ScriptedModel
from goldway.railway import prepare_team_config
from goldway.store import Store
from goldway.telegram import DeliveryError, Dispatcher, Updates
from test_operator_tasks import result
from test_team_profile import raw_profile


class API:
    def __init__(self):
        self.calls=[];self.message_id=100;self.unknown=False

    def call(self,method,**kwargs):
        self.calls.append((method,kwargs))
        if method=='getBusinessConnection':
            return {'user':{'id':99},'is_enabled':True,'rights':{'can_reply':True}}
        assert method=='sendMessage'
        if self.unknown:raise DeliveryError('unknown','Synthetic network uncertainty')
        self.message_id+=1
        return {'message_id':self.message_id}


@pytest.fixture
def team(tmp_path):
    clock=[1791979200.0]
    raw=raw_profile()
    names={'test_operator':'Оператор','frost':'Фрост','alen':'Алэн','nastya':'Настя','dasha_it':'Даша'}
    for key,name in names.items():raw['employees'][key]['name']=name
    path=prepare_team_config(json.dumps(raw),tmp_path/'config',now=lambda:clock[0]-60)
    config=json.loads(path.read_text());config['live_send']=True
    store=Store(str(tmp_path/'team.sqlite'))
    engine=Engine(store,config,ScriptedModel({}),lambda:clock[0]);api=API()
    for key,person in config['employees'].items():
        store.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?)',
                      (engine.b,key,str(person['telegram_user_id'])))
    return engine,store,config,clock,api


def inbound(team,text,id,sender='test_operator',channel='bot'):
    engine,store,config,clock,api=team
    uid=config['employees'][sender]['telegram_user_id']
    message={'message_id':id,'date':int(clock[0]),'chat':{'id':uid,'type':'private'},
             'from':{'id':uid,'is_bot':False},'text':text}
    if channel=='business':message['business_connection_id']='synthetic-connection'
    Updates(engine,api,900).handle({'update_id':id,'business_message' if channel=='business' else 'message':message})
    return 'update:'+str(id)


def process(team,text,id,proposal,sender='test_operator',channel='bot',flush=True):
    engine,store,config,clock,api=team
    engine.model=ScriptedModel(proposal)
    event=inbound(team,text,id,sender,channel)
    engine.process(event)
    if flush:Dispatcher(store,config,api,lambda:clock[0]).flush()
    return event


def create(team,channel='bot'):
    text='Создай задачу: подготовить список товаров. Ответственный Алэн.\nСрок завтра.'
    action={'op':'create','title':'Подготовить список товаров','owner':'alen','owner_lines':[1],
            'deadline':'2099-01-01','deadline_lines':[2],'next_step':'Составить список',
            'criterion':'Список из трёх пунктов','status':'confirmed','evidence_lines':[1]}
    process(team,text,10,result([action]),channel=channel)
    return team[0].tasks()[0]


def update(task,**fields):
    return dict(op='update',task_id=task['id'],evidence_lines=[1],**fields)


def confirm(team,task,flush=True):
    process(team,'Задача №1: подтверждаю поручение.',11,
            result([update(task,status='confirmed')]),flush=flush)
    return team[0].tasks()[0]


@pytest.mark.parametrize('channel',['bot','business'])
def test_admin_draft_confirmation_delivery_employee_result(team,channel):
    engine,store,config,clock,api=team
    task=create(team,channel)
    assert task['status']=='proposed' and task['owner']=='alen'
    expected=datetime.fromtimestamp(clock[0],ZoneInfo(config['timezone'])).date()+timedelta(days=1)
    assert task['deadline']==expected.isoformat()
    assert {r['recipient'] for r in store.rows('SELECT recipient FROM outbox')}=={'test_operator'}
    task=confirm(team,task)
    assigned=store.rows("SELECT * FROM outbox WHERE dedupe LIKE 'assignment:%'")
    assert len(assigned)==1 and assigned[0]['state']=='sent' and assigned[0]['recipient']=='alen'
    assert '👤 Вы' in assigned[0]['text'] and assigned[0]['preferred_transport'] is None
    assert task['status']=='confirmed'
    text='Задача №1: список из трёх пунктов подготовлен и сохранён в TEST.txt.'
    process(team,text,12,result([update(task,status='completed')],reply='Спасибо, результат зафиксирован.'),sender='alen')
    assert engine.tasks()[0]['status']=='completed'
    assert 'Выполнено:' in store.rows('SELECT text FROM outbox ORDER BY id DESC LIMIT 1')[0]['text']
    assert store.rows('SELECT recipient FROM outbox ORDER BY id DESC LIMIT 1')[0]['recipient']=='test_operator'
    assert all(r['recipient'] in ('test_operator','alen') for r in store.rows('SELECT recipient FROM outbox'))


def test_assignment_uses_fresh_business_route_then_own_bot_after_window(team):
    engine,store,config,clock,api=team;task=create(team)
    store.execute("UPDATE chats SET business_chat=bot_chat,connection=?,last_inbound=? WHERE recipient='alen'",
                  ('synthetic-connection',clock[0]))
    task=confirm(team,task)
    assert store.rows("SELECT transport FROM outbox WHERE dedupe LIKE 'assignment:%'")==[{'transport':'business'}]
    clock[0]+=86400
    process(team,'Задача №1: следующий шаг — сверить три пункта.',12,
            result([update(task,next_step='Сверить три пункта')]))
    routes=store.rows("SELECT transport FROM outbox WHERE dedupe LIKE 'assignment:%' ORDER BY id")
    assert routes==[{'transport':'business'},{'transport':'bot'}]


def test_unknown_assignment_is_not_retried_or_switched_to_bot(team):
    engine,store,config,clock,api=team;task=create(team)
    store.execute("UPDATE chats SET business_chat=bot_chat,connection=?,last_inbound=? WHERE recipient='alen'",
                  ('synthetic-connection',clock[0]))
    confirm(team,task,flush=False);api.unknown=True
    dispatch=Dispatcher(store,config,api,lambda:clock[0]);dispatch.flush()
    sends=len([call for call in api.calls if call[0]=='sendMessage'])
    clock[0]+=86400;dispatch.flush()
    assert len([call for call in api.calls if call[0]=='sendMessage'])==sends
    row=store.rows("SELECT state,transport,attempts FROM outbox WHERE dedupe LIKE 'assignment:%'")[0]
    assert row=={'state':'unknown','transport':'business','attempts':1}


def test_repeated_confirmation_does_not_repeat_employee_assignment(team):
    engine,store,config,clock,api=team;task=confirm(team,create(team))
    process(team,'Задача №1: подтверждаю поручение.',12,result([update(task,status='confirmed')]))
    assert len(store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'"))==1


def test_reassign_cancels_unsent_old_assignment_and_old_reminder(team):
    engine,store,config,clock,api=team;task=confirm(team,create(team),flush=False)
    store.queue(engine.b,'alen','Старое напоминание','reminder:'+task['id']+':test',clock[0],task['id'])
    process(team,'Задача №1: ответственный теперь Фрост.',12,
            result([update(task,owner='frost',owner_lines=[1])]),flush=False)
    assignments=store.rows("SELECT recipient,state FROM outbox WHERE dedupe LIKE 'assignment:%' ORDER BY id")
    assert assignments==[{'recipient':'alen','state':'cancelled'},{'recipient':'frost','state':'pending'}]
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")==[{'state':'cancelled'}]
    assert engine.tasks()[0]['owner']=='frost'


@pytest.mark.parametrize('state',['pending','retry','blocked'])
def test_updated_instruction_replaces_undelivered_assignment(team,state):
    engine,store,config,clock,api=team;task=confirm(team,create(team),flush=False)
    store.execute("UPDATE outbox SET state=? WHERE dedupe LIKE 'assignment:%'",(state,))
    process(team,'Задача №1: следующий шаг — сверить три пункта.',12,
            result([update(task,next_step='Сверить три пункта')]),flush=False)
    rows=store.rows("SELECT state,text FROM outbox WHERE dedupe LIKE 'assignment:%' ORDER BY id")
    assert [r['state'] for r in rows]==['cancelled','pending']
    assert 'Сверить три пункта' in rows[1]['text']


@pytest.mark.parametrize('state',['sending','unknown','sent'])
def test_new_revision_preserves_delivery_with_existing_or_uncertain_outcome(team,state):
    engine,store,config,clock,api=team;task=confirm(team,create(team),flush=False)
    store.execute("UPDATE outbox SET state=? WHERE dedupe LIKE 'assignment:%'",(state,))
    process(team,'Задача №1: следующий шаг — сверить три пункта.',12,
            result([update(task,next_step='Сверить три пункта')]),flush=False)
    assert [r['state'] for r in store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'assignment:%' ORDER BY id")]==[state,'pending']


def test_admin_completion_cancels_unsent_assignment_and_reminder(team):
    engine,store,config,clock,api=team;task=confirm(team,create(team),flush=False)
    store.queue(engine.b,'alen','Старое напоминание','reminder:'+task['id']+':test',clock[0],task['id'])
    process(team,'Задача №1: список из трёх пунктов сохранён в TEST.txt.',12,
            result([update(task,status='completed')]),flush=False)
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'assignment:%'")==[{'state':'cancelled'}]
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")==[{'state':'cancelled'}]


def test_unresolved_deadline_does_not_send_redundant_employee_instruction(team):
    engine,store,config,clock,api=team;task=confirm(team,create(team))
    process(team,'Задача №1: перенеси срок на пятницу.',12,
            result([update(task,deadline='2099-01-01',deadline_lines=[1])]))
    assert engine.tasks()[0]['deadline']==task['deadline']
    assert len(store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'"))==1


@pytest.mark.parametrize('operation',['create','confirm','reassign','foreign_update'])
def test_employee_cannot_gain_administrator_permissions(team,operation):
    engine,store,config,clock,api=team;task=create(team)
    if operation=='reassign':task=confirm(team,task)
    original_status=task['status']
    assignments=len(store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'"))
    text={'create':'Создай задачу: подготовить второй список. Ответственный Алэн.',
          'confirm':'Задача №1: подтверждаю поручение.',
          'reassign':'Задача №1: ответственный теперь Фрост.',
          'foreign_update':'Задача №1: следующий шаг — сверить три пункта.'}[operation]
    if operation=='create':
        action={'op':'create','title':'Второй список','owner':'alen','owner_lines':[1],
                'deadline':None,'next_step':'Составить список','criterion':'Список','status':'proposed','evidence_lines':[1]}
    else:action=update(task,**({'status':'confirmed'} if operation=='confirm' else
                             {'owner':'frost','owner_lines':[1]} if operation=='reassign' else
                             {'next_step':'Сверить три пункта'}))
    sender='frost' if operation=='foreign_update' else 'alen'
    if operation=='foreign_update':
        class ForbiddenModel:
            def propose(self,context):raise AssertionError('A foreign task number must clarify without exposing context')
        engine.model=ForbiddenModel()
        engine.process(inbound(team,text,12,sender=sender))
        assert 'Уточните задачу' in store.rows("SELECT text FROM outbox WHERE dedupe='clarify:update:12'")[0]['text']
    else:
        with pytest.raises(ValueError):process(team,text,12,result([action]),sender=sender,flush=False)
    assert len(engine.tasks())==1 and engine.tasks()[0]['status']==original_status
    assert engine.tasks()[0]['owner']=='alen'
    assert len(store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'"))==assignments


@pytest.mark.parametrize('mutation',['unverified','extra_admin','mixed','group','extra_recipient'])
def test_invalid_team_profile_does_not_open_natural_creation(team,mutation):
    engine,store,config,clock,api=team
    if mutation=='unverified':config['team_roster_verified']=False
    elif mutation=='extra_admin':config['admins'].append('frost')
    elif mutation=='mixed':config['personal_intake']={'recipient':'test_operator','started_at':1}
    elif mutation=='group':config['allowed_group_ids']=[-123]
    else:config['allowed_live_recipients'].append('outsider')
    assert not engine._operator_turn({'sender':'test_operator','channel':'bot'},'reply')


def test_business_account_own_outbound_is_still_rejected_before_event(team):
    engine,store,config,clock,api=team
    update={'update_id':999,'business_message':{'message_id':999,'date':int(clock[0]),
            'chat':{'id':3,'type':'private'},'from':{'id':99},'text':'Создай задачу',
            'business_connection_id':'synthetic-connection'}}
    Updates(engine,api,900).handle(update)
    assert not store.rows('SELECT id FROM events') and not api.calls


def test_missing_employee_route_blocks_assignment_but_admin_receipt_is_delivered(team):
    engine,store,config,clock,api=team;task=create(team)
    store.execute("DELETE FROM chats WHERE recipient='alen'")
    confirm(team,task)
    assert store.rows("SELECT state FROM outbox WHERE dedupe LIKE 'assignment:%'")==[{'state':'blocked'}]
    assert store.rows("SELECT state FROM outbox WHERE recipient='test_operator' ORDER BY id DESC LIMIT 1")==[{'state':'sent'}]


def test_dry_team_profile_queues_but_does_not_send(team):
    engine,store,config,clock,api=team;config['live_send']=False
    confirm(team,create(team))
    assert len(store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'"))==1
    assert not any(method=='sendMessage' for method,_ in api.calls)


@pytest.mark.parametrize('phrase',['не подтверждаю поручение','не утверждаю поручение','не беру задачу'])
def test_negated_confirmation_cannot_delegate_a_draft(team,phrase):
    engine,store,config,clock,api=team;task=create(team)
    with pytest.raises(ValueError,match='explicit confirmation'):
        process(team,'Задача №1: '+phrase+'.',12,result([update(task,status='confirmed')]),flush=False)
    assert engine.tasks()[0]['status']=='proposed'
    assert not store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")
