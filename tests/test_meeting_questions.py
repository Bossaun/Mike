"""Actual current-input meeting question boundary; no network or old core suite."""
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.model import ScriptedModel

NOW=1792174600
TEXT='Итоги обсуждения.\nКакой образец отправить клиенту?\nРешение пока не принимали.'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
_MISSING=object()


def result(actions=(),questions=_MISSING,intent='meeting',text='Нужно уточнить, какой образец отправить клиенту.'):
 value=dict(actions=list(actions),summary='Synthetic meeting draft',reply_text=text,intent=intent,
  dialogue_state=dict(topic='',entities=[],pending_offer=None,task_id=None),evidence_ids=[],knowledge_mode='not_needed')
 if questions is not _MISSING:value['meeting_question_lines']=questions
 return value


def make_case():
 s=Store(':memory:');clock=[NOW]
 config=dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,
  team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,
  allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,intelligence=dict(enabled=True,sources=[]))
 engine=Engine(s,config,None,lambda:clock[0]);engine.intelligence.sources.refresh=lambda *a,**kw:{}
 return SimpleNamespace(s=s,e=engine,c=config,clock=clock)


@pytest.fixture
def case(monkeypatch):
 value=make_case()
 import builtins,socket,httpx
 def no_io(*a,**k):pytest.fail('unexpected external I/O')
 monkeypatch.setattr(builtins,'open',no_io);monkeypatch.setattr(socket,'socket',no_io);monkeypatch.setattr(httpx,'Client',no_io)
 yield value
 value.s.db.close()


def submit(case,value,text=TEXT,sender='test_operator',channel='bot',event='meeting',callback=None):
 case.clock[0]+=1;seen=[]
 class Model:
  def propose(self,context):
   seen.append(dict(context))
   if callback:callback(context)
   return value
 case.e.model=Model()
 uid=case.c['employees'][sender]['telegram_user_id']
 source=f'telegram:{uid}:10' if channel=='bot' else f'business:conn:{uid}:10'
 case.e.reply(event,sender,text,source_id=source,channel=channel,message_at=case.clock[0]);case.e.process(event)
 return seen


def outputs(case):return case.s.rows('SELECT * FROM outbox ORDER BY id')


def new_action():
 return dict(op='create',task_id=None,title='Проверить образцы',owner=None,deadline=None,next_step='Сверить образцы',criterion='Готов отчёт проверки',status='proposed',set_fields=[],evidence_lines=[1],owner_lines=[],deadline_lines=[])


def question_rows(case):return [r for r in outputs(case) if r['dedupe'].startswith('meeting-questions:')]


def invalid(case,event='meeting'):
 from goldway.meeting_questions import NOTICE
 assert case.s.execute('SELECT state,error FROM events WHERE business=? AND id=?',('GOLDWAY',event)).fetchone()['state']=='done'
 assert not case.e.tasks() and not question_rows(case)
 assert not case.s.execute("SELECT 1 FROM sources WHERE id LIKE 'telegram:%' OR id LIKE 'business:%'").fetchone()
 assert all(r['text']==NOTICE for r in outputs(case))


@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_zero_task_meeting_preserves_source_questions_once(case,channel):
 seen=submit(case,result(questions=[[2]]),channel=channel)
 assert len(seen)==1 and not case.e.tasks()
 assert seen[0]['capabilities']['meeting_question_draft'] is True
 rows=question_rows(case);assert len(rows)==1
 assert 'Какой образец отправить клиенту?' in rows[0]['text'] and 'строки 2 текущего сообщения' in rows[0]['text']
 assert 'Статус решения не подтверждён' in rows[0]['text'] and 'не полный список' in rows[0]['text']
 assert rows[0]['recipient']=='test_operator' and rows[0]['preferred_transport']==channel and rows[0]['parse_mode']=='HTML'
 assert rows[0]['task_id'] is None
 assert 'Решение пока не принимали' not in rows[0]['text']


def test_proposal_cards_remain_proposals_and_sidecar_has_no_action_authority(case):
 seen=submit(case,result([new_action()],[[2]]))
 assert len(seen)==1 and len(case.e.tasks())==1
 task=case.e.tasks()[0];assert task['status']=='proposed' and task['owner'] is None and task['deadline'] is None
 assert any('Черновик' in r['text'] and r['dedupe'].startswith('review:') for r in outputs(case))
 assert len(question_rows(case))==1 and all(r['recipient']=='test_operator' for r in outputs(case))


@pytest.mark.parametrize('questions',[_MISSING,[]])
def test_missing_or_empty_field_keeps_legacy_meeting_compatible(case,questions):
 submit(case,result(questions=questions))
 assert not question_rows(case)
 assert len(outputs(case))==1 and 'Новых поручений не выделено' in outputs(case)[0]['text']


def test_later_answer_does_not_turn_candidate_into_unresolved_fact(case):
 submit(case,result(questions=[[2]]),text=TEXT+'\nОтвет: образец ALPHA согласовали позже.')
 text=question_rows(case)[0]['text']
 assert 'Какой образец' in text and 'Статус решения не подтверждён' in text
 assert 'нерешён' not in text.casefold() and 'ALPHA' not in text


def test_quotes_exact_escaped_no_normalization_or_transport_identity(case):
 source='Итоги\n  <b>Какой  образец?</b> & отправить 🚀\nВторая строка'
 submit(case,result(questions=[[2,3]]),text=source)
 text=question_rows(case)[0]['text']
 assert '  &lt;b&gt;Какой  образец?&lt;/b&gt; &amp; отправить 🚀\nВторая строка' in text
 assert 'строки 2–3' in text and 'telegram:' not in text and 'business:' not in text


@pytest.mark.parametrize('spans',[None,{},'2',[2],[[True]],[[0]],[[4]],[[2,1]],[[1,3]],[[1,2,3,4]],[[2],[2]],[[1,2],[2,3]],[[1]]*9,[{'source_id':'foreign','lines':[2]}]])
def test_whole_malformed_sidecar_rejected_before_proposal_or_source_write(case,spans):
 submit(case,result([new_action()],spans));invalid(case)


@pytest.mark.parametrize('text,spans',[
 ('Итоги\n'+'x'*801,[[2]]),('Итоги\n'+'🚀'*401,[[2]]),
 ('Итоги\n\nВопрос',[[2]]),('Итоги\n'+'x'*700+'\n'+'y'*700+'\n'+'z'*700+'\n'+'q'*700,[[2],[3],[4],[5]])])
def test_question_quote_units_and_empty_lines_fail_without_truncation(case,text,spans):
 submit(case,result([new_action()],spans),text=text);invalid(case)


@pytest.mark.parametrize('intent',['conversation','task_action','idea'])
def test_nonmeeting_nonempty_sidecar_cannot_acquire_a_route(case,intent):
 submit(case,result(questions=[[2]],intent=intent));invalid(case)


def test_employee_no_capability_or_question_content(case):
 seen=submit(case,result(questions=[[2]]),sender='frost')
 assert seen[0]['capabilities']['meeting_question_draft'] is False
 assert not question_rows(case) and not outputs(case) and not case.e.tasks()


def test_employee_empty_field_preserves_existing_proposal_path(case):
 submit(case,result([new_action()],[]),sender='frost')
 assert len(case.e.tasks())==1 and case.e.tasks()[0]['status']=='proposed'
 assert not question_rows(case)


def test_unsupported_nonadmin_meeting_recipient_cannot_activate_sidecar(case):
 case.c['meeting_recipient']='frost'
 seen=submit(case,result(questions=[[2]]))
 assert seen[0]['capabilities']['meeting_question_draft'] is False
 assert not question_rows(case) and not outputs(case) and not case.e.tasks()


@pytest.mark.parametrize('drift',['business','remove_team','replace_intel','admin','context','input'])
def test_callback_identity_policy_input_drift_no_tasks_or_content(case,drift):
 def change(context):
  if drift=='business':case.c['business']='OTHER'
  elif drift=='remove_team':case.c.pop('team_intake')
  elif drift=='replace_intel':case.e.intelligence=SimpleNamespace()
  elif drift=='admin':case.c['admins']=['frost']
  elif drift=='context':context['text']='1 | Foreign text'
  else:context['sender_role']='employee'
 submit(case,result([new_action()],[[2]]),callback=change)
 assert not case.e.tasks() and not question_rows(case)
 assert all('Какой образец' not in r['text'] for r in outputs(case))


def test_question_queue_callback_drift_rolls_back_cards_tasks_and_source(case):
 queue=case.s.queue
 def drift(business,recipient,text,dedupe,*args,**kwargs):
  value=queue(business,recipient,text,dedupe,*args,**kwargs)
  if dedupe.startswith('meeting-questions:'):case.c['business']='OTHER'
  return value
 case.s.queue=drift
 submit(case,result([new_action()],[[2]]));invalid(case)


def test_replay_has_no_model_or_duplicate_queue(case):
 submit(case,result(questions=[[2]]));before=[dict(r) for r in outputs(case)]
 class NoModel:
  def propose(self,*a):pytest.fail('model replay')
 case.e.model=NoModel();case.e.process('meeting')
 assert [dict(r) for r in outputs(case)]==before
 assert case.e.reply('meeting','test_operator',TEXT,source_id='telegram:1:10',channel='bot') is False
 assert [dict(r) for r in outputs(case)]==before


def test_assembled_review_budget_rolls_back_new_task(case,monkeypatch):
 import goldway.meeting_questions as mq
 monkeypatch.setattr(mq,'MAX_PAGES',1)
 submit(case,result([new_action()],[[2]]));invalid(case)


def test_sender_scope_must_match_actual_private_chat(case):
 case.clock[0]+=1;case.e.model=ScriptedModel(result(questions=[[2]]))
 case.e.reply('meeting','test_operator',TEXT,source_id='telegram:2:10',channel='bot',message_at=case.clock[0]);case.e.process('meeting')
 assert not question_rows(case) and not outputs(case)


def test_nonmeeting_empty_field_does_not_replace_direct_reply(case):
 seen=submit(case,result(questions=[],intent='conversation',text='Это обычный ответ.'),text='Объясните порядок проверки образцов.')
 assert len(seen)==1 and not question_rows(case) and any(r['text']=='Это обычный ответ.' for r in outputs(case))


def test_jsonmodel_actual_engine_request_exposes_bounded_host_field_once(case,monkeypatch):
 import json,httpx
 from goldway.model import JsonModel
 seen=[];value=result(questions=[[2]])
 class FakeClient:
  def __init__(self,**kwargs):pass
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def post(self,url,**kwargs):
   seen.append(kwargs['json'])
   return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':json.dumps(value)},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}})
 monkeypatch.setattr(httpx,'Client',FakeClient)
 model_config=dict(provider='openai',model='synthetic',input_per_million=0,output_per_million=0,daily_budget=1,max_context_chars=44000,max_output_tokens=3000)
 case.e.model=JsonModel(case.s,'GOLDWAY',model_config,'synthetic-key',lambda:case.clock[0])
 case.clock[0]+=1;case.e.reply('meeting','test_operator',TEXT,source_id='telegram:1:10',channel='bot',message_at=case.clock[0]);case.e.process('meeting')
 assert len(seen)==1 and len(question_rows(case))==1
 schema=seen[0]['response_format']['json_schema']['schema'];field=schema['properties']['meeting_question_lines']
 assert field['maxItems']==8 and field['items']['maxItems']==3 and field['items']['items']['maximum']==3
 assert set(schema['properties'])==set(schema['required'])
 assert 'meeting_question_draft' in seen[0]['messages'][-1]['content']


@pytest.mark.parametrize('readonly',['rewrite_only','ordinal_only','comparison_only'])
def test_readonly_schemas_never_admit_new_field(case,readonly):
 from goldway.model import response_schema
 context=dict(kind='conversation',intelligence=True,employees={},rewrite_only=True,rewrite_reply_chars=2600,
  sender_role='admin',can_create_tasks=True,capabilities={'meeting_question_draft':True},meeting_question_line_count=3)
 context[readonly]=True
 schema=response_schema(context)
 assert 'meeting_question_lines' not in schema['properties'] and schema['properties']['actions']['maxItems']==0
 assert schema['properties']['knowledge_mode']['enum']==['not_needed']


def test_malformed_sidecar_does_not_rewind_existing_accepted_reply_metadata(case):
 # Existing Engine.reply acceptance precedes process/model and is not a sidecar write.
 from goldway.task_transition_history import append_transition
 task='synthetic-owned-task'
 case.s.execute('INSERT INTO sources(business,id,body,link) VALUES(?,?,?,?)',('GOLDWAY','synthetic-source','Original','synthetic'))
 case.s.execute('INSERT INTO tasks(business,id,title,owner,deadline,next_step,criterion,status,source_id,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
  ('GOLDWAY',task,'ALPHA','test_operator','2026-10-19','next','criterion','confirmed','synthetic-source','Original',NOW))
 case.s.queue('GOLDWAY','test_operator','Synthetic pending reminder','reminder:synthetic',NOW,task)
 before=dict(case.s.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone());case.clock[0]+=1
 case.e.model=ScriptedModel(result(questions=[[999]]))
 case.e.reply('meeting','test_operator',TEXT,task_id=task,source_id='telegram:1:10',channel='bot',message_at=case.clock[0])
 accepted=dict(case.s.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone())
 assert accepted['last_reply']==case.clock[0] and accepted['revision']==before['revision']
 assert case.s.execute("SELECT state FROM outbox WHERE dedupe='reminder:synthetic'").fetchone()[0]=='cancelled'
 case.e.process('meeting')
 assert dict(case.s.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone())==accepted
 assert not question_rows(case) and not case.s.execute("SELECT 1 FROM sources WHERE id='telegram:1:10'").fetchone()
 assert not case.s.execute('SELECT 1 FROM task_sources WHERE task_id=?',(task,)).fetchone()


def protected_document(case):
 from goldway.intelligence import reader_key
 case.e.intelligence.index.upsert(dict(source_id='synthetic-doc',document_id='synthetic-doc',title='Образец',url='https://example.invalid/doc',
  text='Образец описан в справке. SENTINEL_PRIVATE_NOT_IN_INPUT.',readers=[reader_key('GOLDWAY','test_operator')],observed_at=NOW,permission_expires_at=NOW+1000,confirmed=True))


def test_sidecar_protects_all_consulted_context_and_delivery_rechecks_revocation(case):
 import json
 from goldway.intelligence import delivery_permitted
 protected_document(case);seen=submit(case,result(questions=[[2]]))
 refs={x['id'] for x in seen[0]['knowledge']['evidence']};assert refs
 row=question_rows(case)[0]
 protected=case.s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=? AND business=?',(row['id'],'GOLDWAY')).fetchone()
 assert refs<=set(json.loads(protected[0]))
 assert 'SENTINEL_PRIVATE_NOT_IN_INPUT' not in row['text']
 assert delivery_permitted(case.s,'GOLDWAY','test_operator',row['id'],case.clock[0],config=case.c)
 case.s.execute('DELETE FROM knowledge_readers')
 assert not delivery_permitted(case.s,'GOLDWAY','test_operator',row['id'],case.clock[0],config=case.c)


def test_final_permission_loss_after_existing_validation_blocks_projection(case):
 protected_document(case);validate=case.e.intelligence.validate
 def revoke(*args):
  value=validate(*args);case.s.execute('DELETE FROM knowledge_readers');return value
 case.e.intelligence.validate=revoke
 submit(case,result([new_action()],[[2]]));invalid(case)


def test_actual_group_does_not_get_question_projection(case):
 case.clock[0]+=1;case.e.model=ScriptedModel(result(questions=[[2]]))
 case.e.reply('meeting','test_operator',TEXT,source_id='group:-100:10',channel='group',message_at=case.clock[0]);case.e.process('meeting')
 assert not outputs(case) and not case.e.tasks()


def test_outsider_is_rejected_before_model_or_projection(case):
 class NoModel:
  def propose(self,*a):pytest.fail('outsider reached model')
 case.e.model=NoModel()
 assert case.e.reply('meeting','outsider',TEXT,source_id='telegram:999:10',channel='bot') is False
 assert not outputs(case) and not case.s.execute("SELECT 1 FROM events WHERE id='meeting'").fetchone()


def test_strict_schema_host_count_bool_rejected_and_cap_absent_has_no_field():
 from goldway.model import response_schema
 context=dict(kind='operator_chat',intelligence=True,sender_role='admin',can_create_tasks=True,employees={},capabilities={})
 assert 'meeting_question_lines' not in response_schema(context)['properties']
 context['capabilities']['meeting_question_draft']=True;context['meeting_question_line_count']=True
 with pytest.raises(ValueError,match='Invalid meeting question context'):response_schema(context)
