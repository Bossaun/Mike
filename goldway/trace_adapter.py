"""Exact private TEAM admin diagnostic command; aggregate SQL only.

No payload/audit/config/credential/model-response retrieval. Uses fixed enums
from model_trace; proposal/applied facts are deliberately unknown, not inferred
from done events or queued messages. No schema, provider or dispatch changes.
"""
from dataclasses import dataclass
from enum import Enum
import math

from .model_trace import DeliveryState, Processing, SafeReason, TokenUsage


_MAX_COUNT = 10**9
_MAX_USAGE_TOKENS = 10_000_000


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


def _render(snapshot):
    def counts(values):
        return ', '.join(key.value+'='+str(value) for key,value in values)
    def token(value):
        return 'нет данных' if value is None else str(value)
    return ('Диагностика текущего бизнеса: агрегаты до завершения этой команды.\n'
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
        engine.s.queue(business,payload['sender'],_render(snapshot),
                       'trace:'+event_id,engine.now(),preferred_transport=payload['channel'])
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",
                     (business,event_id))
