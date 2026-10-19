"""Actual current task metadata → legacy brief; synthetic fixtures only."""
from types import SimpleNamespace
from dataclasses import replace,FrozenInstanceError
import ast,copy,json,sqlite3
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
from goldway.intelligence import reader_key
from goldway.work_context import work_id
from goldway import task_card_command as cards
from goldway import design_brief_command as legacy
from goldway import task_design_brief as m

MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
NOW=1792174600.0
class NoExternal:
 def propose(self,*a,**k):pytest.fail('model forbidden')
 def call(self,*a,**k):pytest.fail('provider forbidden')


def config():
 return dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name='Synthetic '+x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=False,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[]))


def host(path=':memory:'):
 clock=[NOW];s=Store(path);e=Engine(s,config(),NoExternal(),lambda:clock[0])
 return SimpleNamespace(e=e,s=s,clock=clock)


@pytest.fixture
def c(monkeypatch):
 value=host()
 def forbidden(*a,**k):pytest.fail('unexpected constructor, refresh, history/context or network')
 value.e._context=forbidden;value.e.intelligence.sources.refresh=forbidden
 import socket,httpx
 for cls in (Engine,Store,KnowledgeIndex):monkeypatch.setattr(cls,'__init__',forbidden)
 monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden)
 yield value
 value.s.db.set_authorizer(None);value.s.db.close()


def seed(c,**kw):
 data=dict(business='GOLDWAY',id='opaque-task',title='Canonical task title',owner='alen',deadline='2026-10-19',next_step='Canonical next step',criterion='Canonical criterion',status='confirmed',source_id='opaque-source',evidence='DO NOT READ EVIDENCE',created=NOW)
 data.update(kw)
 c.s.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,0)',(data['business'],data['source_id'],'DO NOT READ BODY','DO NOT READ URL'))
 c.s.execute('INSERT INTO tasks('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')',tuple(data.values()))
 return c.s.execute('SELECT public_number FROM tasks WHERE business=? AND id=?',(data['business'],data['id'])).fetchone()[0]


def document(c):
 c.e.intelligence.index.upsert(dict(source_id='doc-source',document_id='doc',title='NO TITLE READ',url='https://example.invalid/no',text='NO BODY READ',readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW-10,permission_expires_at=NOW+100,confirmed=True))
 ref=c.s.execute('SELECT id FROM knowledge_chunks').fetchone()[0]
 c.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?)',('GOLDWAY','opaque-task',json.dumps([ref])))
 return ref


def payload(channel='bot'):
 return dict(sender='test_operator',channel=channel,source_id='telegram:1:12' if channel=='bot' else 'business:conn:1:12')


def preview(c,driver=None,scope=None,**kw):
 args=dict(number=1,goal='Explicit design goal',audience='Explicit audience',format='Banner',materials='Supplied photo description, not a file')
 args.update(kw)
 return (driver or m.TaskDesignBrief(engine=c.e,enabled=True)).preview(payload() if scope is None else scope,**args)


def readonly(c):
 reads=[]
 def authorizer(action,table,column,*unused):
  if action==sqlite3.SQLITE_READ:
   reads.append((table,column))
   if (table=='tasks' and column=='evidence') or (table=='sources' and column in ('body','link')) or (table in ('knowledge_chunks','knowledge_documents') and column in ('text','title','url')):return sqlite3.SQLITE_DENY
  if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
  return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(authorizer);return reads


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_initialized_metadata_legacy_core_once_current_refs_readonly(c,monkeypatch,channel):
 seed(c);ref=document(c);calls=[];actual=legacy.render_design_brief
 def render(value):calls.append(value);return actual(value)
 monkeypatch.setattr(legacy,'render_design_brief',render)
 before=c.s.db.total_changes;reads=readonly(c)
 result=preview(c,scope=payload(channel))
 assert c.s.db.total_changes==before and len(calls)==1
 assert calls[0].goal=='Explicit design goal' and calls[0].assets==() and not calls[0].assets_confirmed
 assert 'Canonical task title' in result.text and 'Цель: Explicit design goal' in result.text
 assert 'Записанный срок задачи: 2026-10-19' in result.text and 'Срок: [уточнить]' in result.brief.text
 assert 'Canonical criterion' in result.text and 'не проверены' in result.text
 assert result.evidence_ids==tuple(sorted((work_id('GOLDWAY','opaque-task'),ref)))
 assert result.brief.source_ids==() and result.task_source.evidence_ids==result.evidence_ids
 assert result.requires_review and not result.live_authorized and not result.delivery_authorized
 assert 'opaque-task' not in result.text and 'opaque-source' not in result.text and ref not in result.text
 assert ('tasks','title') in reads
 with pytest.raises(FrozenInstanceError):result.text='changed'


@pytest.mark.parametrize('status',['confirmed','in_progress','blocked'])
def test_supported_current_status_and_explicit_missing_design_fields(c,status):
 seed(c,status=status,deadline=None)
 result=preview(c,goal=None,audience=None,format=None,materials=None)
 assert 'Записанный срок задачи: не записан' in result.text
 assert 'Цель: [уточнить]' in result.text and 'Canonical task title' not in result.brief.text
 assert 'доступность материалов' in result.text and 'подтверждено: материалов нет' not in result.text
 assert result.task_source.status==status


@pytest.mark.parametrize('status',['proposed','completed'])
def test_new_work_draft_refuses_proposed_or_closed_task(c,status):
 seed(c,status=status)
 with pytest.raises(m.TaskDesignBriefUnavailable):preview(c)


@pytest.mark.parametrize('kind',['disabled','employee','unknown_sender','other_private','group','wrong_transport','extra_payload','missing_payload','no_intelligence','business','admins','timezone'])
def test_gate_before_any_task_display_transfer(c,kind):
 seed(c);p=payload();driver=m.TaskDesignBrief(engine=c.e,enabled=kind!='disabled')
 if kind=='employee':p['sender']='alen';p['source_id']='telegram:3:12'
 elif kind=='unknown_sender':p['sender']='outsider'
 elif kind=='other_private':p['source_id']='telegram:333:12'
 elif kind=='group':p['source_id']='telegram:-100:12'
 elif kind=='wrong_transport':p['channel']='business'
 elif kind=='extra_payload':p['text']='not a command'
 elif kind=='missing_payload':del p['source_id']
 elif kind=='no_intelligence':c.e.intelligence=None
 elif kind=='business':c.e.b='OTHER'
 elif kind=='admins':c.e.c['admins']=['test_operator','alen']
 elif kind=='timezone':c.e.c['timezone']='UTC'
 reads=readonly(c)
 with pytest.raises(m.TaskDesignBriefUnavailable):preview(c,driver,scope=p)
 assert ('tasks','title') not in reads


@pytest.mark.parametrize('field,value',[
 ('number',None),('number',True),('number',0),('number',2),('number','1'),
 ('goal','a\nАудитория: injected'),('goal','a\rtest'),('goal','a\tvalue'),('goal','a\u2028b'),
 ('goal','a\x7fb'),('goal',''),('goal',False),('audience','a'*1001),('format','a'*101),('materials','a'*1001),
 ('goal','a'*1001),('materials',{}),('audience',12),
])
def test_strict_input_unknowns_or_whole_refusal(c,field,value):
 seed(c)
 with pytest.raises(m.TaskDesignBriefUnavailable):preview(c,**{field:value})


def after_render(monkeypatch,callback):
 actual=legacy.render_design_brief
 def render(value):
  result=actual(value);callback();return result
 monkeypatch.setattr(legacy,'render_design_brief',render)


@pytest.mark.parametrize('column,value',[('title','Changed title'),('criterion','Changed criterion'),('deadline','2026-10-20'),('next_step','Invisible changed next step'),('revision',2),('status','completed')])
def test_renderer_late_task_change_or_hidden_field_drift_denied(c,monkeypatch,column,value):
 seed(c)
 after_render(monkeypatch,lambda:c.s.execute('UPDATE tasks SET '+column+'=? WHERE business=? AND id=?',(value,'GOLDWAY','opaque-task')))
 with pytest.raises(m.TaskDesignBriefUnavailable):preview(c)


@pytest.mark.parametrize('kind',['source_deleted','source_missing','link_added','acl_missinggrant','grant_revoke','expiry','source_watermark'])
def test_renderer_current_source_task_doc_acl_drift_denied(c,monkeypatch,kind):
 seed(c);document(c)
 def change():
  if kind=='source_deleted':c.s.execute("UPDATE sources SET deleted=1")
  elif kind=='source_missing':c.s.execute('DELETE FROM sources')
  elif kind=='link_added':c.s.execute('INSERT INTO task_sources VALUES(?,?,?)',('GOLDWAY','opaque-task','missing-source'))
  elif kind=='acl_missinggrant':c.s.execute('UPDATE intelligence_task_acl SET evidence_ids=?',(json.dumps(['missing-doc']),))
  elif kind=='grant_revoke':c.s.execute('DELETE FROM knowledge_readers')
  elif kind=='expiry':c.clock[0]+=101
  else:c.e.intelligence.index.revoke('doc-source',observed_at=NOW+1)
 after_render(monkeypatch,change)
 with pytest.raises(m.TaskDesignBriefUnavailable):preview(c)


@pytest.mark.parametrize('kind',['config_identity','intel_identity','index','store','connection','clock','timezone','public_source_method','legacy_method','scope_mutation','policy'])
def test_renderer_captured_host_method_input_drift_denied(c,monkeypatch,kind):
 seed(c);scope=payload();original=c.s.db
 def change():
  if kind=='config_identity':c.e.c=copy.deepcopy(c.e.c)
  elif kind=='intel_identity':c.e.intelligence=copy.copy(c.e.intelligence)
  elif kind=='index':c.e.intelligence.index=copy.copy(c.e.intelligence.index)
  elif kind=='store':c.e.s=copy.copy(c.s)
  elif kind=='connection':c.s.db=None
  elif kind=='clock':c.e.now=lambda:NOW
  elif kind=='timezone':c.e.tz=None
  elif kind=='public_source_method':monkeypatch.setattr(cards,'build_task_brief_source',lambda *a,**k:None)
  elif kind=='legacy_method':monkeypatch.setattr(legacy,'build_brief',lambda *a,**k:None)
  elif kind=='scope_mutation':scope['source_id']='telegram:1:99'
  else:c.e.c['allowed_live_recipients'].remove('alen')
 after_render(monkeypatch,change)
 try:
  with pytest.raises(m.TaskDesignBriefUnavailable):preview(c,scope=scope)
 finally:c.s.db=original


@pytest.mark.parametrize('column,value',[('title','x'*3100),('criterion','x'*3100),('deadline','x'*65)])
def test_oversize_canonical_fields_refuse_without_truncation(c,column,value):
 seed(c,**{column:value})
 with pytest.raises(m.TaskDesignBriefUnavailable):preview(c)


def test_old_card_and_supplied_brief_api_compatibility_and_exact_four_snapshots(c,monkeypatch):
 seed(c);calls=[];readers=[];actual=cards._snapshot;reader=cards._Reader
 def snap(*a,**k):calls.append(a[0]);return actual(*a,**k)
 class Reader(reader):
  def __init__(self,*a):super().__init__(*a);readers.append(self)
 monkeypatch.setattr(cards,'_snapshot',snap);monkeypatch.setattr(cards,'_Reader',Reader)
 result=preview(c)
 assert len(calls)==4 and len(readers)==2 and calls[0] is calls[1] and calls[2] is calls[3]
 assert sum(r.bytes for r in readers)<=8*1024*1024
 card=cards.build_task_card(c.s,c.e.c,'test_operator',c.e.intelligence.index,clock=c.e.now,number=1)
 assert card.task_id==result.task_source.task_id and card.evidence_ids==result.evidence_ids
 assert card.snapshot_digest==result.task_source.snapshot_digest
 old=legacy.build_brief('ТЗ дизайнеру\nЦель: Legacy goal',business='GOLDWAY',event_id='literal-fixture')
 assert old.source_ids==() and 'Цель: Legacy goal' in old.text


def test_actual_file_close_reopen_current_task_correction(tmp_path):
 path=str(tmp_path/'synthetic-design.sqlite');c=host(path);seed(c)
 first=preview(c);c.s.db.close()
 c=host(path)
 try:
  c.s.execute("UPDATE tasks SET criterion='Corrected criterion',deadline='2026-10-21',revision=revision+1 WHERE business='GOLDWAY'")
  second=preview(c)
  assert 'Corrected criterion' in second.text and '2026-10-21' in second.text
  assert second.task_source.snapshot_digest!=first.task_source.snapshot_digest
 finally:c.s.db.close()


def test_public_metadata_api_explicit_number_and_shared_reader(c):
 seed(c)
 with pytest.raises(cards.CardUnavailable):cards.build_task_brief_source(c.s,c.e.c,'test_operator',c.e.intelligence.index,clock=c.e.now,number=None)
 reader=cards._Reader(c.s)
 source=cards.build_task_brief_source(c.s,c.e.c,'test_operator',c.e.intelligence.index,clock=c.e.now,number=1,reader=reader)
 assert source.title=='Canonical task title' and reader.bytes>0 and source.deadline=='2026-10-19'


def test_clock_backward_between_source_passes_refused(c,monkeypatch):
 seed(c);after_render(monkeypatch,lambda:c.clock.__setitem__(0,NOW-1))
 with pytest.raises(m.TaskDesignBriefUnavailable):preview(c)


def test_public_source_clock_connection_drift_denied_before_query(c):
 seed(c);original=c.s.db
 def clock():c.s.db=None;return NOW
 try:
  with pytest.raises(cards.CardUnavailable):cards.build_task_brief_source(c.s,c.e.c,'test_operator',c.e.intelligence.index,clock=clock,number=1)
 finally:c.s.db=original


def test_default_disabled_constructor_has_no_sql_or_clock_callbacks(c):
 def fail(*a,**k):pytest.fail('disabled reader accessed host dependency')
 c.e.now=fail
 c.s.db.set_authorizer(lambda *a:pytest.fail('disabled reader SQL'))
 driver=m.TaskDesignBrief(engine=c.e)
 with pytest.raises(m.TaskDesignBriefUnavailable):driver.preview(payload(),1,'Goal',None,None,None)
