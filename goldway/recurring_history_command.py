"""Existing registered recurring relationships only; no scheduling or model action."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from .own_task_overview import _PassReader
from .task_card_command import (_snapshot, _gate, _intelligence_matches, CardUnavailable,
    _bounded_columns, _text, _integer)
from .monthly_task_history import MonthlyHistoryUnavailable, _numeric
from .presentation import STATUS
from .answer_citations import _scope

MAX_SERIES=64
MAX_OCCURRENCES=64
MAX_REFS=256
MAX_OUTPUT_UNITS=3500
NOTICE='Не удалось показать историю регулярного поручения. Укажите номер доступной задачи или ответьте на её карточку.'
AMBIGUOUS='У этого поручения несколько сохранённых серий. Чтобы уточнить серию, ответьте на карточку её конкретного экземпляра или укажите номер экземпляра.'
_REQUEST=re.compile(r'покажи историю регулярной задачи(?:\s+№\s*(?P<number>[0-9]{1,9}))?')


class HistoryUnavailable(ValueError):
    def __init__(self):super().__init__('recurring_history_unavailable')


@dataclass(frozen=True)
class RecurringHistory:
    text:str
    evidence_ids:tuple[str,...]
    snapshot_digest:str


def _request(text):
    if type(text) is not str or len(text)>128:return None
    return _REQUEST.fullmatch(' '.join(text.casefold().split()).rstrip('?!.' ))


def is_recurring_history_command(text):return _request(text) is not None


def _selected(reader,business,number,task_id):
    if number is not None:
        if type(number) is not int or not 1<=number<=999999999:raise HistoryUnavailable()
        where,params='business=? AND public_number=?',(business,number)
    else:
        if type(task_id) is not str or not 0<len(task_id)<=160:raise HistoryUnavailable()
        where,params='business=? AND id=?',(business,task_id)
    rows=reader.query('SELECT '+_bounded_columns({'id':_text('id',160)})+' FROM tasks WHERE '+where,params)
    if len(rows)!=1:raise HistoryUnavailable()
    return rows[0]['id']


def _clock_label(value,zone):return datetime.fromtimestamp(value,zone).strftime('%d.%m.%Y %H:%M')


def _collect(reader,store,config,sender,index,now,number,task_id):
    policy=_gate(config,sender,index,store,now)
    if sender not in config['admins']:raise HistoryUnavailable()
    business=config['business'];target_id=_selected(reader,business,number,task_id)
    target,target_refs,target_fp=_snapshot(reader,store,config,sender,index,now,None,target_id)
    tables=reader.query("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('c07_series','c07_occurrences')",maximum=2)
    if {r['name'] for r in tables}!={'c07_series','c07_occurrences'}:raise HistoryUnavailable()
    spec={'id':_text('id',160),'task_id':_text('task_id',160),'actor':_text('actor',128),'owner':_text('owner',128),
        'state':_text('state',16),'reason':_text('reason',64,True),'next_at':_numeric('next_at'),'created':_numeric('created')}
    series=reader.query('SELECT '+_bounded_columns(spec)+' FROM c07_series WHERE business=?',(business,),maximum=MAX_SERIES)
    # Existing unique(business,task_id) resolves an explicitly selected occurrence.
    occurrence=reader.query('SELECT '+_bounded_columns({'series_id':_text('series_id',160)})+
        ' FROM c07_occurrences WHERE business=? AND task_id=?',(business,target_id))
    matches=([r for r in series if r['id']==occurrence[0]['series_id']] if occurrence
             else [r for r in series if r['task_id']==target_id])
    refs=set(target_refs)
    if len(matches)>1:
        # No old/active/replacement selection inferred from timestamps or titles.
        return 'ambiguous',(),tuple(sorted(refs)),_hash([policy,target_fp,[(r['id'],r['task_id']) for r in matches]])
    if not matches:
        if occurrence:raise HistoryUnavailable()
        return 'empty',(target['public_number'],),tuple(sorted(refs)),_hash([policy,target_fp,'no_series'])
    series=matches[0]
    if (series['state'] not in ('active','paused') or series['actor'] not in config['admins']
        or series['owner'] not in config['employees'] or series['created']>now):raise HistoryUnavailable()
    # Validate stored clock range before output; paused next_at can legitimately be past.
    _clock_label(series['next_at'],ZoneInfo(config['timezone']))
    template,template_refs,template_fp=_snapshot(reader,store,config,sender,index,now,None,series['task_id'])
    refs.update(template_refs)
    entries=reader.query('SELECT '+_bounded_columns({'occurrence_key':_text('occurrence_key',256),
        'task_id':_text('task_id',160),'at':_numeric('at'),'created':_numeric('created')})+
        ' FROM c07_occurrences WHERE business=? AND series_id=?',(business,series['id']),maximum=MAX_OCCURRENCES)
    allowed=[]
    for row in entries:
        if row['task_id']==series['task_id'] or not row['at']<=row['created']<=now:raise HistoryUnavailable()
        _clock_label(row['at'],ZoneInfo(config['timezone']));_clock_label(row['created'],ZoneInfo(config['timezone']))
        status=reader.query('SELECT '+_bounded_columns({'status':_text('status',32)})+
            ' FROM tasks WHERE business=? AND id=?',(business,row['task_id']))
        if len(status)!=1 or status[0]['status'] not in STATUS:raise HistoryUnavailable()
        try:task,taskrefs,fp=_snapshot(reader,store,config,sender,index,now,None,row['task_id'])
        except (CardUnavailable,MonthlyHistoryUnavailable):continue
        refs.update(taskrefs)
        if len(refs)>MAX_REFS:raise HistoryUnavailable()
        allowed.append((row['at'],row['created'],task['public_number'],task['status'],fp))
    state=[policy,target_fp,template_fp,series,entries,allowed]
    data=(template['public_number'],series['state'],series['next_at'],tuple(sorted(allowed,reverse=True)))
    return 'series',data,tuple(sorted(refs)),_hash(state)


def _hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _render(kind,data,config,now):
    if kind=='ambiguous':return AMBIGUOUS
    if kind=='empty':return 'Для задачи №'+str(data[0])+' нет сохранённой связи с регулярной серией. Расписание не изменено.'
    number,state,next_at,rows=data;zone=ZoneInfo(config['timezone'])
    lines=['История регулярного поручения №'+str(number),
        'Срез на '+_clock_label(now,zone)+' ('+zone.key+').',
        'Сохранённая серия: '+('активна' if state=='active' else 'приостановлена')+'.',
        'Сохранённое следующее время: '+_clock_label(next_at,zone)+'. Это не обещание запуска или доставки.']
    for slot,created,public_number,status,_ in rows[:5]:
        if status not in STATUS:raise HistoryUnavailable()
        lines.append('• Задача №'+str(public_number)+' · '+STATUS[status]+
            '\n  Плановый период: '+_clock_label(slot,zone)+'; запись экземпляра создана: '+_clock_label(created,zone)+'.')
    if not rows:lines.append('Нет доступных зарегистрированных экземпляров в этом срезе.')
    if len(rows)>5:lines.append('Другие доступные экземпляры не показаны: '+str(len(rows)-5)+'.')
    lines.append('Статусы взяты из текущих записей. Время периода и создания не является временем выполнения; результат и доставка независимо не проверены. Задачи и расписание не изменены.')
    text='\n'.join(lines)
    if len(text.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise HistoryUnavailable()
    return text


def build_recurring_history(store,config,sender,index,*,clock,number=None,task_id=None):
    try:
        now=clock();reader=_PassReader(store)
        initial=_collect(reader,store,config,sender,index,now,number,task_id)
        text=_render(initial[0],initial[1],config,now)
        final_now=clock()
        if type(final_now) not in (int,float) or final_now<now:raise HistoryUnavailable()
        reader.cache.clear()
        final=_collect(reader,store,config,sender,index,final_now,number,task_id)
        if final!=initial:raise HistoryUnavailable()
        return RecurringHistory(text,initial[2],initial[3])
    except HistoryUnavailable:raise
    except Exception:raise HistoryUnavailable() from None


def _authorized(engine,payload,intelligence):
    if (not _intelligence_matches(engine,intelligence) or not engine._closed_team()
        or not engine._operator_turn(payload,'reply') or 'team_intake' not in engine.c):raise HistoryUnavailable()
    scope,parts=_scope(engine.c,payload)
    if parts[3]!=str(engine.c['employees'][payload['sender']]['telegram_user_id']):raise HistoryUnavailable()
    return scope,_gate(engine.c,payload['sender'],intelligence.index,engine.s,engine.now())


def handle_recurring_history(engine,event_id,payload):
    request=_request(payload.get('text')) if type(payload) is dict else None
    if request is None or not engine.s.db.in_transaction:raise HistoryUnavailable()
    intelligence=engine.intelligence
    try:policy=_authorized(engine,payload,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text,refs=NOTICE,()
    try:
        draft=build_recurring_history(engine.s,engine.c,payload['sender'],intelligence.index,clock=engine.now,
            number=int(request['number']) if request['number'] is not None else None,
            task_id=payload.get('task_id') if request['number'] is None else None)
        text,refs=draft.text,draft.evidence_ids
    except HistoryUnavailable:pass
    if _authorized(engine,payload,intelligence)!=policy:raise HistoryUnavailable()
    key='recurring-history:'+event_id
    engine.s.queue(engine.b,payload['sender'],text,key,engine.now(),None,preferred_transport=payload['channel'])
    if _authorized(engine,payload,intelligence)!=policy:raise HistoryUnavailable()
    if refs:
        row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,key)).fetchone()
        intelligence.protect_outbox(row[0],refs)
        if _authorized(engine,payload,intelligence)!=policy:raise HistoryUnavailable()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
