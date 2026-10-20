"""One root-approved public/synthetic provider probe beside the existing worker.

Default has no I/O. No Telegram client/Store/provider is constructed here. The
only child is the fixed packaged harness, with its existing shared usage ledger.
An exclusive persistent marker is consumed before starting; no automatic retry.
Daemon supervision/normal atexit cleanup do not promise survival of SIGKILL, OS
failure or a malicious child/callback. Structured public evidence is not semantic
acceptance, observed Telegram delivery or a real employee outcome.
"""
import atexit
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

RUN='20261020-d'
RUN_DIRECTORY=Path('/data/public-chain')
MARKER=RUN_DIRECTORY/(RUN+'.started')
PROBE=Path('/app/operations/public_chain/probe.py')
SOURCE_ROOT=Path('/app')
TIMEOUT_SECONDS=900
MAX_OUTPUT_BYTES=262144
MAX_LINE_BYTES=65536
_REQUIRED=frozenset(('provider','model','daily_budget','input_per_million','output_per_million','max_output_tokens','max_context_chars'))
_OPTIONAL=frozenset(('api','base_url','timeout_seconds','json_mode','long_context_threshold','long_input_multiplier','long_output_multiplier'))
_INPUTS=(
 'Что такое GOLDWAY и какие три подтверждённых бренда представлены в публичном каталоге? '
 'Кратко перечисли их с явными номерами 1, 2, 3 и предложи следующим ответом пояснить '
 'каждый из этих же трёх брендов в том же порядке, также списком 1, 2, 3. Не создавай задач.',
 'Давай','Расскажи о втором бренде','Сделай покороче','Откуда ты это знаешь?')
_REASONS=frozenset(('source_manifest_changed missing_model_profile_projection profile_not_allowlisted_model_only '
 'profile_prices_not_finite_numbers existing_mini_one_dollar_profile_required unexpected_provider_endpoint '
 'unsupported_profile_api profile_limits_exceed_existing_public_ceiling invalid_profile_timeout ledger_sum_scope '
 'ledger_insert_scope ledger_update_scope ledger_sql_outside_allowlist unexpected_capture_route invalid_capture_body '
 'capture_utf16_limit provider_call_cap profile_changed nonconversation_provider_result task_authority_in_public_chain '
 'public_registration_changed existing_provider_environment_missing event_rejected event_not_done unexpected_output_projection '
 'ordinary_turn_not_one_provider_call readonly_turn_not_one_provider_call readonly_context_contract wrong_latest_answer_target '
 'source_followup_not_saved_latest_authority outbox_protection_denied capture_delivery_not_proven synthetic_domain_mutation '
 'current_delivered_grounding_receipt_missing delivered_memory_mismatch offer_or_three_delivered_entities_absent '
 'bare_acceptance_did_not_use_offered_explanation accepted_offer_not_same_strict_numbered_objects '
 'derivative_citation_authority_changed latest_answer_not_shortened root_execution_not_authorized source_root_required '
 'engine_or_provider_failure').split())
_ERRORS=frozenset(('Stop','Exception','ValueError','TypeError','RuntimeError','KeyError','AssertionError','OperationalError',
 'OSError','OverflowError','UnicodeError','JSONDecodeError'))
_lock=threading.Lock()
_children=set()
_stopping=False
_registered=False


class _Stop(Exception):
    pass


def _require(value,reason='malformed_output'):
    if not value:raise _Stop(reason)


def _number(value,low,high):return type(value) in (int,float) and math.isfinite(value) and low<=value<=high


def _profile(config):
    from .intake import validate_team
    _require(type(config) is dict and config.get('business')=='GOLDWAY','preflight_failed')
    validate_team(config)
    original=config.get('model');_require(type(original) is dict,'preflight_failed')
    profile={key:original[key] for key in _REQUIRED|_OPTIONAL if key in original}
    _require(_REQUIRED<=set(profile),'preflight_failed')
    _require(profile['provider']=='openai' and profile['model']=='gpt-4.1-mini','preflight_failed')
    for name,value in (('daily_budget',1.0),('input_per_million',0.4),('output_per_million',1.6)):
        _require(_number(profile[name],value,value),'preflight_failed')
    _require(type(profile['max_context_chars']) is int and 1<=profile['max_context_chars']<=44000 and
        type(profile['max_output_tokens']) is int and 1<=profile['max_output_tokens']<=6000,'preflight_failed')
    _require(profile.get('base_url','https://api.openai.com')=='https://api.openai.com' and
        profile.get('api','chat_completions') in ('chat_completions','responses'),'preflight_failed')
    _require(_number(profile.get('timeout_seconds',60),0.000001,180),'preflight_failed')
    if 'json_mode' in profile:_require(type(profile['json_mode']) is bool,'preflight_failed')
    if 'long_context_threshold' in profile:
        value=profile['long_context_threshold'];_require(type(value) is int and 1<=value<2**53,'preflight_failed')
    for name in ('long_input_multiplier','long_output_multiplier'):
        if name in profile:_require(_number(profile[name],1,10),'preflight_failed')
    encoded=json.dumps(profile,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    _require(len(encoded.encode())<=4096,'preflight_failed')
    return encoded


def _emit(payload):
    try:
        print(json.dumps(dict(schema='goldway_public_chain_probe_v1',run=RUN,payload=payload),
                         ensure_ascii=False,sort_keys=True,allow_nan=False),flush=True)
        return True
    except Exception:return False


def _failure(reason):
    # Call sites supply only these internal constants, never exception strings.
    _emit(dict(status='launcher_stopped_not_accepted',safe_reason=reason))


def _consume():
    RUN_DIRECTORY.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=RUN_DIRECTORY.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid(),'marker_failed')
    descriptor=os.open(MARKER,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        os.fchmod(descriptor,0o600)
        data=b'goldway-public-chain-v1 20261020-d started\n'
        _require(os.write(descriptor,data)==len(data),'marker_failed')
        os.fsync(descriptor)
    finally:os.close(descriptor)


def _kill(process):
    try:
        if process.poll() is None:process.kill()
    except Exception:pass
    try:process.wait(timeout=1)
    except Exception:pass
    try:process.stdout.close()
    except Exception:pass


def _cleanup():
    global _stopping
    with _lock:
        _stopping=True;children=tuple(_children)
    for process in children:_kill(process)


def _unique(pairs):
    result={}
    for key,value in pairs:
        _require(key not in result);result[key]=value
    return result


def _integer(value):
    _require(len(value)<=16)
    return int(value)


def _reject_number(value):raise _Stop('malformed_output')


def _keys(value,names):_require(type(value) is dict and set(value)==set(names.split()))


def _count(value,low,high):return type(value) is int and low<=value<=high


def _text(value,limit):
    return type(value) is str and len(value.encode())<=limit and not any(ord(c)<32 and c not in '\n\r\t' for c in value)


def _strings(value):
    if type(value) is str:yield value
    elif type(value) is dict:
        for item in value.values():yield from _strings(item)
    elif type(value) is list:
        for item in value:yield from _strings(item)


class _Protocol:
    def __init__(self,key):self.key=key;self.started=False;self.delivered=0;self.checked=0;self.ended=False;self.terminal=None
    def parse(self,line):
        _require(not self.ended)
        value=json.loads(line.decode('utf-8'),object_pairs_hook=_unique,parse_int=_integer,
            parse_float=_reject_number,parse_constant=_reject_number)
        _require(type(value) is dict and type(value.get('status')) is str)
        status=value['status']
        if status=='started_not_accepted':
            _keys(value,'status events call_limit synthetic_delivery telegram_calls profile_sha256')
            _require(not self.started and value['events']==5 and type(value['events']) is int and
                value['call_limit']==5 and type(value['call_limit']) is int and value['synthetic_delivery'] is True and
                value['telegram_calls']==0 and type(value['telegram_calls']) is int and
                type(value['profile_sha256']) is str and re.fullmatch('[0-9a-f]{64}',value['profile_sha256']))
            self.started=True
        elif status=='synthetic_delivery_observed':
            _keys(value,'status turn input provider_calls actual_captured_delivery public_retrieved_evidence telegram_delivery')
            turn=value['turn'];_require(self.started and self.delivered==self.checked and _count(turn,1,5) and turn==self.checked+1)
            _require(value['input']==_INPUTS[turn-1] and type(value['provider_calls']) is int and
                value['provider_calls']==(0 if turn==5 else 1) and value['telegram_delivery'] is False)
            body=value['actual_captured_delivery']
            _require(_text(body,14000) and 0<len(body.encode('utf-16-le'))//2<=3500)
            evidence=value['public_retrieved_evidence'];_require(type(evidence) is list and len(evidence)<=5)
            for row in evidence:
                _keys(row,'id title url text')
                for name,cap in (('id',640),('title',4800),('text',32000)):
                    _require(row[name] is None or _text(row[name],cap))
                url=row['url']
                if url is not None:
                    _require(_text(url,2048));parts=urlsplit(url)
                    _require(parts.scheme=='https' and parts.hostname in ('goldwaybaltic.com','www.goldwaybaltic.com','gw-api.abdulwaheedaziz8.workers.dev')
                        and parts.username is None and parts.password is None and parts.port in (None,443))
            self.delivered=turn
        elif status=='turn_structural_checks_complete':
            _keys(value,'status turn grounding_refs')
            _require(self.started and self.delivered==self.checked+1 and _count(value['turn'],1,5) and
                value['turn']==self.delivered and _count(value['grounding_refs'],0,256))
            self.checked=value['turn']
        elif status=='structural_chain_complete_semantic_review_required':
            _keys(value,'status events provider_calls synthetic_deliveries telegram_delivery business_acceptance')
            _require(self.started and self.checked==self.delivered==5 and type(value['events']) is int and value['events']==5 and
                type(value['provider_calls']) is int and value['provider_calls']==4 and type(value['synthetic_deliveries']) is int and
                value['synthetic_deliveries']==5 and value['telegram_delivery'] is False and value['business_acceptance'] is False)
            self.ended=True;self.terminal=status
        elif status=='stopped_not_accepted':
            _keys(value,'status turn provider_calls safe_reason error_type reservations_retained')
            _require(self.started and _count(value['turn'],0,5) and _count(value['provider_calls'],0,5) and
                value['safe_reason'] in _REASONS and value['error_type'] in _ERRORS and value['reservations_retained'] is True)
            self.ended=True;self.terminal=status
        elif status=='preflight_stopped_not_accepted':
            _keys(value,'status provider_calls')
            _require(not self.started and type(value['provider_calls']) is int and value['provider_calls']==0)
            self.ended=True;self.terminal=status
        else:raise _Stop('malformed_output')
        _require(not any(self.key in item for item in _strings(value)),'secret_output_refused')
        return value


def _monitor(process,key):
    deadline=time.monotonic()+TIMEOUT_SECONDS;protocol=_Protocol(key);total=0;pending=b''
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout,selectors.EVENT_READ)
        while True:
            remaining=deadline-time.monotonic()
            _require(remaining>0,'timeout')
            if not selector.select(min(remaining,0.25)):continue
            chunk=os.read(process.stdout.fileno(),min(8192,MAX_OUTPUT_BYTES-total+1))
            if not chunk:break
            total+=len(chunk);_require(total<=MAX_OUTPUT_BYTES,'output_limit')
            pending+=chunk
            while b'\n' in pending:
                line,pending=pending.split(b'\n',1);_require(0<len(line)<=MAX_LINE_BYTES,'line_limit')
                payload=protocol.parse(line)
                _require(_emit(payload),'log_failed')
            _require(len(pending)<=MAX_LINE_BYTES,'line_limit')
        _require(not pending and protocol.ended,'incomplete_output')
        remaining=deadline-time.monotonic();_require(remaining>0,'timeout')
        result=process.wait(timeout=min(1,remaining))
        expected={'structural_chain_complete_semantic_review_required':0,'stopped_not_accepted':1,'preflight_stopped_not_accepted':2}[protocol.terminal]
        _require(type(result) is int and result==expected,'unexpected_exit')


def _run(environment):
    process=None
    try:
        with _lock:
            if _stopping:return
            process=subprocess.Popen([sys.executable,str(PROBE),'--source-root',str(SOURCE_ROOT),'--execute'],
                cwd=str(SOURCE_ROOT),env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                close_fds=True)
            _children.add(process)
        _monitor(process,environment['MODEL_API_KEY'])
    except _Stop as error:_failure(error.args[0])
    except Exception:_failure('child_failed')
    finally:
        if process is not None:
            _kill(process)
            with _lock:_children.discard(process)


def maybe_start(config):
    """Nonfatal startup hook; exact root nonce is required before any I/O."""
    global _registered
    if os.environ.get('GOLDWAY_PUBLIC_CHAIN_RUN')!=RUN:return False
    try:
        profile=_profile(config);key=os.environ.get('MODEL_API_KEY')
        _require(type(key) is str and 0<len(key)<=16384 and bool(key.strip()) and '\0' not in key,'preflight_failed')
        environment={'MODEL_API_KEY':key,'GOLDWAY_PUBLIC_MODEL_PROFILE':profile,
            'GOLDWAY_PROVIDER_CHAIN_AUTHORIZED':'root-approved-once','PYTHONUTF8':'1','PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'}
        try:_consume()
        except FileExistsError:return False
        except Exception:_failure('marker_failed');return False
        with _lock:
            if _stopping:return False
            if not _registered:atexit.register(_cleanup);_registered=True
        thread=threading.Thread(target=_run,args=(environment,),name='goldway-public-chain-once',daemon=True)
        thread.start()
        return True
    except Exception:_failure('start_failed');return False
