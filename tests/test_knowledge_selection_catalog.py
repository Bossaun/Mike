"""NEW real Engine/index/catalog/I100/Dispatcher synthetic composition only."""
import copy,hashlib,json,sqlite3
from dataclasses import replace
from types import SimpleNamespace
import pytest
from goldway.engine import Engine
from goldway.store import Store
from goldway.telegram import Dispatcher,Updates
from goldway.intelligence import reader_key
from goldway.trusted_provider import TrustedProviders,SourceSpec,ProviderSnapshot,Document
from goldway import knowledge_selection_catalog as catalog
from goldway import selected_knowledge_command as selected

NOW=1792174600
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
FORM=catalog.DOCUMENTS+'\nИсточник: регистрация manual'

class NoModel:
 def propose(self,*a,**k):pytest.fail('model forbidden')

class API:
 def __init__(self):self.sent=[];self.callback=None;self.lookups=[]
 def call(self,method,**params):
  if method=='getBusinessConnection':
   self.lookups.append(params)
   if self.callback:self.callback()
   return dict(user=dict(id=99),is_enabled=True,rights=dict(can_reply=True))
  assert method=='sendMessage';self.sent.append(params);return dict(message_id=100+len(self.sent))

def config():
 return dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name=x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[dict(id='manual',kind='http',url='https://example.invalid/manual',readers=['test_operator'])]))

def make(path=':memory:',cfg=None,registry=None,bindings=()):
 s=Store(path);clock=[NOW];cfg=config() if cfg is None else cfg
 e=Engine(s,cfg,NoModel(),lambda:clock[0],trusted_registry=registry,trusted_sources=bindings)
 def forbidden(*a,**k):pytest.fail('context/refresh forbidden')
 e._context=forbidden;e.intelligence.sources.refresh=forbidden
 return SimpleNamespace(s=s,e=e,c=cfg,clock=clock,api=API())

@pytest.fixture
def c(monkeypatch):
 c=make()
 import socket,httpx
 monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('network forbidden'))
 monkeypatch.setattr(httpx,'Client',lambda *a,**k:pytest.fail('provider forbidden'))
 yield c
 c.s.db.set_authorizer(None);c.s.db.close()

def doc(c,name='guide',**kw):
 data=dict(source_id=c.e.intelligence.sources.specs[0]['id'],document_id=name,title='Shipping guide',url='https://example.invalid/guide',text='Shipping takes three days. SOURCE_BODY_SECRET',readers=[reader_key('GOLDWAY','test_operator')],observed_at=c.clock[0]-1,permission_expires_at=c.clock[0]+1000,confirmed=True,updated_at=None,facts=[]);data.update(kw);c.e.intelligence.index.upsert(data)
 key=hashlib.sha256(json.dumps([data['source_id'],data['document_id']],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
 return key,tuple(r['id'] for r in c.s.rows('SELECT id FROM knowledge_chunks WHERE document_key=? ORDER BY id',(key,)))

def ready(c,guard=True):
 c.s.execute('INSERT OR REPLACE INTO chats(business,recipient,bot_chat,business_chat,connection,last_inbound) VALUES(?,?,?,?,?,?)',('GOLDWAY','test_operator','1','1','conn',c.clock[0]))
 return Dispatcher(c.s,c.c,c.api,lambda:c.clock[0],selected_knowledge_guard=selected.make_delivery_guard(c.e) if guard else None)

def ask(c,text=FORM,event='request',sender='test_operator',channel='bot',source=None):
 uid=c.c['employees'][sender]['telegram_user_id']
 return c.e.reply(event,sender,text,source_id=source or (f'telegram:{uid}:10' if channel=='bot' else f'business:conn:{uid}:10'),channel=channel,message_at=c.clock[0])

def update(c,text=FORM,channel='bot',number=10):
 u=Updates(c.e,c.api,100)
 if channel=='business':u.handle(dict(update_id=number-1,business_connection=dict(id='conn',user=dict(id=99),is_enabled=True,rights=dict(can_reply=True))))
 msg=dict(message_id=number,date=c.clock[0],chat=dict(id=1,type='private'),**{'from':dict(id=1,is_bot=False)},text=text)
 if channel=='business':msg['business_connection_id']='conn'
 u.handle(dict(update_id=number,**{('business_message' if channel=='business' else 'message'):msg}))

def outputs(c):return c.s.rows("SELECT * FROM outbox WHERE dedupe LIKE 'selected-knowledge-preview:catalog:%' ORDER BY id")
def receipt(c):return json.loads(c.s.execute('SELECT value FROM runtime WHERE key=?',(catalog._key('GOLDWAY',outputs(c)[0]['id']),)).fetchone()[0])

def trusted():
 members=frozenset(reader_key('GOLDWAY',m) for m in MEMBERS);r=TrustedProviders(business='GOLDWAY',members=members,clock=lambda:NOW);calls=[]
 def provider(request,key):calls.append(request);return ProviderSnapshot('synthetic','manual',1,NOW,(Document('guide','Trusted guide','Shipping is declared here.'),))
 r.register_provider('synthetic',provider,credential=object());binding=SourceSpec('synthetic','manual',frozenset({reader_key('GOLDWAY','test_operator')}),1,300,False);r.register_source(binding)
 c=make(registry=r,bindings=(binding,));assert c.e.intelligence.sources.trusted_bridge.refresh(NOW)=={'trusted:GOLDWAY:synthetic:manual':'ok'}
 c.registry=r;c.calls=calls;c.binding=binding
 r.refresh=lambda *a,**k:pytest.fail('refresh forbidden');r.documents=lambda *a,**k:pytest.fail('body projection forbidden')
 return c

def no_bodies(c):
 reads=[]
 def auth(action,table,col,*rest):
  if action==sqlite3.SQLITE_READ:
   reads.append((table,col))
   if (table=='knowledge_chunks' and col=='text') or table=='knowledge_facts':return sqlite3.SQLITE_DENY
  return sqlite3.SQLITE_OK
 c.s.db.set_authorizer(auth);return reads

@pytest.mark.parametrize('channel',['bot','business'])
def test_actual_update_catalog_dispatch_copy_pair_actual_i100(c,channel):
 key,refs=doc(c);d=ready(c);reads=no_bodies(c);update(c,channel=channel)
 out=outputs(c)[0];assert out['task_id'] is None and out['preferred_transport']==channel
 assert 'Shipping guide' in out['text'] and 'Источник: регистрация manual\nДокумент: guide' in out['text']
 assert key not in out['text'] and refs[0] not in out['text'] and 'SOURCE_BODY_SECRET' not in out['text']
 assert receipt(c)['refs']==list(refs);d.flush();assert len(c.api.sent)==1 and outputs(c)[0]['state']=='sent'
 assert ('knowledge_chunks','text') not in reads
 c.s.db.set_authorizer(None)
 pair='Источник: регистрация manual\nДокумент: guide'
 update(c,selected.HEADER+'\n'+pair+'\nЗапрос: shipping',channel,11);d.flush()
 assert len(c.api.sent)==2 and 'Shipping takes three days' in c.api.sent[-1]['text']

@pytest.mark.parametrize('mode',[catalog.SOURCES,FORM])
def test_empty_current_index_is_not_archive_or_company_absence(c,mode):
 d=ready(c);no_bodies(c);ask(c,mode);d.flush();body=c.api.sent[0]['text']
 assert 'Сейчас нет доступных' in body and 'не ' in body and receipt(c)['refs']==[]


def test_source_labels_only_registered_readable_no_title_transfer(c):
 doc(c,title='PRIVATE_TITLE');doc(c,'hidden',source_id='foreign',title='FOREIGN_TITLE')
 d=ready(c);queries=[];actual=c.s.execute
 def execute(sql,args=()):
  queries.append(sql);return actual(sql,args)
 c.s.execute=execute;ask(c,catalog.SOURCES);d.flush();body=c.api.sent[0]['text']
 assert 'Источник: регистрация manual' in body and 'PRIVATE_TITLE' not in body and 'FOREIGN_TITLE' not in body
 assert not any('c.text' in q or ('knowledge_facts' in q and q.lstrip().upper().startswith('SELECT')) for q in queries)
 assert all('ds.title END' not in q for q in queries)


def test_twelve_plus_overflow_protects_thirteenth_and_honest_partial(c):
 allrefs=[]
 for i in range(15):allrefs+=list(doc(c,'d'+str(i).zfill(2),title='Same title')[1])
 ready(c);ask(c);r=outputs(c)[0];assert r['text'].count('Документ: ')==12 and 'список неполный' in r['text']
 assert len(receipt(c)['refs'])==13 and receipt(c)['refs']==sorted(allrefs[:13])
 assert 'd12' not in r['text'] and 'd11' in r['text']


def test_duplicate_titles_keep_distinct_exact_ids_and_escape_literals(c):
 doc(c,'first',title='<b>same & title</b>');doc(c,'second',title='<b>same & title</b>')
 ready(c);ask(c);body=outputs(c)[0]['text'];assert body.count('&lt;b&gt;same &amp; title&lt;/b&gt;')==2
 assert 'Документ: first' in body and 'Документ: second' in body


def test_long_complete_pairs_reduce_with_partial_honesty_not_id_truncation(c):
 ids=[]
 for i in range(12):
  name=str(i)+'x'*250;ids.append(name);doc(c,name,title='я'*300)
 ready(c);ask(c);body=outputs(c)[0]['text'];assert len(body.encode('utf-16-le'))//2<=3500 and len(body.encode())<=8000
 assert 0<body.count('Документ: ')<12 and 'список неполный' in body and 'название сокращено' in body
 for name in ids:
  assert (name in body)==('Документ: '+name in body)

@pytest.mark.parametrize('kind',['grant','expiry','watermark','observation','confirmed','document_id','title','url','anchor'])
def test_current_index_metadata_drift_cancels(c,kind):
 key,refs=doc(c);d=ready(c);ask(c)
 if kind=='grant':c.s.execute('DELETE FROM knowledge_readers')
 elif kind=='expiry':c.clock[0]+=1001
 elif kind=='watermark':c.s.execute('INSERT INTO knowledge_source_revocations VALUES(?,?,?)',(c.e.intelligence.sources.specs[0]['id'],NOW,1))
 elif kind=='observation':c.s.execute('UPDATE knowledge_document_observations SET observed_at=observed_at+1')
 elif kind=='anchor':c.s.execute('DELETE FROM knowledge_chunks')
 else:c.s.execute('UPDATE knowledge_documents SET '+kind+'=?',({'confirmed':0,'document_id':'other','title':'Shipping GUIDE','url':'https://example.invalid/other'}[kind],))
 d.flush();assert not c.api.sent and outputs(c)[0]['state']=='cancelled'

@pytest.mark.parametrize('field,value',[('title','x'*1201),('url','x'*8193),('document_id','x'*1025),('title',sqlite3.Binary(b'blob')),('observed_at','bad'),('confirmed',2)])
def test_malformed_sql_metadata_whole_fixed_notice_no_value_transfer(c,field,value):
 doc(c);c.s.execute('UPDATE knowledge_documents SET '+field+'=?',(value,));ready(c);no_bodies(c);ask(c)
 assert not outputs(c)
 assert c.s.execute('SELECT text FROM outbox').fetchone()[0]==catalog.NOTICE

@pytest.mark.parametrize('text',[FORM+'\nextra',catalog.SOURCES+'\n',catalog.DOCUMENTS+'\nИсточник: manual',catalog.DOCUMENTS+'\nИсточник: регистрация unknown',catalog.DOCUMENTS+'\nИсточник: регистрация manual\nИсточник: регистрация manual'])
def test_exact_grammar_and_unknown_registration_refuse_no_model(c,text):
 doc(c);ready(c);ask(c,text);assert not outputs(c)
 assert c.s.execute('SELECT text FROM outbox').fetchone()[0]==catalog.NOTICE

@pytest.mark.parametrize('sender,source',[('frost',None),('test_operator','telegram:2:10'),('test_operator','telegram-group:-1:10')])
def test_staff_or_nonown_scope_no_metadata(c,monkeypatch,sender,source):
 doc(c);ready(c);monkeypatch.setattr(catalog,'_slice',lambda *a,**k:pytest.fail('unauthorized metadata read'))
 ask(c,sender=sender,source=source);assert not outputs(c) and not c.api.sent

@pytest.mark.parametrize('kind',['config','intel','index','connection','clock_method','mapping'])
def test_captured_host_drift_denies_before_catalog_sql(c,monkeypatch,kind):
 doc(c);d=ready(c);ask(c)
 if kind=='config':c.e.c=copy.deepcopy(c.e.c)
 elif kind=='intel':c.e.intelligence=copy.copy(c.e.intelligence)
 elif kind=='index':c.e.intelligence.index=copy.copy(c.e.intelligence.index)
 elif kind=='connection':c.s.db=sqlite3.connect(':memory:',isolation_level=None)
 elif kind=='clock_method':c.e.now=lambda:NOW
 else:c.e.intelligence.sources.specs=[]
 monkeypatch.setattr(catalog,'_slice',lambda *a,**k:pytest.fail('drift metadata read'))
 assert not d.selected_knowledge_guard(outputs(c)[0]) if kind!='connection' else not d.selected_knowledge_guard(dict(id=1,dedupe=catalog.PREFIX+'x'))

@pytest.mark.parametrize('where',['before','route','preclaim'])
def test_immediate_trusted_revoke_before_document_metadata_without_refresh(where,monkeypatch):
 c=trusted();d=ready(c);ask(c,FORM.replace('регистрация manual','провайдер synthetic/manual'),channel='business')
 actual=catalog._slice
 if where=='before':c.registry.revoke('manual')
 elif where=='route':c.api.callback=lambda:c.registry.revoke('manual')
 else:
  real=d.selected_knowledge_guard
  def guard(row,route=None,transport=None):
   if route is not None:c.registry.revoke('manual')
   return real(row,route,transport)
  d.selected_knowledge_guard=guard
 calls=[]
 def sliced(*a,**k):calls.append(1);return actual(*a,**k)
 monkeypatch.setattr(catalog,'_slice',sliced);d.flush()
 assert not c.api.sent and outputs(c)[0]['state']=='cancelled' and len(c.calls)==1
 if where=='before':assert not calls
 c.s.db.close()

@pytest.mark.parametrize('kind',['expiry','version','fingerprint','business','spec'])
def test_trusted_public_metadata_current_index_coherence(kind):
 c=trusted();d=ready(c);ask(c,FORM.replace('регистрация manual','провайдер synthetic/manual'))
 if kind=='expiry':c.clock[0]+=300
 elif kind=='version':c.s.execute('UPDATE trusted_source_versions SET version=2')
 elif kind=='fingerprint':c.s.execute('UPDATE trusted_source_versions SET fingerprint=?',('0'*64,))
 elif kind=='business':c.registry.business='OTHER'
 else:c.e.intelligence.sources.trusted_bridge.bindings=(replace(c.binding,confirmed=True),)
 d.flush();assert not c.api.sent and outputs(c)[0]['state']=='cancelled' and len(c.calls)==1
 c.s.db.close()

@pytest.mark.parametrize('where',['queue','protect'])
@pytest.mark.parametrize('mutation',['title','acl'])
def test_queue_protect_callback_mutation_rolls_back_new_output_receipt(c,monkeypatch,where,mutation):
 doc(c);ready(c);target=c.s if where=='queue' else c.e.intelligence;name='queue' if where=='queue' else 'protect_outbox';actual=getattr(target,name);done=[]
 def callback(*a,**k):
  result=actual(*a,**k)
  if not done:
   done.append(1)
   c.s.execute('DELETE FROM knowledge_readers' if mutation=='acl' else "UPDATE knowledge_documents SET title='Changed title'")
  return result
 monkeypatch.setattr(target,name,callback);ask(c)
 assert not outputs(c) and not c.s.rows('SELECT * FROM intelligence_outbox_acl') and not c.s.rows('SELECT * FROM runtime WHERE key LIKE ?',(catalog.PREFIX+'%',))

@pytest.mark.parametrize('where',['clock','source_metadata'])
def test_external_callback_before_final_metadata_data_phase_not_stale(where,monkeypatch):
 c=trusted();calls=[];data_pass=[]
 if where=='clock':
  actual_snapshot=catalog._snapshot
  def snapshot(*a,**kw):
   result=actual_snapshot(*a,**kw);data_pass.append(1);return result
  monkeypatch.setattr(catalog,'_snapshot',snapshot)
  def clock():
   calls.append(1)
   if len(data_pass)==1:c.s.execute("UPDATE knowledge_documents SET title='Changed title'")
   return NOW
  c.e.now=clock
 else:
  actual=TrustedProviders.source_metadata
  def metadata(self,*a,**kw):
   result=actual(self,*a,**kw);calls.append(1)
   if len(calls)==2:c.s.execute("UPDATE knowledge_documents SET title='Changed title'")
   return result
  monkeypatch.setattr(TrustedProviders,'source_metadata',metadata)
 ready(c);ask(c,FORM.replace('регистрация manual','провайдер synthetic/manual'))
 assert not outputs(c)
 c.s.db.close()

@pytest.mark.parametrize('kind',['body','receipt','command','refs','later_turn','route','generic_acl'])
def test_pending_tamper_barrier_route_generic_acl_independent(c,kind):
 doc(c);d=ready(c);ask(c,channel='business')
 if kind=='body':c.s.execute("UPDATE outbox SET text='tampered'")
 elif kind=='receipt':c.s.execute('UPDATE runtime SET value=? WHERE key=?',('{}',catalog._key('GOLDWAY',outputs(c)[0]['id'])))
 elif kind=='command':c.s.execute("UPDATE events SET payload=json_set(payload,'$.text',?) WHERE id='request'",(catalog.SOURCES,))
 elif kind=='refs':c.s.execute("UPDATE intelligence_outbox_acl SET evidence_ids='[]'")
 elif kind=='later_turn':ask(c,catalog.SOURCES,event='later',channel='business')
 elif kind=='route':c.api.callback=lambda:c.s.execute("UPDATE chats SET connection='other'")
 else:
  real=d.selected_knowledge_guard
  def guarded(row,route=None,transport=None):
   answer=real(row,route,transport)
   if route is not None:c.s.execute('DELETE FROM knowledge_readers')
   return answer
  d.selected_knowledge_guard=guarded
 d.flush();assert outputs(c)[0]['state']=='cancelled'
 if kind!='later_turn':assert not c.api.sent

@pytest.mark.parametrize('state',['sending','unknown','sent'])
def test_unknown_sending_sent_never_rebuilt_or_replayed(c,state):
 doc(c);d=ready(c);ask(c);c.s.execute('UPDATE outbox SET state=?',(state,));before=c.s.db.total_changes;d.flush()
 assert not c.api.sent and c.s.db.total_changes==before and outputs(c)[0]['state']==state


def test_guard_absent_default_deny_and_ordinary_i100_still_works(c):
 doc(c);d=ready(c,False);ask(c);d.flush();assert not c.api.sent and outputs(c)[0]['state']=='cancelled'
 d=ready(c);ask(c,selected.HEADER+'\nИсточник: регистрация manual\nДокумент: guide\nЗапрос: shipping','ordinary');d.flush()
 assert len(c.api.sent)==1 and 'Shipping takes' in c.api.sent[0]['text']


def test_replay_dedupe_header_template_and_no_intel_factory(c):
 doc(c);d=ready(c);ask(c);assert not ask(c);assert len(outputs(c))==1
 assert selected.make_delivery_guard(SimpleNamespace(intelligence=None))({'dedupe':'unrelated'}) is False
 ask(c,catalog.DOCUMENTS,event='template');assert c.s.execute("SELECT text FROM outbox WHERE dedupe LIKE 'knowledge-catalog-template:%'").fetchone()[0]==catalog.TEMPLATE


def test_actual_file_close_reopen_old_pending_cancels_new_observation_new_catalog(tmp_path):
 path=str(tmp_path/'synthetic.sqlite');c=make(path);doc(c);ready(c);ask(c);c.s.db.close()
 fresh=make(path);d=ready(fresh);d.flush();assert outputs(fresh)[0]['state']=='cancelled' and not fresh.api.sent
 fresh.clock[0]+=2;doc(fresh);ask(fresh,event='fresh');d.flush();assert outputs(fresh)[-1]['state']=='sent' and len(fresh.api.sent)==1
 fresh.s.db.close()


def test_one_cumulative_reader_instrumented_full_sixteen_source_and_doc_paths(c,monkeypatch):
 seen=[];actual=catalog.Reader
 class Reader(actual):
  def __init__(self,*a):super().__init__(*a);seen.append(self)
 monkeypatch.setattr(catalog,'Reader',Reader)
 for i in range(15):
  c.c['intelligence']['sources'].append(dict(id='s'+str(i),kind='http',url='https://example.invalid/source',readers=['test_operator']))
  c.e.intelligence.sources.specs.append(dict(id=c.e.intelligence.prefix+'s'+str(i),kind='http',url='https://example.invalid/source',readers=[reader_key('GOLDWAY','test_operator')]))
 for spec in c.e.intelligence.sources.specs:doc(c,spec['id'].rsplit(':',1)[-1],source_id=spec['id'])
 d=ready(c);ask(c,catalog.SOURCES);d.flush();assert len(seen)==3 and len(c.api.sent)==1
 assert all(r.queries<=128 and r.rows<=512 and r.bytes<=4*1024*1024 for r in seen)
 assert seen[0].queries>60

@pytest.mark.parametrize('which',['queries','rows','bytes'])
def test_budget_refusal_whole_output(c,monkeypatch,which):
 doc(c);ready(c);actual=catalog.Reader
 class Reader(actual):
  def __init__(self,*a):super().__init__(*a);setattr(self,which,{'queries':128,'rows':512,'bytes':4*1024*1024}[which])
 monkeypatch.setattr(catalog,'Reader',Reader);ask(c);assert not outputs(c)

@pytest.mark.parametrize('where',['handler','guard'])
def test_registry_withdrawn_before_any_document_metadata_statement(where,monkeypatch):
 c=trusted();d=ready(c);form=FORM.replace('регистрация manual','провайдер synthetic/manual')
 if where=='guard':ask(c,form)
 c.registry.revoke('manual');statements=[];actual=c.s.execute
 def execute(sql,args=()):
  if 'knowledge_documents' in sql and 'd.title' in sql:statements.append(sql)
  return actual(sql,args)
 monkeypatch.setattr(c.s,'execute',execute)
 if where=='handler':ask(c,form)
 else:d.flush()
 assert not statements and not c.api.sent
 assert not outputs(c) if where=='handler' else outputs(c)[0]['state']=='cancelled'
 c.s.db.close()


def test_guard_rebuilds_before_no_new_saved_outbox_text_projection(c,monkeypatch):
 doc(c);d=ready(c);ask(c);row=dict(outputs(c)[0]);actual=c.s.execute;queries=[]
 def execute(sql,args=()):
  if 'FROM outbox' in sql:queries.append(sql)
  return actual(sql,args)
 monkeypatch.setattr(c.s,'execute',execute)
 assert d.selected_knowledge_guard(row)
 assert queries and all('body_matches' in sql and 'THEN text END AS text' not in sql and 'SELECT *' not in sql for sql in queries)

@pytest.mark.parametrize('field,value',[('document_id','a\nb'),('document_id','a\u2028b'),('url',sqlite3.Binary(b'blob')),('title',''),('url','')])
def test_unusable_metadata_pair_never_dropped_or_silently_changed(c,field,value):
 doc(c);c.s.execute('UPDATE knowledge_documents SET '+field+'=?',(value,));ready(c);ask(c)
 assert not outputs(c) and c.s.execute('SELECT text FROM outbox').fetchone()[0]==catalog.NOTICE


def test_unrelated_oversize_documents_and_private_bodies_never_transfer(c):
 doc(c);doc(c,'foreign',source_id='foreign',title='x'*300,text='x'*50000)
 c.s.execute('UPDATE knowledge_documents SET title=? WHERE source_id=?',('x'*1000000,'foreign'))
 d=ready(c);reads=no_bodies(c);ask(c);d.flush();assert len(c.api.sent)==1 and len(c.api.sent[0]['text'])<1000
 assert ('knowledge_chunks','text') not in reads

@pytest.mark.parametrize('stage',['queue','protect','receipt'])
def test_actual_storage_failure_rolls_back_whole_new_catalog(c,monkeypatch,stage):
 doc(c);ready(c)
 if stage=='queue':
  actual=c.s.queue
  def queue(*a,**kw):actual(*a,**kw);raise sqlite3.OperationalError('synthetic storage failure')
  monkeypatch.setattr(c.s,'queue',queue)
 elif stage=='protect':
  actual=c.e.intelligence.protect_outbox
  def protect(*a,**kw):actual(*a,**kw);raise sqlite3.OperationalError('synthetic storage failure')
  monkeypatch.setattr(c.e.intelligence,'protect_outbox',protect)
 else:c.s.execute("CREATE TRIGGER synthetic_receipt_failure BEFORE INSERT ON runtime BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END")
 with pytest.raises(sqlite3.IntegrityError if stage=='receipt' else sqlite3.OperationalError,match='synthetic storage failure'):ask(c)
 assert not outputs(c) and not c.s.rows('SELECT * FROM intelligence_outbox_acl') and not c.s.rows('SELECT * FROM runtime')


def test_connection_swap_queue_preserves_unrelated_transaction_and_rolls_original(c,monkeypatch):
 doc(c);ready(c);original=c.s.db;replacement=sqlite3.connect(':memory:',isolation_level=None)
 replacement.execute('CREATE TABLE unrelated(value)');replacement.execute('BEGIN');replacement.execute('INSERT INTO unrelated VALUES(1)');actual=c.s.queue
 def queue(*a,**kw):result=actual(*a,**kw);c.s.db=replacement;return result
 monkeypatch.setattr(c.s,'queue',queue)
 try:
  with pytest.raises(RuntimeError,match='store_transaction_connection_changed'):ask(c)
  assert not original.in_transaction and original.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
  assert replacement.in_transaction and replacement.execute('SELECT COUNT(*) FROM unrelated').fetchone()[0]==1
 finally:c.s.db=original;replacement.rollback();replacement.close()


def test_source_list_omits_current_withdrawn_trusted_registration_no_old_index_fallback():
 c=trusted();doc(c);d=ready(c);c.registry.revoke('manual');ask(c,catalog.SOURCES);d.flush()
 assert len(c.api.sent)==1 and 'регистрация manual' in c.api.sent[0]['text'] and 'провайдер synthetic/manual' not in c.api.sent[0]['text']
 c.s.db.close()


def test_overflow_sentinel_change_cancels_even_unshown_thirteenth(c):
 keys=[]
 for i in range(13):keys.append(doc(c,'d'+str(i).zfill(2))[0])
 d=ready(c);ask(c);assert 'Документ: d12' not in outputs(c)[0]['text']
 c.s.execute('UPDATE knowledge_documents SET title=? WHERE id=?',('Changed sentinel',keys[-1]));d.flush()
 assert not c.api.sent and outputs(c)[0]['state']=='cancelled'
