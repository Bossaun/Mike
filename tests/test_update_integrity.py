import json
from goldway.telegram import Updates
from test_telegram import API

def msg(clock,text):
    return {'from':{'id':2},'business_connection_id':'conn','chat':{'id':22,'type':'private'},'message_id':5,'date':int(clock[0]),'text':text}

def test_duplicate_update_receipt(env):
    e,s,c,clock=env; u=Updates(e,API(),999)
    update={'update_id':1,'message':{'from':{'id':2},'chat':{'id':22,'type':'private'},'message_id':5,'date':int(clock[0]),'text':'Жду документы'}}
    u.handle(update); u.handle(update)
    assert len(s.rows('SELECT * FROM events'))==1

def test_deleted_pending_message_never_applies(env,seed):
    e,s,c,clock=env; u=Updates(e,API(),999)
    u.handle({'update_id':1,'business_message':msg(clock,'Почти готово')})
    u.handle({'update_id':2,'deleted_business_messages':{'business_connection_id':'conn','chat':{'id':22},'message_ids':[5]}})
    assert s.rows("SELECT * FROM events WHERE id='update:1'")[0]['state']=='superseded'
    e.process('update:1')
    assert e.tasks()[0]['status']=='confirmed'
    assert s.rows("SELECT * FROM sources WHERE id='business:conn:22:5'")[0]['deleted']==1

def test_edit_supersedes_unprocessed_original(env,seed):
    e,s,c,clock=env; u=Updates(e,API(),999)
    u.handle({'update_id':1,'business_message':msg(clock,'Список завершён, отправлен файл')})
    u.handle({'update_id':2,'edited_business_message':msg(clock,'Список ещё не готов, исправляю')})
    assert s.rows("SELECT * FROM events WHERE id='update:1'")[0]['state']=='superseded'
    assert s.rows("SELECT * FROM events WHERE id='update:2'")[0]['state']=='pending'

def test_outgoing_human_business_owner_ignored(env):
    e,s,c,clock=env; c['employees']['boss']['telegram_user_id']=99
    u=Updates(e,API(),999); m=msg(clock,'Отправил сообщение сотруднику'); m['from']['id']=99
    u.handle({'update_id':1,'business_message':m})
    assert not s.rows('SELECT * FROM events')

def test_edited_old_message_does_not_open_24_hour_window(env):
    e,s,c,clock=env; u=Updates(e,API(),999); m=msg(clock,'Уточнение старого сообщения'); m['date']=int(clock[0]-200000)
    u.handle({'update_id':1,'edited_business_message':m})
    assert s.rows('SELECT * FROM chats')[0]['last_inbound']==m['date']
