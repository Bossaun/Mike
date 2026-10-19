"""Private TEAM admin aggregates and prospective metadata-only model action sample.

No payload/audit/config/credential/model-response retrieval. Uses fixed enums
from model_trace; proposal/applied facts are deliberately unknown, not inferred
from done events or queued messages. Prospective counters are captured at the
actual model boundary and committed alongside business writes. No schema change.
"""
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math

from .model_trace import (DeliveryState, Processing, SafeReason, TokenUsage,
    RequestKind, Intent, Operation, ExecutionSource, Validation, TraceRecord, trace_views)


_MAX_COUNT = 10**9
_MAX_USAGE_TOKENS = 10_000_000
ACTION_PREFIX = 'runtime-action-trace:v1:'
MAX_ACTION_RECORDS = 64
MAX_ACTION_BYTES = 65536
_FIELDS = {'event','at','request_kind','intent','proposed_ops','proposed_known',
           'validation','applied_count','queued_count','processing','reason','phase'}


@dataclass(frozen=True)
class _ActionToken:
    store: object
    connection: object
    business: str
    event: str


def _action_key(business):
    if type(business) is not str or not 0 < len(business) <= 200:
        raise ValueError('action_trace_unavailable')
    return ACTION_PREFIX+hashlib.sha256(business.encode()).hexdigest()


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _pairs(values):
    result={}
    for key,value in values:
        if key in result:raise ValueError('action_trace_unavailable')
        result[key]=value
    return result


def _trace_record(row):
    if type(row) is not dict or set(row)!=_FIELDS:
        raise ValueError('action_trace_unavailable')
    if (type(row['event']) is not str or len(row['event'])!=64 or
            any(c not in '0123456789abcdef' for c in row['event']) or
            type(row['at']) not in (int,float) or not math.isfinite(row['at']) or
            not 0<=row['at']<=2**53-1 or type(row['proposed_known']) is not bool or
            type(row['proposed_ops']) is not list or len(row['proposed_ops'])>20 or
            row['phase'] not in ('pending','proposed','committed','failed','rejected')):
        raise ValueError('action_trace_unavailable')
    for name in ('request_kind','intent','validation','processing','reason','phase'):
        if type(row[name]) is not str:raise ValueError('action_trace_unavailable')
    if any(type(op) is not str for op in row['proposed_ops']):
        raise ValueError('action_trace_unavailable')
    if not row['proposed_known'] and (row['proposed_ops'] or row['intent']!='unknown'):
        raise ValueError('action_trace_unavailable')
    phases={'pending':('pending','not_run'),'proposed':('pending','not_run'),
            'committed':('done','passed'),'failed':('failed','unknown'),
            'rejected':('done','rejected')}
    if (row['processing'],row['validation'])!=phases[row['phase']]:
        raise ValueError('action_trace_unavailable')
    if row['phase']!='committed' and (row['applied_count'] is not None or row['queued_count'] is not None):
        raise ValueError('action_trace_unavailable')
    return TraceRecord(RequestKind(row['request_kind']),Intent(row['intent']),ExecutionSource.MODEL,
        tuple(Operation(op) for op in row['proposed_ops']),Validation(row['validation']),
        row['applied_count'],row['queued_count'],(),Processing(row['processing']),
        TokenUsage(None,None),SafeReason(row['reason']))


def _read_actions(store,business):
    rows=store.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN value END FROM runtime WHERE key=? LIMIT 2",
                       (MAX_ACTION_BYTES,_action_key(business))).fetchall()
    if not rows:return None,{'version':1,'records':[]}
    if len(rows)!=1 or type(rows[0][0]) is not str:raise ValueError('action_trace_unavailable')
    raw=rows[0][0];doc=json.loads(raw,object_pairs_hook=_pairs)
    if (type(doc) is not dict or set(doc)!={'version','records'} or type(doc['version']) is not int or
            doc['version']!=1 or type(doc['records']) is not list or len(doc['records'])>MAX_ACTION_RECORDS):
        raise ValueError('action_trace_unavailable')
    for row in doc['records']:_trace_record(row)
    if len({row['event'] for row in doc['records']})!=len(doc['records']):
        raise ValueError('action_trace_unavailable')
    return raw,doc


def _change_actions(store,business,change):
    """Isolate ordinary statement faults without issuing global transaction control.

    Transaction-fatal SQLite/connection faults (including RAISE(ROLLBACK)) can
    abort the parent transaction; a same-database savepoint cannot isolate them.
    Existing Engine failure handling must remain visible in that case.
    """
    connection=store.db;opened=False
    try:
        store.execute('SAVEPOINT runtime_action_trace');opened=True
        raw,doc=_read_actions(store,business)
        if not change(doc):
            store.execute('RELEASE runtime_action_trace');return False
        for row in doc['records']:_trace_record(row)
        encoded=_json(doc)
        if len(encoded.encode())>MAX_ACTION_BYTES:raise ValueError('action_trace_unavailable')
        if raw is None:
            changed=store.execute('INSERT OR IGNORE INTO runtime(key,value) VALUES(?,?)',(_action_key(business),encoded)).rowcount
        else:
            changed=store.execute('UPDATE runtime SET value=? WHERE key=? AND value=?',(encoded,_action_key(business),raw)).rowcount
        if changed!=1 or store.db is not connection:raise ValueError('action_trace_unavailable')
        store.execute('RELEASE runtime_action_trace');return True
    except Exception:
        if opened:
            # Use only this named savepoint on the captured connection, including
            # when a Store.execute fault was injected. Never issue global ROLLBACK.
            try:connection.execute('ROLLBACK TO runtime_action_trace')
            except Exception:pass
            try:connection.execute('RELEASE runtime_action_trace')
            except Exception:pass
        raise


def begin_action_trace(engine,event_id,context):
    """Prospective first observed model.propose invocation per event, metadata only."""
    try:
        if getattr(engine,'_action_trace_unavailable',False):return None
        if type(event_id) is not str or not 0<len(event_id.encode())<=4096 or engine.s.db.in_transaction:
            raise ValueError('action_trace_unavailable')
        kind=context.get('kind') if type(context) is dict else None
        request={'conversation':'conversation','transcript':'meeting'}.get(kind,'unknown')
        token=_ActionToken(engine.s,engine.s.db,engine.b,
            hashlib.sha256(_json(['action-trace-event:v1',engine.b,event_id]).encode()).hexdigest())
        row=dict(event=token.event,at=engine.now(),request_kind=request,intent='unknown',proposed_ops=[],
            proposed_known=False,validation='not_run',applied_count=None,queued_count=None,
            processing='pending',reason='none',phase='pending')
        def append(doc):
            if any(old['event']==token.event for old in doc['records']):return False
            doc['records']=(doc['records']+[row])[-MAX_ACTION_RECORDS:];return True
        return token if _change_actions(token.store,token.business,append) else None
    except Exception:
        engine._action_trace_unavailable=True
        return None


def _update_action_trace(engine,token,change):
    if token is None:return
    try:
        if (type(token) is not _ActionToken or engine.s is not token.store or engine.s.db is not token.connection or
                engine.b!=token.business or getattr(engine,'_action_trace_unavailable',False)):
            raise ValueError('action_trace_unavailable')
        def update(doc):
            matches=[r for r in doc['records'] if r['event']==token.event]
            if len(matches)!=1:raise ValueError('action_trace_unavailable')
            change(matches[0]);return True
        _change_actions(token.store,token.business,update)
    except Exception:engine._action_trace_unavailable=True


def proposal_action_trace(engine,token,result):
    def change(row):
        if row['phase']!='pending':raise ValueError('action_trace_unavailable')
        known=(type(result) is dict and type(result.get('actions')) is list and len(result['actions'])<=20 and
            all(type(a) is dict and type(a.get('op')) is str and a['op'] in ('create','update') for a in result['actions']) and
            ('intent' not in result or (type(result['intent']) is str and result['intent'] in ('conversation','task_action','idea','meeting'))))
        row['phase']='proposed'
        if known:
            row['proposed_known']=True;row['intent']=result.get('intent','unknown')
            row['proposed_ops']=[{'create':'create_task','update':'update_task'}[a['op']] for a in result['actions']]
    _update_action_trace(engine,token,change)


def committed_action_trace(engine,token,applied_count,first_outbox_id):
    def change(row):
        if not token.connection.in_transaction or row['phase']!='proposed':raise ValueError('action_trace_unavailable')
        queued=token.store.execute('SELECT id FROM outbox WHERE business=? AND id>? LIMIT 1001',
                                  (token.business,first_outbox_id)).fetchall()
        row.update(phase='committed',processing='done',validation='passed',
            applied_count=applied_count if row['proposed_known'] else None,
            queued_count=len(queued) if len(queued)<=1000 else None)
    _update_action_trace(engine,token,change)


def failed_action_trace(engine,token,*,rejected=False):
    def change(row):
        # Even a failed COMMIT can have an uncertain outcome. Never retain an
        # applied>0 claim after an exception, or infer zero from event.failed.
        row.update(phase='rejected' if rejected else 'failed',processing='done' if rejected else 'failed',
            validation='rejected' if rejected else 'unknown',applied_count=None,queued_count=None,
            reason='task_validation' if rejected else 'unknown')
    _update_action_trace(engine,token,change)


def _action_lines(engine):
    if getattr(engine,'_action_trace_unavailable',False):return ('Перспективная диагностика действий недоступна.',)
    try:
        _,doc=_read_actions(engine.s,engine.b)
        if not doc['records']:return ('Перспективные действия: пока не наблюдались.',)
        lines=['Новая выборка: последние наблюдённые вызовы Engine.process → model.propose; не вся история.',
               'Применено — committed вызовы _apply; это не завершение сотрудником. C07/регистры отдельно не считались.']
        for i,row in enumerate(reversed(doc['records'][-3:]),1):
            view=trace_views((_trace_record(row),))[0]
            rendered=list(view.lines)
            if not row['proposed_known']:rendered[1]='Предложено: нет данных; проверка: '+row['validation']+'.'
            lines.append('Наблюдение '+str(i)+':\n'+'\n'.join(rendered))
        return tuple(lines)
    except Exception:return ('Перспективная диагностика действий недоступна.',)


class EventKind(Enum):
    REPLY='reply'; TRANSCRIPT='transcript'; TELEGRAM_FAILURE='telegram_failure'; UNKNOWN='unknown'


@dataclass(frozen=True)
class AdminSnapshot:
    events: tuple[tuple[Processing,int], ...]
    event_kinds: tuple[tuple[EventKind,int], ...]
    errors: tuple[tuple[SafeReason,int], ...]
    messages: tuple[tuple[DeliveryState,int], ...]
    usage_rows_last24h: int
    usage: TokenUsage
    unknown_input_rows: int
    unknown_output_rows: int
    proposed_actions: None = None
    applied_actions: None = None


def _count(value):
    if type(value) is not int or not 0 <= value <= _MAX_COUNT:
        raise ValueError('trace_aggregate_out_of_bounds')
    return value


def _buckets(store, business, *, table, column, cls, members=None):
    """All interpolated identifiers/enums are source-owned constants, never input."""
    if (table,column,cls) not in {
        ('events','state',Processing),('events','kind',EventKind),
        ('events','error',SafeReason),('outbox','state',DeliveryState)}:
        raise ValueError('trace_invalid_aggregate')
    members=tuple(cls) if members is None else members
    params=[]
    if column=='error':
        expression='CASE WHEN error IS NULL THEN ?'
        params.append(SafeReason.NONE.value)
        for member in members:
            expression+=' WHEN error=? THEN ?'
            params.extend(('ValueError: '+member.value,member.value))
    else:
        expression='CASE'
        for member in members:
            expression+=' WHEN '+column+'=? THEN ?'
            params.extend((member.value,member.value))
    expression+=' ELSE ? END'
    params.extend((cls.UNKNOWN.value,business))
    rows=store.rows('SELECT '+expression+' AS bucket,COUNT(*) AS n FROM '+table+
                    ' WHERE business=? GROUP BY 1',tuple(params))
    counts={member:0 for member in cls}
    for row in rows:
        counts[cls(row['bucket'])]=_count(row['n'])
    _count(sum(counts.values()))
    return tuple(counts.items())


def _snapshot(store, *, business, now):
    if (type(business) is not str or not business or len(business)>200
            or type(now) not in (int,float) or not 0 <= now <= 2**53-1
            or not math.isfinite(now)):
        raise ValueError('trace_invalid_scope_or_clock')
    events=_buckets(store,business,table='events',column='state',cls=Processing)
    kinds=_buckets(store,business,table='events',column='kind',cls=EventKind)
    safe_codes=tuple(reason for reason in SafeReason if reason not in
                     (SafeReason.NONE,SafeReason.UNKNOWN,SafeReason.EXTERNAL_ERROR))
    errors=_buckets(store,business,table='events',column='error',cls=SafeReason,members=safe_codes)
    messages=_buckets(store,business,table='outbox',column='state',cls=DeliveryState)
    # Usage zero placeholders are ambiguous after timeout/parser failure. Do not
    # present them as known actual zero tokens or infer usage from event.done.
    row=store.execute('''SELECT COUNT(*) AS calls,
        COALESCE(SUM(CASE WHEN typeof(input_tokens)='integer' AND input_tokens>0
            AND input_tokens<=10000000 THEN input_tokens ELSE 0 END),0) AS in_known,
        COALESCE(SUM(CASE WHEN typeof(output_tokens)='integer' AND output_tokens>0
            AND output_tokens<=10000000 THEN output_tokens ELSE 0 END),0) AS out_known,
        COALESCE(SUM(CASE WHEN typeof(input_tokens)='integer' AND input_tokens>0
            AND input_tokens<=10000000 THEN 0 ELSE 1 END),0) AS in_unknown,
        COALESCE(SUM(CASE WHEN typeof(output_tokens)='integer' AND output_tokens>0
            AND output_tokens<=10000000 THEN 0 ELSE 1 END),0) AS out_unknown
        FROM usage WHERE business=? AND at>=? AND at<=?''',(business,now-86400,now)).fetchone()
    calls=_count(row['calls']); in_unknown=_count(row['in_unknown']); out_unknown=_count(row['out_unknown'])
    def known(total, unknown):
        return total if calls and not unknown and type(total) is int and 0<=total<=_MAX_USAGE_TOKENS else None
    usage=TokenUsage(known(row['in_known'],in_unknown),known(row['out_known'],out_unknown))
    return AdminSnapshot(events,kinds,errors,messages,calls,usage,in_unknown,out_unknown)


def _render(snapshot,action_lines=()):
    def counts(values):
        return ', '.join(key.value+'='+str(value) for key,value in values)
    def token(value):
        return 'нет данных' if value is None else str(value)
    body=('Диагностика текущего бизнеса: агрегаты до завершения этой команды.\n'
        'События, накопленные: '+counts(snapshot.events)+'\n'
        'Виды событий: '+counts(snapshot.event_kinds)+'\n'
        'Причины: '+counts(snapshot.errors)+'\n'
        'Очередь/доставка, накопленные: '+counts(snapshot.messages)+'\n'
        'Usage-строк за последние24ч: '+str(snapshot.usage_rows_last24h)+
        '; входных токенов: '+token(snapshot.usage.input_tokens)+
        '; выходных токенов: '+token(snapshot.usage.output_tokens)+'.\n'
        'Предложенные действия: нет данных; применённые действия: нет данных.\n'
        'done означает обработку события; sent — отправку сообщения. '
        'unknown не означает успех или выполнение сотрудником. Это не счёт провайдера.')
    if action_lines:
        body=body.replace('Предложенные действия: нет данных; применённые действия: нет данных.',
                          'Исторические предложенные/применённые действия: нет данных.')
    for line in action_lines:
        candidate=body+'\n\n'+line
        if len(candidate.encode('utf-16-le'))//2>3500 or len(candidate.encode())>8000:break
        body=candidate
    if len(body.encode('utf-16-le'))//2>3500 or len(body.encode())>8000:
        return 'Диагностика недоступна: превышен размер.'
    return body


def handle_trace(engine, event_id, payload):
    """Called only for a fresh exact /trace inside Engine.reply transaction.

    No diagnostic response for staff/groups/personal/invalid TEAM profiles. This
    command changes no tasks/schedules/permissions. It queues only an authorized
    private reply; actual transport remains the existing Dispatcher's job.
    """
    if type(payload) is not dict or payload.get('text')!='/trace':
        raise ValueError('trace_exact_command_required')
    business=engine.b
    authorized=('team_intake' in engine.c and engine.c.get('business')==business
                and engine._operator_turn(payload,'reply'))
    if authorized:
        snapshot=_snapshot(engine.s,business=business,now=engine.now())
        engine.s.queue(business,payload['sender'],_render(snapshot,_action_lines(engine)),
                       'trace:'+event_id,engine.now(),preferred_transport=payload['channel'])
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",
                     (business,event_id))
