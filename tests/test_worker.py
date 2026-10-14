import signal
import goldway.cli as cli
from goldway.model import ScriptedModel


def test_worker_cycle_runs_without_google_credentials(env,action,monkeypatch):
    e,s,c,clock=env
    c['sheets_write']=False
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','unit-test-token')
    for name in ('GOOGLE_SERVICE_ACCOUNT_FILE','GOOGLE_SHEETS_ACCESS_TOKEN'):
        monkeypatch.delenv(name,raising=False)
    class Model:
        def propose(self,context):
            if context['kind']=='transcript':return {'actions':[action]}
            return {'actions':[{'op':'update','task_id':context['tasks'][0]['id'],'status':'completed','evidence_lines':[1]}]}
    e.model=Model()
    class API:
        def __init__(self,*_):self.polls=0;self.sent=[];self.client=self
        def close(self):pass
        def call(self,method,**args):
            if method=='getMe':return {'id':999}
            if method=='sendMessage':self.sent.append(args);return {'message_id':len(self.sent)}
            if method=='getUpdates':
                self.polls+=1
                if self.polls==1:
                    return [{'update_id':1,'message':{'from':{'id':1},'chat':{'id':11,'type':'private'},'date':int(clock[0]),'message_id':1,'text':action['evidence']}},
                            {'update_id':2,'message':{'from':{'id':2},'chat':{'id':22,'type':'private'},'date':int(clock[0]),'message_id':2,'text':'/start'}}]
                if self.polls==2:
                    row=s.rows("SELECT * FROM outbox WHERE task_id IS NOT NULL AND state='sent'")[0]
                    return [{'update_id':3,'message':{'from':{'id':2},'chat':{'id':22,'type':'private'},'date':int(clock[0]),'message_id':3,'text':'Список завершён: накладные 18 и 19 сверены, результат отправлен.','reply_to_message':{'message_id':int(row['message_id'])}}}]
                return []
            raise AssertionError('Unexpected API method')
    monkeypatch.setattr(cli,'Telegram',API)
    previous={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:cli.run(e,run_seconds=.1)
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
    assert e.tasks()[0]['status']=='completed'
    assert s.rows("SELECT * FROM runtime WHERE key='GOLDWAY:heartbeat'")
    assert s.execute("SELECT value FROM runtime WHERE key='GOLDWAY:offset'").fetchone()[0]=='4'
    assert not s.rows("SELECT * FROM events WHERE state!='done'")
