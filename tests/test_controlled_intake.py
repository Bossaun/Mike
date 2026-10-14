import copy
import pytest
from goldway.telegram import Updates
from goldway.intake import accepts, FORBIDDEN_FIELDS
from test_telegram import API


def profile(c, clock):
    c['employees']['test_operator']={'name':'Synthetic operator','telegram_user_id':77}
    c['admins']=['test_operator']
    c['controlled_intake']={'recipient':'test_operator','started_at':int(clock[0])-10,'ends_at':int(clock[0])+290}
    c['live_send']=False


def update(now):
    return {'update_id':1,'message':{'from':{'id':77},'chat':{'id':77,'type':'private'},
        'message_id':1,'date':int(now),'text':'Жду документы'}}


@pytest.mark.parametrize('field',sorted(FORBIDDEN_FIELDS))
def test_rejected_input_never_saved(env, field):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);u['message'][field]={}
    api=API();Updates(e,api,999).handle(u)
    for table in ['events','sources','chats','runtime','outbox','pending_members','connections']:
        assert not s.rows('SELECT * FROM '+table)
    assert not api.calls


def test_new_operator_message_saved_once(env):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);api=API();handler=Updates(e,api,999)
    handler.handle(u);handler.handle(u)
    assert len(s.rows('SELECT * FROM events'))==1
    assert not api.calls


@pytest.mark.parametrize('kind',['edited_message','business_message','edited_business_message','business_connection','deleted_business_messages'])
def test_non_ordinary_updates_rejected(env,kind):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);u[kind]=u.pop('message')
    Updates(e,API(),999).handle(u)
    assert not s.rows('SELECT * FROM runtime')
    assert not s.rows('SELECT * FROM events')


@pytest.mark.parametrize('change',['old','future','group','other_sender','other_chat','bot','attachment','old_reply'])
def test_context_rejected(env,change):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);m=u['message']
    if change=='old':m['date']=c['controlled_intake']['started_at']-1
    if change=='future':m['date']=int(clock[0])+1
    if change=='group':m['chat']['type']='group'
    if change=='other_sender':m['from']['id']=2
    if change=='other_chat':m['chat']['id']=2
    if change=='bot':m['from']['is_bot']=True
    if change=='attachment':m.pop('text');m['document']={}
    if change=='old_reply':m['reply_to_message']=copy.deepcopy(m);m['reply_to_message']['date']=c['controlled_intake']['started_at']-1
    Updates(e,API(),999).handle(u)
    assert not s.rows('SELECT * FROM events')
    assert not s.rows('SELECT * FROM runtime')


@pytest.mark.parametrize('gate',[None,{},False,{'recipient':'test_operator','started_at':True,'ends_at':300},{'recipient':'test_operator','started_at':1,'ends_at':9999}])
def test_bad_gate_fails_closed(env,gate):
    e,s,c,clock=env;profile(c,clock);c['controlled_intake']=gate
    assert not accepts(update(clock[0]),c,clock[0])


def test_expired_gate_rejects_even_new_message(env):
    e,s,c,clock=env;profile(c,clock);clock[0]=c['controlled_intake']['ends_at']
    assert not accepts(update(clock[0]),c,clock[0])


def test_default_profile_unchanged(env):
    e,s,c,clock=env
    assert accepts(update(clock[0]),c,clock[0])


def test_reply_to_fresh_operator_text_is_allowed(env):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);u['message']['reply_to_message']=copy.deepcopy(u['message'])
    assert accepts(u,c,clock[0])


def safe_profile(c,clock):
    profile(c,clock)
    for flag in ('live_send','sheets_write','allow_onboarding_replies','allow_registered_replies','daily_summary'):
        c[flag]=False
    c['meeting_recipient']=None;c['allowed_group_ids']=[];c['group_destinations']={}
    c['allowed_live_recipients']=['test_operator']


def test_safe_config_loads(env,tmp_path):
    from goldway.config import load
    import json
    e,s,c,clock=env;safe_profile(c,clock);p=tmp_path/'c.json';p.write_text(json.dumps(c))
    assert load(p)==c


@pytest.mark.parametrize('field',['test_intake','live_send','sheets_write','allow_onboarding_replies','allow_registered_replies','daily_summary','meeting_recipient','allowed_group_ids','group_destinations','allowed_live_recipients','admins'])
def test_unsafe_config_rejected(env,tmp_path,field):
    from goldway.config import load
    import json
    e,s,c,clock=env;safe_profile(c,clock)
    changes={'test_intake':{},'meeting_recipient':'group','allowed_group_ids':[123],
      'group_destinations':{'group':{}},'allowed_live_recipients':['boss'],'admins':['boss']}
    c[field]=changes.get(field,True)
    p=tmp_path/'c.json';p.write_text(json.dumps(c))
    with pytest.raises(ValueError):load(p)


def test_legacy_gate_alone_rejected(env,tmp_path):
    from goldway.config import load
    import json
    e,s,c,clock=env;c['test_intake']={};p=tmp_path/'c.json';p.write_text(json.dumps(c))
    with pytest.raises(ValueError):load(p)
    assert not accepts(update(clock[0]),c,clock[0])


@pytest.mark.parametrize('field',['from','chat','date','text','message_id'])
def test_missing_parent_fields_rejected(env,field):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);parent=copy.deepcopy(u['message']);parent.pop(field)
    u['message']['reply_to_message']=parent
    assert not accepts(u,c,clock[0])


@pytest.mark.parametrize('field',['document','forward_origin','edit_date','business_connection_id','reply_to_message'])
def test_unsafe_parent_rejected(env,field):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);parent=copy.deepcopy(u['message']);parent[field]={}
    u['message']['reply_to_message']=parent
    assert not accepts(u,c,clock[0])


@pytest.mark.parametrize('field',['from','chat'])
def test_malformed_headers_rejected_without_persistence(env,field):
    e,s,c,clock=env;profile(c,clock);u=update(clock[0]);u['message'][field]=None
    Updates(e,API(),999).handle(u)
    assert not s.rows('SELECT * FROM runtime')


@pytest.mark.parametrize('seconds',[None,0,301])
def test_test_worker_requires_bounded_process_before_api(env,seconds,monkeypatch):
    import goldway.cli as cli
    e,s,c,clock=env;safe_profile(c,clock)
    def forbidden(*args,**kwargs):raise AssertionError('API must not be constructed')
    monkeypatch.setattr(cli,'Telegram',forbidden)
    with pytest.raises(ValueError):cli.run(e,seconds)


def test_expired_worker_fails_before_api(env,monkeypatch):
    import goldway.cli as cli
    e,s,c,clock=env;safe_profile(c,clock);clock[0]=c['controlled_intake']['ends_at']
    def forbidden(*args,**kwargs):raise AssertionError('API must not be constructed')
    monkeypatch.setattr(cli,'Telegram',forbidden)
    with pytest.raises(ValueError):cli.run(e,300)
