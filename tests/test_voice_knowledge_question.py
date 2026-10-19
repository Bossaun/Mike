"""Actual bounded index and synthetic admitted voice path; no real provider."""
from dataclasses import replace,FrozenInstanceError
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from io import BytesIO
import ast,copy,hashlib,json,sqlite3,wave
import pytest

from goldway.engine import Engine
from goldway.store import Store
from goldway.intelligence import reader_key
from goldway import knowledge as k
from goldway import voice_knowledge_question as m
from goldway import voice_meeting_bridge as meeting
from goldway import synthetic_wav_bridge as wav_port
from goldway.trusted_provider import TrustedProviders,SourceSpec

BASE=datetime(2026,10,20,12,tzinfo=timezone.utc);NOW=BASE.timestamp()+10
KEY=meeting.voice.SessionKey('GOLDWAY','synthetic-source','synthetic-session')
SPEC=meeting.voice.SessionSpec(KEY,BASE,BASE+timedelta(hours=1));SENDER='test_operator'
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']
BASE_SEARCH="def search(self, query, sender, *, now, limit=6, max_chars=6000, subjects=()) -> dict:\n    now = _time(now, 'now')\n    if not isinstance(query, str):\n        raise ValueError('Query must be text')\n    if type(limit) is not int or limit < 0:\n        raise ValueError('Invalid result limit')\n    if type(max_chars) is not int or max_chars < len(_json(_empty())):\n        raise ValueError('Character budget is smaller than the empty result')\n    if not isinstance(subjects, (list, tuple, set)) or any(not isinstance(s, str) for s in subjects):\n        raise ValueError('Subjects must be explicit text identifiers')\n    limit, max_chars = min(limit, MAX_RESULTS), min(max_chars, MAX_CONTEXT_CHARS)\n    query_terms = set(_tokens(query[:MAX_QUERY_CHARS])[:24])\n    subject_terms = [_normal(s[:160]) for s in list(subjects)[:12] if s.strip()]\n    if not limit or (not query_terms and not subject_terms):\n        return _empty()\n    ids = self.allowed_ids(sender, now=now)\n    if not ids:\n        return _empty()\n    chunks, facts = self._read(sender, now, ids)\n    by_id = {row['id']: row for row in chunks}\n    grouped, document_groups, document_subjects = {}, {}, {}\n    for fact in facts:\n        row = by_id.get(fact['evidence_id'])\n        if row is None:\n            continue\n        document_subjects.setdefault(row['document_key'], set()).add(_normal(fact['subject']))\n        if not row['confirmed']:\n            continue\n        key = (_normal(fact['subject']), _normal(fact['predicate']))\n        item = {name: fact[name] for name in ('subject', 'predicate', 'value', 'quote', 'evidence_id')}\n        item.update({name: row[name] for name in ('title', 'url', 'updated_at', 'observed_at',\n                                                 'source_id', 'document_id', 'priority')})\n        item.update(confirmed=True, status='confirmed')\n        grouped.setdefault(key, []).append(item)\n        document_groups.setdefault(row['document_key'], set()).add(key)\n    candidates = []\n    for row in chunks:\n        title_terms, body_terms = set(_tokens(row['title'])), set(_tokens(row['text']))\n        subject_match = any(subject in document_subjects.get(row['document_key'], set())\n                            or subject in _normal(row['title']) or subject in _normal(row['text'])\n                            for subject in subject_terms)\n        if subject_terms and not subject_match:\n            continue\n        score = 3 * len(query_terms & title_terms) + len(query_terms & body_terms) + 8 * subject_match\n        if score:\n            candidates.append((score, row))\n    candidates.sort(key=lambda pair: (-pair[0], -pair[1]['confirmed'],\n                    -(pair[1]['priority'] if pair[1]['priority'] is not None else -math.inf),\n                    -(pair[1]['updated_epoch'] if pair[1]['updated_epoch'] is not None else -math.inf),\n                    pair[1]['id']))\n    result, included = _empty(), set()\n    for _, row in candidates:\n        if len(result['evidence']) >= limit:\n            break\n        keys = included | document_groups.get(row['document_key'], set())\n        trial = {'evidence': [*result['evidence'], self._evidence(row)],\n                 'facts': [], 'conflicts': [], 'status': 'ok'}\n        for key in sorted(keys):\n            group = sorted(grouped[key], key=lambda fact: (fact['source_id'], fact['document_id'], fact['value']))\n            trial['facts'].extend(group)\n            if len({fact['value'] for fact in group}) > 1:\n                resolution, selected = self._resolution(group)\n                trial['conflicts'].append({'subject': group[0]['subject'], 'predicate': group[0]['predicate'],\n                    'values': group, 'resolution': resolution, 'selected_value': selected})\n        # Never omit a known conflicting alternative to make a result fit.\n        # If its complete provenance exceeds the budget, skip that evidence.\n        if len(_json(trial)) > max_chars:\n            text = trial['evidence'][-1]['text']\n            trial['evidence'][-1]['truncated'] = True\n            low, high = 0, len(text)\n            while low < high:\n                middle = (low + high + 1) // 2\n                trial['evidence'][-1]['text'] = text[:middle]\n                if len(_json(trial)) <= max_chars:\n                    low = middle\n                else:\n                    high = middle - 1\n            trial['evidence'][-1]['text'] = text[:low]\n        if trial['evidence'][-1]['text'].strip() and len(_json(trial)) <= max_chars:\n            result, included = trial, keys\n    referenced = {item['id'] for item in result['evidence']}\n    referenced.update(fact['evidence_id'] for fact in result['facts'])\n    if referenced and not referenced <= self.allowed_ids(sender, now=now):\n        return _empty()\n    return result\n"
baseline_namespace=dict(vars(k));exec(BASE_SEARCH,baseline_namespace);baseline_search=baseline_namespace['search']


class NoExternal:
    def propose(self,*args,**kwargs):pytest.fail('model forbidden')
    def call(self,*args,**kwargs):pytest.fail('provider forbidden')


def config():
    return dict(business='GOLDWAY',timezone='Europe/Moscow',employees={x:dict(name='Synthetic '+x,telegram_user_id=i+1) for i,x in enumerate(MEMBERS)},admins=[SENDER],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=100),allowed_live_recipients=MEMBERS[:],team_roster_verified=True,recipients_verified=True,live_send=False,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,bot_user_id=100,bot_user_id_verified=True,intelligence=dict(enabled=True,sources=[dict(id='manual',kind='http',url='https://example.invalid/manual',readers=[SENDER])]))


@pytest.fixture
def c(monkeypatch):
    state={'now':NOW,'forbid_clock':False,'permission':meeting.TextReviewPermission(KEY,SENDER,1,BASE,BASE+timedelta(hours=1),True)}
    def clock():
        if state['forbid_clock']:pytest.fail('late external clock call')
        return state['now']
    scoped=frozenset(reader_key('GOLDWAY',name) for name in MEMBERS)
    registry=TrustedProviders(business='GOLDWAY',members=scoped,clock=clock)
    registry.register_provider('synthetic',NoExternal().call,credential=object())
    binding=SourceSpec('synthetic','registered',frozenset([reader_key('GOLDWAY',SENDER)]),1,600,True)
    registry.register_source(binding)
    store=Store(':memory:');engine=Engine(store,config(),NoExternal(),clock,trusted_registry=registry,trusted_sources=(binding,))
    def permission():return state['permission']
    bundles=[];bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=bundles.append,enabled=True)
    def forbidden(*args,**kwargs):pytest.fail('unexpected I/O/constructor/refresh/context/write')
    engine._context=forbidden;engine.intelligence.sources.refresh=forbidden;store.queue=forbidden
    registry.refresh=forbidden;registry.documents=forbidden
    import builtins,socket,subprocess
    for cls in (Store,Engine,k.KnowledgeIndex):monkeypatch.setattr(cls,'__init__',forbidden)
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    value=SimpleNamespace(e=engine,s=store,index=engine.intelligence.index,bridge=bridge,state=state,bundles=bundles,
        permission=permission,registry=registry,binding=binding,clock=clock)
    yield value
    store.db.set_authorizer(None);store.db.close()


def seed(c,document='one',*,text='Лимит — десять.',confirmed=True,priority=None,updated=None,source=None,readers=None,facts=True):
    source=source or c.e.intelligence.sources.specs[0]['id']
    fact=[] if not facts else [dict(subject='отдел',predicate='лимит',value='десять' if 'десять' in text else 'двадцать',quote=text)]
    c.index.upsert(dict(source_id=source,document_id=document,title='Лимит отдела',url='https://example.invalid/doc',text=text,
        readers=[reader_key('GOLDWAY',SENDER)] if readers is None else readers,observed_at=NOW-1,permission_expires_at=NOW+100,
        confirmed=confirmed,priority=priority,updated_at=updated,facts=fact))
    key=hashlib.sha256(k._json([source,document]).encode()).hexdigest()
    return k.SelectedDocument(source,document,key)


def frame(text='Найди в выбранных источниках: лимит',**changes):
    values=dict(id='question',key=KEY,sequence=1,epoch=0,at=BASE+timedelta(seconds=2),evidence_id='question-evidence',segment_id='question',revision=1,start=BASE,end=BASE+timedelta(seconds=1),final=True,text=text,speaker_label=None)
    values.update(changes);return meeting.voice.Segment(**values)


def reference(**changes):
    values=dict(key=KEY,epoch=0,segment_id='question',revision=1);values.update(changes)
    return meeting.FinalSegmentRef(**values)


def search(c,documents,**changes):
    values=dict(query='лимит',reader=reader_key('GOLDWAY',SENDER),now=c.state['now'],documents=documents)
    values.update(changes);return c.index.search_selected(**values)


def consumer(c,documents,**changes):
    values=dict(engine=c.e,bridge=c.bridge,sender=SENDER,documents=documents,enabled=True);values.update(changes)
    return m.VoiceKnowledgeQuestion(**values)


def instrument(monkeypatch,hook=None):
    captured=[];original=k._SelectedBudget.query
    def query(self,sql,params,maximum):
        result=original(self,sql,params,maximum);captured.append((sql,dict(params),result,self.queries,self.rows,self.bytes))
        if hook is not None:hook(sql,params,result)
        return result
    monkeypatch.setattr(k._SelectedBudget,'query',query);return captured


def forbid_writes(c):
    def authorizer(action,*unused):
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_CREATE_TABLE):return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    c.s.db.set_authorizer(authorizer)


def test_actual_wav_final_selected_source_real_ranker_conflict_private_readonly_pack(c,monkeypatch):
    first=seed(c);second=seed(c,'two',text='Лимит — двадцать.')
    seed(c,'foreign',text='UNRELATED_SENTINEL '*10000,source='other-business-source',readers=['other-reader'],facts=False)
    data=BytesIO()
    with wave.open(data,'wb') as stream:
        stream.setparams((1,2,16000,16000,'NONE','not compressed'));stream.writeframes(b'\0'*32000)
    calls=[]
    def callback(request):calls.append(request.clip_hash);return wav_port.TranscriptionResult('Найди в выбранных источниках: лимит',True)
    port=wav_port.SyntheticWavBridge(bridge=c.bridge,transcribe=callback,clock=lambda:datetime.fromtimestamp(c.state['now'],timezone.utc),enabled=True)
    request=wav_port.WavSegmentEnvelope('wav-question',KEY,1,0,BASE+timedelta(seconds=2),'question-evidence','question',1,BASE,BASE+timedelta(seconds=1),None)
    ref=port.accept_wav(request,data.getvalue());logs=instrument(monkeypatch)
    def forbidden(*a,**kw):pytest.fail('selected path used unbounded legacy acquisition')
    monkeypatch.setattr(c.index,'allowed_ids',forbidden);monkeypatch.setattr(c.index,'_read',forbidden)
    before=tuple(c.s.db.iterdump());changes=c.s.db.total_changes;forbid_writes(c)
    answer=consumer(c,(first,second)).read_question(ref,'лимит')
    body=json.loads(answer.knowledge.result_json)
    assert len(calls)==1 and c.s.db.total_changes==changes and c.bundles==[]
    assert body['status']=='ok' and len(body['facts'])==2 and body['conflicts'][0]['resolution']=='unresolved'
    assert {fact['value'] for fact in body['facts']}=={'десять','двадцать'}
    assert answer.intended_reader==SENDER and answer.review_required and not answer.live_authorized and not answer.broadcast_authorized and not answer.delivery_authorized and not answer.capture_authorized
    assert answer.question.evidence_ids==('question-evidence',) and 'question-evidence' not in answer.knowledge.consulted_ids
    assert answer.knowledge.scope=='selected_documents_only' and 'UNRELATED_SENTINEL' not in repr(logs)
    assert all('LIMIT' in sql and set(key for key in params if key.startswith('k'))=={'k0','k1'} for sql,params,*_ in logs)
    assert all(q<=64 and rows<=256 and size<=1024*1024 for _,_,_,q,rows,size in logs)
    with pytest.raises(FrozenInstanceError):answer.intended_reader='other'
    c.s.db.set_authorizer(None);assert tuple(c.s.db.iterdump())==before


def test_ranker_extracted_ast_is_original_block_not_another_algorithm():
    old=ast.parse(BASE_SEARCH).body[0]
    begin=next(i for i,node in enumerate(old.body) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='by_id' for t in node.targets))
    end=next(i for i,node in enumerate(old.body) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='referenced' for t in node.targets))
    import inspect,textwrap
    helper=ast.parse(textwrap.dedent(inspect.getsource(k.KnowledgeIndex._rank))).body[0]
    assert ast.dump(ast.Module(body=old.body[begin:end],type_ignores=[]),include_attributes=False)==ast.dump(ast.Module(body=helper.body[:-1],type_ignores=[]),include_attributes=False)


@pytest.mark.parametrize('kind',['noresult','assumption','ranking','unresolved','priority','date','budget'])
def test_new_legacy_baseline_differential_compatibility_self_contained(c,kind):
    first=seed(c,confirmed=kind!='assumption',priority=1 if kind=='priority' else None,updated='2026-10-19' if kind=='date' else None)
    if kind in ('ranking','unresolved','priority','date','budget'):
        seed(c,'two',text='Лимит — двадцать.',priority=2 if kind=='priority' else None,updated='2026-10-20' if kind=='date' else None)
    query='совсем постороннее' if kind=='noresult' else 'лимит'
    args=dict(now=NOW,max_chars=150 if kind=='budget' else 6000)
    assert c.index.search(query,reader_key('GOLDWAY',SENDER),**args)==baseline_search(c.index,query,reader_key('GOLDWAY',SENDER),**args)


def test_new_legacy_baseline_postload_revocation_compatibility(c,monkeypatch):
    seed(c);actual=c.index._read
    def load(*args,**kwargs):
        result=actual(*args,**kwargs);c.s.execute('DELETE FROM knowledge_readers');return result
    monkeypatch.setattr(c.index,'_read',load)
    result=baseline_search(c.index,'лимит',reader_key('GOLDWAY',SENDER),now=NOW)
    c.s.execute('INSERT INTO knowledge_readers SELECT id,? FROM knowledge_documents',(reader_key('GOLDWAY',SENDER),))
    assert c.index.search('лимит',reader_key('GOLDWAY',SENDER),now=NOW)==result==k._empty()


@pytest.mark.parametrize('kind',['priority','date','unresolved','assumption','noresult','budget'])
def test_selected_actual_fact_conflict_assumption_and_budget_semantics(c,kind):
    docs=[seed(c,confirmed=kind!='assumption',priority=1 if kind=='priority' else None,updated='2026-10-19' if kind=='date' else None)]
    if kind not in ('assumption','noresult'):
        docs.append(seed(c,'two',text='Лимит — двадцать.',priority=2 if kind=='priority' else None,updated='2026-10-20' if kind=='date' else None))
    result=json.loads(search(c,tuple(docs),query='постороннее' if kind=='noresult' else 'лимит',max_chars=150 if kind=='budget' else 6000).result_json)
    if kind=='assumption':assert result['facts']==[] and result['evidence'][0]['status']=='assumption' and not result['evidence'][0]['confirmed']
    elif kind in ('noresult','budget'):assert result==k._empty()
    else:
        assert result['conflicts'][0]['resolution']==('updated_at' if kind=='date' else kind)
        assert len(result['conflicts'][0]['values'])==2


@pytest.mark.parametrize('change',['docs','chunks','facts','bytes','blob','crosslink','missing','reader','observation','revocation','expiry'])
def test_whole_selected_preflight_refuses_before_any_body_rows(c,monkeypatch,change):
    one=seed(c);docs=(one,)
    if change=='docs':docs=tuple(seed(c,str(i)) for i in range(4))
    elif change=='chunks':
        for i in range(16):c.s.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?)',('extra-'+str(i),one.document_key,i+1,'small'))
    elif change=='facts':
        chunk=c.s.execute('SELECT id FROM knowledge_chunks').fetchone()[0]
        for i in range(64):c.s.execute('INSERT INTO knowledge_facts VALUES(?,?,?,?,?,?,?)',(one.document_key,i+1,chunk,'subject','predicate','десять','Лимит — десять.'))
    elif change=='bytes':
        for i in range(3):seed(c,'wide-'+str(i),text='я'*1200,readers=[reader_key('GOLDWAY',SENDER)],facts=False)
        docs=tuple(k.SelectedDocument(row['source_id'],row['document_id'],row['id']) for row in c.s.rows("SELECT * FROM knowledge_documents WHERE document_id LIKE 'wide-%'"))
        for item in docs:
            chunk=c.s.execute('SELECT id,text FROM knowledge_chunks WHERE document_key=? LIMIT 1',(item.document_key,)).fetchone()
            for i in range(12):c.s.execute('INSERT INTO knowledge_facts VALUES(?,?,?,?,?,?,?)',(item.document_key,i,chunk['id'],'я'*160,'я'*160,'я'*1200,'я'*1200))
    elif change=='blob':c.s.execute('UPDATE knowledge_chunks SET text=?',(sqlite3.Binary(b'bad'),))
    elif change=='crosslink':
        foreign=seed(c,'foreign',source='other-source');foreign_id=c.s.execute('SELECT id FROM knowledge_chunks WHERE document_key=?',(foreign.document_key,)).fetchone()[0]
        c.s.execute('UPDATE knowledge_facts SET evidence_id=? WHERE document_key=?',(foreign_id,one.document_key))
    elif change=='missing':c.s.execute('DELETE FROM knowledge_documents')
    elif change=='reader':c.s.execute('DELETE FROM knowledge_readers')
    elif change=='observation':c.s.execute("UPDATE knowledge_document_observations SET source_id='other'")
    elif change=='revocation':c.s.execute('INSERT OR REPLACE INTO knowledge_source_revocations VALUES(?,?,?)',(one.source_id,NOW,1))
    elif change=='expiry':c.s.execute('UPDATE knowledge_documents SET permission_expires_at=?',(NOW,))
    logs=instrument(monkeypatch)
    with pytest.raises(k.SelectedKnowledgeUnavailable):search(c,docs)
    assert all(set(row)<= {'ok','nd','nc','nf','nb'} for _,_,rows,*_ in logs for row in rows)


@pytest.mark.parametrize('change',['chunks','text','facts'])
def test_growth_after_metadata_guard_has_no_unguarded_body_transfer(c,monkeypatch,change):
    one=seed(c);fired=[]
    def hook(sql,params,rows):
        if 'SELECT * FROM gate LIMIT 2' in sql and not fired:
            fired.append(1)
            if change=='chunks':
                for i in range(16):c.s.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?)',('growth-'+str(i),one.document_key,i+1,'GROWTH_SENTINEL'))
            elif change=='text':c.s.execute('UPDATE knowledge_chunks SET text=?',('GROWTH_SENTINEL'*10000,))
            elif change=='facts':
                chunk=c.s.execute('SELECT id FROM knowledge_chunks').fetchone()[0]
                for i in range(64):c.s.execute('INSERT INTO knowledge_facts VALUES(?,?,?,?,?,?,?)',(one.document_key,i+1,chunk,'x','x','десять','Лимит — десять.'))
    logs=instrument(monkeypatch,hook)
    with pytest.raises(k.SelectedKnowledgeUnavailable):search(c,(one,))
    assert fired and 'GROWTH_SENTINEL' not in repr(logs)


@pytest.mark.parametrize('change',['equal_content','reader','source','fact','priority'])
def test_final_typed_reread_denies_changes_during_actual_ranker(c,monkeypatch,change):
    one=seed(c);actual=c.index._rank
    def rank(*args,**kwargs):
        result=actual(*args,**kwargs)
        if change=='equal_content':c.s.execute("UPDATE knowledge_chunks SET text=replace(text,'Лимит','ЛИМИТ')")
        elif change=='reader':c.s.execute('DELETE FROM knowledge_readers')
        elif change=='source':c.index.revoke_source(one.source_id)
        elif change=='fact':c.s.execute("UPDATE knowledge_facts SET subject='другой'")
        elif change=='priority':c.s.execute('UPDATE knowledge_documents SET priority=7')
        return result
    monkeypatch.setattr(c.index,'_rank',rank)
    with pytest.raises(k.SelectedKnowledgeUnavailable):search(c,(one,))


@pytest.mark.parametrize('field,value',[
    ('query',None),('query',''),('query','x'*257),('reader','*'),('reader',1),('now',float('inf')),
    ('limit',True),('limit',0),('limit',4),('max_chars',1),('max_chars',6001),('documents',[]),
])
def test_selected_closed_public_inputs_no_sql(c,monkeypatch,field,value):
    one=seed(c)
    def fail(*a,**kw):pytest.fail('invalid selected input reached SQL')
    monkeypatch.setattr(c.s,'execute',fail)
    values=dict(query='лимит',reader=reader_key('GOLDWAY',SENDER),now=NOW,documents=(one,))
    values[field]=value
    with pytest.raises(k.SelectedKnowledgeUnavailable):c.index.search_selected(**values)


@pytest.mark.parametrize('kind',['local','trusted'])
def test_actual_initialized_source_mapping_no_prefix_guess_or_provider_call(c,kind):
    source=None if kind=='local' else c.e.intelligence.sources.trusted_bridge.source_id(c.binding)
    one=seed(c,source=source);c.bridge.accept(frame())
    answer=consumer(c,(one,)).read_question(reference(),'лимит')
    assert answer.knowledge.documents==(one,) and json.loads(answer.knowledge.result_json)['status']=='ok'


@pytest.mark.parametrize('change',['unknown_source','members','source_readers','trusted_business','trusted_bindings','reviewer','admin','wrong_session','partial'])
def test_audience_and_current_source_parent_before_knowledge_transfer(c,monkeypatch,change):
    source='unmapped-source' if change=='unknown_source' else None
    one=seed(c,source=source);c.bridge.accept(frame(final=change!='partial'))
    if change=='members':c.e.intelligence.sources.members.remove(reader_key('GOLDWAY',SENDER))
    elif change=='source_readers':c.e.intelligence.sources.specs[0]['readers']=[]
    elif change=='trusted_business':c.e.intelligence.sources.trusted_bridge.business='OTHER'
    elif change=='trusted_bindings':c.e.intelligence.sources.trusted_bridge.bindings=(replace(c.binding,audience=frozenset(['other'])),)
    elif change=='reviewer':c.state['permission']=replace(c.state['permission'],reviewer='alen')
    elif change=='admin':c.e.c['admins']=['alen']
    elif change=='wrong_session':c.state['permission']=replace(c.state['permission'],key=replace(KEY,session_id='other'))
    def fail(*a,**kw):pytest.fail('invalid audience/source reached knowledge read')
    monkeypatch.setattr(c.index,'search_selected',fail)
    with pytest.raises(m.VoiceKnowledgeUnavailable):consumer(c,(one,)).read_question(reference(),'лимит')


@pytest.mark.parametrize('text,query',[
    ('Найди в выбранных источниках: лимит','other'),('Найди лимит','лимит'),
    ('Найди в выбранных источниках: лимит и измени задачу','лимит'),('x'*2000,'лимит'),
])
def test_exact_question_query_binding_before_search(c,monkeypatch,text,query):
    one=seed(c);c.bridge.accept(frame(text=text))
    def fail(*a,**kw):pytest.fail('unsupported voice question reached selected search')
    monkeypatch.setattr(c.index,'search_selected',fail)
    with pytest.raises(m.VoiceKnowledgeUnavailable):consumer(c,(one,)).read_question(reference(),query)


@pytest.mark.parametrize('change',['reader','source','content','unchanged'])
def test_final_voice_permission_callback_cannot_return_old_knowledge_pack(c,monkeypatch,change):
    one=seed(c);lookups=[];fired=[];actual=c.index.search_selected
    def lookup(*args,**kwargs):
        result=actual(*args,**kwargs);lookups.append(result);return result
    def permission():
        if len(lookups)==1 and not fired:
            fired.append(change)
            if change=='reader':c.s.execute('DELETE FROM knowledge_readers')
            elif change=='source':c.index.revoke_source(one.source_id)
            elif change=='content':c.s.execute("UPDATE knowledge_chunks SET text=replace(text,'Лимит','ЛИМИТ')")
        return c.state['permission']
    c.bridge=meeting.VoiceMeetingBridge(SPEC,current_permission=permission,sink=c.bundles.append,enabled=True);c.bridge.accept(frame())
    monkeypatch.setattr(c.index,'search_selected',lookup)
    if change=='unchanged':assert consumer(c,(one,)).read_question(reference(),'лимит').knowledge==lookups[-1]
    else:
        with pytest.raises(m.VoiceKnowledgeUnavailable):consumer(c,(one,)).read_question(reference(),'лимит')
    assert fired==[change] and c.bundles==[]


@pytest.mark.parametrize('change',['config','index','connection','clock','sources','method','selected','documents','members'])
def test_host_and_input_drift_after_first_lookup(c,monkeypatch,change):
    one=seed(c);c.bridge.accept(frame());actual=c.index.search_selected;calls=[];db=c.s.db
    def lookup(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(1)
        if len(calls)==1:
            if change=='config':c.e.c=copy.deepcopy(c.e.c)
            elif change=='index':c.e.intelligence.index=object()
            elif change=='connection':c.s.db=object()
            elif change=='clock':c.e.now=lambda:NOW
            elif change=='sources':c.e.intelligence.sources=object()
            elif change=='method':monkeypatch.setattr(c.index,'search_selected',lambda *a,**k:None)
            elif change=='selected':monkeypatch.setattr(c.bridge,'selected_final',lambda *a,**k:None)
            elif change=='documents':object.__setattr__(one,'document_id','other')
            elif change=='members':c.e.intelligence.sources.members.remove(reader_key('GOLDWAY',SENDER))
        return result
    monkeypatch.setattr(c.index,'search_selected',lookup);value=consumer(c,(one,))
    try:
        with pytest.raises(m.VoiceKnowledgeUnavailable):value.read_question(reference(),'лимит')
    finally:c.s.db=db


def test_no_clock_source_callback_or_model_after_final_data(c,monkeypatch):
    one=seed(c);c.bridge.accept(frame());actual=c.index.search_selected;calls=[]
    def lookup(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(1)
        if len(calls)==2:c.state['forbid_clock']=True
        return result
    monkeypatch.setattr(c.index,'search_selected',lookup)
    assert consumer(c,(one,)).read_question(reference(),'лимит').knowledge.result_json and len(calls)==2


@pytest.mark.parametrize('enabled',[False,None,0,1,'true'])
def test_defaultoff_zero_callbacks_and_data(c,monkeypatch,enabled):
    def fail(*a,**kw):pytest.fail('disabled consumer called host')
    c.e.now=fail;monkeypatch.setattr(c.bridge,'selected_final',fail);monkeypatch.setattr(c.index,'search_selected',fail)
    with pytest.raises(m.VoiceKnowledgeUnavailable):consumer(c,(),enabled=enabled).read_question(reference(),'лимит')


@pytest.mark.parametrize('value',[float('nan'),True,-1,None])
def test_invalid_clock_before_read(c,value):
    one=seed(c);c.bridge.accept(frame());c.state['now']=value
    with pytest.raises(m.VoiceKnowledgeUnavailable):consumer(c,(one,)).read_question(reference(),'лимит')


def test_all_sixteen_chunks_sixtyfour_facts_fit_one_cumulative_reader(c,monkeypatch):
    docs=(seed(c,'one'),seed(c,'two'))
    for item in docs:
        for i in range(7):c.s.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?)',('chunk-'+item.document_id+'-'+str(i),item.document_key,i+1,'Лимит — десять.'))
        chunk=c.s.execute('SELECT id FROM knowledge_chunks WHERE document_key=? LIMIT 1',(item.document_key,)).fetchone()[0]
        for i in range(31):c.s.execute('INSERT INTO knowledge_facts VALUES(?,?,?,?,?,?,?)',(item.document_key,i+1,chunk,'subject-'+str(i),'predicate','десять','Лимит — десять.'))
    logs=instrument(monkeypatch);result=search(c,docs)
    assert len(result.consulted_ids)==16 and result.documents==docs
    assert max(log[4] for log in logs)<256 and max(log[5] for log in logs)<1024*1024
    assert len(logs)==8 and all('LIMIT 17' in sql and 'LIMIT 65' in sql for sql,*_ in logs)


@pytest.mark.parametrize('kind',['duplicate','wrong_source','wrong_document','bad_key','subclass'])
def test_closed_exact_selected_bindings_fail_without_text(c,monkeypatch,kind):
    one=seed(c)
    if kind=='duplicate':docs=(one,one)
    elif kind=='wrong_source':docs=(replace(one,source_id='other'),)
    elif kind=='wrong_document':docs=(replace(one,document_id='other'),)
    elif kind=='bad_key':docs=(replace(one,document_key='x'*64),)
    else:
        class Sub(k.SelectedDocument):pass
        docs=(Sub(one.source_id,one.document_id,one.document_key),)
    logs=instrument(monkeypatch)
    with pytest.raises(k.SelectedKnowledgeUnavailable):search(c,docs)
    assert logs==[]


@pytest.mark.parametrize('change',['interrupt','reviewer','expired_clock'])
def test_current_question_or_expiry_drift_between_voice_and_final_fixed_sample(c,monkeypatch,change):
    one=seed(c);c.bridge.accept(frame());actual=c.index.search_selected;calls=[]
    def lookup(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(1)
        if len(calls)==1:
            if change=='interrupt':c.bridge.accept(meeting.voice.Control('interrupt',KEY,2,0,BASE+timedelta(seconds=3),'interrupt-evidence','interrupt',True))
            elif change=='reviewer':c.state['permission']=replace(c.state['permission'],reviewer='alen')
            elif change=='expired_clock':c.state['now']=NOW+101
        return result
    monkeypatch.setattr(c.index,'search_selected',lookup)
    with pytest.raises(m.VoiceKnowledgeUnavailable):consumer(c,(one,)).read_question(reference(),'лимит')
    assert c.bundles==[]
