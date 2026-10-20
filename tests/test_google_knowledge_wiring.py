"""Actual CLI credential factory -> Engine -> Drive reader, synthetic transport."""
import copy
import json

import httpx
import pytest
from google.oauth2.service_account import Credentials

from goldway.cli import make_knowledge_credentials
from goldway.engine import Engine
from goldway.knowledge import KnowledgeIndex
from goldway.store import Store
from goldway.intelligence import reader_key
import goldway.cli as cli

SCOPE='https://www.googleapis.com/auth/drive.readonly'


class Readonly:
    scopes=[SCOPE]
    def before_request(self,request,method,url,headers):
        assert method=='GET'
        headers['Authorization']='Bearer SYNTHETIC_TEST_ONLY'


def configured(config):
    c=copy.deepcopy(config)
    c['intelligence']={'enabled':True,'sources':[{
        'id':'synthetic-drive','kind':'drive','file_id':'synthetic_document_001',
        'readers':['boss','alen'],
        'reader_emails':{'boss':'boss@example.invalid','alen':'alen@example.invalid'}}]}
    return c


@pytest.mark.parametrize('mode',['missing','disabled','no-drive'])
def test_unused_credentials_never_open_file_or_use_other_google_auth(env,monkeypatch,mode):
    c=configured(env[2])
    if mode=='missing':c.pop('intelligence')
    if mode=='disabled':c['intelligence']['enabled']=False
    if mode=='no-drive':c['intelligence']['sources']=[]
    monkeypatch.setenv('GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE','DO_NOT_OPEN')
    monkeypatch.setenv('GOOGLE_SERVICE_ACCOUNT_FILE','SHEETS_WRITE_DO_NOT_OPEN')
    monkeypatch.setattr(Credentials,'from_service_account_file',lambda *a,**k:pytest.fail('unexpected credential read'))
    assert make_knowledge_credentials(c) is None


def test_absent_explicit_knowledge_file_does_not_reuse_sheets_or_ambient(env,monkeypatch):
    monkeypatch.delenv('GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE',raising=False)
    monkeypatch.setenv('GOOGLE_SERVICE_ACCOUNT_FILE','SHEETS_WRITE_DO_NOT_OPEN')
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS','AMBIENT_DO_NOT_OPEN')
    monkeypatch.setattr(Credentials,'from_service_account_file',lambda *a,**k:pytest.fail('unexpected credential read'))
    assert make_knowledge_credentials(configured(env[2])) is None


def test_file_failure_is_constant_and_does_not_disclose_path_or_key(env,monkeypatch,capsys):
    monkeypatch.setenv('GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE','PRIVATE_PATH')
    def fail(*a,**k):raise ValueError('PRIVATE_KEY_AND_PATH')
    monkeypatch.setattr(Credentials,'from_service_account_file',fail)
    assert make_knowledge_credentials(configured(env[2])) is None
    assert capsys.readouterr().err == 'knowledge_credentials_unavailable: registered Drive sources remain blocked\n'


def test_optional_credential_warning_failure_is_also_nonfatal(env,monkeypatch):
    monkeypatch.setenv('GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE','PRIVATE_PATH')
    def fail(*a,**k):raise OSError('PRIVATE_ERROR')
    monkeypatch.setattr(Credentials,'from_service_account_file',fail)
    monkeypatch.setattr('builtins.print',fail)
    assert make_knowledge_credentials(configured(env[2])) is None


@pytest.mark.parametrize('permissions,expected_calls', [
    ([{'type':'user','role':'reader','emailAddress':'boss@example.invalid'}],2),([],1)])
def test_cli_factory_credentials_reach_actual_engine_reader_with_current_acl(env,monkeypatch,permissions,expected_calls):
    _,store,c,clock=env
    c=configured(c)
    credential=Readonly()
    reads=[]
    monkeypatch.setenv('GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE','synthetic-readonly.json')
    def load(filename,*,scopes):
        reads.append((filename,scopes));return credential
    monkeypatch.setattr(Credentials,'from_service_account_file',load)
    calls=[]
    def respond(request):
        calls.append(request)
        assert request.method=='GET'
        assert request.headers['Authorization']=='Bearer SYNTHETIC_TEST_ONLY'
        if '/export' in request.url.path:return httpx.Response(200,text='Лира: вымышленная марка для проверки.')
        return httpx.Response(200,json={'id':'synthetic_document_001','name':'Synthetic only',
            'mimeType':'application/vnd.google-apps.document','trashed':False,'permissions':permissions})
    client=httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr('goldway.knowledge_sources.httpx.Client',lambda *a,**k:client)
    engine=Engine(store,c,None,lambda:clock[0],google_credentials=make_knowledge_credentials(c))
    assert engine.intelligence.sources.credentials is credential
    engine.intelligence.sources.refresh(clock[0])
    assert reads==[('synthetic-readonly.json',[SCOPE])]
    assert len(calls)==expected_calls
    boss=engine.intelligence.index.search('Лира',engine.intelligence.reader('boss'),now=clock[0]+1)['evidence']
    alen=engine.intelligence.index.search('Лира',engine.intelligence.reader('alen'),now=clock[0]+1)['evidence']
    assert bool(boss)==bool(permissions)
    assert not alen
    assert store.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
    assert store.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0


@pytest.mark.parametrize('scopes', [[],[SCOPE,'https://www.googleapis.com/auth/spreadsheets'],['https://www.googleapis.com/auth/drive']])
def test_engine_rejects_broad_or_missing_scopes_before_sql(env,scopes):
    c=configured(env[2]);credential=Readonly();credential.scopes=scopes
    class NoSql:
        def execute(self,*a,**k):pytest.fail('SQL before credential rejection')
    with pytest.raises(ValueError,match='^knowledge_credentials_invalid$'):
        Engine(NoSql(),c,None,google_credentials=credential)


def test_engine_rejects_credentials_when_intelligence_disabled_before_sql(env):
    c=configured(env[2]);c['intelligence']['enabled']=False
    with pytest.raises(ValueError,match='^knowledge_credentials_invalid$'):
        Engine(object(),c,None,google_credentials=Readonly())


def test_real_cli_main_passes_readonly_account_to_real_engine(env,tmp_path,monkeypatch):
    c=configured(env[2]);c['live_send']=False
    path=tmp_path/'config.json';path.write_text(json.dumps(c))
    credential=Readonly();seen=[]
    monkeypatch.setenv('GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE','synthetic.json')
    monkeypatch.setattr(Credentials,'from_service_account_file',lambda *a,**k:credential)
    monkeypatch.setattr(cli,'make_model',lambda *a,**k:None)
    monkeypatch.setattr(cli,'run',lambda engine,*args:seen.append(engine))
    monkeypatch.setattr('sys.argv',['goldway','--config',str(path),'--db',str(tmp_path/'state.sqlite'),'run'])
    cli.main()
    assert len(seen)==1
    assert seen[0].intelligence.sources.credentials is credential
    assert seen[0].c['sheets_write'] is c['sheets_write']


def test_bad_optional_google_file_keeps_real_cli_worker_and_public_source_available(env,tmp_path,monkeypatch,capsys):
    c=configured(env[2]);c['live_send']=False
    c['intelligence']['sources'].append({'id':'synthetic-public','kind':'website',
        'url':'https://synthetic.example.invalid/info','readers':['boss']})
    path=tmp_path/'config.json';path.write_text(json.dumps(c))
    monkeypatch.setenv('GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE','PRIVATE_PATH')
    def fail(*a,**k):raise ValueError('PRIVATE_KEY_ERROR')
    monkeypatch.setattr(Credentials,'from_service_account_file',fail)
    monkeypatch.setattr(cli,'make_model',lambda *a,**k:None)
    calls=[];seen=[]
    def respond(request):
        calls.append(request)
        assert request.url.host=='synthetic.example.invalid'
        return httpx.Response(200,text='<p>Лира — вымышленный публичный источник.</p>')
    client=httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr('goldway.knowledge_sources.httpx.Client',lambda *a,**k:client)
    dbpath=tmp_path/'state.sqlite'
    seed=Store(str(dbpath));index=KnowledgeIndex(seed)
    import hashlib
    source='source:'+hashlib.sha256(c['business'].encode()).hexdigest()+':synthetic-drive'
    index.upsert({'source_id':source,'document_id':'synthetic_document_001',
        'title':'Synthetic cached document','url':'https://drive.google.com/file/d/synthetic_document_001/view',
        'text':'ARCHIVED_GOOGLE_ONLY_MARKER','readers':[reader_key(c['business'],'boss')],
        'observed_at':1791979000,'permission_expires_at':1791980000,'confirmed':True})
    assert index.search('ARCHIVED_GOOGLE_ONLY_MARKER',reader_key(c['business'],'boss'),now=1791979200)['evidence']
    seed.db.close()
    def run(engine,*args):
        seen.append(engine)
        engine.intelligence.sources.refresh(1791979200)
    monkeypatch.setattr(cli,'run',run)
    monkeypatch.setattr('sys.argv',['goldway','--config',str(path),'--db',str(dbpath),'run'])
    cli.main()
    assert len(seen)==1 and len(calls)==1
    intel=seen[0].intelligence
    assert intel.sources.credentials is None
    assert intel.sources.status[intel.prefix+'synthetic-drive']=='blocked'
    assert intel.sources.status[intel.prefix+'synthetic-public']=='ok'
    assert intel.index.search('Лира',intel.reader('boss'),now=1791979201)['evidence']
    assert not intel.index.search('ARCHIVED_GOOGLE_ONLY_MARKER',intel.reader('boss'),now=1791979201)['evidence']
    raw=capsys.readouterr()
    assert 'knowledge_credentials_unavailable' in raw.err
    assert 'PRIVATE' not in raw.err+raw.out
