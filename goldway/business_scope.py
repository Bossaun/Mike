"""Additive synthetic tenant contracts; no existing DB/config/auth adapter.

Host-supplied scope/ACL records are assertions, not authenticated credentials.
Passing these checks never authorizes a live read, write, migration or send.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re

KINDS = frozenset({'source','reader','task','outbox','delivery','connection','rule','event','runtime'})
OPERATIONS = frozenset({'read','write','link','prepare_delivery','preview_migration'})


class ScopeError(ValueError):
    pass


def _tenant(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value) is None:raise ScopeError('invalid_tenant')


def _id(value):
    if type(value) is not str or value=='*' or not value.strip() or value!=value.strip() or len(value)>128 or any(ord(c)<32 for c in value):raise ScopeError('invalid_identifier')
    try:value.encode('utf-8')
    except UnicodeError:raise ScopeError('invalid_identifier') from None


def _integer(value):
    if type(value) is not int or not 0<=value<2**63:raise ScopeError('invalid_timestamp')


def _window(issued,expires):
    _integer(issued);_integer(expires)
    if not issued<expires or expires-issued>86400:raise ScopeError('invalid_validity_window')


def _set(value,maximum,minimum=0):
    if type(value) is not frozenset or not minimum<=len(value)<=maximum:raise ScopeError('invalid_bounded_set')
    for item in value:_id(item)


def _tuple(value,cls,maximum,minimum=0):
    if type(value) is not tuple or not minimum<=len(value)<=maximum or any(type(item) is not cls for item in value):raise ScopeError('invalid_bounded_records')
    if len(set(value))!=len(value):raise ScopeError('duplicate_record')


@dataclass(frozen=True)
class ScopedRef:
    tenant: str
    kind: str
    id: str

    def __post_init__(self):
        _tenant(self.tenant);_id(self.id)
        if type(self.kind) is not str or self.kind not in KINDS:raise ScopeError('invalid_namespace_kind')


def scoped_key(ref: ScopedRef) -> str:
    """Unambiguous candidate v1 key; not an authorization or current reader_key."""
    if type(ref) is not ScopedRef:raise ScopeError('validated_reference_required')
    data=json.dumps(['business-scope-v1',ref.tenant,ref.kind,ref.id],ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return 'scope-v1:'+ref.kind+':'+hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class TenantScope:
    tenant: str
    readers: frozenset[str]
    admins: frozenset[str]
    connections: frozenset[str]
    rules: frozenset[str]
    operations: frozenset[str]
    issued_at: int
    expires_at: int
    revoked: bool = False
    live_authorized: bool = field(default=False,init=False)

    def __post_init__(self):
        _tenant(self.tenant)
        _set(self.readers,200,1);_set(self.admins,20,1);_set(self.connections,20);_set(self.rules,50)
        _set(self.operations,len(OPERATIONS))
        if not self.admins<=self.readers or not self.operations<=OPERATIONS:raise ScopeError('invalid_scope_policy')
        _window(self.issued_at,self.expires_at)
        if type(self.revoked) is not bool:raise ScopeError('invalid_revocation')


def _check(scope,operation,now):
    if type(scope) is not TenantScope:raise ScopeError('validated_scope_required')
    _integer(now)
    if scope.revoked or not scope.issued_at<=now<scope.expires_at:raise ScopeError('inactive_scope')
    if operation not in scope.operations:raise ScopeError('operation_not_granted')


def _ref(scope,ref,kind=None):
    if type(ref) is not ScopedRef or ref.tenant!=scope.tenant:raise ScopeError('cross_tenant_reference')
    if kind is not None and ref.kind!=kind:raise ScopeError('wrong_reference_kind')
    if ref.kind=='reader' and ref.id not in scope.readers:raise ScopeError('unregistered_reader')
    if ref.kind=='connection' and ref.id not in scope.connections:raise ScopeError('unregistered_connection')
    if ref.kind=='rule' and ref.id not in scope.rules:raise ScopeError('unregistered_rule')


def require_operation(scope: TenantScope, *, operation: str, actor: ScopedRef,
                      references: tuple[ScopedRef,...], now: int) -> None:
    """Namespace/member check only; entity/task/content permissions need guards below."""
    if type(operation) is not str or operation not in OPERATIONS:raise ScopeError('invalid_operation')
    _check(scope,operation,now);_ref(scope,actor,'reader');_tuple(references,ScopedRef,100,1)
    for ref in references:_ref(scope,ref)
    if operation in ('preview_migration',) and actor.id not in scope.admins:raise ScopeError('admin_required')


@dataclass(frozen=True)
class SourceACL:
    source: ScopedRef
    readers: tuple[ScopedRef,...]
    issued_at: int
    expires_at: int
    revoked: bool = False

    def __post_init__(self):
        if type(self.source) is not ScopedRef or self.source.kind!='source':raise ScopeError('source_reference_required')
        _tuple(self.readers,ScopedRef,200,1);_window(self.issued_at,self.expires_at)
        if any(r.tenant!=self.source.tenant or r.kind!='reader' for r in self.readers):raise ScopeError('cross_tenant_source_acl')
        if type(self.revoked) is not bool:raise ScopeError('invalid_revocation')


def validate_source_read(scope: TenantScope, *, source: ScopedRef, reader: ScopedRef,
                         acl: SourceACL, now: int) -> None:
    require_operation(scope,operation='read',actor=reader,references=(source,),now=now)
    _ref(scope,source,'source')
    if type(acl) is not SourceACL or acl.source!=source:raise ScopeError('source_acl_mismatch')
    if acl.revoked or not acl.issued_at<=now<acl.expires_at or reader not in acl.readers:raise ScopeError('source_not_readable')
    # Administrator status never bypasses an explicit document's audience.


@dataclass(frozen=True)
class TaskBinding:
    task: ScopedRef
    owner: ScopedRef
    sources: tuple[ScopedRef,...]

    def __post_init__(self):
        if type(self.task) is not ScopedRef or self.task.kind!='task' or type(self.owner) is not ScopedRef or self.owner.kind!='reader':raise ScopeError('invalid_task_binding')
        _tuple(self.sources,ScopedRef,20,1)
        if self.owner.tenant!=self.task.tenant or any(s.tenant!=self.task.tenant or s.kind!='source' for s in self.sources):raise ScopeError('cross_tenant_task_link')


def validate_task_access(scope: TenantScope, *, binding: TaskBinding, actor: ScopedRef,
                         acls: tuple[SourceACL,...], now: int, operation: str='read') -> None:
    if type(binding) is not TaskBinding:raise ScopeError('validated_task_binding_required')
    if operation not in ('read','write'):raise ScopeError('invalid_task_operation')
    require_operation(scope,operation=operation,actor=actor,references=(binding.task,)+binding.sources,now=now)
    if actor.id!=binding.owner.id and actor.id not in scope.admins:raise ScopeError('task_role_not_permitted')
    _tuple(acls,SourceACL,20,1)
    if len({acl.source for acl in acls})!=len(acls):raise ScopeError('ambiguous_source_acl')
    if {acl.source for acl in acls}!=set(binding.sources):raise ScopeError('task_source_acl_inventory_mismatch')
    for acl in acls:validate_source_read(scope,source=acl.source,reader=actor,acl=acl,now=now)


@dataclass(frozen=True)
class DeliveryPreview:
    outbox: ScopedRef
    task: ScopedRef
    recipient: ScopedRef
    connection: ScopedRef
    source_keys: tuple[str,...]
    live_authorized: bool = field(default=False,init=False)
    review_required: bool = field(default=True,init=False)


def prepare_delivery(scope: TenantScope, *, outbox: ScopedRef, binding: TaskBinding,
                     recipient: ScopedRef, connection: ScopedRef,
                     acls: tuple[SourceACL,...], now: int) -> DeliveryPreview:
    if type(binding) is not TaskBinding:raise ScopeError('validated_task_binding_required')
    require_operation(scope,operation='prepare_delivery',actor=recipient,references=(outbox,connection),now=now)
    _ref(scope,outbox,'outbox');_ref(scope,connection,'connection')
    validate_task_access(scope,binding=binding,actor=recipient,acls=acls,now=now)
    return DeliveryPreview(outbox,binding.task,recipient,connection,tuple(sorted(scoped_key(s) for s in binding.sources)))


@dataclass(frozen=True)
class LegacyIdentity:
    business: str | None
    kind: str
    id: str

    def __post_init__(self):
        if self.business is not None:_tenant(self.business)
        _id(self.id)
        if type(self.kind) is not str or self.kind not in KINDS:raise ScopeError('invalid_namespace_kind')


@dataclass(frozen=True)
class MigrationEntry:
    legacy: LegacyIdentity
    target: ScopedRef
    target_key: str


@dataclass(frozen=True)
class MigrationPreview:
    tenant: str
    entries: tuple[MigrationEntry,...]
    unresolved: tuple[LegacyIdentity,...]
    ready: bool
    live_authorized: bool = field(default=False,init=False)
    review_required: bool = field(default=True,init=False)


def migration_preview(scope: TenantScope, *, target_tenant: str, actor: ScopedRef,
                      records: tuple[LegacyIdentity,...], now: int) -> MigrationPreview:
    """Identity mapping only, never rehome A data/ACL into B or infer missing tenant.

    Existing delivery-log/runtime keys without business must be resolved from
    trusted parent/config evidence outside this candidate; not guessed by prefix.
    """
    _tenant(target_tenant);_check(scope,'preview_migration',now);_ref(scope,actor,'reader')
    if actor.id not in scope.admins:raise ScopeError('admin_required')
    if target_tenant!=scope.tenant:raise ScopeError('cross_tenant_migration')
    _tuple(records,LegacyIdentity,200)
    entries=[];unresolved=[]
    for record in records:
        if record.business is None:
            unresolved.append(record);continue
        if record.business!=scope.tenant:raise ScopeError('cross_tenant_migration')
        ref=ScopedRef(record.business,record.kind,record.id);_ref(scope,ref)
        entries.append(MigrationEntry(record,ref,scoped_key(ref)))
    return MigrationPreview(scope.tenant,tuple(entries),tuple(unresolved),not unresolved)
