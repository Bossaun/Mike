"""Owner-curated verified releases, quiet without changes, on the existing worker."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class VerifiedUpdates:
    def __init__(self,engine,path='/data/verified_updates.json'):
        self.e=engine;self.path=Path(path);self.next_check=0
        engine.s.execute('CREATE TABLE IF NOT EXISTS verified_updates(business TEXT,id TEXT,outbox_key TEXT,PRIMARY KEY(business,id))')

    def tick(self):
        e=self.e;now=e.now()
        if now<self.next_check:return
        self.next_check=now+60
        if not self.path.is_file() or self.path.stat().st_size>16000:return
        c=json.loads(self.path.read_text())
        if c.get('enabled') is not True or 'test_operator' not in e.c['admins'] or not e.c.get('live_send'):return
        local=datetime.fromtimestamp(now,ZoneInfo('Europe/Moscow'))
        if not 10<=local.hour<19:return
        last=e.s.execute("SELECT state,created FROM outbox WHERE business=? AND dedupe LIKE 'verified-release:%' ORDER BY id DESC LIMIT 1",(e.b,)).fetchone()
        if last and (last['state']!='sent' or now-last['created']<4*3600):return
        entries=c.get('releases',[])
        if not isinstance(entries,list) or len(entries)>100:return
        pending=[]
        for item in entries:
            if not isinstance(item,dict):continue
            ident=item.get('id');text=item.get('text');sha=item.get('sha');at=item.get('verified_at')
            if not isinstance(ident,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',ident):continue
            if item.get('deployment_success') is not True or item.get('runtime_verified') is not True:continue
            if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{40}',sha):continue
            if type(at) not in (int,float) or not 0<at<=now:continue
            if not isinstance(text,str) or not 5<=len(text)<=300 or re.search(r'https?://|@',text):continue
            if e.s.execute('SELECT 1 FROM verified_updates WHERE business=? AND id=?',(e.b,ident)).fetchone():continue
            pending.append(item)
        if not pending:return
        pending=pending[:3]
        key='verified-release:'+hashlib.sha256('|'.join(x['id'] for x in pending).encode()).hexdigest()[:24]
        body='Привет, Майк! Что нового у Валли:\n\n'+'\n\n'.join('• '+x['text'] for x in pending)
        with e.s.transaction():
            e.s.queue(e.b,'test_operator',body,key,now,preferred_transport='bot')
            for x in pending:e.s.execute('INSERT INTO verified_updates VALUES(?,?,?)',(e.b,x['id'],key))
