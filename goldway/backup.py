"""Consistent SQLite backup, including WAL state, without overwriting prior snapshots."""
from pathlib import Path
import sqlite3

def backup(store,destination):
    path=Path(destination)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb'):
        pass
    path.chmod(0o600)
    try:
        with sqlite3.connect(str(path)) as target:
            store.db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeError('Backup integrity check failed')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
