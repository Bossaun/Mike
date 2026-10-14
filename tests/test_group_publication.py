import json
import pytest
from goldway.store import Store
from goldway.presentation import meeting_messages
from goldway.telegram import Dispatcher,Updates,DeliveryError
from test_telegram import API,chat

class GroupAPI(API):
    def call(self,method,**args):
        if method=='getChat':return {'type':'supergroup','is_forum':True,'permissions':{'can_send_messages':True}}
        if method=='getChatMember':return {'status':'administrator'}
        return super().call(method,**args)

def register_group(s,c,thread=None):
    s.execute('INSERT INTO chats(business,recipient,group_chat,group_thread_id) VALUES(?,?,?,?)',(c['business'],'meeting_group','-10055',thread))

def test_group_route_is_bot_not_business_and_preserves_topic(env):
    e,s,c,clock=env;register_group(s,c,thread=321)
    s.queue(e.b,'meeting_group','<b>Итоги</b>','key',clock[0],parse_mode='HTML')
    api=GroupAPI();Dispatcher(s,c,api,lambda:clock[0]).flush()
    call=next(args for method,args in api.calls if method=='sendMessage')
    assert 'business_connection_id' not in call
    assert call['message_thread_id']==321 and call['parse_mode']=='HTML'
    assert s.rows('SELECT * FROM outbox')[0]['destination_thread']==321

def test_general_topic_omits_message_thread_id(env):
    e,s,c,clock=env;register_group(s,c)
    route,transport=Dispatcher(s,c,GroupAPI(),lambda:clock[0]).route('meeting_group')
    assert transport=='group' and 'message_thread_id' not in route

def test_ordinary_bot_reply_stays_in_bot_dialog(env):
    e,s,c,clock=env;chat(s,c,'alen',clock[0])
    api=GroupAPI();d=Dispatcher(s,c,api,lambda:clock[0])
    route,transport=d.route('alen','bot')
    assert transport=='bot' and 'business_connection_id' not in route

def test_meeting_pages_escape_user_text_and_keep_all_task_numbers(env,seed):
    e,s,c,*_=env
    tasks=[dict(seed,id=str(i),public_number=i,title='<b>НЕ разметка</b> & данные',next_step='X'*500,criterion='Y'*500) for i in range(1,10)]
    pages=meeting_messages(tasks,c)
    assert len(pages)==3
    joined='\n'.join(pages)
    assert '&lt;b&gt;НЕ разметка&lt;/b&gt;' in joined
    for i in range(1,10):assert f'Задача №{i} ·' in joined
    assert all(len(p.encode('utf-16-le'))//2<=3500 for p in pages)

def test_public_task_numbers_survive_restart_and_are_business_scoped(env,seed,tmp_path):
    e,s,c,*_=env;assert e.tasks()[0]['public_number']==1
    reopened=Store(str(tmp_path/'state.sqlite'))
    assert reopened.rows('SELECT * FROM tasks')[0]['public_number']==1
    s.execute('INSERT INTO tasks(business,id,title,next_step,criterion,status,source_id,created) VALUES(?,?,?,?,?,?,?,?)',('OTHER','different','Другой бизнес','шаг','результат','confirmed','src',e.now()))
    assert s.rows("SELECT public_number FROM tasks WHERE business='OTHER'")[0]['public_number']==1

def test_numeric_task_reference_selects_correct_existing_task(env,seed,action):
    e,s,c,*_=env;action['title']='Другая задача';e.model.result={'actions':[action]};e.process(e.transcript(action['evidence']+' Вторая тема'))
    text='Задача №1: срок 2026-10-22'
    e.model.result={'actions':[{'op':'update','task_id':seed['id'],'deadline':'2026-10-22','deadline_lines':[1],'evidence_lines':[1]}]}
    e.reply('number','boss',text);e.process('number')
    assert e.tasks()[0]['deadline']=='2026-10-22'
    assert e.tasks()[1]['deadline']=='2026-10-20'

def test_regular_group_chat_is_not_interpreted_as_meeting(env):
    e,s,c,clock=env;c['allowed_group_ids']=['-10055'];c['bot_username']='GOLDWAYCHAT_BOT'
    u=Updates(e,GroupAPI(),999)
    u.handle({'update_id':11,'message':{'from':{'id':1},'chat':{'id':-10055,'type':'supergroup'},'date':int(clock[0]),'message_id':5,'text':'Обычное обсуждение '+('текст '*100)}})
    assert not s.rows('SELECT * FROM events') and not s.rows('SELECT * FROM outbox')

def test_direct_group_reply_uses_same_group_transport(env,seed):
    e,s,c,clock=env;c['allowed_group_ids']=['-10055']
    u=Updates(e,GroupAPI(),999);e.model.result={'actions':[],'summary':'Когда будет результат?'}
    u.handle({'update_id':11,'message':{'from':{'id':2},'chat':{'id':-10055,'type':'supergroup'},'date':int(clock[0]),'message_id':5,'message_thread_id':1,'text':'Задача №1: жду накладные'}})
    e.process('update:11')
    out=s.rows("SELECT * FROM outbox WHERE dedupe='clarify:update:11'")[0]
    assert out['preferred_transport']=='group'
    assert s.rows("SELECT * FROM chats WHERE recipient='alen'")[0]['group_thread_id'] is None

def test_unknown_start_only_registers_request_without_task_access(env):
    e,s,c,clock=env;c['allow_onboarding_replies']=True;c['allowed_live_recipients']=['boss']
    u=Updates(e,GroupAPI(),999)
    msg={'update_id':44,'message':{'from':{'id':777,'first_name':'Новый сотрудник'},'chat':{'id':777,'type':'private'},'date':int(clock[0]),'message_id':1,'text':'/start'}}
    u.handle(msg);u.handle(msg)
    assert len(s.rows('SELECT * FROM pending_members'))==1
    assert len(s.rows('SELECT * FROM outbox'))==1
    assert not e.tasks() and '777' not in [str(p.get('telegram_user_id')) for p in c['employees'].values()]
    route,transport=Dispatcher(s,c,GroupAPI(),lambda:clock[0]).route('pending:777','bot')
    assert transport=='bot' and route['chat_id']=='777'

def test_missing_sender_cannot_match_unconfigured_member(env):
    e,s,c,clock=env;c['employees']['boss']['telegram_user_id']=None
    u=Updates(e,GroupAPI(),999)
    u.handle({'update_id':55,'message':{'chat':{'id':11,'type':'private'},'date':int(clock[0]),'message_id':1,'text':'Большой текст '+('пример '*100)}})
    assert not s.rows('SELECT * FROM events')

def test_reply_to_group_task_card_cannot_cross_topic(env,seed):
    e,s,c,clock=env;c['allowed_group_ids']=['-10055']
    s.queue(e.b,'meeting_group','card','card',clock[0],task_id=seed['id'])
    s.execute("UPDATE outbox SET state='sent',message_id='10',transport='group',destination_chat='-10055',destination_thread=77 WHERE dedupe='card'")
    u=Updates(e,GroupAPI(),999)
    u.handle({'update_id':56,'message':{'from':{'id':2},'chat':{'id':-10055,'type':'supergroup'},'date':int(clock[0]),'message_id':1,'message_thread_id':88,'text':'Готов результат','reply_to_message':{'message_id':10,'from':{'id':999}}}})
    payload=json.loads(s.rows("SELECT * FROM events WHERE id='update:56'")[0]['payload'])
    assert payload['task_id'] is None

def test_general_chat_does_not_require_task_number_or_modify_tasks(env,seed):
    e,s,c,*_=env;e.model.result={'actions':[],'summary':'Да, можно общаться и присылать информацию.'}
    e.reply('general','alen','А с тобой можно общаться?',channel='bot');e.process('general')
    assert e.tasks()[0]['status']=='confirmed'
    out=s.rows("SELECT * FROM outbox WHERE dedupe='clarify:general'")[0]
    assert 'Да, можно' in out['text'] and out['preferred_transport']=='bot'

def test_general_chat_cannot_execute_model_actions(env,seed):
    e,s,c,*_=env;e.model.result={'actions':[{'op':'update','task_id':seed['id'],'status':'completed','evidence_lines':[1]}]}
    e.reply('general','alen','Что ты умеешь?',channel='bot')
    with pytest.raises(ValueError,match='cannot modify'):e.process('general')
    assert e.tasks()[0]['status']=='confirmed'

def test_group_bootstrap_on_fresh_database_is_repeatable(env):
    from goldway.telegram import bootstrap_groups
    e,s,c,*_=env;c['allowed_group_ids']=['-10055'];c['group_destinations']={'meeting_group':{'chat_id':'-10055','message_thread_id':321}}
    bootstrap_groups(s,c);bootstrap_groups(s,c)
    assert len(s.rows('SELECT * FROM chats'))==1
    assert s.rows('SELECT * FROM chats')[0]['group_thread_id']==321

def test_group_bootstrap_rejects_unknown_destination_before_commit(env):
    from goldway.telegram import bootstrap_groups
    e,s,c,*_=env;c['allowed_group_ids']=['-10055'];c['group_destinations']={'meeting_group':{'chat_id':'-10066'}}
    with pytest.raises(ValueError,match='allowlist'):bootstrap_groups(s,c)
    assert not s.rows('SELECT * FROM chats')
