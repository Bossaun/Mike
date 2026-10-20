"""Default-disabled macOS CryptoKit composition over unchanged synthetic I65.

I65 plaintext0600 scratch exists. This native layer uses key/data pipes only.
No server support, persistent key custody, offsite storage, worker or live export.
"""
from dataclasses import dataclass,field,asdict
from pathlib import Path
import base64,hashlib,json,os,re,secrets,selectors,signal,sqlite3,stat,subprocess,sys,tempfile,time
from . import backup_adapter as plain
from . import recovery_contract as recovery
from .store import Store

MAX_PLAIN=1048576
MAX_BOX=MAX_PLAIN+1024
MAX_FRAME=2*1048576
MAX_AAD=4096
MAX_STDERR=4096
SWIFTC='/Library/Developer/CommandLineTools/usr/bin/swiftc'
_SOURCE=Path(__file__).with_name('backup_crypto.swift')


class NativeBackupUnavailable(ValueError):
    def __init__(self,stage,outcome='not_completed'):
        super().__init__('native_encrypted_backup_unavailable')
        self.stage=stage;self.outcome=outcome

def _deny(stage):raise NativeBackupUnavailable(stage)
def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def _hash(value):return hashlib.sha256(value).hexdigest()
def _method(value):return (getattr(value,'__self__',None),getattr(value,'__func__',value))
def _label(value):
    if type(value) is not str or re.fullmatch('[A-Za-z0-9_-]{1,64}',value) is None:_deny('metadata')
def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:_deny('metadata')
def _int(value):
    if type(value) is not int or not 0<=value<2**63:_deny('metadata')
def _identity(path,directory=False):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid!=os.getuid() or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode)):_deny('path_identity')
    return (info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode),stat.S_IMODE(info.st_mode))
def _read(path,identity,cap):
    if _identity(path)!=identity:_deny('path_identity')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if (info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode),stat.S_IMODE(info.st_mode))!=identity or info.st_size>cap:_deny('path_identity')
        value=b''
        while len(value)<=cap:
            part=os.read(fd,min(65536,cap+1-len(value)))
            if not part:break
            value+=part
        if len(value)>cap or _identity(path)!=identity:_deny('path_identity')
        return value
    finally:os.close(fd)


def _run(arguments,payload,*,cwd,env,timeout,out_cap,err_cap=MAX_STDERR):
    """Bounded simultaneous pipe transport; no shell or unbounded capture."""
    if type(payload) is not bytes or len(payload)>MAX_FRAME:_deny('frame')
    process=None;selector=selectors.DefaultSelector();out=bytearray();err=bytearray();offset=0
    try:
        process=subprocess.Popen(arguments,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            cwd=cwd,env=env,shell=False,close_fds=True,start_new_session=True)
        for stream,kind in ((process.stdout,'out'),(process.stderr,'err')):
            os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,kind)
        if payload:
            os.set_blocking(process.stdin.fileno(),False);selector.register(process.stdin,selectors.EVENT_WRITE,'in')
        else:process.stdin.close()
        deadline=time.monotonic()+timeout
        while selector.get_map():
            remaining=deadline-time.monotonic()
            if remaining<=0:_deny('subprocess_timeout')
            for key,_ in selector.select(min(.1,remaining)):
                if key.data=='in':
                    try:count=os.write(key.fileobj.fileno(),payload[offset:offset+65536])
                    except BlockingIOError:continue
                    offset+=count
                    if offset==len(payload):selector.unregister(key.fileobj);key.fileobj.close()
                else:
                    target,cap=(out,out_cap) if key.data=='out' else (err,err_cap)
                    try:part=os.read(key.fileobj.fileno(),min(65536,cap+1-len(target)))
                    except BlockingIOError:continue
                    if not part:selector.unregister(key.fileobj);key.fileobj.close()
                    else:
                        if len(target)+len(part)>cap:_deny('subprocess_output')
                        target.extend(part)
        code=process.wait(timeout=max(.001,deadline-time.monotonic()))
        if code!=0:_deny('subprocess_failed')
        return bytes(out)
    except BaseException as error:
        if process is not None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            try:process.wait(timeout=5)
            except Exception:pass
        if isinstance(error,(NativeBackupUnavailable,KeyboardInterrupt,SystemExit,GeneratorExit)):raise
        raise NativeBackupUnavailable('subprocess_failed') from None
    finally:
        selector.close()
        if process is not None:
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None:stream.close()


class _OwnedCache:
    def __init__(self,parent):
        if type(parent) is not Path and not isinstance(parent,Path):_deny('cache_parent')
        if not parent.is_absolute() or '..' in parent.parts or parent.resolve(strict=True)!=parent:_deny('cache_parent')
        self.parent=parent;self.parent_identity=_identity(parent,True)
        self.root=Path(tempfile.mkdtemp(prefix='native-backup-',dir=parent));os.chmod(self.root,0o700)
        self.identities={};self.remember()
    def _parent(self):
        if _identity(self.parent,True)!=self.parent_identity:_deny('path_identity')
    def _tree(self):
        self._parent();result={};pending=[self.root];total=0
        while pending:
            path=pending.pop();info=path.lstat()
            directory=stat.S_ISDIR(info.st_mode);identity=_identity(path,directory)
            result[path]=identity
            if len(result)>4096:_deny('cache_limit')
            if directory:
                with os.scandir(path) as entries:
                    for entry in entries:pending.append(path/entry.name)
            else:
                total+=info.st_size
                if total>256*1048576:_deny('cache_limit')
        return result
    def remember(self):
        current=self._tree()
        for path,identity in self.identities.items():
            if current.get(path)!=identity:_deny('path_identity')
        self.identities.update(current)
    def check(self):
        if self._tree()!=self.identities:_deny('path_identity')
    def close(self):
        self.check()
        for path in sorted(self.identities,key=lambda p:len(p.parts),reverse=True):
            identity=self.identities[path]
            if _identity(path,identity[2]==stat.S_IFDIR)!=identity:_deny('path_identity')
            if identity[2]==stat.S_IFDIR:path.rmdir()
            else:path.unlink()
        self.identities.clear()


@dataclass(frozen=True)
class EphemeralKey:
    material:bytes=field(repr=False)
    def __post_init__(self):
        if type(self.material) is not bytes or len(self.material)!=32:_deny('key')


class NativeCryptoKit:
    def __init__(self,*,scratch,enabled=False):
        self._scratch=scratch;self._enabled=enabled is True;self._cache=None;self._binary=None;self._source_identity=None;self._source_hash=None;self._binary_identity=None;self._binary_hash=None
    def prepare(self):
        if not self._enabled:_deny('disabled')
        if sys.platform!='darwin':_deny('unsupported_platform')
        if self._cache is not None:_deny('already_prepared')
        try:
            identity=_identity(_SOURCE);source=_read(_SOURCE,identity,32768)
            if not source:_deny('native_source')
            compiler=Path(SWIFTC)
            if not compiler.is_absolute() or not compiler.is_file():_deny('compiler_unavailable')
            cache=_OwnedCache(self._scratch);self._cache=cache
            local=cache.root/'helper.swift'
            fd=os.open(local,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(source)
            except BaseException:raise
            (cache.root/'modules').mkdir(mode=0o700);(cache.root/'tmp').mkdir(mode=0o700)
            cache.remember();binary=cache.root/'helper'
            env={'PATH':'/usr/bin:/bin','TMPDIR':str(cache.root/'tmp')}
            _run([SWIFTC,'-sdk','/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk','-module-cache-path',str(cache.root/'modules'),str(local),'-o',str(binary)],b'',cwd=cache.root,env=env,timeout=60,out_cap=65536)
            os.chmod(binary,0o700);cache.remember()
            binary_identity=_identity(binary);binary_data=_read(binary,binary_identity,16*1048576)
            if _read(local,cache.identities[local],32768)!=source or _read(_SOURCE,identity,32768)!=source:_deny('native_source')
            health=_run([str(binary),'health'],b'',cwd=cache.root,env=env,timeout=30,out_cap=128)
            if health!=b'goldway-cryptokit-v1\n':_deny('native_health')
            cache.check();self._source_identity=identity;self._source_hash=_hash(source);self._binary=binary;self._binary_identity=binary_identity;self._binary_hash=_hash(binary_data)
        except BaseException as error:
            if self._cache is not None:
                try:self._cache.remember();self._cache.close()
                except BaseException:pass
            self._cache=None
            if isinstance(error,(NativeBackupUnavailable,KeyboardInterrupt,SystemExit,GeneratorExit)):raise
            raise NativeBackupUnavailable('compile_or_health_failed') from None
    def _check(self):
        if not self._enabled or self._cache is None or self._binary is None:_deny('native_not_ready')
        self._cache.check()
        if _hash(_read(_SOURCE,self._source_identity,32768))!=self._source_hash or _hash(_read(self._binary,self._binary_identity,16*1048576))!=self._binary_hash:_deny('native_identity')
    def new_key(self):self._check();return EphemeralKey(secrets.token_bytes(32))
    def _crypt(self,mode,key,aad,payload):
        self._check()
        if type(key) is not EphemeralKey:_deny('key')
        key.__post_init__();material=key.material
        if type(aad) is not bytes or not 1<=len(aad)<=MAX_AAD or type(payload) is not bytes or not 1<=len(payload)<=(MAX_PLAIN if mode=='seal' else MAX_BOX):_deny('frame')
        request=_json(dict(version=1,mode=mode,key=base64.b64encode(material).decode(),aad=base64.b64encode(aad).decode(),payload=base64.b64encode(payload).decode()))
        if len(request)>MAX_FRAME:_deny('frame')
        result=_run([str(self._binary),'crypt'],request,cwd=self._cache.root,env={'PATH':'/usr/bin:/bin','TMPDIR':str(self._cache.root/'tmp')},timeout=30,out_cap=MAX_FRAME)
        self._check()
        if key.material is not material:_deny('key_identity')
        def pairs(values):
            result={}
            for k,v in values:
                if k in result:_deny('native_protocol')
                result[k]=v
            return result
        try:
            obj=json.loads(result,object_pairs_hook=pairs)
            if type(obj) is not dict or set(obj)!={'version','mode','payload'} or type(obj['version']) is not int or obj['version']!=1 or obj['mode']!=mode or type(obj['payload']) is not str or _json(obj)!=result:_deny('native_protocol')
            output=base64.b64decode(obj['payload'],validate=True)
            if base64.b64encode(output).decode()!=obj['payload'] or not 1<=len(output)<=(MAX_BOX if mode=='seal' else MAX_PLAIN):_deny('native_protocol')
            if mode=='seal' and len(output)!=len(payload)+28:_deny('native_protocol')
            return output
        except NativeBackupUnavailable:raise
        except Exception:raise NativeBackupUnavailable('native_protocol') from None
    def seal(self,key,aad,payload):return self._crypt('seal',key,aad,payload)
    def open(self,key,aad,payload):return self._crypt('open',key,aad,payload)
    def close(self):
        if self._cache is not None:self._cache.close();self._cache=None;self._binary=None


def _original(receipt):
    if type(receipt) is not plain.SnapshotReceipt or type(receipt.object_version) is not plain.VaultVersion:_deny('receipt')
    for value in (receipt.snapshot_id,receipt.business,receipt.source_name):_label(value)
    for value in (receipt.object_version.token,receipt.manifest_digest,receipt.schema_fingerprint):_digest(value)
    _int(receipt.created_at)
    if type(receipt.schema_version) is not int or not 1<=receipt.schema_version<2**31 or type(receipt.size_bytes) is not int or not 1<=receipt.size_bytes<=MAX_PLAIN or receipt.encrypted is not False or receipt.state!='stored_unverified':_deny('receipt')
    if type(receipt.table_counts) is not tuple or len(receipt.table_counts)>32:_deny('receipt')
    names=[];count=0
    for pair in receipt.table_counts:
        if type(pair) is not tuple or len(pair)!=2 or type(pair[0]) is not str or not 0<len(pair[0].encode())<=512:_deny('receipt')
        _int(pair[1]);count+=pair[1];names.append(pair[0])
    if names!=sorted(set(names)) or count>10000:_deny('receipt')
    encoded=_json(asdict(receipt))
    if len(encoded)>32768:_deny('receipt')
    return encoded

def _aad(receipt):
    encoded=_original(receipt)
    aad=_json(dict(domain='goldway-synthetic-backup-aesgcm-v1',version=1,snapshot_id=receipt.snapshot_id,
        business=receipt.business,source_name=receipt.source_name,schema_version=receipt.schema_version,
        manifest_digest=receipt.manifest_digest,original_receipt=_hash(encoded)))
    if len(aad)>MAX_AAD:_deny('aad')
    return aad


@dataclass(frozen=True)
class CipherVersion:
    token:str


@dataclass(frozen=True)
class CipherObject:
    version:CipherVersion
    manifest:recovery.Manifest
    original:plain.SnapshotReceipt
    ciphertext:bytes=field(repr=False)


class EncryptedMemoryVault:
    """Explicit ciphertext-only fake, never network/offsite storage."""
    def __init__(self):self._objects={}
    def put_if_absent(self,snapshot_id,manifest,original,ciphertext):
        _label(snapshot_id);aad=_aad(original)
        if original.snapshot_id!=snapshot_id or type(manifest) is not recovery.Manifest or recovery.manifest_digest(manifest)!=original.manifest_digest:_deny('vault_metadata')
        if type(ciphertext) is not bytes or not 28<len(ciphertext)<=MAX_BOX:_deny('vault_ciphertext')
        if snapshot_id in self._objects or len(self._objects)>=4 or sum(len(v.ciphertext) for v in self._objects.values())+len(ciphertext)>4*MAX_BOX:_deny('vault_capacity')
        version=CipherVersion(_hash(aad+ciphertext))
        self._objects[snapshot_id]=CipherObject(version,manifest,original,ciphertext)
        return version
    def get(self,snapshot_id,version):
        _label(snapshot_id)
        if type(version) is not CipherVersion:_deny('vault_version')
        _digest(version.token);value=self._objects.get(snapshot_id)
        if value is None or value.version!=version:_deny('vault_version')
        return value


@dataclass(frozen=True)
class EncryptedReceipt:
    original:plain.SnapshotReceipt
    object_version:CipherVersion
    cipher_digest:str
    aad_digest:str
    encrypted:bool=field(default=True,init=False)
    state:str=field(default='ciphertext_stored_unverified',init=False)
    live_authorized:bool=field(default=False,init=False)


@dataclass(frozen=True)
class EncryptedRestoreReport:
    original:plain.RestoreReport
    encrypted_object_verified:bool=field(default=True,init=False)
    live_authorized:bool=field(default=False,init=False)


class NativeEncryptedBackup:
    def __init__(self,*,source,source_name,business,vault,native,alert,scratch,enabled=False):
        self._source=source;self._name=source_name;self._business=business;self._vault=vault;self._native=native;self._alert=alert;self._scratch=scratch;self._enabled=enabled is True
        self._host=(source,getattr(source,'db',None),source_name,business,vault,_method(getattr(vault,'put_if_absent',None)),
            _method(getattr(vault,'get',None)),native,getattr(native,'_cache',None),_method(getattr(native,'seal',None)),
            _method(getattr(native,'open',None)),alert,_method(alert),scratch)
    def _check(self):
        source,db,name,business,vault,put,get,native,cache,seal,open_,alert,alert_method,scratch=self._host
        if (not self._enabled or self._source is not source or type(source) is not Store or source.db is not db
                or self._name!=name or self._business!=business or self._vault is not vault or type(vault) is not EncryptedMemoryVault
                or self._native is not native or type(native) is not NativeCryptoKit or native._cache is not cache
                or self._alert is not alert or not callable(alert) or self._scratch is not scratch):_deny('host')
        for before,after in ((put,_method(vault.put_if_absent)),(get,_method(vault.get)),(seal,_method(native.seal)),(open_,_method(native.open)),(alert_method,_method(alert))):
            if any(a is not b for a,b in zip(before,after)):_deny('host')
        _label(name);_label(business);native._check()
        # Metadata-only before I65 content/snapshot, never a real file source.
        if type(db) is not sqlite3.Connection or db.in_transaction:_deny('source_policy')
        rows=db.execute('PRAGMA database_list').fetchmany(3)
        if len(rows)!=1 or tuple(rows[0])!=(0,'main',''):_deny('source_policy')
    def snapshot_once(self,snapshot_id,*,key,now):
        if not self._enabled:_deny('disabled')
        stage='preflight'
        try:
            _label(snapshot_id);_int(now);self._check()
            if type(key) is not EphemeralKey:_deny('key')
            key.__post_init__()
            # Accepted I65 PLAINTEXT0600 scratch and memory staging, unchanged.
            staging=plain.MemoryVault();adapter=plain.BackupAdapter(source=self._source,source_name=self._name,business=self._business,vault=staging,alert=self._alert,scratch=self._scratch,enabled=True)
            stage='snapshot';receipt=adapter.snapshot_once(snapshot_id,now=now);self._check()
            obj=staging.get(snapshot_id,receipt.object_version)
            if type(obj) is not plain.VaultObject or len(obj.artifacts)!=1 or obj.artifacts[0].name!='store.sqlite':_deny('snapshot')
            aad=_aad(receipt);stage='native_seal'
            ciphertext=self._native.seal(key,aad,obj.artifacts[0].content);self._check()
            if _aad(receipt)!=aad:_deny('receipt')
            stage='vault_put';version=self._vault.put_if_absent(snapshot_id,obj.manifest,receipt,ciphertext)
            self._check()
            if type(version) is not CipherVersion or version.token!=_hash(aad+ciphertext):_deny('vault_version')
            return EncryptedReceipt(receipt,version,_hash(ciphertext),_hash(aad))
        except BaseException as error:
            if not isinstance(error,Exception):raise
            if isinstance(error,NativeBackupUnavailable) and stage=='preflight':raise
            raise NativeBackupUnavailable(stage,'vault_write_unknown' if stage=='vault_put' else 'not_completed') from None
    def verify_restore(self,receipt,*,key,now,max_age_seconds):
        if not self._enabled:_deny('disabled')
        stage='receipt'
        try:
            self._check();_int(now);_int(max_age_seconds)
            if max_age_seconds<1 or type(receipt) is not EncryptedReceipt or type(receipt.object_version) is not CipherVersion:_deny('receipt')
            for value in (receipt.object_version.token,receipt.cipher_digest,receipt.aad_digest):_digest(value)
            original=receipt.original;aad=_aad(original)
            if original.business!=self._business or original.source_name!=self._name or receipt.aad_digest!=_hash(aad):_deny('receipt')
            stage='vault_get';obj=self._vault.get(original.snapshot_id,receipt.object_version);self._check()
            if (type(obj) is not CipherObject or obj.original!=original or type(obj.manifest) is not recovery.Manifest
                    or recovery.manifest_digest(obj.manifest)!=original.manifest_digest or type(obj.ciphertext) is not bytes
                    or not 28<len(obj.ciphertext)<=MAX_BOX or _aad(original)!=aad):_deny('vault_metadata')
            stage='native_open';content=self._native.open(key,aad,obj.ciphertext);self._check()
            if _aad(original)!=aad or _hash(obj.ciphertext)!=receipt.cipher_digest or len(content)!=original.size_bytes:_deny('receipt')
            # Only authenticated plaintext reaches Artifact/staging/recovery.
            stage='restore_staging';artifact=recovery.Artifact('store.sqlite',content);staging=plain.MemoryVault()
            version=staging.put_if_absent(original.snapshot_id,obj.manifest,(artifact,))
            if version!=original.object_version:_deny('restore_version')
            adapter=plain.BackupAdapter(source=self._source,source_name=self._name,business=self._business,vault=staging,alert=self._alert,scratch=self._scratch,enabled=True)
            stage='restore';report=adapter.verify_restore(original,now=now,max_age_seconds=max_age_seconds)
            self._check()
            if type(report) is not plain.RestoreReport or report.encrypted is not False or report.live_authorized is not False:_deny('restore_report')
            return EncryptedRestoreReport(report)
        except BaseException as error:
            if not isinstance(error,Exception):raise
            raise NativeBackupUnavailable(stage,'restore_not_completed') from None
