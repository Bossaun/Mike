"""Fresh admin attachments through the real update gate, using synthetic APIs."""
import json

import httpx
import pytest

from goldway.cli import pending
from goldway.model import ScriptedModel
from goldway.telegram import Telegram, Updates
from test_team_tasks import team
from test_operator_tasks import result


def attachment(team, channel='bot'):
    e,s,c,clock,api=team
    uid=c['employees']['test_operator']['telegram_user_id']
    message={'message_id':42,'date':int(clock[0]),'from':{'id':uid},
             'chat':{'id':uid,'type':'private'},
             'document':{'file_id':'synthetic-file','file_name':'meeting.TXT','file_size':100},
             'caption':'/meeting caption is not the transcript'}
    if channel=='business':message['business_connection_id']='synthetic-connection'
    return {'update_id':42,'business_message' if channel=='business' else 'message':message}


@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('prefix',['','/meeting '])
def test_admin_attachment_uses_current_reply_review_once(team,monkeypatch,channel,prefix):
    e,s,c,clock,api=team
    update=attachment(team,channel)
    downloaded=[]
    text=prefix+'На встрече договорились: Алэн подготовит список.\nСрок завтра.'
    monkeypatch.setattr(api,'document',lambda file_id: downloaded.append(file_id) or text,raising=False)
    action={'op':'create','title':'Подготовить список','owner':'alen','owner_lines':[1],
            'deadline':None,'deadline_lines':[2],'next_step':'Составить список',
            'criterion':'Список сохранён','status':'confirmed','evidence_lines':[1]}
    e.model=ScriptedModel(result([action],intent='meeting',reply='Подготовлены предложения.'))
    Updates(e,api,900).handle(update)
    Updates(e,api,900).handle(update)
    rows=s.rows('SELECT kind,payload FROM events')
    assert len(rows)==1 and rows[0]['kind']=='reply'
    payload=json.loads(rows[0]['payload'])
    assert payload['text']==text and payload['sender']=='test_operator'
    assert payload['channel']==channel and payload['message_at']==int(clock[0])
    expected='business:synthetic-connection:1:42' if channel=='business' else 'telegram:1:42'
    assert payload['source_id']==expected
    pending(e)
    assert downloaded==['synthetic-file']
    assert len(e.tasks())==1 and e.tasks()[0]['status']=='proposed'
    assert {row['recipient'] for row in s.rows('SELECT recipient FROM outbox')}=={'test_operator'}
    assert not s.rows("SELECT id FROM outbox WHERE dedupe LIKE 'assignment:%'")


@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('change',['employee','unknown','old','future','group','forward','edit',
                                  'reply','oversized','bool_size','missing_size','not_txt','photo','empty_id'])
def test_rejected_attachment_has_no_download_or_persistence(team,monkeypatch,channel,change):
    e,s,c,clock,api=team
    update=attachment(team,channel)
    kind='business_message' if channel=='business' else 'message'
    m=update[kind]
    if change in ('employee','unknown'):
        uid=c['employees']['alen']['telegram_user_id'] if change=='employee' else 777
        m['from']['id']=m['chat']['id']=uid
    elif change=='old':m['date']=c['team_intake']['started_at']-1
    elif change=='future':m['date']=int(clock[0])+1
    elif change=='group':m['chat']['type']='group'
    elif change=='forward':m['forward_origin']={'type':'user'}
    elif change=='edit':update['edited_'+kind]=update.pop(kind)
    elif change=='reply':m['reply_to_message']={}
    elif change=='oversized':m['document']['file_size']=64001
    elif change=='bool_size':m['document']['file_size']=True
    elif change=='missing_size':m['document'].pop('file_size')
    elif change=='not_txt':m['document']['file_name']='meeting.pdf'
    elif change=='photo':m['photo']=[]
    elif change=='empty_id':m['document']['file_id']=''
    monkeypatch.setattr(api,'document',lambda *a: pytest.fail('rejected file downloaded'),raising=False)
    Updates(e,api,900).handle(update)
    assert not s.rows('SELECT * FROM events')
    assert not s.rows('SELECT * FROM runtime')


@pytest.mark.parametrize(('body','metadata','error'),[
    (b'x'*64001,100,ValueError), (b'\xff',100,UnicodeDecodeError),
    (b' \n',100,ValueError),(b'x',64001,ValueError),(b'x',True,ValueError),
])
def test_document_actual_bytes_decode_and_metadata_guards(body,metadata,error):
    api=Telegram('synthetic-token')
    api.call=lambda *a,**k:{'file_size':metadata,'file_path':'/synthetic.txt'}
    api.client.close()
    api.client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=body)))
    try:
        with pytest.raises(error):api.document('synthetic-file')
    finally:api.client.close()


@pytest.mark.parametrize('body',[b'x'*64000,b'\xef\xbb\xbfSynthetic UTF8 transcript'])
def test_document_accepts_size_boundary_and_utf8_bom(body):
    api=Telegram('synthetic-token')
    api.call=lambda *a,**k:{'file_size':len(body),'file_path':'/synthetic.txt'}
    api.client.close()
    api.client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=body)))
    try:assert api.document('synthetic-file')==body.decode('utf-8-sig')
    finally:api.client.close()


@pytest.mark.parametrize('channel',['bot','business'])
def test_invalid_decoded_attachment_rolls_back_update(team,monkeypatch,channel):
    e,s,c,clock,api=team
    def invalid(file_id):
        raise UnicodeDecodeError('utf-8',b'\xff',0,1,'synthetic invalid byte')
    monkeypatch.setattr(api,'document',invalid,raising=False)
    with pytest.raises(UnicodeDecodeError):
        Updates(e,api,900).handle(attachment(team,channel))
    assert not s.rows('SELECT * FROM events') and not s.rows('SELECT * FROM runtime')
    assert not e.tasks()


@pytest.mark.parametrize('channel',['bot','business'])
def test_64000_ascii_attachment_explains_split_once_before_model(team,monkeypatch,channel):
    e,s,c,clock,api=team
    update=attachment(team,channel)
    message=update['business_message' if channel=='business' else 'message']
    message['document']['file_size']=64000
    downloads=[]
    monkeypatch.setattr(api,'document',lambda file_id: downloads.append(file_id) or 'x'*64000,raising=False)
    monkeypatch.setattr(e.model,'propose',lambda *a: pytest.fail('oversized transcript reached model'))
    updates=Updates(e,api,900)
    updates.handle(update)
    updates.handle(update)
    pending(e)
    assert downloads==['synthetic-file']
    assert not s.rows('SELECT * FROM events') and not e.tasks()
    assert not s.rows('SELECT * FROM sources') and not s.rows('SELECT * FROM usage')
    notices=s.rows('SELECT recipient,text,preferred_transport,dedupe FROM outbox')
    assert len(notices)==1
    notice=notices[0]
    assert notice['recipient']=='test_operator' and notice['preferred_transport']==channel
    assert notice['dedupe']=='attachment-limit:42:test_operator'
    assert 'Разделите' in notice['text'] and '.txt' in notice['text']
    assert 'x'*100 not in notice['text']


@pytest.mark.parametrize('length',[32000,32001])
def test_attachment_character_limit_boundary(team,monkeypatch,length):
    e,s,c,clock,api=team
    update=attachment(team)
    monkeypatch.setattr(api,'document',lambda *a:'x'*length,raising=False)
    Updates(e,api,900).handle(update)
    assert len(s.rows('SELECT * FROM events'))==(1 if length==32000 else 0)
    assert len(s.rows('SELECT * FROM outbox'))==(0 if length==32000 else 1)
