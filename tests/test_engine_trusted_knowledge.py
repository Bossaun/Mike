"""Synthetic ordinary Engine host injection; no source seeding/real model/provider."""
from dataclasses import replace
from types import SimpleNamespace
import copy
import json
import pytest
from goldway.engine import Engine
from goldway.intelligence import Intelligence,reader_key,delivery_permitted
from goldway.trusted_source_bridge import TrustedSourceBridge,BridgeError
from goldway.trusted_provider import TrustedProviders,SourceSpec,ProviderSnapshot,Document
from goldway.store import Store
from goldway.model import ScriptedModel
from goldway.telegram import Dispatcher,Updates

NOW=1792400000


def config(business='SYNTHETIC'):
 return dict(business=business,employees={name:{'name':'Synthetic '+name,'telegram_user_id':uid} for name,uid in [('test_operator',77),('frost',88),('alen',89),('nastya',90),('dasha_it',91)]},admins=['test_operator'],timezone='UTC',intelligence={'enabled':True,'sources':[]},team_intake={'recipients':['test_operator','frost','alen','nastya','dasha_it'],'started_at':NOW-10},team_roster_verified=True,business_account_user_id=99,allowed_live_recipients=['test_operator','frost','alen','nastya','dasha_it'],live_send=True,recipients_verified=True,review_mode=True,allow_onboarding_replies=False,allow_registered_replies=False,sheets_write=False,daily_summary=False,rules=[],max_transcript_chars=32000,model={'max_context_chars':44000})


class API:
 def __init__(self):self.calls=[]
 def call(self,method,**kw):
  self.calls.append((method,kw));assert method=='sendMessage';return {'message_id':len(self.calls)}


class Model(ScriptedModel):
 def __init__(self):super().__init__({});self.contexts=[];self.force_ground=False
 def propose(self,context):
  self.contexts.append(copy.deepcopy(context));rows=context['knowledge']['evidence'];refs=[rows[0]['id']] if rows else []
  self.result=dict(actions=[],intent='conversation',summary='Internal synthetic summary',reply_text='Синтетический ALPHA пример.' if refs else 'В подключённом источнике сведений нет.',dialogue_state=dict(topic='ALPHA',entities=[],pending_offer=None,task_id=None),evidence_ids=refs,knowledge_mode='grounded' if refs or self.force_ground else 'not_found')
  return super().propose(context)


@pytest.fixture
def case(monkeypatch):
 c=config();s=Store(':memory:');clock=[NOW];calls=[];values={};credential=object()
 readers=frozenset(reader_key(c['business'],x) for x in c['employees'])
 registry=TrustedProviders(business=c['business'],members=readers,clock=lambda:clock[0])
 def callback(request,key):
  assert key is credential;calls.append(request)
  value=values[request.source_id]
  if isinstance(value,Exception):raise value
  return value
 registry.register_provider('provider',callback,credential=credential)
 def binding(identifier='source',confirmed=True,audience=None):
  spec=SourceSpec('provider',identifier,audience or frozenset({reader_key(c['business'],'test_operator')}),1,120,confirmed);registry.register_source(spec)
  values[identifier]=ProviderSnapshot('provider',identifier,1,NOW,(Document('document','Synthetic title','ALPHA synthetic v1 evidence'),));return spec
 spec=binding();model=Model();api=API()
 def engine(bindings=None,reg=registry,cfg=c):return Engine(s,cfg,model,lambda:clock[0],trusted_registry=reg,trusted_sources=(spec,) if bindings is None else bindings)
 def forbidden(*a,**kw):pytest.fail('network/files/credential/provider construction prohibited')
 import builtins,socket,httpx,subprocess
 monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(httpx,'Client',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
 yield SimpleNamespace(c=c,s=s,clock=clock,calls=calls,values=values,r=registry,spec=spec,binding=binding,model=model,api=api,engine=engine)
 s.db.close()


def query(case,engine,number=1,*,sender='test_operator',flush=False):
 if sender=='test_operator':
  update={'update_id':number,'message':{'message_id':number,'date':case.clock[0],'from':{'id':77,'is_bot':False},'chat':{'id':77,'type':'private'},'text':'Расскажите про ALPHA synthetic'}}
  Updates(engine,case.api,999).handle(update);event='update:'+str(number)
 else:
  event='synthetic:'+str(number);engine.reply(event,sender,'Расскажите про ALPHA synthetic',source_id='telegram:88:'+str(number),channel='bot',message_at=case.clock[0])
 engine.process(event)
 row=case.s.execute('SELECT * FROM outbox WHERE business=? ORDER BY id DESC LIMIT 1',(engine.b,)).fetchone()
 if flush:Dispatcher(case.s,case.c,case.api,lambda:case.clock[0]).flush()
 return dict(row) if row else None


def canonical(case,spec=None):
 spec=spec or case.spec;return 'trusted:'+case.c['business']+':'+spec.provider_id+':'+spec.source_id


def test_actual_ordinary_engine_retrieval_citation_protected_output_fake_dispatcher(case):
 e=case.engine();assert not case.calls and case.model.contexts==[]
 row=query(case,e,flush=True)
 assert row and case.s.execute('SELECT state FROM outbox WHERE id=?',(row['id'],)).fetchone()[0]=='sent'
 assert len(case.calls)==2 and len(case.model.contexts)==1 and case.api.calls[0][0]=='sendMessage'
 context=case.model.contexts[0];assert context['knowledge']['evidence'][0]['source_id']==canonical(case)
 assert context['knowledge']['evidence'][0]['text']=='ALPHA synthetic v1 evidence'
 assert 'Источники:' in row['text'] and 'Synthetic title' in row['text']
 acl=json.loads(case.s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=?',(row['id'],)).fetchone()[0]);assert acl
 assert delivery_permitted(case.s,e.b,'test_operator',row['id'],case.clock[0],case.c)
 assert not delivery_permitted(case.s,e.b,'frost',row['id'],case.clock[0],case.c)
 assert not case.s.rows('SELECT * FROM tasks')
 assert case.s.rows('SELECT id,body FROM sources')==[{'id':'telegram:77:1','body':'Расскажите про ALPHA synthetic'}]
 assert e.intelligence.sources.trusted_bridge.registry is case.r


def test_unpermitted_reader_and_wrong_business_cannot_receive_admitted_content(case):
 e=case.engine();query(case,e);query(case,e,2,sender='frost')
 assert case.model.contexts[-1]['knowledge']['evidence']==[]
 foreign=config('OTHER');foreign['team_intake']['started_at']=NOW-10
 e2=Engine(case.s,foreign,case.model,lambda:case.clock[0]);query(case,e2,3)
 assert case.model.contexts[-1]['knowledge']['evidence']==[]
 assert case.s.rows('SELECT source_id FROM knowledge_documents')==[{'source_id':canonical(case)}]


def test_unconfirmed_registered_policy_cannot_ground_facts(case):
 spec=case.binding('assumption',confirmed=False);e=case.engine(bindings=(spec,));case.model.force_ground=True
 with pytest.raises(ValueError,match='^Assumptions cannot ground corporate facts$'):query(case,e)
 assert case.model.contexts[-1]['knowledge']['evidence'][0]['confirmed'] is False
 assert all('Синтетический ALPHA пример.' not in row['text'] for row in case.s.rows('SELECT * FROM outbox'))
 assert not case.s.rows('SELECT * FROM intelligence_outbox_acl')


def test_restart_same_time_blocked_then_newer_v2_current_context_no_time_rebase(case):
 e=case.engine();query(case,e);old=case.s.rows('SELECT observed_at,permission_expires_at FROM knowledge_documents')
 e2=case.engine();assert not case.s.rows('SELECT * FROM knowledge_documents')
 marker=case.s.execute('SELECT observed_at,inclusive FROM knowledge_source_revocations WHERE source_id=?',(canonical(case),)).fetchone();assert tuple(marker)==(NOW,1)
 assert e2.intelligence.sources.refresh(NOW)=={canonical(case):'blocked'}
 case.clock[0]+=1;case.values['source']=replace(case.values['source'],version=2,observed_at=case.clock[0],documents=(Document('document','Current synthetic title','ALPHA synthetic v2 evidence'),))
 query(case,e2,2)
 evidence=case.model.contexts[-1]['knowledge']['evidence'];assert evidence and all('v1' not in row['text'] for row in evidence)
 assert case.s.rows('SELECT observed_at,permission_expires_at FROM knowledge_documents')==[{'observed_at':NOW+1,'permission_expires_at':NOW+121}]
 assert old==[{'observed_at':NOW,'permission_expires_at':NOW+120}]
 assert case.s.execute('SELECT version FROM trusted_source_versions').fetchone()[0]==2


@pytest.mark.parametrize('failure',['revoke','exception','malformed','rollback'])
def test_actual_refresh_withdrawal_blocks_already_queued_protected_output(case,failure):
 e=case.engine();row=query(case,e);assert row['state']=='pending'
 case.clock[0]+=1
 if failure=='revoke':case.r.revoke('source')
 elif failure=='exception':case.values['source']=RuntimeError('PRIVATE_PROVIDER_SECRET')
 elif failure=='malformed':case.values['source']={'audience':['frost'],'text':'PRIVATE_PROVIDER_SECRET'}
 else:
  case.values['source']=replace(case.values['source'],version=2,observed_at=case.clock[0]);e.intelligence.sources.refresh(case.clock[0]);case.clock[0]+=1
  case.values['source']=replace(case.values['source'],version=1,observed_at=case.clock[0])
 assert e.intelligence.sources.refresh(case.clock[0])=={canonical(case):'blocked'}
 assert not delivery_permitted(case.s,e.b,'test_operator',row['id'],case.clock[0],case.c)
 Dispatcher(case.s,case.c,case.api,lambda:case.clock[0]).flush()
 assert case.api.calls==[] and case.s.execute('SELECT state FROM outbox WHERE id=?',(row['id'],)).fetchone()[0]=='blocked'
 assert not case.s.rows('SELECT * FROM knowledge_documents')


@pytest.mark.parametrize('restart',['default','subset'])
def test_restart_omitted_sources_all_owned_retired_other_business_preserved(case,restart):
 second=case.binding('second');e=case.engine(bindings=(case.spec,second));query(case,e)
 # Separate actual Engine/registry for another business admits its own source.
 other=config('OTHER');other_model=Model();other_registry=TrustedProviders(business='OTHER',members=frozenset({reader_key('OTHER','test_operator')}),clock=lambda:case.clock[0])
 other_registry.register_provider('provider',lambda req,key:ProviderSnapshot('provider','foreign',1,NOW,(Document('doc','Foreign','ALPHA foreign'),)),credential=object())
 other_spec=SourceSpec('provider','foreign',frozenset({reader_key('OTHER','test_operator')}),1,120,True);other_registry.register_source(other_spec)
 other_engine=Engine(case.s,other,other_model,lambda:case.clock[0],trusted_registry=other_registry,trusted_sources=(other_spec,));other_engine.intelligence.sources.refresh(NOW)
 foreign_rows=case.s.rows("SELECT * FROM knowledge_documents WHERE source_id='trusted:OTHER:provider:foreign'");foreign_versions=case.s.rows("SELECT * FROM trusted_source_versions WHERE business='OTHER'")
 if restart=='default':Engine(case.s,case.c,case.model,lambda:case.clock[0])
 else:case.engine(bindings=(case.spec,))
 assert case.s.rows('SELECT * FROM knowledge_documents')==foreign_rows
 assert case.s.rows("SELECT * FROM trusted_source_versions WHERE business='OTHER'")==foreign_versions
 assert len(case.s.rows('SELECT * FROM trusted_source_versions WHERE business=?',(case.c['business'],)))==2
 assert all(row['inclusive']==1 for row in case.s.rows('SELECT inclusive FROM knowledge_source_revocations WHERE source_id IN (?,?)',(canonical(case),canonical(case,second))))


def test_default_positional_engine_shape_and_no_registry_table_creation(case,monkeypatch):
 calls=[];actual=Intelligence.__init__
 def init(self,*a,**kw):calls.append((a,kw));return actual(self,*a,**kw)
 monkeypatch.setattr(Intelligence,'__init__',init)
 e=Engine(case.s,case.c,case.model,lambda:case.clock[0]);assert e.intelligence and calls[0][1]=={}
 assert not case.s.execute("SELECT 1 FROM sqlite_master WHERE name='trusted_source_versions'").fetchone() and not case.calls
 row=query(case,e);assert row and not case.model.contexts[-1]['knowledge']['evidence']


@pytest.mark.parametrize('constructor',['engine','intelligence'])
@pytest.mark.parametrize('bad',['registry_missing','empty','list','tuple_subclass','spec_subclass','wrong_registry','business','unregistered','policy','outsider','duplicate','disabled','count','combined_count','mutated_spec'])
def test_invalid_injection_before_all_sql_init_clock_provider_model(case,monkeypatch,constructor,bad):
 reg=case.r;bindings=(case.spec,);cfg=copy.deepcopy(case.c)
 if bad=='registry_missing':reg=None
 elif bad=='empty':bindings=()
 elif bad=='list':bindings=[case.spec]
 elif bad=='tuple_subclass':
  class T(tuple):pass
  bindings=T(bindings)
 elif bad=='spec_subclass':
  class S(SourceSpec):pass
  bindings=(S('provider','source',case.spec.audience,1,120,True),)
 elif bad=='wrong_registry':reg=object()
 elif bad=='business':cfg['business']='OTHER'
 elif bad=='unregistered':bindings=(replace(case.spec,source_id='unknown'),)
 elif bad=='policy':bindings=(replace(case.spec,ttl_seconds=121),)
 elif bad=='outsider':
  extra=reader_key(case.c['business'],'not-member');case.r._members|={extra};bindings=(case.binding('outsider',audience=frozenset({extra})),)
 elif bad=='duplicate':bindings=(case.spec,case.spec)
 elif bad=='disabled':cfg['intelligence']['enabled']=False
 elif bad=='count':bindings=tuple(case.binding('s'+str(i)) for i in range(17))
 elif bad=='combined_count':cfg['intelligence']['sources']=[{'id':str(i),'readers':['test_operator']} for i in range(16)]
 elif bad=='mutated_spec':object.__setattr__(case.spec,'ttl_seconds',True)
 before=case.s.db.serialize()
 def fail(*a,**kw):pytest.fail('invalid injection had initialization/SQL/clock/model effect')
 import goldway.intelligence as intel
 monkeypatch.setattr(case.s,'execute',fail);monkeypatch.setattr(case.s,'rows',fail);monkeypatch.setattr(case.s,'transaction',fail)
 case.s.db.set_authorizer(fail)
 monkeypatch.setattr(intel.DialogueMemory,'__init__',fail);monkeypatch.setattr(intel.KnowledgeIndex,'__init__',fail);monkeypatch.setattr(case.model,'propose',fail);case.r._clock=fail
 with pytest.raises(ValueError,match='^trusted_injection_invalid$'):
  if constructor=='engine':Engine(case.s,cfg,case.model,fail,trusted_registry=reg,trusted_sources=bindings)
  else:Intelligence(case.s,cfg,trusted_registry=reg,trusted_sources=bindings)
 case.s.db.set_authorizer(None)
 assert case.s.db.serialize()==before and not case.calls


@pytest.mark.parametrize('bad',['oversize','blob','wrong_business_prefix','grammar','over_cap'])
def test_owned_cleanup_metadata_refuses_before_any_partial_revocation(case,monkeypatch,bad):
 e=case.engine();query(case,e);source=canonical(case)
 if bad=='over_cap':
  for i in range(64):case.s.execute('INSERT INTO trusted_source_versions VALUES(?,?,?,?,?)',(case.c['business'],'trusted:SYNTHETIC:provider:extra'+str(i),1,NOW,'x'))
 else:
  value={'oversize':'x'*203,'blob':b'trusted:SYNTHETIC:provider:blob','wrong_business_prefix':'trusted:OTHER:provider:wrong','grammar':'trusted:SYNTHETIC:provider:bad:part'}[bad]
  case.s.execute('INSERT INTO trusted_source_versions VALUES(?,?,?,?,?)',(case.c['business'],value,1,NOW,'x'))
 before=case.s.db.serialize();calls=[];actual=e.intelligence.index.revoke_source
 import goldway.intelligence as intel
 def fail(*a,**kw):calls.append(a);pytest.fail('metadata failure partially revoked')
 monkeypatch.setattr(intel.KnowledgeIndex,'revoke_source',fail)
 with pytest.raises(ValueError,match='^trusted_initialization_unavailable$'):case.engine()
 assert before==case.s.db.serialize() and not calls


def test_metadata_exact_202_ascii_limit_supported_and_no_body_scan_before_cleanup(case):
 business='B'*64;provider='P'*64;identifier='S'*64;cfg=config(business)
 r=TrustedProviders(business=business,members=frozenset({reader_key(business,'test_operator')}),clock=lambda:NOW)
 r.register_provider(provider,lambda req,key:ProviderSnapshot(provider,identifier,1,NOW,(Document('d','Title','ALPHA maximum labels'),)),credential=object())
 spec=SourceSpec(provider,identifier,frozenset({reader_key(business,'test_operator')}),1,120,True);r.register_source(spec)
 e=Engine(case.s,cfg,case.model,lambda:NOW,trusted_registry=r,trusted_sources=(spec,));e.intelligence.sources.refresh(NOW)
 source='trusted:'+business+':'+provider+':'+identifier;assert len(source)==202
 trace=[];case.s.db.set_trace_callback(trace.append)
 Engine(case.s,cfg,case.model,lambda:NOW)
 assert not case.s.execute('SELECT 1 FROM knowledge_documents WHERE source_id=?',(source,)).fetchone()
 ownership=next(q for q in trace if 'FROM trusted_source_versions WHERE business=' in q)
 assert 'LIMIT 65' in ownership and 'typeof(source_id)' in ownership and '202' in ownership
 assert not any('text' in q.lower() and 'knowledge_' in q.lower() and q.startswith('SELECT') for q in trace)


def test_shared_metadata_preflight_collision_and_boundaries_no_callbacks(case):
 with pytest.raises(BridgeError,match='^trusted_injection_invalid$'):
  TrustedSourceBridge.validate_bindings(case.r,(case.spec,),business=case.c['business'],members=set(case.spec.audience),existing_source_ids=(canonical(case),))
 assert not case.calls
 assert TrustedSourceBridge.validate_bindings(case.r,(case.spec,),business=case.c['business'],members=set(case.spec.audience))==(canonical(case),)


def test_max_64_owned_cleanup_uses_only_current_business_bounded_metadata(case):
 e=case.engine();query(case,e)
 for i in range(63):case.s.execute('INSERT INTO trusted_source_versions VALUES(?,?,?,?,?)',(case.c['business'],'trusted:SYNTHETIC:provider:extra'+str(i),1,NOW,'metadata'))
 for i in range(70):case.s.execute('INSERT INTO trusted_source_versions VALUES(?,?,?,?,?)',('OTHER','trusted:OTHER:provider:extra'+str(i),1,NOW,'metadata'))
 before=case.s.rows("SELECT * FROM trusted_source_versions WHERE business='OTHER'")
 Engine(case.s,case.c,case.model,lambda:NOW)
 assert not case.s.rows('SELECT * FROM knowledge_documents')
 assert len(case.s.rows('SELECT * FROM trusted_source_versions WHERE business=?',(case.c['business'],)))==64
 assert case.s.rows("SELECT * FROM trusted_source_versions WHERE business='OTHER'")==before
 assert len(case.s.rows("SELECT * FROM knowledge_source_revocations WHERE source_id LIKE 'trusted:SYNTHETIC:%'"))==64


def test_default_disabled_intelligence_still_positional_no_injection(case):
 cfg=copy.deepcopy(case.c);cfg['intelligence']['enabled']=False
 e=Engine(case.s,cfg,case.model,lambda:NOW)
 assert e.intelligence is None and not case.calls
 assert not case.s.execute("SELECT 1 FROM sqlite_master WHERE name='trusted_source_versions'").fetchone()


def test_source_health_still_projects_actual_optional_bridge_current_status(case):
 e=case.engine();query(case,e)
 health=case.model.contexts[-1].get('source_health')
 assert health is not None and 'ok' in json.dumps(health) and canonical(case) not in json.dumps(health)
