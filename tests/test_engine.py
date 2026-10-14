import copy
import pytest
from goldway.store import Store
from goldway.engine import Engine

def test_repeated_meeting_and_event_no_duplicates(env,action):
    e,s,c,clock=env; e.model.result={'actions':[action]}
    first=e.transcript(action['evidence']); e.process(first)
    second=e.transcript('  '+action['evidence'].upper()+'  '); e.process(second)
    assert first==second
    assert len(e.tasks())==1
    assert len(s.rows('SELECT * FROM outbox'))==1

def test_same_task_in_new_meeting_links_source(env,seed,action):
    e,s,*_=env
    event=e.transcript(action['evidence']+' Дополнительный контекст')
    e.process(event)
    assert len(e.tasks())==1
    assert len(s.rows('SELECT * FROM task_sources'))==2

def test_employee_completion_and_source(env,seed):
    e,s,*_=env
    text='Список завершён: накладные 18, 19 и 20 внесены в файл https://example.org/result'
    e.model.result={'actions':[{'op':'update','task_id':seed['id'],'status':'completed','evidence':text}]}
    e.reply('reply:1','alen',text,seed['id']); e.process('reply:1')
    t=e.tasks()[0]
    assert t['status']=='completed' and t['last_reply']
    assert len(s.rows('SELECT * FROM task_sources'))==2
    assert 'Выполнено' in e.summary()

@pytest.mark.parametrize('text',['почти готово, сегодня закончу','Список не готов, жду документы','almost done with the shipment list'])
def test_almost_done_cannot_complete(env,seed,text):
    e,*_=env
    e.model.result={'actions':[{'op':'update','task_id':seed['id'],'status':'completed','evidence':text}]}
    e.reply('r','alen',text,seed['id'])
    with pytest.raises(ValueError): e.process('r')
    assert e.tasks()[0]['status']=='confirmed'

def test_missing_owner_and_deadline_are_questions(env,action):
    e,s,c,*_=env
    for key in ('owner','owner_evidence','deadline','deadline_evidence'): action.pop(key)
    e.model.result={'actions':[action]}; id=e.transcript(action['evidence']); e.process(id)
    t=e.tasks()[0]; out=s.rows('SELECT * FROM outbox')[0]
    assert t['owner'] is None and t['deadline'] is None
    assert out['recipient']=='boss' and 'Кто отвечает?' in out['text'] and 'Какой срок?' in out['text']

@pytest.mark.parametrize('change',[{'owner':'ghost'},{'deadline':'2026-02-30'},{'deadline':'2026-10-25'}, {'evidence':'invented'}, {'op':'delete'}, {'extra':'bad'}])
def test_invalid_model_action_is_atomic(env,action,change):
    e,s,*_=env
    bad=copy.deepcopy(action); bad.update(change)
    e.model.result={'actions':[action,bad]}; id=e.transcript(action['evidence'])
    with pytest.raises((ValueError,TypeError)): e.process(id)
    assert not e.tasks() and not s.rows('SELECT * FROM outbox')
    assert s.rows('SELECT * FROM events')[0]['state']=='failed'

def test_other_employee_cannot_modify(env,seed):
    e,*_=env
    e.model.result={'actions':[{'op':'update','task_id':seed['id'],'status':'completed','evidence':'Завершил список накладных'}]}
    e.reply('r','nastya','Завершил список накладных',seed['id']); e.process('r')
    assert e.tasks()[0]['status']=='confirmed'

def test_unknown_sender_ignored(env):
    e,s,*_=env
    assert not e.reply('r','outsider','some text')
    assert not s.rows('SELECT * FROM events')

def test_proposal_requires_admin(env,action):
    e,s,*_=env; action['status']='proposed'; e.model.result={'actions':[action]}
    e.process(e.transcript(action['evidence']))
    t=e.tasks()[0]
    assert s.rows('SELECT * FROM outbox')[0]['recipient']=='boss'
    text='Подтверждаю поручение подготовить список'
    e.model.result={'actions':[{'op':'update','task_id':t['id'],'status':'confirmed','evidence':text}]}
    e.reply('r','alen',text,t['id'])
    with pytest.raises(ValueError): e.process('r')
    e.reply('admin','boss',text,t['id']); e.process('admin')
    assert e.tasks()[0]['status']=='confirmed'

def test_recent_reply_postpones_and_cancels_reminder(env,seed):
    e,s,c,clock=env; clock[0]+=86401; e.tick()
    assert len(s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%'"))==1
    e.model.result={'actions':[],'summary':'Какой следующий шаг?'}
    e.reply('r','alen','Жду накладные от поставщика',seed['id']); e.process('r')
    assert s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%'")[0]['state']=='cancelled'
    clock[0]+=3600; e.tick()
    assert len(s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%'"))==1
    clock[0]+=86400; e.tick(); e.tick()
    assert len(s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%'"))==2

def test_restart_preserves_state(env,seed,tmp_path):
    e,s,c,clock=env
    other=Store(str(tmp_path/'state.sqlite'))
    restored=Engine(other,c,e.model,lambda:clock[0])
    assert restored.tasks()[0]['id']==seed['id']
    assert other.rows('SELECT * FROM outbox')
    restored.process(other.rows('SELECT * FROM events')[0]['id'])
    assert len(restored.tasks())==1

def test_ambiguous_free_reply_requests_reference(env,seed,action):
    e,s,*_=env
    action['title']='Вторая задача'; e.model.result={'actions':[action]}; e.process(e.transcript(action['evidence']+' Вторая задача'))
    e.reply('r','alen','Всё готово'); e.process('r')
    assert all(t['status']=='confirmed' for t in e.tasks())
    assert 'номер' in s.rows("SELECT * FROM outbox WHERE dedupe='clarify:r'")[0]['text']
