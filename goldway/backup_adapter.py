"""Default-disabled PLAINTEXT synthetic memory-SQLite backup/recovery rehearsal.

No encryption, authenticated vault, offsite durability, live DB export or activation.
"""
from contextlib import closing,contextmanager
from dataclasses import dataclass,field
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile

from . import backup as backup_core
from . import recovery_contract as recovery
from .store import Store

MAX_BYTES=1048576
MAX_TABLES=32
MAX_ROWS=10000
MAX_SCHEMA_OBJECTS=256


def _deny():raise ValueError('synthetic_backup_unavailable')


def _label(value):
    if type(value) is not str or re.fullmatch('[A-Za-z0-9_-]{1,64}',value) is None:_deny()
    return value


def _integer(value,minimum=0,maximum=2**63-1):
    if type(value) is not int or not minimum<=value<=maximum:_deny()


def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _quote(value):return '"'+value.replace('"','""')+'"'


def _identity(value):return getattr(value,'__self__',None),getattr(value,'__func__',value)


@dataclass(frozen=True)
class VaultVersion:
    token:str


@dataclass(frozen=True)
class VaultObject:
    version:VaultVersion
    manifest:recovery.Manifest
    artifacts:tuple[recovery.Artifact,...]


class MemoryVault:
    """Explicit fake dictionary transport; plaintext, no external destination."""
    def __init__(self):self._objects={}

    def put_if_absent(self,snapshot_id,manifest,artifacts):
        _label(snapshot_id)
        if snapshot_id in self._objects or len(self._objects)>=4:_deny()
        if type(manifest) is not recovery.Manifest or type(artifacts) is not tuple or not 1<=len(artifacts)<=16 or any(type(item) is not recovery.Artifact for item in artifacts):_deny()
        total=sum(len(item.content) for item in artifacts)
        if total>4*MAX_BYTES or sum(sum(len(a.content) for a in obj.artifacts) for obj in self._objects.values())+total>4*MAX_BYTES:_deny()
        version=VaultVersion(_hash([snapshot_id,recovery.manifest_digest(manifest)]))
        self._objects[snapshot_id]=VaultObject(version,manifest,artifacts)
        return version

    def get(self,snapshot_id,version):
        _label(snapshot_id)
        result=self._objects.get(snapshot_id)
        if result is None or type(version) is not VaultVersion or result.version!=version:_deny()
        return result


@dataclass(frozen=True)
class SafeAlert:
    code:str
    snapshot_id:str
    stage:str


class BackupAdapterUnavailable(ValueError):
    def __init__(self,stage,alert_status,outcome):
        super().__init__('synthetic_backup_unavailable')
        self.stage=stage;self.alert_status=alert_status;self.outcome=outcome


@dataclass(frozen=True)
class SnapshotReceipt:
    snapshot_id:str
    object_version:VaultVersion
    manifest_digest:str
    source_name:str
    business:str
    schema_version:int
    created_at:int
    schema_fingerprint:str
    table_counts:tuple[tuple[str,int],...]
    size_bytes:int
    state:str=field(default='stored_unverified',init=False)
    encrypted:bool=field(default=False,init=False)


@dataclass(frozen=True)
class RestoreReport:
    snapshot_id:str
    schema_version:int
    schema_fingerprint:str
    table_counts:tuple[tuple[str,int],...]
    size_bytes:int
    alert_status:str
    state:str=field(default='restore_rehearsal_verified',init=False)
    encrypted:bool=field(default=False,init=False)
    live_authorized:bool=field(default=False,init=False)


def _memory_source(source):
    if type(source) is not Store or type(source.db) is not sqlite3.Connection or source.db.in_transaction:_deny()
    rows=source.db.execute('PRAGMA database_list').fetchmany(3)
    if len(rows)!=1 or tuple(rows[0])!=(0,'main',''):_deny()


def _inspect(db,*,checks):
    version=db.execute('PRAGMA user_version').fetchone()[0];_integer(version,1,2**31-1)
    pages=db.execute('PRAGMA page_count').fetchone()[0];size=db.execute('PRAGMA page_size').fetchone()[0]
    _integer(pages);_integer(size,1)
    if pages*size>MAX_BYTES:_deny()
    # Guard schema cell types/bytes in SQLite before returning definitions.
    rows=db.execute('''SELECT CASE WHEN typeof(type)='text' AND length(CAST(type AS BLOB))<=16 THEN type END,
        CASE WHEN typeof(name)='text' AND length(CAST(name AS BLOB))<=512 THEN name END,
        CASE WHEN typeof(tbl_name)='text' AND length(CAST(tbl_name AS BLOB))<=512 THEN tbl_name END,
        CASE WHEN sql IS NULL OR (typeof(sql)='text' AND length(CAST(sql AS BLOB))<=8192) THEN sql END,
        CASE WHEN sql IS NULL OR (typeof(sql)='text' AND length(CAST(sql AS BLOB))<=8192) THEN 0 ELSE 1 END
        FROM sqlite_master ORDER BY type,name LIMIT ?''',(MAX_SCHEMA_OBJECTS+1,)).fetchall()
    if len(rows)>MAX_SCHEMA_OBJECTS:_deny()
    schema=[];tables=[]
    for row in rows:
        kind,name,parent,sql,bad=tuple(row)
        if bad or kind not in ('table','index','view','trigger') or type(name) is not str or type(parent) is not str:_deny()
        if sql is not None and type(sql) is not str:_deny()
        if sql and re.match(r'\s*CREATE\s+VIRTUAL\s+TABLE\b',sql,re.I):_deny()
        schema.append((kind,name,parent,sql))
        if kind=='table':tables.append(name)
    if len(tables)>MAX_TABLES:_deny()
    counts=[];total=0
    for name in sorted(tables):
        # Bounded enumeration, including internal tables; never row contents.
        count=db.execute('SELECT COUNT(*) FROM (SELECT 1 FROM '+_quote(name)+' LIMIT ?)',(MAX_ROWS-total+1,)).fetchone()[0]
        _integer(count);total+=count
        if total>MAX_ROWS:_deny()
        counts.append((name,count))
    if checks:
        integrity=db.execute('PRAGMA integrity_check').fetchmany(2)
        if len(integrity)!=1 or integrity[0][0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone() is not None:_deny()
    return version,_hash(schema),tuple(counts)


class _Scratch:
    def __init__(self,root):
        root=Path(root)
        if not root.is_absolute():_deny()
        for component in (root,*root.parents):
            info=component.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):_deny()
        root_info=root.stat();self._root_identity=(root_info.st_dev,root_info.st_ino)
        with os.scandir(root) as entries:
            if next(entries,None) is not None:_deny()
        self.root=root;self.child=Path(tempfile.mkdtemp(prefix='synthetic-backup-',dir=root))
        info=self.child.lstat();self._child_identity=(info.st_dev,info.st_ino);self.files={}
        current=root.lstat()
        if (current.st_dev,current.st_ino)!=self._root_identity or stat.S_ISLNK(current.st_mode):_deny()
        self.path=self.child/'store.sqlite'

    def check(self):
        root=self.root.lstat();child=self.child.lstat()
        if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode) or (root.st_dev,root.st_ino)!=self._root_identity:_deny()
        if not stat.S_ISDIR(child.st_mode) or stat.S_ISLNK(child.st_mode) or (child.st_dev,child.st_ino)!=self._child_identity:_deny()

    def remember(self):
        self.check()
        info=self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o777!=0o600:_deny()
        identity=(info.st_dev,info.st_ino)
        if self.path in self.files and self.files[self.path]!=identity:_deny()
        if self.path not in self.files:self.files[self.path]=identity

    def read(self):
        self.remember()
        fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as handle:
            info=os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=self.files[self.path] or not 1<=info.st_size<=MAX_BYTES:_deny()
            value=handle.read(MAX_BYTES+1)
            if len(value)!=info.st_size or len(value)>MAX_BYTES:_deny()
            return value

    def write(self,value):
        self.check()
        if type(value) is not bytes or not 1<=len(value)<=MAX_BYTES:_deny()
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        info=os.fstat(fd);self.files[self.path]=(info.st_dev,info.st_ino)
        with os.fdopen(fd,'wb') as handle:handle.write(value)
        self.remember()

    def close(self):
        self.check()
        # Never recurse or delete a replacement/symlink/foreign file.
        for path,identity in self.files.items():
            try:
                info=path.lstat()
                if stat.S_ISREG(info.st_mode) and (info.st_dev,info.st_ino)==identity:path.unlink()
            except FileNotFoundError:pass
        try:
            info=self.child.lstat()
            if stat.S_ISDIR(info.st_mode) and (info.st_dev,info.st_ino)==self._child_identity:self.child.rmdir()
        except FileNotFoundError:pass


@contextmanager
def _sqlite_bytes(content):
    # SQLite never reopens an externally mutable scratch pathname for verification.
    if type(content) is not bytes or not 1<=len(content)<=MAX_BYTES:_deny()
    with closing(sqlite3.connect(':memory:')) as db:
        if not callable(getattr(db,'deserialize',None)):_deny()
        db.deserialize(content)
        db.execute('PRAGMA query_only=ON')
        yield db


class BackupAdapter:
    def __init__(self,*,source,source_name,business,vault,alert,scratch,enabled=False):
        self._source=source;self._source_name=source_name;self._business=business;self._vault=vault;self._alert=alert;self._scratch=scratch;self._enabled=enabled is True
        self._host=(source,getattr(source,'db',None),source_name,business,vault,_identity(getattr(vault,'put_if_absent',None)),_identity(getattr(vault,'get',None)),_identity(alert),scratch)

    def _host_check(self):
        source,db,name,business,vault,put,get,alert,scratch=self._host
        if self._source is not source or self._source.db is not db or self._source_name!=name or self._business!=business or self._vault is not vault or self._scratch is not scratch:_deny()
        for before,after in ((put,_identity(self._vault.put_if_absent)),(get,_identity(self._vault.get)),(alert,_identity(self._alert))):
            if any(a is not b for a,b in zip(before,after)):_deny()
        if type(vault) is not MemoryVault:_deny()
        _label(name);_label(business);_memory_source(source)

    def _alert_once(self,code,snapshot_id,stage):
        try:
            if any(a is not b for a,b in zip(self._host[7],_identity(self._alert))):return 'not_attempted'
            self._alert(SafeAlert(code,snapshot_id,stage));return 'callback_returned'
        except Exception:return 'callback_failed'

    def snapshot_once(self,snapshot_id,*,now):
        if not self._enabled:raise BackupAdapterUnavailable('disabled','not_attempted','not_attempted')
        stage='source_policy';label='unknown_snapshot';scratch=None
        try:
            _label(snapshot_id);label=snapshot_id;_integer(now);self._host_check()
            _inspect(self._source.db,checks=False)
            stage='scratch';scratch=_Scratch(self._scratch)
            stage='sqlite_backup';backup_core.backup(self._source,scratch.path);scratch.remember()
            stage='snapshot_observation'
            content=scratch.read()
            with _sqlite_bytes(content) as db:
                version,schema,counts=_inspect(db,checks=True)
            artifact=recovery.Artifact('store.sqlite',content)
            stage='manifest';manifest=recovery.build_manifest(source_name=self._source_name,business=self._business,schema_version=version,created_at=now,artifacts=(artifact,));digest=recovery.manifest_digest(manifest)
            self._host_check();stage='vault_put'
            object_version=self._vault.put_if_absent(snapshot_id,manifest,(artifact,))
            if type(object_version) is not VaultVersion or type(object_version.token) is not str or re.fullmatch('[0-9a-f]{64}',object_version.token) is None:_deny()
            self._host_check()
            result=SnapshotReceipt(snapshot_id,object_version,digest,self._source_name,self._business,version,now,schema,counts,len(artifact.content))
            stage='scratch_cleanup';scratch.close();scratch=None
            return result
        except Exception:
            if scratch is not None:
                try:scratch.close()
                except Exception:pass
            outcome='vault_write_unknown' if stage in ('vault_put','scratch_cleanup') else 'snapshot_failed'
            status=self._alert_once('backup_failure',label,stage)
            raise BackupAdapterUnavailable(stage,status,outcome) from None

    def verify_restore(self,receipt,*,now,max_age_seconds):
        if not self._enabled:raise BackupAdapterUnavailable('disabled','not_attempted','not_attempted')
        stage='receipt';label='unknown_snapshot';scratch=None
        try:
            if type(receipt) is not SnapshotReceipt:_deny()
            _label(receipt.snapshot_id);label=receipt.snapshot_id;_integer(now);_integer(max_age_seconds,1,2**31-1);self._host_check()
            if receipt.business!=self._business or receipt.source_name!=self._source_name or type(receipt.object_version) is not VaultVersion or type(receipt.size_bytes) is not int or not 1<=receipt.size_bytes<=MAX_BYTES:_deny()
            _integer(receipt.schema_version,1,2**31-1);_integer(receipt.created_at)
            for digest in (receipt.manifest_digest,receipt.schema_fingerprint,receipt.object_version.token):
                if type(digest) is not str or re.fullmatch('[0-9a-f]{64}',digest) is None:_deny()
            if type(receipt.table_counts) is not tuple or len(receipt.table_counts)>MAX_TABLES:_deny()
            total=0;names=[]
            for row in receipt.table_counts:
                if type(row) is not tuple or len(row)!=2 or type(row[0]) is not str or len(row[0].encode())>512:_deny()
                _integer(row[1]);total+=row[1];names.append(row[0])
            if total>MAX_ROWS or names!=sorted(set(names)):_deny()
            stage='vault_get';obj=self._vault.get(receipt.snapshot_id,receipt.object_version)
            if type(obj) is not VaultObject or obj.version!=receipt.object_version or type(obj.manifest) is not recovery.Manifest or obj.manifest.source_name!=receipt.source_name or obj.manifest.created_at!=receipt.created_at:_deny()
            self._host_check();stage='manifest_validation'
            target_name='rehearsal' if receipt.source_name!='rehearsal' else 'rehearsal_target'
            restored=recovery.dry_run_restore(obj.manifest,obj.artifacts,expected_manifest_digest=receipt.manifest_digest,expected_business=receipt.business,expected_schema_version=receipt.schema_version,required_artifacts=('store.sqlite',),now=now,max_age_seconds=max_age_seconds,target=recovery.FakeTarget(target_name,receipt.business,receipt.schema_version))
            if type(restored) is not recovery.RestorePreview or len(restored.target.artifacts)!=1 or restored.target.artifacts[0].name!='store.sqlite':_deny()
            content=restored.target.artifacts[0].content
            if len(content)!=receipt.size_bytes:_deny()
            stage='scratch';scratch=_Scratch(self._scratch);scratch.write(content)
            stage='sqlite_restore'
            pinned=scratch.read()
            if pinned!=content:_deny()
            with _sqlite_bytes(pinned) as db:
                observed=_inspect(db,checks=True)
                if observed!=(receipt.schema_version,receipt.schema_fingerprint,receipt.table_counts):_deny()
                with closing(sqlite3.connect(':memory:')) as fresh:
                    db.backup(fresh)
                    if _inspect(fresh,checks=True)!=observed:_deny()
            self._host_check();stage='scratch_cleanup';scratch.close();scratch=None
            status=self._alert_once('restore_rehearsal_ok',label,'complete')
            return RestoreReport(label,receipt.schema_version,receipt.schema_fingerprint,receipt.table_counts,receipt.size_bytes,status)
        except Exception:
            if scratch is not None:
                try:scratch.close()
                except Exception:pass
            status=self._alert_once('restore_failure',label,stage)
            raise BackupAdapterUnavailable(stage,status,'restore_failed') from None
