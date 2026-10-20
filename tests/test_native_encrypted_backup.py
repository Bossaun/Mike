"""NEW macOS CryptoKit/I65 composition. Actual AEAD cases and transport fakes named."""
from dataclasses import replace,FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import base64,json,os,sqlite3,stat,sys,time
import pytest
from goldway import native_encrypted_backup as m
from goldway import backup_adapter as old
from goldway import recovery_contract as recovery
from goldway.store import Store

NOW=1000
SECRET=b'SYNTHETIC_CONTENT_NEVER_IN_CIPHERTEXT_VAULT_LOG'


@pytest.fixture(scope='session')
def native(tmp_path_factory):
 if sys.platform!='darwin':pytest.skip('actual CryptoKit is macOS-only')
 parent=tmp_path_factory.mktemp('native-compile').resolve()
 backend=m.NativeCryptoKit(scratch=parent,enabled=True)
 backend.prepare()  # REAL installed compiler/link/health before any fixture key.
 yield backend
 backend.close()
 assert not list(parent.iterdir())


@pytest.fixture
def c(tmp_path,native):
 s=Store(':memory:');s.execute('PRAGMA user_version=7')
 s.execute('INSERT INTO events(business,id,kind,payload) VALUES(?,?,?,?)',('SYNTHETIC','fixture','synthetic',SECRET.decode()))
 s.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)');s.execute('CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id))');s.execute('INSERT INTO parent VALUES(1)');s.execute('INSERT INTO child VALUES(1,1)')
 scratch=tmp_path/'plain';scratch.mkdir(mode=0o700);vault=m.EncryptedMemoryVault();alerts=[]
 value=SimpleNamespace(s=s,native=native,key=native.new_key(),vault=vault,alerts=alerts,alert=alerts.append,scratch=scratch,tmp=tmp_path)
 yield value
 s.db.set_authorizer(None)
 if s.db.in_transaction:s.db.rollback()
 s.db.close()


def adapter(c,**kw):
 args=dict(source=c.s,source_name='synthetic_source',business='SYNTHETIC',vault=c.vault,native=c.native,alert=c.alert,scratch=c.scratch,enabled=True);args.update(kw);return m.NativeEncryptedBackup(**args)
def snapshot(c,a=None,name='snapshot'):return (a or adapter(c)).snapshot_once(name,key=c.key,now=NOW)
def verify(c,receipt,a=None,**kw):
 args=dict(key=c.key,now=NOW+1,max_age_seconds=100);args.update(kw);return (a or adapter(c)).verify_restore(receipt,**args)


def test_native_real_sqlite_manifest_aead_fakevault_auth_then_real_i65_restore(c,monkeypatch):
 calls=[];backup=old.backup_core.backup;manifest=recovery.build_manifest;restore=recovery.dry_run_restore;seal=c.native.seal;open_=c.native.open;puts=c.vault.put_if_absent;gets=c.vault.get
 def backup_call(*a,**k):
  p=backup(*a,**k);assert stat.S_IMODE(p.stat().st_mode)==0o600;calls.append('sqlite_backup');return p
 def manifest_call(**kw):calls.append('manifest');return manifest(**kw)
 def seal_call(*a):calls.append('seal');return seal(*a)
 def open_call(*a):calls.append('open');return open_(*a)
 def restore_call(*a,**kw):calls.append('dry_restore');return restore(*a,**kw)
 def put(*a):calls.append('cipher_put');return puts(*a)
 def get(*a):calls.append('cipher_get');return gets(*a)
 monkeypatch.setattr(old.backup_core,'backup',backup_call);monkeypatch.setattr(recovery,'build_manifest',manifest_call);monkeypatch.setattr(recovery,'dry_run_restore',restore_call)
 monkeypatch.setattr(c.native,'seal',seal_call);monkeypatch.setattr(c.native,'open',open_call);monkeypatch.setattr(c.vault,'put_if_absent',put);monkeypatch.setattr(c.vault,'get',get)
 a=adapter(c);changes=c.s.db.total_changes;cache_paths=set(c.native._cache.identities)
 receipt=snapshot(c,a);obj=c.vault.get(receipt.original.snapshot_id,receipt.object_version)
 assert type(receipt) is m.EncryptedReceipt and receipt.encrypted and not receipt.original.encrypted and receipt.original.state=='stored_unverified'
 assert not obj.ciphertext.startswith(b'SQLite format 3') and SECRET not in obj.ciphertext and c.key.material not in obj.ciphertext
 assert len(obj.ciphertext)==receipt.original.size_bytes+28 and receipt.original.schema_version==7
 calls.remove('cipher_get')  # Explicit test inspection is not adapter restore I/O.
 report=verify(c,receipt,a)
 assert report.encrypted_object_verified and not report.original.encrypted and not report.live_authorized
 assert report.original.table_counts==receipt.original.table_counts and report.original.schema_fingerprint==receipt.original.schema_fingerprint
 assert calls==['sqlite_backup','manifest','seal','cipher_put','cipher_get','open','dry_restore']
 assert c.s.db.total_changes==changes and c.s.execute('SELECT payload FROM events').fetchone()[0]==SECRET.decode()
 assert not list(c.scratch.iterdir()) and set(c.native._cache.identities)==cache_paths
 assert c.alerts==[old.SafeAlert('restore_rehearsal_ok','snapshot','complete')]
 assert SECRET.decode() not in repr(receipt) and c.key.material.hex() not in repr(c.key) and c.key.material.hex() not in repr(receipt)
 with pytest.raises(FrozenInstanceError):receipt.cipher_digest='changed'


@pytest.mark.parametrize('kind',['key','nonce','ciphertext','tag','aad','truncate'])
def test_native_real_aead_tamper_fails_before_any_restore_staging(c,monkeypatch,kind):
 a=adapter(c);receipt=snapshot(c,a);obj=c.vault.get(receipt.original.snapshot_id,receipt.object_version)
 key=c.key;aad=m._aad(receipt.original);ciphertext=obj.ciphertext
 if kind=='key':key=c.native.new_key()
 elif kind=='aad':aad+=b'changed'
 elif kind=='truncate':ciphertext=ciphertext[:-1]
 else:
  index={'nonce':0,'ciphertext':12,'tag':len(ciphertext)-1}[kind];ciphertext=ciphertext[:index]+bytes([ciphertext[index]^1])+ciphertext[index+1:]
 monkeypatch.setattr(recovery,'Artifact',lambda *a,**k:pytest.fail('authentication failure reached plaintext artifact'))
 monkeypatch.setattr(old.BackupAdapter,'verify_restore',lambda *a,**k:pytest.fail('authentication failure reached I65'))
 with pytest.raises(m.NativeBackupUnavailable):c.native.open(key,aad,ciphertext)


def test_native_wrapper_wrong_key_no_artifact_recovery_or_plaintext_file(c,monkeypatch):
 receipt=snapshot(c);before=set(c.scratch.iterdir());key=c.native.new_key()
 monkeypatch.setattr(recovery,'Artifact',lambda *a,**k:pytest.fail('authentication failure reached Artifact'))
 monkeypatch.setattr(old.BackupAdapter,'verify_restore',lambda *a,**k:pytest.fail('authentication failure reached restore'))
 with pytest.raises(m.NativeBackupUnavailable) as error:verify(c,receipt,key=key)
 assert error.value.stage=='native_open' and set(c.scratch.iterdir())==before


def test_native_library_generated_nonce_fresh_same_plaintext(c):
 first=c.native.seal(c.key,b'explicit-context',b'synthetic')
 second=c.native.seal(c.key,b'explicit-context',b'synthetic')
 assert first!=second and c.native.open(c.key,b'explicit-context',first)==b'synthetic' and c.native.open(c.key,b'explicit-context',second)==b'synthetic'


def test_native_key_and_plaintext_only_pipes_not_args_environment_or_cache(c,monkeypatch):
 actual=m.subprocess.Popen;observed=[]
 def start(*a,**kw):observed.append((a,kw));return actual(*a,**kw)
 monkeypatch.setattr(m.subprocess,'Popen',start)
 receipt=snapshot(c);verify(c,receipt)
 assert len(observed)==2
 for args,kw in observed:
  assert args[0]==[str(c.native._binary),'crypt'] and kw['shell'] is False
  assert set(kw['env'])=={'PATH','TMPDIR'}
  text=repr((args,kw['env']))
  assert SECRET.decode() not in text and c.key.material.hex() not in text and base64.b64encode(c.key.material).decode() not in text
 assert 'material=' not in repr(c.key)


@pytest.mark.parametrize('size',[1,1048576])
def test_native_real_payload_caps_roundtrip(native,size):
 key=native.new_key();plaintext=b'x'*size;box=native.seal(key,b'bounded-context',plaintext)
 assert len(box)==size+28 and native.open(key,b'bounded-context',box)==plaintext


@pytest.mark.parametrize('kind',['file','attached','transaction'])
def test_native_memory_source_gate_before_content_and_seal(c,monkeypatch,kind):
 source=c.s;other=None
 if kind=='file':other=Store(str(c.tmp/'synthetic-file.sqlite'));other.execute('PRAGMA user_version=7');source=other
 elif kind=='attached':source.execute("ATTACH DATABASE ':memory:' AS extra")
 else:source.execute('BEGIN IMMEDIATE')
 monkeypatch.setattr(c.native,'seal',lambda *a:pytest.fail('invalid source reached native seal'))
 reads=[]
 def authorizer(action,table,column,*unused):
  if action==sqlite3.SQLITE_READ:reads.append((table,column));return sqlite3.SQLITE_DENY
  return sqlite3.SQLITE_OK
 source.db.set_authorizer(authorizer)
 try:
  with pytest.raises(m.NativeBackupUnavailable):snapshot(c,adapter(c,source=source))
  assert not reads and not list(c.scratch.iterdir()) and not c.vault._objects
 finally:
  source.db.set_authorizer(None)
  if source.db.in_transaction:source.db.rollback()
  if other:other.db.close()


def test_native_source_snapshot_larger_than_i65_cap_never_sealed(c,monkeypatch):
 c.s.execute('CREATE TABLE big(payload BLOB)');c.s.execute('INSERT INTO big VALUES(?)',(b'x'*(1048576),))
 monkeypatch.setattr(c.native,'seal',lambda *a:pytest.fail('oversized source reached native seal'))
 with pytest.raises(m.NativeBackupUnavailable):snapshot(c)
 assert not c.vault._objects


def test_native_ambiguous_cipher_put_no_retry_or_compensating_delete(c,monkeypatch):
 actual=c.vault.put_if_absent;calls=[]
 def put(*a):calls.append(1);actual(*a);raise RuntimeError('synthetic ambiguous write')
 monkeypatch.setattr(c.vault,'put_if_absent',put)
 with pytest.raises(m.NativeBackupUnavailable) as error:snapshot(c)
 assert error.value.outcome=='vault_write_unknown' and calls==[1] and len(c.vault._objects)==1


def test_native_duplicate_vault_id_never_overwrites(c):
 first=snapshot(c);stored=c.vault.get('snapshot',first.object_version)
 with pytest.raises(m.NativeBackupUnavailable):snapshot(c)
 assert c.vault.get('snapshot',first.object_version) is stored


@pytest.mark.parametrize('kind',['manifest','version','original','ciphertext'])
def test_native_vault_substitution_denies_before_real_restore(c,monkeypatch,kind):
 receipt=snapshot(c);obj=c.vault.get('snapshot',receipt.object_version)
 if kind=='manifest':changed=replace(obj,manifest=replace(obj.manifest,business='OTHER'))
 elif kind=='version':changed=replace(obj,version=m.CipherVersion('0'*64))
 elif kind=='original':changed=replace(obj,original=replace(obj.original,schema_version=8))
 else:changed=replace(obj,ciphertext=obj.ciphertext[:-1]+bytes([obj.ciphertext[-1]^1]))
 c.vault._objects['snapshot']=changed
 monkeypatch.setattr(old.BackupAdapter,'verify_restore',lambda *a,**k:pytest.fail('substitution reached real restore'))
 with pytest.raises(m.NativeBackupUnavailable):verify(c,receipt)


def test_native_real_closed_noncanonical_input_protocol_rejects(native):
 request=m._json(dict(version=1,mode='seal',key=base64.b64encode(native.new_key().material).decode(),aad=base64.b64encode(b'aad').decode(),payload=base64.b64encode(b'fixture').decode()))
 malformed=[request+b'\n',request.replace(b'"version":1',b'"version":true'),request[:-1]+b',"version":1}',request.replace(b'"mode":"seal"',b'"mode":"other"')]
 for value in malformed:
  with pytest.raises(m.NativeBackupUnavailable):m._run([str(native._binary),'crypt'],value,cwd=native._cache.root,env={'PATH':'/usr/bin:/bin','TMPDIR':str(native._cache.root/'tmp')},timeout=30,out_cap=m.MAX_FRAME)


@pytest.mark.parametrize('version',[1.5,1.9])
def test_native_real_fractional_version_rejects(native,version):
 request=m._json(dict(version=version,mode='seal',key=base64.b64encode(native.new_key().material).decode(),aad=base64.b64encode(b'aad').decode(),payload=base64.b64encode(b'fixture').decode()))
 with pytest.raises(m.NativeBackupUnavailable) as error:
  m._run([str(native._binary),'crypt'],request,cwd=native._cache.root,env={'PATH':'/usr/bin:/bin','TMPDIR':str(native._cache.root/'tmp')},timeout=30,out_cap=m.MAX_FRAME)
 assert error.value.stage=='subprocess_failed'


@pytest.mark.parametrize('kind',['empty_plain','large_plain','large_aad','empty_aad','bad_key'])
def test_protocol_preflight_bounds_before_child(c,monkeypatch,kind):
 monkeypatch.setattr(m,'_run',lambda *a,**k:pytest.fail('invalid bounded protocol reached subprocess'))
 key,aad,payload=c.key,b'aad',b'fixture'
 if kind=='empty_plain':payload=b''
 elif kind=='large_plain':payload=b'x'*(m.MAX_PLAIN+1)
 elif kind=='large_aad':aad=b'a'*(m.MAX_AAD+1)
 elif kind=='empty_aad':aad=b''
 else:key=b'not a typed ephemeral key'
 with pytest.raises(m.NativeBackupUnavailable):c.native.seal(key,aad,payload)


@pytest.mark.parametrize('kind',['json','extra','bool_version','duplicate','base64','oversize'])
def test_fake_native_response_protocol_rejects_without_fallback(c,monkeypatch,kind):
 response={'version':1,'mode':'seal','payload':base64.b64encode(b'x'*35).decode()}
 if kind=='json':value=b'{' 
 elif kind=='extra':value=m._json(dict(response,extra=True))
 elif kind=='bool_version':value=m._json(dict(response,version=True))
 elif kind=='duplicate':value=m._json(response)[:-1]+b',"version":1}'
 elif kind=='base64':value=m._json(dict(response,payload='@@@'))
 else:value=m._json(dict(response,payload=base64.b64encode(b'x'*(m.MAX_BOX+1)).decode()))
 monkeypatch.setattr(m,'_run',lambda *a,**k:value)
 with pytest.raises(m.NativeBackupUnavailable):c.native.seal(c.key,b'aad',b'fixture')


def test_disabled_constructor_and_operations_no_source_subprocess_or_keys(tmp_path,monkeypatch):
 def fail(*a,**k):pytest.fail('disabled external effect')
 monkeypatch.setattr(m,'_run',fail);monkeypatch.setattr(m.secrets,'token_bytes',fail)
 backend=m.NativeCryptoKit(scratch=tmp_path)
 with pytest.raises(m.NativeBackupUnavailable):backend.prepare()
 component=m.NativeEncryptedBackup(source=None,source_name='x',business='x',vault=None,native=backend,alert=fail,scratch=tmp_path)
 with pytest.raises(m.NativeBackupUnavailable):component.snapshot_once('x',key=None,now=1000)
 assert not list(tmp_path.iterdir())


def test_unsupported_platform_gate_before_files_or_compile(tmp_path,monkeypatch):
 monkeypatch.setattr(m.sys,'platform','linux');monkeypatch.setattr(m,'_run',lambda *a,**k:pytest.fail('unsupported platform child'))
 with pytest.raises(m.NativeBackupUnavailable) as error:m.NativeCryptoKit(scratch=tmp_path,enabled=True).prepare()
 assert error.value.stage=='unsupported_platform' and not list(tmp_path.iterdir())


def test_fake_compile_failure_no_snapshot_key_or_fallback_and_owned_cache_cleanup(tmp_path,monkeypatch):
 def fail(*a,**k):raise m.NativeBackupUnavailable('subprocess_failed')
 monkeypatch.setattr(m,'_run',fail);monkeypatch.setattr(m.secrets,'token_bytes',lambda *a:pytest.fail('compile failure generated key'))
 with pytest.raises(m.NativeBackupUnavailable):m.NativeCryptoKit(scratch=tmp_path.resolve(),enabled=True).prepare()
 assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('mode',['stdin_timeout','stdout_limit','stderr_limit','nonzero'])
def test_transport_actual_bounded_pipes_kill_reap_and_stdin_deadline(tmp_path,monkeypatch,mode):
 actual=m.subprocess.Popen;children=[]
 def start(*a,**kw):p=actual(*a,**kw);children.append(p);return p
 monkeypatch.setattr(m.subprocess,'Popen',start)
 scripts={'stdin_timeout':'import time;time.sleep(30)','stdout_limit':'import os;os.write(1,b"x"*65536)','stderr_limit':'import os;os.write(2,b"x"*65536)','nonzero':'raise SystemExit(3)'}
 began=time.monotonic()
 with pytest.raises(m.NativeBackupUnavailable):m._run([sys.executable,'-c',scripts[mode]],b'x'*m.MAX_FRAME if mode=='stdin_timeout' else b'',cwd=tmp_path,env={'PATH':'/usr/bin:/bin'},timeout=.4,out_cap=128,err_cap=128)
 assert time.monotonic()-began<5 and len(children)==1 and children[0].poll() is not None
 assert all(stream.closed for stream in (children[0].stdin,children[0].stdout,children[0].stderr))


def test_owned_cache_regular_replacement_never_adopted_or_deleted(tmp_path):
 cache=m._OwnedCache(tmp_path.resolve());file=cache.root/'owned';file.write_bytes(b'fixed code');os.chmod(file,0o600);cache.remember();file.unlink();file.write_bytes(b'foreign fixture');os.chmod(file,0o600)
 with pytest.raises(m.NativeBackupUnavailable):cache.remember()
 with pytest.raises(m.NativeBackupUnavailable):cache.close()
 assert file.read_bytes()==b'foreign fixture'
 # Explicit fixture cleanup only; production refused foreign deletion.
 file.unlink();cache.identities.pop(file);cache.close()


def test_owned_cache_symlink_escape_refused_no_foreign_read_delete(tmp_path):
 cache=m._OwnedCache(tmp_path.resolve());foreign=tmp_path/'foreign';foreign.write_bytes(b'foreign');(cache.root/'link').symlink_to(foreign)
 with pytest.raises(m.NativeBackupUnavailable):cache.remember()
 with pytest.raises(m.NativeBackupUnavailable):cache.close()
 assert foreign.read_bytes()==b'foreign'
 (cache.root/'link').unlink();cache.close()
