"""Exact private TEAM reminder overview. Read state; never tick or reschedule.

Requires existing Scheduler and KnowledgeIndex. Display is an observation of
registered state, not a delivery promise. IDs/ACLs remain internal provenance.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from .intake import validate_team
from .intelligence import reader_key
from .knowledge import KnowledgeIndex
from .monthly_task_history import _Reader, _bounded_columns, _current, _text, _numeric
from .scheduling import Scheduler
from .work_context import WORK_PREFIX, _readable, work_id

MAX_REGISTER = 128
MAX_SOURCES = 64
MAX_VISIBLE = 16
_COMMANDS = frozenset(('/reminders','какие у меня напоминания?','покажи мои напоминания'))


class OverviewUnavailable(ValueError):pass


def _fail():raise OverviewUnavailable('reminder_overview_unavailable')


def is_reminder_overview(text):
    return type(text) is str and text.casefold() in _COMMANDS


@dataclass(frozen=True)
class ReminderOverview:
    text: str
    evidence_ids: tuple[str,...]
    snapshot_digest: str


def _grant(reader,ref,who,business,now):
    # Exactly the current KnowledgeIndex.allowed_ids predicate, identifiers only.
    found=reader.query('''SELECT c.id FROM knowledge_chunks c
        JOIN knowledge_documents d ON d.id=c.document_key
        JOIN knowledge_readers r ON r.document_key=d.id
        WHERE c.id=? AND r.reader=? AND d.observed_at<=? AND d.permission_expires_at>?''',
        (ref,reader_key(business,who),now,now))
    return bool(found)


def _snapshot(reader,store,config,sender,index,scheduler,now):
    validate_team(config)
    if (sender not in config['employees'] or type(scheduler) is not Scheduler
            or scheduler.s is not store or scheduler.c is not config or scheduler.b!=config['business']
            or type(index) is not KnowledgeIndex or index.store is not store):_fail()
    business=config['business'];zone=ZoneInfo(config['timezone'])
    if type(now) not in (int,float) or not 0<=now<=2**53-1:_fail()
    datetime.fromtimestamp(now,zone)
    policy=[business,sender,config['timezone'],config['admins'],config['employees'],
            config['allowed_live_recipients'],config['team_intake']]
    # Bound the scanned register before state filters and task joins.
    for table in ('c07_checks','c07_series'):
        reader.query('SELECT 1 FROM '+table+' WHERE business=?',(business,),maximum=MAX_REGISTER)
    # Finite whole-business ID register: no title/evidence/source body transfer.
    ids=reader.query('SELECT '+_bounded_columns({'id':_text('id',160)})+
                     ' FROM tasks WHERE business=?',(business,),maximum=MAX_REGISTER)
    records,acl=_current(reader,business,tuple(row['id'] for row in ids))
    common={k:_text(k,160 if k=='task_id' else 256 if k=='source_id' else 128)
            for k in ('id','task_id','actor','owner')}
    checks=reader.query('SELECT '+_bounded_columns(dict(common,source_id=_text('source_id',256),
        at=_numeric('at'),state=_text('state',16)))+
        " FROM c07_checks WHERE business=? AND state IN ('pending','fired')",(business,),maximum=MAX_REGISTER)
    # Frozen series payload is compared INSIDE SQLite, never transferred.
    fields=('title','owner','deadline','next_step','criterion','source_id')
    matching=' AND '.join(f'json_extract(s.snapshot,\'$.{field}\') IS t.{field}' for field in fields)
    live_snapshot=f'''CASE WHEN typeof(s.snapshot)='text' AND length(CAST(s.snapshot AS BLOB))<=32768
        AND json_valid(s.snapshot) THEN ({matching}) AND json_type(s.snapshot,'$.source_ids')='array'
        AND json_array_length(s.snapshot,'$.source_ids')<=64
        AND NOT EXISTS(SELECT 1 FROM json_each(s.snapshot,'$.source_ids') j
            WHERE j.type!='text' OR (j.value IS NOT t.source_id AND NOT EXISTS(SELECT 1 FROM task_sources l
              WHERE l.business=s.business AND l.task_id=s.task_id AND l.source_id=j.value))) ELSE 0 END'''
    series=reader.query('SELECT '+_bounded_columns({f's.{k}':guard.replace(k,'s.'+k) for k,guard in {**common,
        'next_at':_numeric('next_at'),'state':_text('state',16)}.items()}).replace(' AS s.',' AS ')+
        ', '+live_snapshot+''' AS snapshot_matches FROM c07_series s
        JOIN tasks t ON t.business=s.business AND t.id=s.task_id
        WHERE s.business=? AND s.state IN ('active','paused')''',(business,),maximum=MAX_REGISTER)
    candidates=[];visible_refs=set();docs_meta=[];source_meta=[]
    for kind,entries in (('check',checks),('series',series)):
        for row in entries:
            ref=work_id(business,row['task_id']);task=records.get(ref)
            if not task or (sender not in config['admins'] and row['owner']!=sender):continue
            if (row['owner'] not in config['employees'] or row['actor'] not in config['employees']
                    or row['owner'] not in config['allowed_live_recipients'] or task['owner']!=row['owner']
                    or task['status'] not in ({'confirmed','in_progress','blocked'} if kind=='check' else {'confirmed','in_progress','blocked','completed'})
                    or (kind=='series' and (row['actor'] not in config['admins'] or not row['snapshot_matches']))):continue
            # Only reachable task ACLs can affect this row. Missing dependencies deny.
            pending=[ref];closure=set()
            while pending:
                node=pending.pop()
                if node in closure:continue
                closure.add(node)
                if len(closure)>MAX_REGISTER:_fail()
                pending.extend(dep for dep in (acl.get(node) or ()) if dep.startswith(WORK_PREFIX) and dep in records)
            docrefs=set().union(*(acl.get(node) or set() for node in closure))
            docrefs={value for value in docrefs if not value.startswith(WORK_PREFIX)}
            if len(docrefs)>256:_fail()
            authorized=True
            for who in sorted({sender,row['actor'],row['owner']}):
                grants={value for value in docrefs if _grant(reader,value,who,business,now)}
                docs_meta.append([row['id'],who,sorted(grants)])
                role='admin' if who in config['admins'] else 'employee'
                if ref not in _readable(records,acl,role,who,grants):authorized=False
            if not authorized:continue
            # Scheduler._sources_live checks primary and every linked source.
            primary=reader.query('SELECT '+_bounded_columns({'source_id':_text('source_id',256)})+
                                 ' FROM tasks WHERE business=? AND id=?',(business,row['task_id']))
            links=reader.query('SELECT '+_bounded_columns({'source_id':_text('source_id',256)})+
                               ' FROM task_sources WHERE business=? AND task_id=?',(business,row['task_id']),maximum=MAX_SOURCES)
            sources={value['source_id'] for value in primary+links}
            live=True
            for source in sorted(sources):
                states=reader.query('SELECT '+_bounded_columns({'deleted':"(typeof(deleted)='integer' AND deleted IN (0,1))"})+
                                    ' FROM sources WHERE business=? AND id=?',(business,source))
                source_meta.append([source,states])
                if not states or states[0]['deleted']!=0:live=False
            if not live or (kind=='check' and row['source_id']!=primary[0]['source_id']):continue
            candidates.append((kind,task['public_number'],row['at'] if kind=='check' else row['next_at'],row['state']))
            visible_refs.update(closure|docrefs)
    state=[policy,records,{k:sorted(v) if v is not None else None for k,v in acl.items()},checks,series,docs_meta,source_meta]
    fingerprint=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return fingerprint,tuple(sorted(visible_refs)),tuple(sorted(candidates,key=lambda item:(item[0],item[2],item[1]))),zone


def build_reminder_overview(store,config,sender,index,scheduler,*,now):
    try:
        reader=_Reader(store)
        initial=_snapshot(reader,store,config,sender,index,scheduler,now)
        digest,refs,items,zone=initial
        lines=['Сохранённые напоминания · срез '+datetime.fromtimestamp(now,zone).strftime('%d.%m.%Y %H:%M')+' ('+zone.key+')']
        checks=[item for item in items if item[0]=='check'];series=[item for item in items if item[0]=='series']
        shown=0
        for label,values in (('Разовые проверки',checks),('Регулярные серии',series)):
            lines.append(label+':')
            if not values:lines.append('Нет доступных зарегистрированных записей в этом срезе.')
            for kind,number,at,state in values:
                if shown>=MAX_VISIBLE:break
                when=datetime.fromtimestamp(at,zone).strftime('%d.%m.%Y %H:%M')
                status={'pending':'ожидает проверки','fired':'проверка зарегистрирована как сработавшая; доставка неизвестна',
                        'active':'серия активна; сохранённое следующее время','paused':'серия приостановлена; сохранённое время не означает запуск'}[state]
                lines.append(f'• Задача №{number}: {when} — {status}.');shown+=1
        if len(items)>shown:lines.append('Другие доступные записи не показаны: '+str(len(items)-shown)+'.')
        lines+=['Это состояние расписания, не обещание доставки. Задачи и расписание не изменены.',
                'Чтобы отменить разовую проверку: «Отмени напоминание по задаче №…». Регулярную серию отменяет руководитель.']
        text='\n'.join(lines)
        if len(text.encode('utf-16-le'))//2>3500:_fail()
        final=_snapshot(reader,store,config,sender,index,scheduler,now)
        if final[:3]!=initial[:3]:_fail()
        return ReminderOverview(text,refs,digest)
    except OverviewUnavailable:raise
    except Exception:raise OverviewUnavailable('reminder_overview_unavailable') from None


def handle_reminder_overview(engine,event_id,payload):
    if not is_reminder_overview(payload.get('text')):_fail()
    authorized=(payload.get('channel') in ('bot','business') and engine._closed_team()
                and payload.get('sender') in engine.c['employees'] and engine.c.get('business')==engine.b)
    if authorized:
        text='Обзор напоминаний недоступен: расписание или текущие права сейчас не подтверждены.';refs=()
        if engine.scheduler is not None and engine.intelligence is not None:
            try:
                draft=build_reminder_overview(engine.s,engine.c,payload['sender'],engine.intelligence.index,
                                               engine.scheduler,now=engine.now())
                text,refs=draft.text,draft.evidence_ids
            except OverviewUnavailable:pass
        key='reminder-overview:'+event_id
        engine.s.queue(engine.b,payload['sender'],text,key,engine.now(),preferred_transport=payload['channel'])
        if refs:
            row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,key)).fetchone()
            engine.intelligence.protect_outbox(row['id'],refs)
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
