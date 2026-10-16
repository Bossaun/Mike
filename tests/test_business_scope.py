"""Synthetic A/B namespace, current ACL and migration-preview boundaries."""
from dataclasses import FrozenInstanceError,replace
import pytest
from goldway.business_scope import (ScopedRef,TenantScope,SourceACL,TaskBinding,
    LegacyIdentity,scoped_key,require_operation,validate_source_read,
    validate_task_access,prepare_delivery,migration_preview,ScopeError)


def ref(tenant,kind,id='same'):
    return ScopedRef(tenant,kind,id)


def scope(tenant='A'):
    return TenantScope(tenant,frozenset({'owner','admin','other'}),frozenset({'admin'}),
        frozenset({'connection'}),frozenset({'rule'}),
        frozenset({'read','write','link','prepare_delivery','preview_migration'}),1000,1060)


def fixtures(tenant='A'):
    current=scope(tenant);source=ref(tenant,'source');owner=ref(tenant,'reader','owner')
    acl=SourceACL(source,(owner,ref(tenant,'reader','admin')),1000,1060)
    binding=TaskBinding(ref(tenant,'task'),owner,(source,))
    return current,source,owner,acl,binding


def test_namespace_includes_tenant_kind_and_exact_id_without_delimiter_collision():
    assert scoped_key(ref('A','task'))!=scoped_key(ref('B','task'))
    assert scoped_key(ref('A','task'))!=scoped_key(ref('A','source'))
    assert scoped_key(ref('A','task','x:y'))!=scoped_key(ref('A','task','x'))
    assert scoped_key(ref('A','task'))==scoped_key(ref('A','task'))
    assert 'same' not in scoped_key(ref('A','task'))


@pytest.mark.parametrize('operation',['read','write','link','prepare_delivery','preview_migration'])
@pytest.mark.parametrize('kind',['task','source','reader','outbox','connection','rule'])
def test_all_namespace_operations_reject_foreign_business_refs(operation,kind):
    id='owner' if kind=='reader' else 'connection' if kind=='connection' else 'rule' if kind=='rule' else 'same'
    with pytest.raises(ScopeError,match='^cross_tenant_reference$'):
        require_operation(scope('A'),operation=operation,actor=ref('A','reader','admin'),references=(ref('B',kind,id),),now=1000)


def test_foreign_actor_cannot_use_same_local_reader_name():
    with pytest.raises(ScopeError):require_operation(scope(),operation='read',actor=ref('B','reader','owner'),references=(ref('A','task'),),now=1000)


def test_same_tenant_task_read_write_and_exact_source_acl_contract():
    current,source,owner,acl,binding=fixtures()
    validate_source_read(current,source=source,reader=owner,acl=acl,now=1000)
    for operation in ('read','write'):
        validate_task_access(current,binding=binding,actor=owner,acls=(acl,),now=1000,operation=operation)
    assert current.live_authorized is False
    with pytest.raises(FrozenInstanceError):current.tenant='B'


@pytest.mark.parametrize('change',['acl-source','acl-reader','acl-business','revoked','expired','future','removed-reader','admin-not-granted'])
def test_source_acl_exact_business_audience_and_current_validity(change):
    current,source,owner,acl,binding=fixtures()
    if change=='acl-source':acl=replace(acl,source=ref('A','source','other'))
    if change=='acl-reader':acl=replace(acl,readers=(ref('A','reader','other'),))
    if change=='acl-business':acl=SourceACL(ref('B','source'),(ref('B','reader','owner'),),1000,1060)
    if change=='revoked':acl=replace(acl,revoked=True)
    if change=='expired':acl=replace(acl,issued_at=900,expires_at=1000)
    if change=='future':acl=replace(acl,issued_at=1001,expires_at=1060)
    if change=='removed-reader':current=replace(current,readers=frozenset({'admin','other'}))
    if change=='admin-not-granted':owner=ref('A','reader','admin');acl=replace(acl,readers=(ref('A','reader','owner'),))
    with pytest.raises(ScopeError):validate_source_read(current,source=source,reader=owner,acl=acl,now=1000)


def test_acl_and_task_constructors_reject_cross_tenant_links():
    with pytest.raises(ScopeError):SourceACL(ref('A','source'),(ref('B','reader','owner'),),1000,1060)
    with pytest.raises(ScopeError):TaskBinding(ref('A','task'),ref('B','reader','owner'),(ref('A','source'),))
    with pytest.raises(ScopeError):TaskBinding(ref('A','task'),ref('A','reader','owner'),(ref('B','source'),))


def test_task_access_requires_current_role_and_every_source_acl():
    current,source,owner,acl,binding=fixtures()
    other=ref('A','reader','other')
    with pytest.raises(ScopeError):validate_task_access(current,binding=binding,actor=other,acls=(acl,),now=1000)
    extra=ref('A','source','extra');binding=replace(binding,sources=(source,extra))
    with pytest.raises(ScopeError):validate_task_access(current,binding=binding,actor=owner,acls=(acl,),now=1000)
    extra_acl=SourceACL(extra,(owner,),1000,1060)
    validate_task_access(current,binding=binding,actor=owner,acls=(acl,extra_acl),now=1000)
    with pytest.raises(ScopeError):validate_task_access(current,binding=binding,actor=ref('A','reader','admin'),acls=(acl,extra_acl),now=1000)


def test_delivery_preview_is_scoped_to_current_recipient_connection_and_provenance():
    current,source,owner,acl,binding=fixtures()
    result=prepare_delivery(current,outbox=ref('A','outbox'),binding=binding,recipient=owner,
        connection=ref('A','connection','connection'),acls=(acl,),now=1000)
    assert result.source_keys==(scoped_key(source),) and result.review_required and not result.live_authorized
    with pytest.raises(ValueError):replace(result,live_authorized=True)


@pytest.mark.parametrize('change',['outbox','task','recipient','connection','unregistered-connection','source-acl'])
def test_cross_business_delivery_never_becomes_a_live_route(change):
    current,source,owner,acl,binding=fixtures()
    params=dict(outbox=ref('A','outbox'),binding=binding,recipient=owner,
        connection=ref('A','connection','connection'),acls=(acl,),now=1000)
    if change=='outbox':params['outbox']=ref('B','outbox')
    if change=='task':params['binding']=fixtures('B')[-1]
    if change=='recipient':params['recipient']=ref('B','reader','owner')
    if change=='connection':params['connection']=ref('B','connection','connection')
    if change=='unregistered-connection':params['connection']=ref('A','connection','unregistered')
    if change=='source-acl':params['acls']=(fixtures('B')[-2],)
    with pytest.raises(ScopeError):prepare_delivery(current,**params)


@pytest.mark.parametrize('change',['scope-revoked','scope-expired','scope-future','no-op','unknown-reader'])
def test_current_scope_observation_is_explicit_and_fail_closed(change):
    current=scope();actor=ref('A','reader','owner')
    if change=='scope-revoked':current=replace(current,revoked=True)
    if change=='scope-expired':current=replace(current,issued_at=900,expires_at=1000)
    if change=='scope-future':current=replace(current,issued_at=1001,expires_at=1060)
    if change=='no-op':current=replace(current,operations=frozenset())
    if change=='unknown-reader':actor=ref('A','reader','unknown')
    with pytest.raises(ScopeError):require_operation(current,operation='read',actor=actor,references=(ref('A','task'),),now=1000)


def test_migration_preview_does_not_rehome_data_or_infer_unscoped_runtime_delivery():
    records=(LegacyIdentity('A','task','same'),LegacyIdentity('A','source','same'),
             LegacyIdentity(None,'runtime','A:offset'),LegacyIdentity(None,'delivery','1'))
    result=migration_preview(scope(),target_tenant='A',actor=ref('A','reader','admin'),records=records,now=1000)
    assert len(result.entries)==2 and result.unresolved==records[2:] and not result.ready
    assert not result.live_authorized and result.review_required
    assert {entry.target.tenant for entry in result.entries}=={'A'}
    assert result.entries[0].target_key!=result.entries[1].target_key
    complete=migration_preview(scope(),target_tenant='A',actor=ref('A','reader','admin'),records=records[:2],now=1000)
    assert complete.ready and not complete.live_authorized
    with pytest.raises(ScopeError):migration_preview(scope(),target_tenant='B',actor=ref('A','reader','admin'),records=records[:2],now=1000)
    with pytest.raises(ScopeError):migration_preview(scope('B'),target_tenant='B',actor=ref('B','reader','admin'),records=records[:2],now=1000)


def test_migration_requires_explicit_admin_and_unique_bounded_records():
    record=LegacyIdentity('A','task','same')
    params=dict(target_tenant='A',actor=ref('A','reader','admin'),records=(record,),now=1000)
    with pytest.raises(ScopeError):migration_preview(scope(),**dict(params,actor=ref('A','reader','owner')))
    with pytest.raises(ScopeError):migration_preview(scope(),**dict(params,records=(record,record)))
    with pytest.raises(ScopeError):migration_preview(scope(),**dict(params,records=tuple(replace(record,id=str(i)) for i in range(201))))


@pytest.mark.parametrize('field,value',[('tenant',' A'),('readers',frozenset({'*'})),('admins',frozenset({'unknown'})),
    ('connections',set()),('operations',frozenset({'auto_activate'})),('issued_at',True),('expires_at',1000),('revoked',1)])
def test_policy_bounds_types_and_no_wildcard_grants(field,value):
    with pytest.raises(ScopeError):replace(scope(),**{field:value})


def test_ambiguous_id_delimiters_do_not_merge_or_coerce_namespaces():
    assert scoped_key(ref('A','source','x:y'))!=scoped_key(ref('A','source','x/y'))
    with pytest.raises(ScopeError):ref('A','task',' same')
    with pytest.raises(ScopeError):ref('A','task','\ud800')
    with pytest.raises(ScopeError):ref('A','task','x'*129)


def test_core_uses_no_files_sqlite_network_or_wall_clock(monkeypatch):
    import builtins,socket,sqlite3,time
    def forbidden(*a,**k):pytest.fail('unexpected integration side effect')
    for obj,name in ((builtins,'open'),(socket,'socket'),(sqlite3,'connect'),(time,'time')):monkeypatch.setattr(obj,name,forbidden)
    current,source,owner,acl,binding=fixtures()
    validate_task_access(current,binding=binding,actor=owner,acls=(acl,),now=1000)
    assert not prepare_delivery(current,outbox=ref('A','outbox'),binding=binding,recipient=owner,connection=ref('A','connection','connection'),acls=(acl,),now=1000).live_authorized
