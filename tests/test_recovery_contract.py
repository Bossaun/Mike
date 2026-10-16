"""Synthetic memory-only backup rehearsal and sanitized health observations."""
from dataclasses import FrozenInstanceError, replace
import json
import pytest
from goldway.recovery_contract import (Artifact, ArtifactInfo, FakeTarget, HealthCounts,
    Manifest, RecoveryError, build_manifest, dry_run_restore, health_projection, manifest_digest)


@pytest.fixture
def case():
    artifacts=tuple(Artifact('state/'+name+'.json',('synthetic '+name).encode())
                    for name in ('tasks','events','schedules','outbox'))
    manifest=build_manifest(source_name='source',business='SYNTHETIC',schema_version=1,created_at=1000,artifacts=artifacts)
    params=dict(expected_manifest_digest=manifest_digest(manifest),expected_business='SYNTHETIC',
        expected_schema_version=1,required_artifacts=tuple(a.name for a in artifacts),
        now=1060,max_age_seconds=60,target=FakeTarget('rehearsal','SYNTHETIC',1))
    return manifest,artifacts,params


def test_rehearsal_builds_separate_immutable_fake_target_without_source_mutation(case):
    manifest,artifacts,params=case;original=params['target']
    preview=dry_run_restore(manifest,artifacts,**params)
    assert preview.dry_run is True and preview.target.name=='rehearsal'
    assert preview.target.artifacts==tuple(sorted(artifacts,key=lambda a:a.name))
    assert original.artifacts==() and original is not preview.target
    assert preview.artifact_count==4 and preview.total_bytes==sum(len(a.content) for a in artifacts)
    with pytest.raises(FrozenInstanceError):preview.target.name='source'
    with pytest.raises(FrozenInstanceError):manifest.business='changed'
    assert dry_run_restore(manifest,artifacts,**params)==preview


def test_manifest_digest_is_content_metadata_bound_and_order_independent(case):
    manifest,artifacts,params=case
    reordered=build_manifest(source_name='source',business='SYNTHETIC',schema_version=1,
        created_at=1000,artifacts=tuple(reversed(artifacts)))
    assert manifest_digest(manifest)==manifest_digest(reordered)
    changed=replace(manifest,created_at=1001)
    with pytest.raises(RecoveryError,match='^manifest_digest_mismatch$'):
        dry_run_restore(changed,artifacts,**params)


@pytest.mark.parametrize('field,value',[('business','OTHER'),('schema_version',2),('created_at',1061),('created_at',999)])
def test_wrong_business_schema_future_and_stale_backup_rejected(case,field,value):
    manifest,artifacts,params=case;manifest=replace(manifest,**{field:value})
    params['expected_manifest_digest']=manifest_digest(manifest)
    with pytest.raises(RecoveryError):dry_run_restore(manifest,artifacts,**params)


@pytest.mark.parametrize('target',[FakeTarget('source','SYNTHETIC',1),FakeTarget('rehearsal','OTHER',1),
    FakeTarget('rehearsal','SYNTHETIC',2),FakeTarget('rehearsal','SYNTHETIC',1,(Artifact('occupied',b'x'),))])
def test_no_restore_into_source_wrong_or_nonempty_target(case,target):
    manifest,artifacts,params=case;params['target']=target
    with pytest.raises(RecoveryError):dry_run_restore(manifest,artifacts,**params)


@pytest.mark.parametrize('change',['same-size-corrupt','truncated','missing','extra','required-not-declared'])
def test_artifact_integrity_and_required_inventory_fail_closed(case,change):
    manifest,artifacts,params=case
    if change=='same-size-corrupt':artifacts=(replace(artifacts[0],content=b'X'*len(artifacts[0].content)),)+artifacts[1:]
    if change=='truncated':artifacts=(replace(artifacts[0],content=b'x'),)+artifacts[1:]
    if change=='missing':artifacts=artifacts[1:]
    if change=='extra':artifacts=artifacts+(Artifact('extra',b'x'),)
    if change=='required-not-declared':params['required_artifacts']+=('required',)
    with pytest.raises(RecoveryError):dry_run_restore(manifest,artifacts,**params)
    assert params['target'].artifacts==()


@pytest.mark.parametrize('name',['../secret','/absolute','a/../../secret','a/./b','a//b','a\\b',
    'C:drive','https://example.com','%2e%2e/file','a/','./file','.hidden','a/../b','a/'+'x'*65])
def test_path_traversal_and_ambiguous_names_rejected_at_boundary(name):
    with pytest.raises(RecoveryError,match='^unsafe_artifact_name$'):Artifact(name,b'x')


@pytest.mark.parametrize('names',[('same','same'),('STATE','state'),('folder','folder/file')])
def test_duplicate_case_collision_and_file_directory_collision_rejected(names):
    artifacts=tuple(Artifact(name,b'x') for name in names)
    with pytest.raises(RecoveryError):build_manifest(source_name='source',business='SYNTHETIC',schema_version=1,created_at=0,artifacts=artifacts)


@pytest.mark.parametrize('value',[b'',bytearray(b'x'),'text',b'x'*(1024*1024+1)])
def test_payload_type_and_size_bounds(value):
    with pytest.raises(RecoveryError):Artifact('artifact',value)


def test_whole_inventory_and_total_payload_bounds():
    for artifacts in ((),tuple(Artifact(str(i),b'x') for i in range(17)),
                      tuple(Artifact(str(i),b'x'*(1024*1024)) for i in range(5))):
        with pytest.raises(RecoveryError):build_manifest(source_name='source',business='SYNTHETIC',schema_version=1,created_at=0,artifacts=artifacts)


@pytest.mark.parametrize('field,value',[('schema_version',True),('schema_version',0),('created_at',True),
    ('created_at',-1),('format_version',2),('format_version',True),('business','SYNTHETIC secret'),('source_name','../source')])
def test_manifest_metadata_bounds(case,field,value):
    with pytest.raises(RecoveryError):replace(case[0],**{field:value})


@pytest.mark.parametrize('field,value',[('now',True),('now',1.0),('max_age_seconds',0),
    ('max_age_seconds',True),('expected_manifest_digest','not-a-hash'),('required_artifacts',()),
    ('required_artifacts',('../unsafe',)),('required_artifacts',('same','same'))])
def test_restore_policy_inputs_are_explicit_and_typed(case,field,value):
    manifest,artifacts,params=case;params[field]=value
    with pytest.raises(RecoveryError):dry_run_restore(manifest,artifacts,**params)


def health_params():
    return dict(counts=HealthCounts(0,0,0,0,0,0,0),store_state='ok',worker_state='active',
        observed_at=1000,heartbeat_at=990,now=1010,observation_max_age_seconds=10,heartbeat_max_age_seconds=20)


def test_health_boundary_is_allowlisted_and_never_claims_delivery():
    params=health_params();value=health_projection(**params)
    assert value['status']=='no_attention_in_supplied_metrics'
    assert value['observation_state']=='recent' and value['heartbeat_state']=='recent'
    assert value['external_delivery']=='not_evaluated' and value['attention_codes']==[]
    assert set(value['counts'])=={'event_pending','event_failed','outbox_pending','outbox_retry','outbox_blocked','outbox_sending','outbox_unknown'}
    assert not {'token','messages','business','id','payload','task_id'} & set(value)
    params['now']=1011;stale=health_projection(**params)
    assert stale['observation_state']=='stale' and stale['heartbeat_state']=='stale'


def test_health_failed_unknown_sending_are_visible_without_automatic_replay():
    params=health_params();params['counts']=HealthCounts(12,1,8,2,3,4,5)
    params['heartbeat_at']=None;params['store_state']='corrupt';params['worker_state']='stopped'
    value=health_projection(**params)
    assert value['status']=='attention'
    assert set(value['attention_codes'])=={'store_not_ok','worker_not_active','heartbeat_missing',
        'event_failed','outbox_retry','outbox_blocked','outbox_sending','outbox_unknown'}
    assert value['counts']['outbox_unknown']==5 and value['external_delivery']=='not_evaluated'


@pytest.mark.parametrize('field,value',[('store_state','SYNTHETIC_SECRET'),('worker_state',{'token':'SYNTHETIC_SECRET'}),
    ('counts',{'id':'SYNTHETIC_SECRET'}),('observed_at',1011),('heartbeat_at',1001),
    ('now',True),('heartbeat_max_age_seconds',0),('observation_max_age_seconds',1.1)])
def test_health_rejects_free_text_and_invalid_metadata_without_echo(field,value,capsys):
    params=health_params();params[field]=value
    with pytest.raises(RecoveryError) as caught:health_projection(**params)
    assert 'SYNTHETIC_SECRET' not in str(caught.value)
    logs=capsys.readouterr();assert logs.out+logs.err==''


@pytest.mark.parametrize('count',[True,-1,1.0,'SYNTHETIC_SECRET',10**9+1])
def test_health_counts_are_bounded_integers(count):
    with pytest.raises(RecoveryError):HealthCounts(count,0,0,0,0,0,0)


def test_helpers_do_not_access_files_network_sqlite_or_wall_clock(case,monkeypatch):
    import builtins,socket,sqlite3,time
    def forbidden(*args,**kwargs):pytest.fail('unexpected external access')
    monkeypatch.setattr(builtins,'open',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(sqlite3,'connect',forbidden);monkeypatch.setattr(time,'time',forbidden)
    manifest,artifacts,params=case
    assert dry_run_restore(manifest,artifacts,**params).dry_run
    assert health_projection(**health_params())['external_delivery']=='not_evaluated'
