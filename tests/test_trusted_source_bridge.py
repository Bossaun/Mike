"""Only the new local-registry -> actual KnowledgeIndex boundary, synthetic only."""
from dataclasses import replace
import pytest
from goldway.store import Store
from goldway.knowledge import KnowledgeIndex
from goldway.knowledge_sources import ReadOnlySources
from goldway.trusted_provider import TrustedProviders,SourceSpec,ProviderSnapshot,Document
from goldway.trusted_source_bridge import BridgeError

ALICE='reader:'+'a'*64
BOB='reader:'+'b'*64
BUSINESS='SYNTHETIC'


@pytest.fixture
def case(monkeypatch):
    import builtins,socket
    import goldway.knowledge_sources as source_module
    clock=[1000];calls=[];credential=object()
    values=[ProviderSnapshot('provider','source',1,1000,(Document('doc','Synthetic title','ALPHA synthetic knowledge'),))]
    def callback(request,injected):
        assert injected is credential
        calls.append(request)
        if isinstance(values[0],Exception):raise values[0]
        return values[0]
    registry=TrustedProviders(business=BUSINESS,members=frozenset({ALICE,BOB}),clock=lambda:clock[0])
    registry.register_provider('provider',callback,credential=credential)
    policy=SourceSpec('provider','source',frozenset({ALICE}),1,120,True)
    registry.register_source(policy)
    store=Store(':memory:');index=KnowledgeIndex(store)
    sources=ReadOnlySources(index,[],{ALICE,BOB},trusted_registry=registry,trusted_sources=(policy,),business=BUSINESS)
    def forbidden(*a,**k):pytest.fail('local source created HTTP client or performed external I/O')
    monkeypatch.setattr(source_module.httpx,'Client',forbidden)
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    yield sources,index,store,registry,policy,clock,values,calls
    store.db.close()


def evidence(case,reader=ALICE):
    return case[1].search('synthetic',reader,now=case[5][0])['evidence']


def source_id(case):return 'trusted:'+BUSINESS+':provider:source'


def test_local_source_reaches_actual_index_with_registered_reader_only_without_http(case):
    sources,index,store,registry,policy,clock,values,calls=case
    assert sources.specs==[]
    assert sources.refresh(clock[0])=={source_id(case):'ok'}
    assert len(calls)==1 and evidence(case) and evidence(case,BOB)==[]
    doc=store.rows('SELECT source_id,observed_at,permission_expires_at,confirmed FROM knowledge_documents')[0]
    assert doc=={'source_id':source_id(case),'observed_at':1000,'permission_expires_at':1120,'confirmed':1}
    assert index.allowed_ids(ALICE,now=1000) and index.allowed_ids(BOB,now=1000)==set()


def test_revoke_withdraws_immediately_even_inside_remote_refresh_interval(case):
    case[0].refresh(1000);case[3].revoke('source')
    assert case[0].refresh(1000)=={source_id(case):'blocked'}
    assert evidence(case)==[] and not case[2].rows('SELECT * FROM knowledge_documents')
    marker=case[2].rows('SELECT observed_at,inclusive FROM knowledge_source_revocations')[0]
    assert marker=={'observed_at':1000,'inclusive':1}


@pytest.mark.parametrize('failure',['exception','malformed','stale','rollback'])
def test_failed_refresh_never_retains_indexed_cache_or_echoes_provider_payload(case,failure,capsys):
    sources,index,store,registry,policy,clock,values,calls=case
    sources.refresh(1000);clock[0]=1001
    if failure=='exception':values[0]=RuntimeError('SYNTHETIC_PRIVATE_SECRET')
    if failure=='malformed':values[0]={'readers':[BOB],'text':'SYNTHETIC_PRIVATE_SECRET'}
    if failure=='stale':values[0]=replace(values[0],observed_at=881)
    if failure=='rollback':
        values[0]=replace(values[0],version=2,observed_at=1001);sources.refresh(1001)
        clock[0]=1002;values[0]=replace(values[0],version=1,observed_at=1002)
    status=sources.refresh(clock[0])
    assert status=={source_id(case):'blocked'} and evidence(case)==[]
    assert not store.rows('SELECT * FROM knowledge_documents')
    logs=capsys.readouterr();assert logs.out+logs.err=='' and 'SYNTHETIC_PRIVATE_SECRET' not in str(status)


def test_failure_same_second_requires_new_provider_observation_not_rebased_cached_data(case):
    sources,index,store,registry,policy,clock,values,calls=case
    original=values[0];sources.refresh(1000);values[0]=RuntimeError('synthetic failure')
    sources.refresh(1000);values[0]=original
    assert sources.refresh(1000)=={source_id(case):'blocked'} and evidence(case)==[]
    clock[0]=1001;values[0]=replace(original,observed_at=1001)
    assert sources.refresh(1001)=={source_id(case):'ok'} and evidence(case)


def test_original_observation_and_ttl_are_preserved_across_successful_refreshes(case):
    sources,index,store,registry,policy,clock,values,calls=case
    values[0]=replace(values[0],observed_at=900)
    sources.refresh(1000)
    assert store.rows('SELECT observed_at,permission_expires_at FROM knowledge_documents')==[{'observed_at':900,'permission_expires_at':1020}]
    clock[0]=1010;sources.refresh(1010)
    assert store.rows('SELECT observed_at,permission_expires_at FROM knowledge_documents')==[{'observed_at':900,'permission_expires_at':1020}]
    clock[0]=1020;assert sources.refresh(1020)=={source_id(case):'blocked'} and evidence(case)==[]


def test_existing_index_revocation_watermark_rejects_older_snapshot_without_rebase(case):
    sources,index,store,registry,policy,clock,values,calls=case
    index.revoke_source(source_id(case),observed_at=999);index.revoke_source(source_id(case))
    values[0]=replace(values[0],observed_at=900)
    assert sources.refresh(1000)=={source_id(case):'blocked'} and evidence(case)==[]
    assert not store.rows('SELECT * FROM knowledge_documents')
    clock[0]=1001;values[0]=replace(values[0],observed_at=1001)
    assert sources.refresh(1001)=={source_id(case):'ok'}
    assert store.rows('SELECT observed_at FROM knowledge_documents')==[{'observed_at':1001}]


def test_older_clock_never_downgrades_newer_index_watermark(case):
    case[1].revoke_source(source_id(case),observed_at=2000)
    assert case[0].refresh(1000)=={source_id(case):'blocked'}
    assert case[2].rows('SELECT observed_at,inclusive FROM knowledge_source_revocations')==[{'observed_at':2000,'inclusive':0}]
    assert evidence(case)==[]


def test_new_snapshot_removes_old_documents_and_empty_snapshot_removes_all(case):
    sources,index,store,registry,policy,clock,values,calls=case
    sources.refresh(1000);clock[0]=1001
    values[0]=replace(values[0],version=2,observed_at=1001,documents=(Document('new','New title','BETA synthetic text'),))
    sources.refresh(1001)
    assert store.rows('SELECT document_id FROM knowledge_documents')==[{'document_id':'new'}]
    assert all('ALPHA' not in item['text'] for item in evidence(case))
    clock[0]=1002;values[0]=replace(values[0],version=3,observed_at=1002,documents=())
    assert sources.refresh(1002)=={source_id(case):'ok'} and evidence(case)==[]


def test_current_members_removal_cannot_keep_old_reader_grants(case):
    case[0].refresh(1000);before=len(case[7]);case[0].members.remove(ALICE)
    assert case[0].refresh(1000)=={source_id(case):'blocked'}
    assert len(case[7])==before and evidence(case)==[]


@pytest.mark.parametrize('change',['business','registry-missing','unregistered','audience','ttl','confirmation','version'])
def test_host_injection_requires_exact_business_registration_policy_and_current_members(case,change):
    sources,index,store,registry,policy,clock,values,calls=case
    args=dict(trusted_registry=registry,trusted_sources=(policy,),business=BUSINESS)
    if change=='business':args['business']='OTHER'
    if change=='registry-missing':args['trusted_registry']=None
    if change=='unregistered':args['trusted_sources']=(replace(policy,source_id='unknown'),)
    if change=='audience':args['trusted_sources']=(replace(policy,audience=frozenset({BOB})),)
    if change=='ttl':args['trusted_sources']=(replace(policy,ttl_seconds=60),)
    if change=='confirmation':args['trusted_sources']=(replace(policy,confirmed=False),)
    if change=='version':args['trusted_sources']=(replace(policy,minimum_version=2),)
    with pytest.raises(ValueError):ReadOnlySources(index,[],{ALICE,BOB},**args)
    assert calls==[] and not store.rows('SELECT * FROM knowledge_documents')


def test_batch_failure_rolls_back_then_withdraws_source_within_outer_transaction(case,monkeypatch):
    sources,index,store,registry,policy,clock,values,calls=case
    sources.refresh(1000);clock[0]=1001
    values[0]=replace(values[0],version=2,observed_at=1001,
        documents=(Document('one','One','First synthetic text'),Document('two','Two','Second synthetic text')))
    upsert=index.upsert;count=[0]
    def fail_second(doc):
        count[0]+=1
        if count[0]==2:raise ValueError('SYNTHETIC_PRIVATE_FAILURE')
        return upsert(doc)
    monkeypatch.setattr(index,'upsert',fail_second)
    with store.transaction():
        store.execute("INSERT INTO runtime VALUES('synthetic-marker','preserve')")
        assert sources.refresh(1001)=={source_id(case):'blocked'}
        assert not store.rows('SELECT * FROM knowledge_documents')
    assert store.rows('SELECT value FROM runtime')==[{'value':'preserve'}]
    assert evidence(case)==[]


def test_provider_clock_advancing_during_callback_keeps_real_observation(case):
    sources,index,store,registry,policy,clock,values,calls=case
    original=registry._providers['provider'].callback
    def advancing(request,credential):
        clock[0]=1001;values[0]=replace(values[0],observed_at=1001)
        return original(request,credential)
    registry._providers['provider'].callback=advancing
    assert sources.refresh(1000)=={source_id(case):'ok'}
    assert store.rows('SELECT observed_at,permission_expires_at FROM knowledge_documents')==[{'observed_at':1001,'permission_expires_at':1121}]
    assert evidence(case)


def test_one_provider_failure_does_not_remove_other_registered_source(case):
    sources,index,store,registry,policy,clock,values,calls=case
    second=SourceSpec('provider','second',frozenset({BOB}),1,120,True);registry.register_source(second)
    def callback(request,credential):
        if request.source_id=='source':raise RuntimeError('SYNTHETIC_PRIVATE_FAILURE')
        return ProviderSnapshot('provider','second',1,1000,(Document('doc','Other','Other synthetic knowledge'),))
    registry._providers['provider'].callback=callback
    sources=ReadOnlySources(index,[],{ALICE,BOB},trusted_registry=registry,trusted_sources=(policy,second),business=BUSINESS)
    status=sources.refresh(1000)
    assert status[source_id(case)]=='blocked' and status['trusted:SYNTHETIC:provider:second']=='ok'
    assert index.search('synthetic',ALICE,now=1000)['evidence']==[]
    assert index.search('synthetic',BOB,now=1000)['evidence']


def test_default_empty_sources_stay_unchanged_and_create_no_client(case):
    assert ReadOnlySources(case[1],[],{ALICE,BOB}).refresh(1000)=={}


def test_source_namespace_overlap_is_rejected_before_fetch(case):
    with pytest.raises(ValueError):ReadOnlySources(case[1],[{'id':source_id(case)}],{ALICE,BOB},
        trusted_registry=case[3],trusted_sources=(case[4],),business=BUSINESS)
    assert case[7]==[]


@pytest.mark.parametrize('change',['version','content','same-observation-after-failure'])
def test_index_watermarks_survive_registry_and_source_adapter_reconstruction(case,change):
    sources,index,store,registry,policy,clock,values,calls=case
    values[0]=replace(values[0],version=2);sources.refresh(1000)
    if change=='same-observation-after-failure':
        values[0]=RuntimeError('synthetic failure');sources.refresh(1000)
    original=ProviderSnapshot('provider','source',2,1001,(Document('doc','Synthetic title','ALPHA synthetic knowledge'),))
    clock[0]=1001
    if change=='version':original=replace(original,version=1)
    if change=='content':original=replace(original,documents=(Document('doc','Synthetic title','CHANGED synthetic text'),))
    if change=='same-observation-after-failure':original=replace(original,observed_at=1000)
    new_registry=TrustedProviders(business=BUSINESS,members=frozenset({ALICE,BOB}),clock=lambda:clock[0])
    new_registry.register_provider('provider',lambda request,credential:original,credential=None)
    new_registry.register_source(policy)
    replacement=ReadOnlySources(index,[],{ALICE,BOB},trusted_registry=new_registry,trusted_sources=(policy,),business=BUSINESS)
    assert replacement.refresh(1001)=={source_id(case):'blocked'}
    assert evidence(case)==[]
    assert store.rows('SELECT version FROM trusted_source_versions')==[{'version':2}]


def test_default_registry_absence_adds_no_bridge_schema(case):
    case[2].execute('DROP TABLE trusted_source_versions')
    ReadOnlySources(case[1],[],{ALICE,BOB}).refresh(1000)
    assert not case[2].execute("SELECT 1 FROM sqlite_master WHERE name='trusted_source_versions'").fetchone()


def test_failed_lagged_registry_keeps_explicit_refresh_withdrawal_time(case):
    case[0].refresh(1000)
    case[1].revoke_source(source_id(case),observed_at=1005)
    assert case[0].refresh(1010)=={source_id(case):'blocked'}
    assert case[2].rows('SELECT observed_at,inclusive FROM knowledge_source_revocations')==[{'observed_at':1010,'inclusive':1}]
    assert evidence(case)==[]


def test_callback_removing_nonselected_reader_withdraws_complete_source_before_replace(case):
    _,index,store,registry,_,clock,_,_=case
    shared=SourceSpec('provider','shared',frozenset({ALICE,BOB}),1,120,True)
    registry.register_source(shared)
    snapshot=ProviderSnapshot('provider','shared',1,1000,(Document('shared-doc','Shared title','Shared synthetic knowledge'),))
    registry._providers['provider'].callback=lambda request,credential:snapshot
    sources=ReadOnlySources(index,[],{ALICE,BOB},trusted_registry=registry,
        trusted_sources=(shared,),business=BUSINESS)
    source='trusted:SYNTHETIC:provider:shared'
    assert sources.refresh(1000)=={source:'ok'}
    assert index.allowed_ids(ALICE,now=1000) and index.allowed_ids(BOB,now=1000)
    def removing_second_reader(request,credential):
        assert sorted(shared.audience)[0]==ALICE
        sources.members.remove(BOB)
        return snapshot
    registry._providers['provider'].callback=removing_second_reader
    assert sources.refresh(1000)=={source:'blocked'}
    assert index.allowed_ids(ALICE,now=1000)==set() and index.allowed_ids(BOB,now=1000)==set()
    assert store.rows('SELECT source_id FROM knowledge_documents')==[]
    assert store.rows('SELECT observed_at,inclusive FROM knowledge_source_revocations WHERE source_id=?',(source,))==[{'observed_at':1000,'inclusive':1}]
