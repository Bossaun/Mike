import pytest
import httpx
from goldway.telegram import Dispatcher, DeliveryError, Telegram, Updates

class API:
    def __init__(self):
        self.calls=[]; self.fail={}; self.connection={'is_enabled':True,'rights':{'can_reply':True},'user':{'id':99}}
    def call(self,method,**args):
        self.calls.append((method,args))
        if method=='getBusinessConnection': return self.connection
        if method=='getMe': return {'id':999}
        if method=='getChatMember': return {'status':'member'}
        if str(args.get('chat_id')) in self.fail: raise self.fail[str(args['chat_id'])]
        return {'message_id':len(self.calls)}

def chat(s,c,recipient,now,bot='22',business='33'):
    s.execute('INSERT INTO chats(business,recipient,business_chat,connection,last_inbound,bot_chat) VALUES(?,?,?,?,?,?)',(c['business'],recipient,business,'conn',now,bot))

@pytest.mark.parametrize('age,enabled,can_reply,transport',[(0,True,True,'business'),(86399,True,True,'business'),(86400,True,True,'bot'),(200000,True,True,'bot'),(0,False,True,'bot'),(0,True,False,'bot')])
def test_business_window_and_live_rights(env,age,enabled,can_reply,transport):
    e,s,c,clock=env; api=API(); chat(s,c,'alen',clock[0]-age)
    api.connection.update(is_enabled=enabled,rights={'can_reply':can_reply})
    d=Dispatcher(s,c,api,lambda:clock[0]); route,actual=d.route('alen')
    assert actual==transport
    assert ('business_connection_id' in route)==(transport=='business')

def test_wrong_business_account_fallback(env):
    e,s,c,clock=env; api=API(); api.connection['user']['id']=777; chat(s,c,'alen',clock[0])
    assert Dispatcher(s,c,api,lambda:clock[0]).route('alen')[1]=='bot'

def test_no_fallback_blocks_without_send(env):
    e,s,c,clock=env; api=API(); chat(s,c,'alen',clock[0]-90000,bot=None)
    s.queue(e.b,'alen','test','key',clock[0]); Dispatcher(s,c,api,lambda:clock[0]).flush()
    assert s.rows('SELECT * FROM outbox')[0]['state']=='blocked'
    assert not any(m=='sendMessage' for m,_ in api.calls)

def test_error_first_admin_does_not_stop_others(env):
    e,s,c,clock=env; api=API(); c['admins']=['boss','nastya']
    chat(s,c,'boss',clock[0]-90000,bot='11'); chat(s,c,'nastya',clock[0]-90000,bot='33')
    api.fail['11']=DeliveryError('failed','Forbidden')
    e.queue_summary('one'); Dispatcher(s,c,api,lambda:clock[0]).flush()
    assert [r['state'] for r in s.rows('SELECT * FROM outbox ORDER BY id')]==['failed','sent']

def test_unknown_send_never_blindly_retries(env):
    e,s,c,clock=env; api=API(); chat(s,c,'alen',clock[0]-90000)
    api.fail['22']=DeliveryError('unknown','Timeout')
    s.queue(e.b,'alen','test','key',clock[0]); d=Dispatcher(s,c,api,lambda:clock[0])
    d.flush(); clock[0]+=10000; d.flush()
    assert len([m for m,_ in api.calls if m=='sendMessage'])==1
    assert s.rows('SELECT * FROM outbox')[0]['state']=='unknown'

def test_crash_sending_becomes_unknown(env):
    e,s,c,clock=env; s.queue(e.b,'alen','test','key',clock[0]); s.execute("UPDATE outbox SET state='sending'")
    Dispatcher(s,c,API()).recover()
    assert s.rows('SELECT * FROM outbox')[0]['state']=='unknown'

def test_rate_limit_safe_retry(env):
    e,s,c,clock=env; api=API(); chat(s,c,'alen',clock[0]-90000)
    api.fail['22']=DeliveryError('retry','Rate limited',600)
    s.queue(e.b,'alen','test','key',clock[0]); d=Dispatcher(s,c,api,lambda:clock[0]); d.flush()
    assert s.rows('SELECT * FROM outbox')[0]['state']=='retry'
    api.fail.clear(); clock[0]+=601; d.flush()
    assert s.rows('SELECT * FROM outbox')[0]['state']=='sent'

def test_self_and_unknown_messages_ignored(env):
    e,s,*_=env; u=Updates(e,API(),999)
    for sender in [{'id':999},{'id':2,'is_bot':True},{'id':777}]:
        u.handle({'update_id':1,'message':{'from':sender,'chat':{'id':22,'type':'private'},'message_id':1,'date':1,'text':'готово'}})
    assert not s.rows('SELECT * FROM events')

def test_business_edits_and_deletes_preserve_task_audit(env,seed):
    e,s,c,clock=env; e.model.result={'actions':[],'summary':'Какой следующий шаг?'}; u=Updates(e,API(),999)
    u.handle({'update_id':1,'business_connection':{'id':'conn','user':{'id':99},'is_enabled':True,'rights':{'can_reply':True}}})
    m={'from':{'id':2},'business_connection_id':'conn','chat':{'id':22,'type':'private'},'message_id':5,'date':int(clock[0]),'text':'Жду накладные'}
    u.handle({'update_id':2,'business_message':m}); e.process('update:2')
    m['text']='Жду исправленные накладные'
    u.handle({'update_id':3,'edited_business_message':m}); e.process('update:3')
    u.handle({'update_id':4,'deleted_business_messages':{'business_connection_id':'conn','chat':{'id':22},'message_ids':[5]}})
    source=s.rows("SELECT * FROM sources WHERE id='business:conn:22:5'")[0]
    assert source['body']=='Жду исправленные накладные' and source['deleted']==1
    assert e.tasks()[0]['status']=='confirmed'
    assert {'source_edited','source_deleted'}<=set(r['kind'] for r in s.rows('SELECT * FROM audit'))

def test_api_timeout_redacts_url_and_is_unknown():
    def fail(req): raise httpx.ReadTimeout('secret-url',request=req)
    api=Telegram('PRIVATE_TOKEN',httpx.Client(transport=httpx.MockTransport(fail)))
    with pytest.raises(DeliveryError) as exc: api.call('sendMessage',chat_id=1,text='x')
    assert exc.value.state=='unknown' and 'PRIVATE_TOKEN' not in str(exc.value)

def test_repeating_same_edit_can_confirm_already_applied():
    response={'ok':False,'error_code':400,'description':'Bad Request: message is not modified'}
    api=Telegram('unit-test',httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(400,json=response))))
    assert api.call('editMessageText',chat_id=1,message_id=3,text='same')['already_applied'] is True
    with pytest.raises(DeliveryError):api.call('sendMessage',chat_id=1,text='same')
