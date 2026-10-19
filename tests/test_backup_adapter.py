"""Real SQLite backup, actual recovery core, independent fresh SQLite rehearsal."""
from dataclasses import replace,FrozenInstanceError,fields
from pathlib import Path
import json
import os
import sqlite3
import stat
from types import SimpleNamespace
import pytest
from goldway import backup_adapter as module
from goldway.store import Store
from goldway.engine import Engine
from goldway.intelligence import Intelligence
from goldway.knowledge import KnowledgeIndex
from goldway.telegram import Dispatcher

NOW=1000


def adapter(c,**kw):
 values=dict(source=c.s,source_name='synthetic_source',business='SYNTHETIC',vault=c.vault,alert=c.alert,scratch=c.scratch,enabled=True);values.update(kw)
 return module.BackupAdapter(**values)


@pytest.fixture
def case(tmp_path,monkeypatch):
 s=Store(':memory:');s.execute('PRAGMA user_version=7')
 s.execute("INSERT INTO events(business,id,kind,payload) VALUES('SYNTHETIC','event','synthetic','PRIVATE_EVENT_TEXT')")
 s.execute("INSERT INTO sources(business,id,body,link) VALUES('SYNTHETIC','source','PRIVATE_SOURCE_TEXT','local')")
 s.execute("INSERT INTO tasks(business,id,title,owner,next_step,criterion,status,source_id,created) VALUES('SYNTHETIC','task','PRIVATE_TASK_TEXT','operator','next','check','confirmed','source',1)")
 s.queue('SYNTHETIC','operator','PRIVATE_OUTBOX_TEXT','outbox',1)
 s.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)');s.execute('CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id))');s.execute('INSERT INTO parent VALUES(1)');s.execute('INSERT INTO child VALUES(1,1)')
 vault=module.MemoryVault();alerts=[];scratch=tmp_path/'scratch';scratch.mkdir()
 def alert(event):alerts.append(event)
 c=SimpleNamespace(s=s,vault=vault,alerts=alerts,alert=alert,scratch=scratch,tmp=tmp_path);c.a=adapter(c)
 def fail(*a,**kw):pytest.fail('unexpected constructor/network/Engine/model/config/key/dispatch operation')
 for cls in (Store,Engine,Intelligence,KnowledgeIndex,Dispatcher):monkeypatch.setattr(cls,'__init__',fail)
 import socket,httpx,subprocess
 monkeypatch.setattr(socket,'socket',fail);monkeypatch.setattr(httpx,'Client',fail);monkeypatch.setattr(subprocess,'Popen',fail)
 yield c
 if s.db.in_transaction:s.db.rollback()
 s.db.set_authorizer(None);s.db.close()


def snapshot(c,name='snapshot',**kw):return c.a.snapshot_once(name,now=kw.get('now',NOW))


def verify(c,receipt,**kw):return c.a.verify_restore(receipt,now=kw.get('now',NOW+1),max_age_seconds=kw.get('max_age_seconds',100))


def contents(c):
 names=[r[0] for r in c.s.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
 return {name:c.s.rows('SELECT * FROM '+module._quote(name)) for name in names}


def spy_vault(c,monkeypatch):
 counts={'put':0,'get':0};put=c.vault.put_if_absent;get=c.vault.get
 def wrapped_put(*a):counts['put']+=1;return put(*a)
 def wrapped_get(*a):counts['get']+=1;return get(*a)
 monkeypatch.setattr(c.vault,'put_if_absent',wrapped_put);monkeypatch.setattr(c.vault,'get',wrapped_get);c.a=adapter(c)
 return counts


def test_real_memory_backup_manifest_fakevault_real_dryrun_fresh_sqlite_rows_no_live_replay(case,monkeypatch):
 before=contents(case);changes=case.s.db.total_changes;counts=spy_vault(case,monkeypatch);calls=[];paths=[];actual_backup=module.backup_core.backup;actual_manifest=module.recovery.build_manifest;actual_restore=module.recovery.dry_run_restore;actual_inspect=module._inspect;rehearsed=[]
 def backup(*a):
  result=actual_backup(*a);paths.append(result);assert stat.S_IMODE(result.stat().st_mode)==0o600;calls.append('backup');return result
 def manifest(**kw):calls.append('manifest');return actual_manifest(**kw)
 def restore(*a,**kw):calls.append('dry_run');assert kw['target'].artifacts==() and kw['required_artifacts']==('store.sqlite',);return actual_restore(*a,**kw)
 def inspect(db,**kw):
  result=actual_inspect(db,**kw)
  if kw['checks'] and db.execute('PRAGMA database_list').fetchone()[2]=='':
   rehearsed.append(db.execute("SELECT payload FROM events WHERE id='event'").fetchone()[0])
   assert db.execute("SELECT state FROM outbox WHERE dedupe='outbox'").fetchone()[0]=='pending'
  return result
 monkeypatch.setattr(module.backup_core,'backup',backup);monkeypatch.setattr(module.recovery,'build_manifest',manifest);monkeypatch.setattr(module.recovery,'dry_run_restore',restore);monkeypatch.setattr(module,'_inspect',inspect)
 receipt=snapshot(case);assert receipt.state=='stored_unverified' and not receipt.encrypted and receipt.schema_version==7
 report=verify(case,receipt)
 assert report.state=='restore_rehearsal_verified' and report.alert_status=='callback_returned' and not report.encrypted and not report.live_authorized
 assert calls==['backup','manifest','dry_run'] and counts=={'put':1,'get':1} and rehearsed==['PRIVATE_EVENT_TEXT']*3
 assert report.table_counts==receipt.table_counts and report.schema_fingerprint==receipt.schema_fingerprint and report.size_bytes==receipt.size_bytes
 assert contents(case)==before and case.s.db.total_changes==changes and not case.s.db.in_transaction
 assert not list(case.scratch.iterdir()) and all(not p.exists() for p in paths)
 assert case.alerts==[module.SafeAlert('restore_rehearsal_ok','snapshot','complete')]
 assert all(len(fields(a))==3 and 'PRIVATE_' not in repr(a) and str(case.scratch) not in repr(a) for a in case.alerts)
 with pytest.raises(FrozenInstanceError):receipt.schema_version=1


def test_source_mutation_after_snapshot_does_not_change_original_observations(case):
 receipt=snapshot(case);case.s.execute("INSERT INTO events(business,id,kind,payload) VALUES('SYNTHETIC','later','synthetic','LATER')");case.s.execute('PRAGMA user_version=8')
 report=verify(case,receipt);assert report.schema_version==7 and dict(report.table_counts)['events']==1 and case.s.execute('PRAGMA user_version').fetchone()[0]==8


@pytest.mark.parametrize('method',['snapshot','restore'])
def test_default_disabled_zero_sql_files_vault_alert(case,monkeypatch,method):
 a=adapter(case,enabled=False)
 def fail(*args,**kw):pytest.fail('disabled external effect')
 case.s.db.set_authorizer(fail);monkeypatch.setattr(Path,'lstat',fail);monkeypatch.setattr(module.tempfile,'mkdtemp',fail)
 with pytest.raises(module.BackupAdapterUnavailable) as error:
  if method=='snapshot':a.snapshot_once('snapshot',now=NOW)
  else:a.verify_restore(None,now=NOW,max_age_seconds=1)
 assert error.value.stage=='disabled' and not case.alerts and not case.vault._objects


@pytest.mark.parametrize('kind',['file','attached','transaction','version_zero'])
def test_unsafe_source_refused_before_backup_or_content(case,monkeypatch,kind):
 if kind=='file':
  db=sqlite3.connect(case.tmp/'synthetic-file.sqlite',isolation_level=None);source=object.__new__(Store);source.db=db
 elif kind=='attached':case.s.execute("ATTACH ':memory:' AS other");source=case.s
 else:
  source=case.s
  if kind=='transaction':case.s.execute('BEGIN')
  else:case.s.execute('PRAGMA user_version=0')
 def guard(action,table,column,*a):
  if action==sqlite3.SQLITE_READ:pytest.fail('unsafe source content read')
  return sqlite3.SQLITE_OK
 source.db.set_authorizer(guard)
 monkeypatch.setattr(module.backup_core,'backup',lambda *a:pytest.fail('unsafe backup'))
 with pytest.raises(module.BackupAdapterUnavailable):adapter(case,source=source).snapshot_once('snapshot',now=NOW)
 source.db.set_authorizer(None)
 if kind=='file':db.close()
 assert not case.vault._objects and not list(case.scratch.iterdir())


@pytest.mark.parametrize('kind',['bytes','rows','tables','schema_objects','schema_sql'])
def test_source_caps_before_snapshot_bytes_allocation_or_unbounded_enumeration(case,monkeypatch,kind):
 if kind=='bytes':case.s.execute("INSERT INTO sources VALUES('SYNTHETIC','large',?,'local',0)",(b'x'*module.MAX_BYTES,))
 elif kind=='rows':case.s.db.executemany('INSERT INTO parent(id) VALUES(?)',((i,) for i in range(2,10002)))
 elif kind=='tables':
  n=case.s.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
  for i in range(33-n):case.s.execute('CREATE TABLE extra_'+str(i)+'(id)')
 elif kind=='schema_objects':
  for i in range(257):case.s.execute('CREATE VIEW view_'+str(i)+' AS SELECT 1')
 else:case.s.execute("CREATE VIEW huge_definition AS SELECT '"+'x'*8193+"'")
 monkeypatch.setattr(module.backup_core,'backup',lambda *a:pytest.fail('oversize source reached backup'))
 with pytest.raises(module.BackupAdapterUnavailable):snapshot(case)
 assert not case.vault._objects and not list(case.scratch.iterdir())


def test_sql_identifiers_are_quoted_exactly(case):
 case.s.execute('CREATE TABLE "odd""name"(id)');case.s.execute('INSERT INTO "odd""name" VALUES(1)')
 receipt=snapshot(case);report=verify(case,receipt);assert dict(report.table_counts)['odd"name']==1


@pytest.mark.parametrize('name',['../escape','/absolute','bad/name','bad name','x'*65,'','bad\nname','🔥'])
def test_snapshot_labels_never_choose_paths_or_echo_unsafe_input(case,name):
 with pytest.raises(module.BackupAdapterUnavailable) as error:snapshot(case,name)
 assert error.value.stage=='source_policy' and case.alerts[-1].snapshot_id=='unknown_snapshot'
 assert not case.vault._objects and not list(case.scratch.iterdir())


@pytest.mark.parametrize('kind',['nonempty','symlink_root','symlink_parent','missing','relative'])
def test_scratch_no_follow_overwrite_or_foreign_cleanup(case,kind):
 foreign=case.tmp/'foreign';foreign.mkdir();marker=foreign/'keep';marker.write_text('FOREIGN')
 if kind=='nonempty':path=case.scratch;(path/'keep').write_text('FOREIGN')
 elif kind=='symlink_root':path=case.tmp/'link';path.symlink_to(foreign,target_is_directory=True)
 elif kind=='symlink_parent':parent=case.tmp/'parentlink';parent.symlink_to(case.tmp,target_is_directory=True);path=parent/'scratch'
 elif kind=='missing':path=case.tmp/'missing'
 else:path=Path('relative-scratch')
 with pytest.raises(module.BackupAdapterUnavailable):adapter(case,scratch=path).snapshot_once('snapshot',now=NOW)
 assert marker.read_text()=='FOREIGN' and not case.vault._objects
 if kind=='nonempty':assert (path/'keep').read_text()=='FOREIGN'


def test_no_snapshot_overwrite_one_put_no_retry(case,monkeypatch):
 calls=spy_vault(case,monkeypatch);first=snapshot(case);obj=case.vault._objects['snapshot']
 with pytest.raises(module.BackupAdapterUnavailable) as error:snapshot(case)
 assert error.value.outcome=='vault_write_unknown' and case.vault._objects['snapshot'] is obj and calls['put']==2
 assert not list(case.scratch.iterdir()) and first.state=='stored_unverified'


def test_fake_vault_four_object_capacity_whole_refusal(case):
 for i in range(4):snapshot(case,'snapshot_'+str(i))
 with pytest.raises(module.BackupAdapterUnavailable):snapshot(case,'fifth')
 assert len(case.vault._objects)==4 and not list(case.scratch.iterdir())


def test_fake_vault_total_bytes_cap_and_exclusive_semantics():
 v=module.MemoryVault();artifact=module.recovery.Artifact('store.sqlite',b'x'*module.MAX_BYTES)
 manifest=module.recovery.build_manifest(source_name='source',business='SYNTHETIC',schema_version=7,created_at=NOW,artifacts=(artifact,))
 for i in range(4):v.put_if_absent('s'+str(i),manifest,(artifact,))
 with pytest.raises(ValueError):v.put_if_absent('fifth',manifest,(artifact,))
 assert len(v._objects)==4


@pytest.mark.parametrize('fault',['put_before','put_after','get'])
def test_vault_exception_is_safe_ambiguous_and_never_retried(case,monkeypatch,fault):
 calls=[];put=case.vault.put_if_absent;get=case.vault.get
 def broken_put(*a):
  calls.append('put')
  if fault=='put_after':put(*a)
  raise TimeoutError('SECRET_KEY /private/path PRIVATE_ROW')
 def broken_get(*a):calls.append('get');raise TimeoutError('SECRET_KEY /private/path PRIVATE_ROW')
 if fault=='get':
  receipt=snapshot(case);monkeypatch.setattr(case.vault,'get',broken_get)
 else:monkeypatch.setattr(case.vault,'put_if_absent',broken_put)
 case.a=adapter(case)
 with pytest.raises(module.BackupAdapterUnavailable,match='^synthetic_backup_unavailable$') as error:
  if fault=='get':verify(case,receipt)
  else:snapshot(case)
 assert calls==['get' if fault=='get' else 'put'] and not list(case.scratch.iterdir())
 assert 'SECRET' not in repr(case.alerts) and '/private/path' not in repr(error.value)
 if fault.startswith('put'):assert error.value.outcome=='vault_write_unknown'


@pytest.mark.parametrize('failure',[False,True])
def test_alert_failure_separate_from_backup_and_restore_result_no_recursive_retry(case,failure):
 calls=[]
 def alert(event):calls.append(event);raise RuntimeError('private alert details')
 case.a=adapter(case,alert=alert)
 if failure:
  case.s.execute('PRAGMA user_version=0')
  with pytest.raises(module.BackupAdapterUnavailable) as error:snapshot(case)
  assert error.value.alert_status=='callback_failed' and error.value.outcome=='snapshot_failed'
 else:
  receipt=snapshot(case);report=verify(case,receipt);assert report.state=='restore_rehearsal_verified' and report.alert_status=='callback_failed' and receipt.state=='stored_unverified'
 assert len(calls)==1 and 'private alert' not in repr(calls)


@pytest.mark.parametrize('kind',['corrupt','truncate','manifest_and_bytes','wrong_version','wrong_business','wrong_schema','missing','extra'])
def test_vault_tamper_original_receipt_whole_refusal(case,kind):
 receipt=snapshot(case);obj=case.vault._objects['snapshot'];artifact=obj.artifacts[0]
 if kind in ('corrupt','truncate'):changed=replace(artifact,content=(b'corrupt'+artifact.content[7:]) if kind=='corrupt' else artifact.content[:-1]);obj=replace(obj,artifacts=(changed,))
 elif kind=='manifest_and_bytes':
  changed=replace(artifact,content=b'x'*100);manifest=module.recovery.build_manifest(source_name=obj.manifest.source_name,business=obj.manifest.business,schema_version=7,created_at=NOW,artifacts=(changed,));obj=replace(obj,manifest=manifest,artifacts=(changed,))
 elif kind=='wrong_version':obj=replace(obj,version=module.VaultVersion('f'*64))
 elif kind=='wrong_business':obj=replace(obj,manifest=replace(obj.manifest,business='OTHER'))
 elif kind=='wrong_schema':obj=replace(obj,manifest=replace(obj.manifest,schema_version=8))
 elif kind=='missing':obj=replace(obj,artifacts=())
 else:obj=replace(obj,artifacts=(artifact,module.recovery.Artifact('extra.txt',b'x')))
 case.vault._objects['snapshot']=obj
 with pytest.raises(module.BackupAdapterUnavailable):verify(case,receipt)
 assert case.alerts[-1].code=='restore_failure' and not list(case.scratch.iterdir())


def test_valid_hash_non_sqlite_never_becomes_verified(case):
 receipt=snapshot(case);obj=case.vault._objects['snapshot'];artifact=module.recovery.Artifact('store.sqlite',b'NOT SQLITE BUT HASH VALID')
 manifest=module.recovery.build_manifest(source_name=receipt.source_name,business=receipt.business,schema_version=7,created_at=NOW,artifacts=(artifact,))
 case.vault._objects['snapshot']=replace(obj,manifest=manifest,artifacts=(artifact,))
 # Even an explicitly supplied expected digest does not substitute SQLite checks.
 declared=replace(receipt,manifest_digest=module.recovery.manifest_digest(manifest),size_bytes=len(artifact.content))
 with pytest.raises(module.BackupAdapterUnavailable) as error:verify(case,declared)
 assert error.value.stage=='sqlite_restore' and not list(case.scratch.iterdir())


@pytest.mark.parametrize('field,value',[('schema_fingerprint','f'*64),('table_counts',(('invented',1),)),('schema_version',8),('object_version',module.VaultVersion('f'*64)),('business','OTHER'),('source_name','other')])
def test_original_receipt_observations_bound_restore(case,field,value):
 receipt=snapshot(case)
 with pytest.raises(module.BackupAdapterUnavailable):verify(case,replace(receipt,**{field:value}))
 assert case.alerts[-1].code=='restore_failure'


@pytest.mark.parametrize('now,age',[(999,100),(1101,100),(1001,0),(True,10)])
def test_explicit_age_future_and_time_bounds(case,now,age):
 receipt=snapshot(case)
 with pytest.raises(module.BackupAdapterUnavailable):verify(case,receipt,now=now,max_age_seconds=age)
 assert not list(case.scratch.iterdir())


def test_foreign_key_violation_cannot_be_stored_as_checked_snapshot(case):
 case.s.execute('PRAGMA foreign_keys=OFF');case.s.execute('INSERT INTO child VALUES(2,999)');case.s.execute('PRAGMA foreign_keys=ON')
 with pytest.raises(module.BackupAdapterUnavailable):snapshot(case)
 assert not case.vault._objects and not list(case.scratch.iterdir())


def test_oversize_actual_scratch_file_refuses_before_read_allocation(case,monkeypatch):
 actual=module.backup_core.backup
 def oversized(*a):
  path=actual(*a)
  with path.open('ab') as file:file.write(b'x'*module.MAX_BYTES)
  return path
 monkeypatch.setattr(module.backup_core,'backup',oversized)
 with pytest.raises(module.BackupAdapterUnavailable):snapshot(case)
 assert not case.vault._objects and not list(case.scratch.iterdir())


@pytest.mark.parametrize('change',['source','connection','vault','alert','scratch'])
def test_captured_host_identity_drift_never_rebinds(case,change):
 if change=='source':case.a._source=object.__new__(Store);case.a._source.db=case.s.db
 elif change=='connection':case.a._host=(case.s,object(),*case.a._host[2:])
 elif change=='vault':case.a._vault=module.MemoryVault()
 elif change=='alert':case.a._alert=lambda event:pytest.fail('replacement alert called')
 else:case.a._scratch=Path(str(case.scratch))
 with pytest.raises(module.BackupAdapterUnavailable):snapshot(case)
 assert not case.vault._objects and not list(case.scratch.iterdir())


def test_owned_child_replaced_with_symlink_never_reads_or_deletes_foreign_file(case,monkeypatch):
 foreign=case.tmp/'foreign';foreign.mkdir();marker=foreign/'store.sqlite';marker.write_bytes(b'FOREIGN_DATABASE_SENTINEL');marker.chmod(0o600)
 actual=module.backup_core.backup
 def changed(*a):
  path=actual(*a);child=path.parent;child.rename(case.scratch/'moved-owned-child');child.symlink_to(foreign,target_is_directory=True)
  return path
 monkeypatch.setattr(module.backup_core,'backup',changed)
 with pytest.raises(module.BackupAdapterUnavailable):snapshot(case)
 assert marker.read_bytes()==b'FOREIGN_DATABASE_SENTINEL' and not case.vault._objects


@pytest.mark.parametrize('operation',['snapshot','restore'])
@pytest.mark.parametrize('kind',['regular','symlink'])
def test_first_pinned_file_replacement_never_read_or_adopted_or_deleted(case,monkeypatch,operation,kind):
 receipt=snapshot(case) if operation=='restore' else None
 foreign=case.tmp/'foreign-bytes';foreign.write_bytes(b'FOREIGN_PIN_SENTINEL');foreign.chmod(0o600)
 original=module._Scratch.remember;opened=module.os.open;connect=module.sqlite3.connect;injected=[]
 def remember(scratch):
  original(scratch)
  if not injected:
   path=scratch.path;path.rename(path.parent/'displaced-owned.sqlite')
   if kind=='regular':path.write_bytes(b'FOREIGN_PIN_SENTINEL');path.chmod(0o600)
   else:path.symlink_to(foreign)
   injected.append(path)
 def open_guard(path,*a,**kw):
  if injected and Path(path)==injected[0]:pytest.fail('foreign replacement reached open/read')
  return opened(path,*a,**kw)
 def connect_guard(path,*a,**kw):
  assert not kw.get('uri'),'verification reopened mutable pathname'
  return connect(path,*a,**kw)
 monkeypatch.setattr(module._Scratch,'remember',remember);monkeypatch.setattr(module.os,'open',open_guard);monkeypatch.setattr(module.sqlite3,'connect',connect_guard)
 with pytest.raises(module.BackupAdapterUnavailable):
  if operation=='snapshot':snapshot(case)
  else:verify(case,receipt)
 assert injected and foreign.read_bytes()==b'FOREIGN_PIN_SENTINEL'
 assert injected[0].is_symlink() if kind=='symlink' else injected[0].read_bytes()==b'FOREIGN_PIN_SENTINEL'
 if operation=='snapshot':assert not case.vault._objects
