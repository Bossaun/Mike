import sqlite3
import pytest
from goldway.backup import backup
from goldway.store import Store

def test_backup_restores_tasks_events_outbox(env,seed,tmp_path):
    e,s,*_=env;target=tmp_path/'snap.sqlite';backup(s,target)
    other=Store(str(target))
    assert other.rows('SELECT * FROM tasks')[0]['id']==seed['id']
    assert other.rows('SELECT * FROM events') and other.rows('SELECT * FROM outbox')
    assert other.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_backup_does_not_overwrite_existing_file(env,tmp_path):
    _,s,*_=env;target=tmp_path/'existing.sqlite';target.write_text('keep')
    with pytest.raises(FileExistsError):backup(s,target)
    assert target.read_text()=='keep'
