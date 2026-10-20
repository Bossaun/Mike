"""Synthetic launcher tests: no real process/network/provider/credentials/ledger."""
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import pytest

spec=importlib.util.spec_from_file_location('goldway.public_chain_execution',Path(__file__).parents[1]/'goldway'/'public_chain_execution.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
MEMBERS=['test_operator','frost','alen','nastya','dasha_it']

def config():
 return dict(business='GOLDWAY',employees={n:dict(telegram_user_id=i+1,name='PRIVATE_NAME') for i,n in enumerate(MEMBERS)},admins=['test_operator'],business_account_user_id=99,team_intake=dict(recipients=MEMBERS[:],started_at=1),team_roster_verified=True,allowed_live_recipients=MEMBERS[:],recipients_verified=True,live_send=True,review_mode=True,sheets_write=False,daily_summary=False,allow_onboarding_replies=False,allow_registered_replies=False,allowed_group_ids=[],group_destinations={},meeting_recipient=None,telegram_token='PRIVATE_TOKEN',model=dict(provider='openai',model='gpt-4.1-mini',daily_budget=1.0,input_per_million=0.4,output_per_million=1.6,max_output_tokens=6000,max_context_chars=44000,timeout_seconds=60,json_mode=False))

class Thread:
 made=[]
 def __init__(self,**kw):self.kw=kw;self.started=False;self.made.append(self)
 def start(self):self.started=True
 def run(self):self.kw['target'](*self.kw['args'])
class Pipe:
 def __init__(self):self.closed=False
 def fileno(self):return 41
 def close(self):self.closed=True
class Process:
 def __init__(self,result=0):self.stdout=Pipe();self.result=result;self.returncode=None;self.killed=0;self.waits=[]
 def poll(self):return self.returncode
 def kill(self):self.killed+=1;self.returncode=-9
 def wait(self,timeout):
  self.waits.append(timeout)
  if self.returncode is None:self.returncode=self.result
  return self.returncode
class Selector:
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def register(self,stream,events):assert stream.fileno()==41 and events==m.selectors.EVENT_READ
 def select(self,timeout):assert 0<timeout<=0.25;return [(None,None)]

@pytest.fixture
def c(tmp_path,monkeypatch):
 monkeypatch.delenv('GOLDWAY_PUBLIC_CHAIN_RUN',raising=False);monkeypatch.setenv('MODEL_API_KEY','DUMMY_TEST_PROVIDER_KEY')
 monkeypatch.setenv('TELEGRAM_TOKEN','PRIVATE_AMBIENT_TOKEN');monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS','PRIVATE_FILE')
 monkeypatch.setattr(m,'RUN_DIRECTORY',tmp_path/'public-chain');monkeypatch.setattr(m,'MARKER',m.RUN_DIRECTORY/(m.RUN+'.started'))
 monkeypatch.setattr(m,'_children',set());monkeypatch.setattr(m,'_stopping',False);monkeypatch.setattr(m,'_registered',False)
 monkeypatch.setattr(Thread,'made',[]);monkeypatch.setattr(m.threading,'Thread',Thread)
 callbacks=[];monkeypatch.setattr(m.atexit,'register',callbacks.append)
 def forbidden(*a,**kw):pytest.fail('actual subprocess requested')
 monkeypatch.setattr(m.subprocess,'Popen',forbidden)
 outputs=[];monkeypatch.setattr(m,'_emit',lambda p:outputs.append(p) is None)
 return SimpleNamespace(config=config(),outputs=outputs,callbacks=callbacks,mp=monkeypatch)

def authorize(c):c.mp.setenv('GOLDWAY_PUBLIC_CHAIN_RUN',m.RUN)
def line(v):return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()+b'\n'
def start():return dict(status='started_not_accepted',events=5,call_limit=5,synthetic_delivery=True,telegram_calls=0,profile_sha256='a'*64)
def delivery(i):return dict(status='synthetic_delivery_observed',turn=i,input=m._INPUTS[i-1],provider_calls=int(i!=5),actual_captured_delivery='Синтетический публичный ответ '+str(i),public_retrieved_evidence=[] if i==5 else [dict(id='public',title='Публичный источник',url='https://goldwaybaltic.com',text='Публичная выдержка')],telegram_delivery=False)
def check(i):return dict(status='turn_structural_checks_complete',turn=i,grounding_refs=1)
def final():return dict(status='structural_chain_complete_semantic_review_required',events=5,provider_calls=4,synthetic_deliveries=5,telegram_delivery=False,business_acceptance=False)
def complete():
 values=[start()]
 for i in range(1,6):values.extend((delivery(i),check(i)))
 return values+[final()]
def pipe(c,data,result=0):
 process=Process(result);calls=[];pending=[data]
 def popen(*a,**kw):calls.append((a,kw));return process
 def read(fd,size):
  assert fd==41 and 0<size<=8192
  if not pending:return b''
  data=pending.pop(0);part,rest=data[:size],data[size:]
  if rest:pending.insert(0,rest)
  return part
 c.mp.setattr(m.subprocess,'Popen',popen);c.mp.setattr(m.selectors,'DefaultSelector',Selector)
 c.mp.setattr(m.os,'read',read);c.mp.setattr(m.time,'monotonic',lambda:10.0)
 return process,calls

def test_disabled_no_io_or_config_access(c):
 c.mp.setattr(m,'_profile',lambda *a:pytest.fail('disabled profile gate'));c.mp.setattr(m,'_consume',lambda:pytest.fail('disabled marker IO'))
 assert m.maybe_start(object()) is False and not c.outputs and not Thread.made and not c.callbacks
@pytest.mark.parametrize('value',['','20261020','20261020-d ','20261020-D','root-approved-once','20261020-a','20261020-b','20261020-c'])
def test_wrong_nonce_no_io(c,value):
 c.mp.setenv('GOLDWAY_PUBLIC_CHAIN_RUN',value);c.mp.setattr(m,'_consume',lambda:pytest.fail('wrong nonce IO'))
 assert not m.maybe_start(c.config) and not c.outputs and not Thread.made

def test_actual_launcher_fixed_command_private_env_once_and_complete_protocol(c):
 authorize(c);c.config['model']['private_key']='PRIVATE_MODEL';c.config['model']['employees']={'PRIVATE':'PRIVATE'}
 original=json.dumps(c.config,sort_keys=True);process,calls=pipe(c,b''.join(map(line,complete())))
 assert m.maybe_start(c.config) and m.MARKER.exists() and stat.S_IMODE(m.MARKER.stat().st_mode)==0o600
 thread=Thread.made[0];assert thread.started and thread.kw['daemon'] is True and c.callbacks==[m._cleanup] and not calls
 thread.run();assert len(calls)==1 and len(c.outputs)==12 and c.outputs[-1]==final()
 args,kw=calls[0]
 assert args==([sys.executable,'/app/operations/public_chain/probe.py','--source-root','/app','--execute'],)
 assert kw['cwd']=='/app' and kw['stdin']==m.subprocess.DEVNULL and kw['stdout']==m.subprocess.PIPE and kw['stderr']==m.subprocess.DEVNULL and kw['close_fds'] is True
 env=kw['env'];assert set(env)=={'MODEL_API_KEY','GOLDWAY_PUBLIC_MODEL_PROFILE','GOLDWAY_PROVIDER_CHAIN_AUTHORIZED','PYTHONUTF8','PYTHONUNBUFFERED','PYTHONDONTWRITEBYTECODE'}
 assert env['MODEL_API_KEY']=='DUMMY_TEST_PROVIDER_KEY' and env['GOLDWAY_PROVIDER_CHAIN_AUTHORIZED']=='root-approved-once'
 assert json.loads(env['GOLDWAY_PUBLIC_MODEL_PROFILE'])=={k:v for k,v in c.config['model'].items() if k in m._REQUIRED|m._OPTIONAL}
 assert 'PRIVATE' not in env['GOLDWAY_PUBLIC_MODEL_PROFILE'] and 'PRIVATE' not in json.dumps(c.outputs)
 assert json.dumps(c.config,sort_keys=True)==original and process.stdout.closed and process.killed==0 and not m._children
 assert not m.maybe_start(c.config) and len(Thread.made)==1
 c.mp.setattr(m,'_registered',False);assert not m.maybe_start(c.config) and len(Thread.made)==1

@pytest.mark.parametrize('name,value',[('provider','anthropic'),('model','other'),('daily_budget',2),('daily_budget',True),('input_per_million',0.5),('output_per_million',float('nan')),('max_output_tokens',6001),('max_context_chars',44001),('max_output_tokens',True),('base_url','https://private.invalid'),('api','other'),('timeout_seconds',181),('timeout_seconds',float('inf')),('json_mode','yes'),('long_context_threshold',False),('long_input_multiplier',11),('long_output_multiplier',float('nan'))])
def test_refuses_profile_changes_without_marker(c,name,value):
 authorize(c);c.config['model'][name]=value
 assert not m.maybe_start(c.config) and not m.MARKER.exists() and not Thread.made
 assert c.outputs==[dict(status='launcher_stopped_not_accepted',safe_reason='start_failed')]
@pytest.mark.parametrize('change',['missing_model','missing_required','team','business','key','blank_key'])
def test_requires_current_team_model_key(c,change):
 authorize(c)
 if change=='missing_model':c.config.pop('model')
 elif change=='missing_required':c.config['model'].pop('max_output_tokens')
 elif change=='team':c.config['team_roster_verified']=False
 elif change=='business':c.config['business']='OTHER'
 elif change=='key':c.mp.delenv('MODEL_API_KEY')
 else:c.mp.setenv('MODEL_API_KEY',' ')
 assert not m.maybe_start(c.config) and not m.MARKER.exists() and not Thread.made and 'PRIVATE' not in json.dumps(c.outputs)

@pytest.mark.parametrize('fault',['popen','thread','atexit','marker_write'])
def test_consumed_failures_no_retry_no_raw_logs(c,fault):
 authorize(c)
 def fail(*a,**kw):raise RuntimeError('PRIVATE_TOKEN DUMMY_TEST_PROVIDER_KEY')
 if fault=='popen':c.mp.setattr(m.subprocess,'Popen',fail)
 elif fault=='thread':c.mp.setattr(Thread,'start',fail)
 elif fault=='atexit':c.mp.setattr(m.atexit,'register',fail)
 else:c.mp.setattr(m.os,'write',fail)
 ok=m.maybe_start(c.config)
 if fault=='popen':assert ok;Thread.made[0].run()
 else:assert not ok
 assert m.MARKER.exists() and not m.maybe_start(c.config) and len(Thread.made)<=1
 assert 'PRIVATE' not in json.dumps(c.outputs) and 'DUMMY' not in json.dumps(c.outputs)
def test_symlink_directory_refuses_without_following(c,tmp_path):
 authorize(c);target=tmp_path/'other';target.mkdir();m.RUN_DIRECTORY.symlink_to(target,target_is_directory=True)
 assert not m.maybe_start(c.config) and not list(target.iterdir()) and not Thread.made
def test_existing_marker_never_truncated(c):
 authorize(c);m.RUN_DIRECTORY.mkdir();m.MARKER.write_text('consumed');m.MARKER.chmod(0o400)
 assert not m.maybe_start(c.config) and m.MARKER.read_text()=='consumed' and stat.S_IMODE(m.MARKER.stat().st_mode)==0o400

@pytest.mark.parametrize('data,code',[(b'RAW_PRIVATE_TRACEBACK\n','child_failed'),(b'{}\n','malformed_output'),(b'{"status":"started_not_accepted","status":"PRIVATE"}\n','malformed_output'),(b'{"status":NaN}\n','malformed_output'),(b'{"status":1.5}\n','malformed_output'),(b'\xff\n','child_failed'),(line(start())+b'{','incomplete_output'),(line(start())+line(start()),'malformed_output'),(line(start())+line(check(1)),'malformed_output'),(line(start())+line(final()),'malformed_output')])
def test_malformed_or_unordered_output_safe_kill(c,data,code):
 process,calls=pipe(c,data);m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 assert len(calls)==1 and process.killed==1 and process.stdout.closed and not m._children
 assert c.outputs[-1]==dict(status='launcher_stopped_not_accepted',safe_reason=code) and 'PRIVATE' not in json.dumps(c.outputs)
@pytest.mark.parametrize('change',['extra','secret','bad_url','wrong_input','wrong_calls','true_count','raw_error'])
def test_closed_public_shapes(c,change):
 value=delivery(1)
 if change=='extra':value['private_config']='PRIVATE'
 elif change=='secret':value['actual_captured_delivery']='DUMMY_TEST_PROVIDER_KEY'
 elif change=='bad_url':value['public_retrieved_evidence'][0]['url']='https://private.invalid'
 elif change=='wrong_input':value['input']='PRIVATE'
 elif change=='wrong_calls':value['provider_calls']=2
 elif change=='true_count':value['turn']=True
 else:value=dict(status='stopped_not_accepted',turn=1,provider_calls=1,safe_reason='PRIVATE',error_type='Stop',reservations_retained=True)
 process,_=pipe(c,line(start())+line(value));m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 assert process.killed==1 and len(c.outputs)==2 and 'PRIVATE' not in json.dumps(c.outputs) and 'DUMMY' not in json.dumps(c.outputs)
def test_timeout_and_bounded_cleanup(c):
 process,_=pipe(c,b'');ticks=iter([0,901]);c.mp.setattr(m.time,'monotonic',lambda:next(ticks))
 m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 assert m.TIMEOUT_SECONDS<=900 and process.killed==1 and all(t<=1 for t in process.waits)
 assert c.outputs==[dict(status='launcher_stopped_not_accepted',safe_reason='timeout')]
def test_line_and_total_caps(c):
 process,_=pipe(c,b'x'*(m.MAX_LINE_BYTES+1));m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 assert process.killed==1 and c.outputs[-1]['safe_reason']=='line_limit'
 c.outputs.clear();values=[start()]
 for i in range(1,5):
  v=delivery(i);v['public_retrieved_evidence']=[dict(id='public',title='Title',url='https://goldwaybaltic.com',text='') for _ in range(5)]
  needed=65520-len(line(v))
  for row in v['public_retrieved_evidence']:
   size=min(needed,15000);row['text']='x'*size;needed-=size
  assert needed==0 and len(line(v))==65520
  values.extend((v,check(i)))
 data=b''.join(map(line,values));assert len(data)>m.MAX_OUTPUT_BYTES
 process,_=pipe(c,data);m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 assert process.killed==1 and c.outputs[-1]['safe_reason']=='output_limit'
@pytest.mark.parametrize('payload,result',[(dict(status='preflight_stopped_not_accepted',provider_calls=0),2),(dict(status='stopped_not_accepted',turn=0,provider_calls=0,safe_reason='engine_or_provider_failure',error_type='Stop',reservations_retained=True),1)])
def test_known_probe_failure_is_public_structured_evidence(c,payload,result):
 values=([start()] if payload['status']=='stopped_not_accepted' else [])+[payload]
 process,_=pipe(c,b''.join(map(line,values)),result);m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 assert c.outputs==values and process.killed==0 and process.stdout.closed
def test_final_requires_exit_zero(c):
 process,_=pipe(c,b''.join(map(line,complete())),1);m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 assert c.outputs[-1]==dict(status='launcher_stopped_not_accepted',safe_reason='unexpected_exit')
def test_log_failure_stops_child_nonfatal(c):
 process,_=pipe(c,line(start()));c.mp.setattr(m,'_emit',lambda payload:False)
 m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'});assert process.killed==1 and not m._children
def test_atexit_cleanup_kills_and_prevents_new_launch(c):
 process=Process();m._children.add(process);m._cleanup()
 assert process.killed==1 and process.stdout.closed and m._stopping and process.waits==[1]
 m._run({'MODEL_API_KEY':'DUMMY_TEST_PROVIDER_KEY'})
 authorize(c);assert not m.maybe_start(c.config) and m.MARKER.exists() and not Thread.made
def test_actual_logger_wrapper_and_failure(c,capsys):
 spec=importlib.util.spec_from_file_location('fresh_public_chain',Path(m.__file__));fresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(fresh)
 payload=dict(status='preflight_stopped_not_accepted',provider_calls=0);assert fresh._emit(payload)
 assert json.loads(capsys.readouterr().out)==dict(schema='goldway_public_chain_probe_v1',run=m.RUN,payload=payload)
 def fail(*a,**kw):raise OSError('PRIVATE')
 c.mp.setattr('builtins.print',fail);assert fresh._emit(payload) is False
