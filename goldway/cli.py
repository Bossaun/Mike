import argparse
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path
from .config import load
from .store import Store
from .engine import Engine
from .model import JsonModel, ScriptedModel
from .telegram import Telegram, Dispatcher, Updates, bootstrap_groups
from .sheets import Sheets

ALLOWED_UPDATES=['my_chat_member','message','edited_message','business_connection','business_message','edited_business_message','deleted_business_messages']

def make_model(s,c,fixture=None):
    if fixture:
        return ScriptedModel(json.loads(Path(fixture).read_text()))
    key=os.environ.get('MODEL_API_KEY')
    if not key: raise ValueError('MODEL_API_KEY missing; use --fixture only for explicit offline tests')
    return JsonModel(s,c['business'],c['model'],key)

def pending(e, updates=None, should_stop=lambda:False):
    for ev in e.s.rows("SELECT id,kind,payload FROM events WHERE business=? AND state='pending' LIMIT 20",(e.b,)):
        if should_stop():break
        try:
            if ev['kind']=='telegram_failure' and updates is not None:
                updates.handle(json.loads(ev['payload']))
                e.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(e.b,ev['id']))
            else: e.process(ev['id'])
        except Exception as exc:
            e.s.execute("UPDATE events SET state='failed',error=? WHERE business=? AND id=?",(type(exc).__name__,e.b,ev['id']))
            print(f"Event {ev['id']}: {type(exc).__name__}; inspect local events.error",file=sys.stderr)


def register_team_bot_identity(config, bot):
    """Bind the closed team reply gate to the authenticated getMe identity."""
    if 'team_intake' not in config:
        return
    from .intake import validate_team
    ids, account, _ = validate_team(config)
    if (not isinstance(bot, dict) or type(bot.get('id')) is not int
            or bot['id'] <= 0 or bot['id'] in ids or bot['id'] == account
            or bot.get('is_bot') is not True):
        raise ValueError('Closed team requires an authenticated distinct bot identity')
    if ('bot_user_id_verified' in config
            and type(config['bot_user_id_verified']) is not bool):
        raise ValueError('Closed team bot verification metadata must be boolean')
    if 'bot_user_id' in config:
        configured = config['bot_user_id']
        if (type(configured) is not int or configured <= 0
                or configured in ids or configured == account):
            raise ValueError('Closed team bot identity must be distinct and confirmed')
        if config.get('bot_user_id_verified') is True and configured != bot['id']:
            raise ValueError('Authenticated bot differs from the confirmed team bot identity')
    candidate = dict(config, bot_user_id=bot['id'], bot_user_id_verified=True)
    validate_team(candidate)
    config.update(bot_user_id=bot['id'], bot_user_id_verified=True)


def run(e, run_seconds=None):
    if 'controlled_intake' in e.c:
        # Validate even when callers constructed Engine without config.load.
        from .config import load
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as f:
            json.dump(e.c,f);f.flush();load(f.name)
        if run_seconds is None or not 0<run_seconds<=300:
            raise ValueError('Controlled intake requires explicit --run-seconds <=300')
        remaining=e.c['controlled_intake']['ends_at']-e.now()
        if remaining<=0:
            raise ValueError('Controlled intake window expired')
        run_seconds=min(run_seconds,remaining)
    e.c['availability']='test_window' if run_seconds is not None or e.c.get('browser_test') is True else 'running_worker'
    # Only one worker may send from a database. Lock lives beside persistent DB.
    token=os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token: raise ValueError('TELEGRAM_BOT_TOKEN missing')
    api=Telegram(token); bot=api.call('getMe')
    register_team_bot_identity(e.c, bot)
    if bot.get('username'):e.c['bot_username']=bot['username']
    bootstrap_groups(e.s,e.c)
    updates=Updates(e,api,bot['id']); dispatch=Dispatcher(e.s,e.c,api); dispatch.recover()
    sheets=None
    if e.c.get('sheets_write'):
        import gspread
        if os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE'):
            from google.oauth2.service_account import Credentials
            credentials=Credentials.from_service_account_file(os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'],scopes=['https://www.googleapis.com/auth/spreadsheets'])
        elif os.environ.get('GOOGLE_SHEETS_ACCESS_TOKEN'):
            from google.oauth2.credentials import Credentials
            credentials=Credentials(token=os.environ['GOOGLE_SHEETS_ACCESS_TOKEN'])
        else: raise ValueError('Google service-account file or Sheets access token required')
        sheets=Sheets(e,gspread.authorize(credentials).open_by_key(e.c['spreadsheet_id']))
    stop=False
    def shutdown(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,shutdown); signal.signal(signal.SIGINT,shutdown)
    last_sheet=0
    deadline=time.monotonic()+run_seconds if run_seconds is not None else None
    while not stop and (deadline is None or time.monotonic()<deadline):
        offset=e.s.execute('SELECT value FROM runtime WHERE key=?',(e.b+':offset',)).fetchone()
        try:
            batch=api.call('getUpdates',offset=int(offset[0]) if offset else 0,timeout=min(25,max(0,int(deadline-time.monotonic()))) if deadline is not None else 25,allowed_updates=ALLOWED_UPDATES)
            for u in batch:
                # Processing update + persisted offset is atomic. Events can be handled after commit.
                try:
                    updates.handle(u)
                except Exception as exc:
                    e.s.event(e.b,f"telegram-failure:{u['update_id']}",'telegram_failure',u)
                    e.s.execute("UPDATE events SET state='failed',error=? WHERE business=? AND id=?",(type(exc).__name__,e.b,f"telegram-failure:{u['update_id']}"))
                    print('Telegram update failed:',u['update_id'],type(exc).__name__,file=sys.stderr)
                with e.s.transaction():
                    e.s.execute('INSERT INTO runtime VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(e.b+':offset',str(u['update_id']+1)))
        except Exception as exc:
            print('Telegram polling:',type(exc).__name__,file=sys.stderr)
            time.sleep(5)
        if stop:break
        pending(e,updates,lambda:stop)
        if stop:break
        e.tick()
        from datetime import datetime
        day=datetime.fromtimestamp(time.time(),e.tz).date().isoformat()
        if e.c.get('daily_summary',True):e.queue_summary('daily:'+day)
        dispatch.flush(lambda:stop)
        if stop:break
        e.s.execute('INSERT INTO runtime VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(e.b+':heartbeat',str(time.time())))
        if sheets and time.time()-last_sheet>=e.c.get('sheet_sync_seconds',120):
            try: sheets.sync()
            except Exception as exc:
                print('Sheets sync:',type(exc).__name__,file=sys.stderr)
            last_sheet=time.time()
    api.client.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',default='config.json'); p.add_argument('--db',default='state/goldway.sqlite')
    sub=p.add_subparsers(dest='command',required=True)
    i=sub.add_parser('ingest'); i.add_argument('file'); i.add_argument('--fixture')
    imp=sub.add_parser('import-tasks'); imp.add_argument('file'); imp.add_argument('--source',required=True); imp.add_argument('--apply',action='store_true')
    sub.add_parser('summary'); worker=sub.add_parser('run'); worker.add_argument('--run-seconds',type=int); sub.add_parser('status'); sub.add_parser('health')
    snapshot=sub.add_parser('backup'); snapshot.add_argument('destination')
    r=sub.add_parser('retry-event'); r.add_argument('id')
    resolve=sub.add_parser('resolve-delivery'); resolve.add_argument('id',type=int); resolve.add_argument('outcome',choices=['sent','not-sent']); resolve.add_argument('--message-id')
    args=p.parse_args(); c=load(args.config)
    Path(args.db).parent.mkdir(parents=True,exist_ok=True)
    s=Store(args.db)
    if args.command=='backup':
        from .backup import backup
        print('Verified backup:',backup(s,args.destination)); return
    if args.command=='health':
        value=s.execute('SELECT value FROM runtime WHERE key=?',(c['business']+':heartbeat',)).fetchone()
        if not value or time.time()-float(value[0])>180: raise SystemExit(1)
        print('worker heartbeat recent; does not prove external delivery'); return
    if args.command=='status':
        print(json.dumps({'tasks':s.rows('SELECT status,COUNT(*) count FROM tasks GROUP BY status'),
            'events':s.rows('SELECT state,COUNT(*) count FROM events GROUP BY state'),
            'outbox':s.rows('SELECT state,COUNT(*) count FROM outbox GROUP BY state')},ensure_ascii=False,indent=2)); return
    if args.command=='resolve-delivery':
        row=s.execute("SELECT * FROM outbox WHERE id=? AND business=? AND state='unknown'",(args.id,c['business'])).fetchone()
        if not row: raise ValueError('Only unknown deliveries can be reconciled')
        if args.outcome=='sent' and not args.message_id: raise ValueError('Provide observed Telegram message ID')
        s.execute('UPDATE outbox SET state=?,message_id=?,error=NULL WHERE id=?',('sent' if args.outcome=='sent' else 'pending',args.message_id,args.id))
        s.audit(c['business'],'manual_delivery_resolution',{'id':args.id,'outcome':args.outcome},time.time()); return
    if args.command=='retry-event':
        s.execute("UPDATE events SET state='pending',error=NULL WHERE business=? AND id=? AND state='failed'",(c['business'],args.id)); return
    model=make_model(s,c,getattr(args,'fixture',None)) if args.command in ('ingest','run') else None
    e=Engine(s,c,model)
    if args.command=='import-tasks':
        from .importer import import_tasks
        print(json.dumps(import_tasks(e,json.loads(Path(args.file).read_text()),args.source,args.apply),ensure_ascii=False,indent=2))
    elif args.command=='ingest':
        id=e.transcript(Path(args.file).read_text(encoding='utf-8-sig'),str(Path(args.file).resolve()))
        e.process(id); print(e.summary())
    elif args.command=='summary': print(e.summary())
    elif args.command=='run':
        with open(args.db+'.lock','w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            run(e,args.run_seconds)

if __name__=='__main__': main()
