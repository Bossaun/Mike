import json
import httpx
import pytest
from test_group_work import r,inbound
from goldway.photo_reading import attachment,transcribe,MAX_IMAGE_BYTES,IMAGE_INPUT_BOUND
from goldway.telegram import Telegram
from goldway.model import JsonModel
from goldway.work_dialogue import conversation

JPEG=b'\xff\xd8\xffsynthetic-image'
READING={'text':'TELL ME | Блеск для губ | 6 шт.','uncertain':'Артикул частично неразборчив.','summary':'Красным подчёркнута строка TELL ME.'}
PHOTO=[{'file_id':'SYNTHETIC_SMALL','width':100,'height':100},{'file_id':'SYNTHETIC_LARGE','width':1000,'height':600}]


def test_largest_photo_and_image_document():
 assert attachment({'photo':PHOTO})=={'file_id':'SYNTHETIC_LARGE'}
 assert attachment({'document':{'file_id':'PNG','mime_type':'image/png'}})
 assert not attachment({'document':{'file_id':'SVG','mime_type':'image/svg+xml'}})


def test_photo_group_intake_without_caption_is_recipient_scoped(r):
 inbound(r,'',photo=PHOTO);m=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])
 assert m['text']=='' and m['image']['file_id']=='SYNTHETIC_LARGE' and m['addressed']
 inbound(r,'',uid=1234,mid=2,photo=PHOTO)
 assert len(r.s.rows('SELECT payload FROM group_work_events'))==1


def test_photo_in_private_business_keeps_private_response(r):
 m={'message_id':1,'date':int(r.clock[0]),'chat':{'id':3,'type':'private'},'photo':PHOTO}
 assert r.w.handle_private(m,'dasha_it','connection')
 assert json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload'])['private']=='dasha_it'


def test_photo_transcription_once_context_and_task_authority(r):
 inbound(r,'',photo=PHOTO)
 downloaded=[];r.api.photo=lambda fid:(downloaded.append(fid) or JPEG)
 calls=[]
 def propose(ctx):
  calls.append(ctx)
  if ctx['kind']=='image_transcription':return READING
  assert ctx['current_messages'][0]['photo_reading']==READING
  assert 'SYNTHETIC_LARGE' not in str(ctx)
  return dict(reply='На фото строка TELL ME. Артикул читается не полностью.',evidence_ids=['1'],focus_task_ids=['C01'],updates=[dict(task_id='C01',message_id=1,kind='result',quote=READING['text'])],new_tasks=[])
 r.e.model.propose=propose;r.w.process(lambda:False);r.w.process(lambda:False)
 assert len(downloaded)==1 and len(calls)==2 and r.w.tasks()[0]['state']=='open'
 saved=json.loads(r.s.rows('SELECT payload FROM group_work_events')[0]['payload']);assert saved['photo_state']=='done'
 transcribe(r.w,saved);assert len(downloaded)==1
 assert 'TELL ME' in str(conversation(r.w,dict(id=2,actor='dasha_it',text='Что с ним?',at=r.clock[0])))
 assert not r.s.rows('SELECT * FROM group_work_changes')


def test_private_photo_never_enters_group_context(r):
 r.api.photo=lambda fid:JPEG
 m={'message_id':1,'date':int(r.clock[0]),'chat':{'type':'private'},'photo':PHOTO}
 r.w.handle_private(m,'dasha_it','connection')
 r.e.model.propose=lambda ctx:dict(READING,text='PRIVATE_IMAGE') if ctx['kind']=='image_transcription' else dict(reply='Фото прочитал.',updates=[],evidence_ids=['seed'])
 r.w.process(lambda:False)
 assert 'PRIVATE_IMAGE' not in str(conversation(r.w,dict(id=2,actor='alen',text='Что?',at=r.clock[0])))
 assert 'PRIVATE_IMAGE' not in str(conversation(r.w,dict(id=2,actor='alen',text='Что?',private='alen',at=r.clock[0])))
 assert r.s.rows('SELECT preferred_transport FROM outbox')[0]['preferred_transport']=='business_only'


def test_photo_failure_no_model_loop_and_useful_retry(r):
 inbound(r,'',photo=PHOTO);calls=[]
 def broken(fid):calls.append(fid);raise ValueError('bad image')
 r.api.photo=broken;r.w.process(lambda:False);r.w.process(lambda:False)
 assert len(calls)==1 and len(r.e.model.calls)==0
 assert 'крупнее' in r.api.sent[-1]['text']
 assert r.s.rows('SELECT state FROM group_work_events')[0]['state']=='failed'


def test_revoked_after_download_prevents_model(r):
 inbound(r,'',photo=PHOTO)
 def revoke(fid):r.p.write_text('{"enabled":false}');return JPEG
 r.api.photo=revoke;r.w.process(lambda:False)
 assert not r.e.model.calls and not r.api.sent


@pytest.mark.parametrize('api',['chat_completions','responses'])
def test_vision_payload_and_shared_budget_reservation(env,monkeypatch,api):
 import goldway.model as module
 e,s,c,clock=env;requests=[];original=httpx.Client
 def handle(req):
  requests.append(json.loads(req.content))
  if api=='responses':return httpx.Response(200,json={'status':'completed','output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps(READING)}]}],'usage':{'input_tokens':2000,'output_tokens':100}})
  return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(READING)},'finish_reason':'stop'}],'usage':{'prompt_tokens':2000,'completion_tokens':100}})
 monkeypatch.setattr(module.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handle)))
 cfg=dict(c['model'],api=api);m=JsonModel(s,e.b,cfg,'synthetic',lambda:clock[0])
 assert m.propose({'kind':'image_transcription','_image':{'body':JPEG}})==READING
 payload=requests[0];parts=(payload['input'] if api=='responses' else payload['messages'])[-1]['content']
 assert len(parts)==2 and parts[1]['type'] in ('image_url','input_image')
 assert '_image' not in parts[0].get('text','')
 usage=s.rows('SELECT * FROM usage')[0]
 assert usage['reserved_cost']>IMAGE_INPUT_BOUND*cfg['input_per_million']/1e6
 assert usage['input_tokens']==2000
 cfg['daily_budget']=0.000001
 with pytest.raises(ValueError,match='budget'):JsonModel(s,e.b,cfg,'synthetic',lambda:clock[0]).propose({'kind':'image_transcription','_image':{'body':JPEG}})
 assert len(requests)==1


@pytest.mark.parametrize('file,body,ok',[
 ({'file_path':'photos/x.jpg','file_size':20},JPEG,True),
 ({'file_path':'../secret','file_size':20},JPEG,False),
 ({'file_path':'https://evil.test/x','file_size':20},JPEG,False),
 ({'file_path':'photos/x.jpg','file_size':MAX_IMAGE_BYTES+1},JPEG,False),
 ({'file_path':'photos/x.jpg','file_size':20},b'<svg>bad</svg>',False),
 ({'file_path':'photos/x.jpg','file_size':20},b'x'*(MAX_IMAGE_BYTES+1),False),
])
def test_download_bounds_and_no_external_urls(file,body,ok):
 requests=[]
 def handle(req):
  requests.append(req)
  if req.method=='POST':return httpx.Response(200,json={'ok':True,'result':file})
  return httpx.Response(200,content=body)
 api=Telegram('synthetic',httpx.Client(transport=httpx.MockTransport(handle)))
 if ok:assert api.photo('file')==JPEG
 else:
  with pytest.raises(ValueError):api.photo('file')
 assert all(r.url.host=='api.telegram.org' for r in requests)


def test_cached_reading_survives_failed_dialogue_without_retranscription(r):
 inbound(r,'',photo=PHOTO);r.api.photo=lambda fid:JPEG
 r.e.model.propose=lambda ctx:READING if ctx['kind']=='image_transcription' else {}
 r.w.process(lambda:False)
 assert r.s.rows('SELECT state FROM group_work_events')[0]['state']=='failed'
 assert 'TELL ME' in str(conversation(r.w,dict(id=2,actor='dasha_it',text='Почему его нет?',at=r.clock[0])))
 assert 'неразборчив' in r.api.sent[-1]['text']


@pytest.mark.parametrize('channel',['bot','business'])
@pytest.mark.parametrize('as_document',[False,True])
@pytest.mark.parametrize('parent',[False,True])
def test_real_updates_gate_accepts_team_images(r,channel,as_document,parent):
 from goldway.intake import TEAM_RECIPIENTS
 from goldway.telegram import Updates
 c=r.e.c;c['employees'].update(frost={'name':'Фрост','telegram_user_id':4},nastya={'name':'Настя','telegram_user_id':5})
 c.update(team_intake={'recipients':list(TEAM_RECIPIENTS),'started_at':int(r.clock[0]-1)},team_roster_verified=True,business_account_user_id=98,allowed_live_recipients=list(TEAM_RECIPIENTS),recipients_verified=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id_verified=True,bot_user_id=900)
 message={'message_id':10,'date':int(r.clock[0]),'from':{'id':3},'chat':{'id':3,'type':'private'}}
 if as_document:message['document']={'file_id':'doc','file_size':100,'mime_type':'image/png'}
 else:message['photo']=PHOTO
 if channel=='business':
  message['business_connection_id']='synthetic';r.s.execute("INSERT INTO connections(business,id,enabled,can_reply) VALUES('GOLDWAY','synthetic',1,1)")
 if parent:
  p=dict(message_id=9,date=int(r.clock[0]),chat=message['chat'],text='Покажите фото.',**{'from':{'id':900,'is_bot':True}})
  if channel=='business':p.update(business_connection_id='synthetic',sender_business_bot={'id':900,'is_bot':True},**{'from':{'id':98}})
  message['reply_to_message']=p
 update={'update_id':10,'business_message' if channel=='business' else 'message':message}
 updates=Updates(r.e,r.api,900);updates.work=r.w
 updates.handle(update);updates.handle(update)
 rows=r.s.rows('SELECT payload FROM group_work_events');assert len(rows)==1
 m=json.loads(rows[0]['payload']);assert m['private']=='dasha_it' and m['image'] and m['text']==''
 message['message_id']=11;message['forward_origin']={'type':'user'};update['update_id']=11;updates.handle(update)
 assert len(r.s.rows('SELECT payload FROM group_work_events'))==1
