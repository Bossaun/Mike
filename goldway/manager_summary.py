"""Bounded current manager summary using existing current task-card ACL checks.

No model, refresh, task writes, history reconstruction or delivery policy. Queue
keeps the existing summary key/recipient/routing; explicit work refs make existing
before-send role/ACL evaluation meaningful even for tasks without document ACLs.
"""
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from .own_task_overview import _PassReader
from .task_card_command import (_gate, _snapshot, _intelligence_matches, CardUnavailable,
    _current, _bounded_columns, _text)
from .monthly_task_history import MonthlyHistoryUnavailable
from .work_context import _deadline

MAX_REGISTER=64
MAX_REFS=256
MAX_VISIBLE_PER_GROUP=3
MAX_OUTPUT_UNITS=3500
NOTICE='Не удалось подготовить сводку по текущим доступным задачам. Проверьте доступ и повторите запрос.'
_STATUSES={'proposed','confirmed','in_progress','blocked','completed'}


class SummaryUnavailable(ValueError):
    def __init__(self):super().__init__('manager_summary_unavailable')


@dataclass(frozen=True)
class ManagerSummary:
    text:str
    evidence_ids:tuple[str,...]
    snapshot_digest:str


def _deadline_state(value,config,now):
    if value is None:return False
    if type(value) is not str or not value:raise SummaryUnavailable()
    try:
        if date.fromisoformat(value).isoformat()!=value:raise ValueError()
    except ValueError:
        try:
            moment=datetime.fromisoformat(value.replace('Z','+00:00'))
            if moment.utcoffset() is None:raise ValueError()
        except (ValueError,TypeError):raise SummaryUnavailable() from None
    canonical,overdue,_=_deadline(value,config,now)
    if canonical is None:raise SummaryUnavailable()
    return overdue


def _collect(reader,store,config,sender,index,now):
    policy=_gate(config,sender,index,store,now)
    if sender not in config['admins']:raise SummaryUnavailable()
    business=config['business']
    ids=reader.query('SELECT '+_bounded_columns({'id':_text('id',160)})+
        ' FROM tasks WHERE business=?',(business,),maximum=MAX_REGISTER)
    records,_=_current(reader,business,tuple(row['id'] for row in ids))
    tasks=[];refs=set()
    for metadata in sorted(records.values(),key=lambda row:row['id']):
        if (metadata['status'] not in _STATUSES
            or (not metadata['owner_missing'] and metadata['owner'] not in config['employees'])
            or (not metadata['deadline_missing'] and metadata['deadline'] is None)):
            raise SummaryUnavailable()
        _deadline_state(metadata['deadline'],config,now)
        try:
            task,taskrefs,fingerprint=_snapshot(reader,store,config,sender,index,now,None,metadata['id'])
        except (CardUnavailable,MonthlyHistoryUnavailable):
            # Ordinary current permission/source denial contributes no display/count.
            # Reader budget/type failures are fatal _PassReader exceptions instead.
            continue
        if type(task['title']) is not str or not task['title'].strip():raise SummaryUnavailable()
        overdue=_deadline_state(task['deadline'],config,now)
        flags=(task['status']=='completed',
            task['status'] in {'confirmed','in_progress','blocked'} and overdue,
            task['status']=='blocked',
            task['status'] in {'blocked','proposed'} or task['owner'] is None or task['deadline'] is None)
        tasks.append((task['public_number'],task['title'],flags,fingerprint))
        refs.update(taskrefs)
        if len(refs)>MAX_REFS:raise SummaryUnavailable()
    state=[policy,tasks]
    digest=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return tuple(sorted(tasks)),tuple(sorted(refs)),digest


def _render(rows,config,now):
    stamp=datetime.fromtimestamp(now,ZoneInfo(config['timezone']))
    lines=['Сводка по доступным записям задач',
        'Срез на '+stamp.strftime('%d.%m.%Y %H:%M')+' ('+stamp.tzname()+').']
    for i,label in enumerate(('Выполнено','Задерживается','Есть препятствие','Нужно решение')):
        items=[row for row in rows if row[2][i]]
        visible=items[:MAX_VISIBLE_PER_GROUP]
        def item(row):
            title=' '.join(row[1].split())
            if len(title)>90:title=title[:89]+'…'
            return '№'+str(row[0])+' '+title
        text='; '.join(item(row) for row in visible) if items else 'нет в доступных записях'
        if len(items)>len(visible):text+=' (ещё '+str(len(items)-len(visible))+')'
        lines.append(label+': '+text)
    lines.append('Только текущие доступные записи; это не отчёт за период и не независимая проверка результата. Черновики требуют решения, просроченными поручениями не считаются.')
    output='\n'.join(lines)
    if len(output.encode('utf-16-le'))//2>MAX_OUTPUT_UNITS:raise SummaryUnavailable()
    return output


def build_manager_summary(store,config,sender,index,*,clock):
    try:
        now=clock();reader=_PassReader(store)
        initial=_collect(reader,store,config,sender,index,now)
        output=_render(initial[0],config,now)
        final_now=clock()
        if type(final_now) not in (int,float) or final_now<now:raise SummaryUnavailable()
        reader.cache.clear()
        final=_collect(reader,store,config,sender,index,final_now)
        if final!=initial:raise SummaryUnavailable()
        return ManagerSummary(output,initial[1],initial[2])
    except SummaryUnavailable:raise
    except Exception:raise SummaryUnavailable() from None


def _authorized(engine,recipient,intelligence):
    if (not _intelligence_matches(engine,intelligence) or not engine._closed_team()
        or recipient not in engine.c['admins'] or engine.c.get('meeting_recipient') is not None):
        raise SummaryUnavailable()
    return _gate(engine.c,recipient,intelligence.index,engine.s,engine.now())


def queue_manager_summary(engine,key):
    """Only called from the existing TEAM queue_summary transaction; no recipients added."""
    if not engine.s.db.in_transaction:raise SummaryUnavailable()
    intelligence=engine.intelligence
    recipients=tuple(engine.c.get('admins',()))
    if recipients!=('test_operator',):return
    recipient=recipients[0];dedupe=f'summary:{key}:{recipient}'
    # A duplicate request must not re-protect/re-authorize a previously queued body.
    if engine.s.execute('SELECT 1 FROM outbox WHERE business=? AND dedupe=?',(engine.b,dedupe)).fetchone():return
    try:policy=_authorized(engine,recipient,intelligence)
    except (ValueError,TypeError,KeyError,AttributeError):
        # No protected record content, model or task access in unavailable state.
        engine.s.queue(engine.b,recipient,NOTICE,dedupe,engine.now());return
    try:draft=build_manager_summary(engine.s,engine.c,recipient,intelligence.index,clock=engine.now)
    except SummaryUnavailable:
        if _authorized(engine,recipient,intelligence)!=policy:raise SummaryUnavailable()
        engine.s.queue(engine.b,recipient,NOTICE,dedupe,engine.now())
        if _authorized(engine,recipient,intelligence)!=policy:raise SummaryUnavailable()
        return
    if _authorized(engine,recipient,intelligence)!=policy:raise SummaryUnavailable()
    engine.s.queue(engine.b,recipient,draft.text,dedupe,engine.now(),None)
    if _authorized(engine,recipient,intelligence)!=policy:raise SummaryUnavailable()
    row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,dedupe)).fetchone()
    intelligence.protect_outbox(row[0],draft.evidence_ids)
    if _authorized(engine,recipient,intelligence)!=policy:raise SummaryUnavailable()
