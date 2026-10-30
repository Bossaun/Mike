import json
from datetime import datetime,timezone
from test_group_work import r
from goldway.verified_updates import VerifiedUpdates


def test_quiet_without_verified_changes_and_one_delivery(r,tmp_path):
    r.clock[0]=datetime(2026,10,30,10,tzinfo=timezone.utc).timestamp();r.e.c['live_send']=True
    p=tmp_path/'releases.json';u=VerifiedUpdates(r.e,p);u.tick();assert not r.s.rows('SELECT * FROM outbox')
    p.write_text(json.dumps({'enabled':True,'releases':[{'id':'fix','sha':'a'*40,'text':'Исправлены ответы по контексту.','verified_at':r.clock[0]-1,'deployment_success':True,'runtime_verified':False}]}))
    r.clock[0]+=61;u.tick();assert not r.s.rows('SELECT * FROM outbox')
    c=json.loads(p.read_text());c['releases'][0]['runtime_verified']=True;p.write_text(json.dumps(c))
    r.clock[0]+=61;u.tick();rows=r.s.rows('SELECT * FROM outbox');assert len(rows)==1 and rows[0]['recipient']=='test_operator'
    r.clock[0]+=86400;VerifiedUpdates(r.e,p).tick();assert len(r.s.rows('SELECT * FROM outbox'))==1


def test_release_cooldown_and_unknown_not_repeated(r,tmp_path):
    r.clock[0]=datetime(2026,10,30,10,tzinfo=timezone.utc).timestamp();r.e.c['live_send']=True
    p=tmp_path/'r.json';p.write_text(json.dumps({'enabled':True,'releases':[{'id':'fix','sha':'b'*40,'text':'Улучшен рабочий ответ.','verified_at':r.clock[0]-1,'deployment_success':True,'runtime_verified':True}]}))
    r.s.queue('GOLDWAY','test_operator','previous','verified-release:prior',r.clock[0]-60,preferred_transport='bot')
    r.s.execute("UPDATE outbox SET state='sent'");u=VerifiedUpdates(r.e,p);u.tick();assert len(r.s.rows('SELECT * FROM outbox'))==1
    r.clock[0]+=4*3600;r.s.execute("UPDATE outbox SET state='unknown'");u.tick();assert len(r.s.rows('SELECT * FROM outbox'))==1
