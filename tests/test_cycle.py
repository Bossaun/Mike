"""Functional cycle through actual handlers and durable queues; remote APIs are simulated."""
from goldway.store import Store
from goldway.engine import Engine
from goldway.model import ScriptedModel
from goldway.telegram import Updates, Dispatcher
from test_telegram import API
from test_sheets import Sheets, Worksheet, Workbook

def test_meeting_to_reply_result_restart_summary(env,action,tmp_path):
    e,s,c,clock=env; api=API(); u=Updates(e,api,999)
    e.model.result={'actions':[action],'summary':'Создано поручение'}
    u.handle({'update_id':100,'message':{'from':{'id':1},'chat':{'id':11,'type':'private'},'date':int(clock[0]),'message_id':10,'text':action['evidence']}})
    ev=s.rows("SELECT * FROM events WHERE state='pending'")[0]['id']; e.process(ev)
    # Repeated upload of the same transcript does not create task/message duplicates.
    u.handle({'update_id':101,'message':{'from':{'id':1},'chat':{'id':11,'type':'private'},'date':int(clock[0]),'message_id':11,'text':action['evidence']}})
    e.process(ev)
    assert len(e.tasks())==1 and len(s.rows('SELECT * FROM outbox'))==1
    u.handle({'update_id':102,'message':{'from':{'id':2},'chat':{'id':22,'type':'private'},'date':int(clock[0]),'message_id':12,'text':'/start'}})
    d=Dispatcher(s,c,api,lambda:clock[0]); d.flush()
    sent=s.rows("SELECT * FROM outbox WHERE state='sent'")[0]
    assert sent['destination_chat']=='22'
    # Re-open SQLite as after a process restart, then consume a natural-language response.
    restored=Store(str(tmp_path/'state.sqlite')); result='Завершил список: накладные 18, 19 и 20 внесены в итоговый файл.'
    model=ScriptedModel({'actions':[{'op':'update','task_id':e.tasks()[0]['id'],'status':'completed','evidence':result}],'summary':'Список завершён'})
    next_e=Engine(restored,c,model,lambda:clock[0]); next_u=Updates(next_e,api,999)
    next_u.handle({'update_id':103,'message':{'from':{'id':2},'chat':{'id':22,'type':'private'},'date':int(clock[0]),'message_id':13,'text':result,'reply_to_message':{'message_id':int(sent['message_id'])}}})
    next_e.process('update:103'); next_e.queue_summary('verified'); Dispatcher(restored,c,api,lambda:clock[0]).flush()
    assert next_e.tasks()[0]['status']=='completed'
    assert any('Выполнено:' in args.get('text','') and str(args.get('chat_id'))=='11' for method,args in api.calls if method=='sendMessage')
    ws=Worksheet(); Sheets(next_e,Workbook({'Задачи_GOLDWAY':ws})).sync()
    assert ws.rows[1][6]=='completed' and result in ws.rows[1]
    assert not restored.rows("SELECT * FROM outbox WHERE state='pending'")

def test_reply_to_same_message_id_in_different_chat_not_associated(env,seed):
    e,s,c,clock=env; api=API(); u=Updates(e,api,999)
    s.execute("UPDATE outbox SET state='sent',message_id='5',transport='bot',destination_chat='999'")
    u.handle({'update_id':1,'message':{'from':{'id':2},'chat':{'id':22,'type':'private'},'date':int(clock[0]),'message_id':10,'text':'Есть результат','reply_to_message':{'message_id':5}}})
    import json
    assert json.loads(s.rows("SELECT * FROM events WHERE id='update:1'")[0]['payload'])['task_id'] is None
