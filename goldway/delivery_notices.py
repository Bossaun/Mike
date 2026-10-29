"""Idempotent manager receipts for confirmed sends, never for queued attempts."""
import json
import hashlib
from pathlib import Path


def digest_config(c):
    return hashlib.sha256(json.dumps(c,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def seal_queue(store,business,dedupe,path,cfg):
    store.execute('''CREATE TABLE IF NOT EXISTS work_delivery_guards(
        business TEXT,outbox_id INTEGER,path TEXT,digest TEXT,PRIMARY KEY(business,outbox_id,path))''')
    row=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(business,dedupe)).fetchone()
    if row:store.execute('INSERT OR IGNORE INTO work_delivery_guards VALUES(?,?,?,?)',(business,row[0],str(path),digest_config(cfg)))


def permitted(store,business,row):
    if not row['dedupe'].startswith(('delivery-notice:','work-reply:','work-followup:','work-digest:')):return True
    try:
        guards=store.rows('SELECT path,digest FROM work_delivery_guards WHERE business=? AND outbox_id=?',(business,row['id']))
        if not guards:raise ValueError('missing_guard')
        for r in guards:
            p=Path(r['path'])
            if p.stat().st_size>40000:raise ValueError('large_config')
            c=json.loads(p.read_text())
            if c.get('enabled') is not True or digest_config(c)!=r['digest']:raise ValueError('revoked')
        return True
    except Exception:
        store.execute("UPDATE outbox SET state='cancelled',error='Work notification authorization changed' WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",(business,row['id']))
        return False


class DeliveryNotices:
    def __init__(self, store, config, now, path='/data/delivery_notices.json'):
        self.s, self.c, self.now, self.path = store, config, now, path
        self.cfg = self.read()

    def read(self):
        try:
            p = Path(self.path)
            if p.stat().st_size > 2000:return None
            c = json.loads(p.read_text())
            if (set(c) != {'enabled','manager','started_at','transport'}
                    or c['enabled'] is not True or c['manager'] not in self.c.get('admins', [])
                    or c['transport'] not in ('business','business_or_bot')
                    or type(c['started_at']) not in (int,float) or c['started_at'] <= 0):return None
            return c
        except (OSError,ValueError,TypeError):return None

    def record(self, source, recipient, text, mid, at):
        cfg=self.cfg
        if not cfg or self.read()!=cfg or at<cfg['started_at'] or not str(mid).isdigit():return
        # The administrator's own messages, including these receipts, never recurse.
        if recipient==cfg['manager'] or source.startswith('delivery-notice:'):return
        name=self.c.get('employees',{}).get(recipient,{}).get('name',recipient)
        body=f'Я отправил сообщение: {name}.\n\n{text[:450]}\n\nКопия для вас.'
        key='delivery-notice:'+source
        self.s.queue(self.c['business'],cfg['manager'],body,key,
                     self.now(),preferred_transport='business_only' if cfg['transport']=='business' else 'private_only')
        seal_queue(self.s,self.c['business'],key,self.path,cfg)

    def tick(self):
        if not self.cfg or self.read()!=self.cfg:return
        # Recovery is safe: durable source receipt + stable unique dedupe key.
        for r in self.s.rows("""SELECT o.*,MAX(d.at) AS delivered_at FROM outbox o
            JOIN deliveries d ON d.outbox_id=o.id AND d.state='sent'
            WHERE o.business=? AND o.state='sent' AND d.at>=? AND o.recipient!=?
            AND o.dedupe NOT LIKE 'delivery-notice:%' GROUP BY o.id""",
            (self.c['business'],self.cfg['started_at'],self.cfg['manager'])):
            self.record('outbox:'+str(r['id']),r['recipient'],r['text'],r['message_id'],r['delivered_at'])
