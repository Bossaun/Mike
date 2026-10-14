import copy
import pytest


def test_model_failure_keeps_received_answer_and_stops_reminder(env,seed):
    e,s,c,clock=env;clock[0]+=86401;e.tick()
    class Unavailable:
        def propose(self,context):raise ValueError('Model budget exhausted')
    e.model=Unavailable();e.reply('r','alen','Жду документы от поставщика',seed['id'])
    received=clock[0]
    with pytest.raises(ValueError):e.process('r')
    assert e.tasks()[0]['last_reply']==received
    assert e.tasks()[0]['status']=='confirmed'
    assert s.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")[0]['state']=='cancelled'
    clock[0]+=3600;e.tick()
    assert len(s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%'"))==1


def test_invalid_completion_still_counts_actual_response(env,seed):
    e,s,c,clock=env;clock[0]+=86401
    text='Задача №1: почти готово, осталось проверить список'
    e.model.result={'actions':[{'op':'update','task_id':seed['id'],'status':'completed','evidence':text}]}
    e.reply('r','alen',text)
    with pytest.raises(ValueError):e.process('r')
    assert e.tasks()[0]['last_reply']==clock[0]
    assert e.tasks()[0]['status']=='confirmed'
    e.tick();assert not s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'reminder:%'")


def test_processing_delay_and_replay_do_not_extend_receipt_time(env,seed):
    e,s,c,clock=env;clock[0]+=10;received=clock[0]
    e.reply('r','alen','Жду документы',seed['id'])
    clock[0]+=1000;e.model.result={'actions':[],'summary':'Ждём документы'};e.process('r')
    assert e.tasks()[0]['last_reply']==received
    assert not e.reply('r','alen','Жду документы',seed['id'])
    assert e.tasks()[0]['last_reply']==received


@pytest.mark.parametrize('case',['boss','nastya','general','unknown_reference','wrong_task'])
def test_unrelated_message_does_not_postpone_staff_task(env,seed,case):
    e,s,c,clock=env;clock[0]+=10
    sender=case if case in ('boss','nastya') else 'alen'
    text='Как дела?' if case=='general' else ('Задача №999: жду документы' if case=='unknown_reference' else 'Жду документы')
    task=seed['id'] if case in ('boss','nastya') else ('nonexistent' if case=='wrong_task' else None)
    e.reply('r',sender,text,task)
    e.model.result={'actions':[],'summary':'Уточнение','reply_text':'Какой рабочий вопрос обсудим?'};e.process('r')
    assert e.tasks()[0]['last_reply'] is None


def test_ambiguous_reply_does_not_cancel_all_reminders(env,seed,action):
    e,s,c,clock=env;second=copy.deepcopy(action);second['title']='Другая задача'
    e.model.result={'actions':[second]};e.process(e.transcript(second['evidence']+' другая задача'))
    clock[0]+=86401;e.tick();e.reply('r','alen','Жду документы')
    assert all(t['last_reply'] is None for t in e.tasks())
    assert all(r['state']=='pending' for r in s.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'"))


def test_late_processing_does_not_cancel_newly_due_reminder(env,seed):
    e,s,c,clock=env;clock[0]+=10;e.reply('r','alen','Жду документы',seed['id'])
    received=clock[0];clock[0]+=86401;e.tick()
    e.model.result={'actions':[],'summary':'Ждём'};e.process('r')
    assert e.tasks()[0]['last_reply']==received
    assert s.rows("SELECT state FROM outbox WHERE dedupe LIKE 'reminder:%'")[0]['state']=='pending'
