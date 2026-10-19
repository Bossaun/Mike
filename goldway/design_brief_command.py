"""Private administrator supplied fields -> existing review-only design brief.

No NLP, knowledge lookup, assets retrieval, task creation or designer delivery.
Material descriptions remain unverified text; no external Asset is fabricated.
"""
from datetime import datetime
import hashlib
import json
import re

from .marketing_drafts import DesignBrief, DraftError, EditableDraft, render_design_brief

MAX_INPUT_CHARS = 4000
MAX_OUTPUT_UNITS = 3500
_REQUESTS = frozenset(('подготовь тз дизайнеру', 'подготовь тз для дизайнера'))
_HEADER = 'тз дизайнеру'
_LABELS = {'цель': 'goal', 'аудитория': 'audience', 'формат': 'format',
           'срок': 'deadline', 'материалы': 'materials'}
_LIMITS = {'goal': 1000, 'audience': 1000, 'format': 100, 'deadline': 200, 'materials': 1000}
_UNKNOWN = frozenset(('', '[уточнить]'))
_TEMPLATE = ('ТЗ дизайнеру\nЦель: [уточнить]\nАудитория: [уточнить]\n'
             'Формат: [уточнить]\nСрок: [уточнить]\nМатериалы: [уточнить]')
_NOTICE = ('Черновик не подготовлен. Проверьте названия полей, повторы и размер блока: '
           'Цель, Аудитория, Формат, Срок, Материалы.')
_AWARE_ISO = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'
                        r'(?::[0-9]{2})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')


class BriefUnavailable(ValueError):
    def __init__(self):
        super().__init__('design_brief_unavailable')


def _normalize(value):
    return ' '.join(value.casefold().split())


def is_design_brief_command(text):
    if type(text) is not str:
        return False
    if text.startswith('ТЗ дизайнеру по задаче'):
        return True
    if _normalize(text) in _REQUESTS:
        return True
    return _normalize(text.split('\n', 1)[0]) == _HEADER


def _fields(text):
    if (type(text) is not str or len(text) > MAX_INPUT_CHARS
            or any((ord(c) < 32 and c not in '\r\n') or ord(c) == 127 for c in text)):
        raise BriefUnavailable()
    lines = text.splitlines()
    if not lines or _normalize(lines[0]) != _HEADER or len(lines) > 6:
        raise BriefUnavailable()
    result = {}
    for line in lines[1:]:
        if ':' not in line:
            raise BriefUnavailable()
        label, value = line.split(':', 1)
        field = _LABELS.get(_normalize(label))
        if field is None or field in result:
            raise BriefUnavailable()
        value = value.strip()
        if len(value) > _LIMITS[field]:
            raise BriefUnavailable()
        result[field] = None if value.casefold() in _UNKNOWN else value
    return result


def build_brief(text, *, business, event_id):
    """Literal field parsing only; absent/placeholder values remain unknown.

    Only a complete explicit ISO datetime with offset can become a typed deadline.
    Other deadline text is preserved separately for clarification, never guessed.
    No listed material is treated as an available/confirmed file or source.
    """
    try:
        fields = _fields(text)
        deadline, supplied_deadline = None, fields.get('deadline')
        if supplied_deadline is not None and _AWARE_ISO.fullmatch(supplied_deadline):
            try:
                deadline = datetime.fromisoformat(supplied_deadline.replace('Z', '+00:00'))
            except ValueError:
                pass
        unknown = frozenset({'assets'} | {field for field in ('goal', 'audience', 'format')
                             if fields.get(field) is None} | ({'deadline'} if deadline is None else set()))
        identifier = 'brief:'+hashlib.sha256(json.dumps([business, event_id]).encode()).hexdigest()
        brief = DesignBrief(identifier, fields.get('goal'), fields.get('audience'), fields.get('format'),
                            deadline, (), False, unknown)
        rendered = render_design_brief(brief)
        lines = []
        for line in rendered.text.splitlines():
            if line == 'Материалы:':
                line = 'Подтверждённые материалы:'
            elif line == '[не предоставлены]':
                line = '[доступность не подтверждена]'
            elif line.startswith('Неизвестные поля: '):
                labels = {'goal': 'цель', 'audience': 'аудитория', 'format': 'формат',
                          'deadline': 'срок', 'assets': 'доступность материалов'}
                line = 'Нужно уточнить: '+', '.join(labels[field] for field in sorted(unknown))
            lines.append(line)
        if deadline is None and supplied_deadline is not None:
            lines.append('Срок из сообщения (требует уточнения): '+supplied_deadline)
        if fields.get('materials') is not None:
            lines.append('Описания материалов из сообщения (не проверены): '+fields['materials'])
        result = EditableDraft('\n'.join(lines), ())
        if len(result.text.encode('utf-16-le'))//2 > MAX_OUTPUT_UNITS:
            raise BriefUnavailable()
        return result
    except BriefUnavailable:
        raise
    except (DraftError, ValueError, TypeError, UnicodeError, OverflowError):
        raise BriefUnavailable() from None


def handle_design_brief(engine, event_id, payload):
    if type(payload) is dict and type(payload.get('text')) is str and payload['text'].startswith('ТЗ дизайнеру по задаче'):
        return _handle_task_brief(engine,event_id,payload)
    if (type(payload) is not dict or not is_design_brief_command(payload.get('text'))
            or not engine.s.db.in_transaction):
        raise BriefUnavailable()
    authorized = ('team_intake' in engine.c and engine.c.get('business') == engine.b
                  and engine._operator_turn(payload, 'reply'))
    if authorized:
        text = _NOTICE
        try:
            if len(payload['text']) > MAX_INPUT_CHARS:
                raise BriefUnavailable()
            if _normalize(payload['text']) in _REQUESTS:
                text = _TEMPLATE
            else:
                draft = build_brief(payload['text'], business=engine.b, event_id=event_id)
                text = draft.text
        except BriefUnavailable:
            pass
        engine.s.queue(engine.b, payload['sender'], text, 'design-brief:'+event_id,
                       engine.now(), preferred_transport=payload['channel'])
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",
                     (engine.b, event_id))


# This separate explicit command authorizes only its sender's own review reply.
# I92's delivery_authorized=False is never interpreted as a generic send grant.
TASK_PREFIX='task-design-brief:'
TASK_CANCEL='Task brief source or authorization changed; request a fresh draft'
_TASK_HEADER=re.compile(r'ТЗ дизайнеру по задаче №([1-9][0-9]{0,8})')


def _task_fields(text):
    if (type(text) is not str or len(text)>4000 or len(text.encode())>8000
            or any(ord(c)<32 and c!='\n' or ord(c)==127 or c in '\u0085\u2028\u2029' for c in text)):
        raise BriefUnavailable()
    lines=text.splitlines();match=_TASK_HEADER.fullmatch(lines[0]) if lines else None
    if match is None:raise BriefUnavailable()
    if len(lines)==1:
        if text!=lines[0]:raise BriefUnavailable()
        return int(match[1]),None
    if len(lines)!=5:raise BriefUnavailable()
    fields={}
    for line in lines[1:]:
        if ':' not in line:raise BriefUnavailable()
        label,value=line.split(':',1)
        key={'Цель':'goal','Аудитория':'audience','Формат':'format','Материалы':'materials'}.get(label)
        if key is None or key in fields:raise BriefUnavailable()
        value=value.strip()
        fields[key]=None if value.casefold() in _UNKNOWN else value
    return int(match[1]),fields


def _task_key(business,identifier):
    from .answer_citations import _hash
    return TASK_PREFIX+'v1:'+_hash([business,identifier])


def _task_accepted(reader,business,event,*,done=False):
    from .monthly_task_history import _bounded_columns,_text
    spec={'sender':_text('sender',128),'channel':_text('channel',16),
          'source_id':_text('source_id',1024),'text':_text('text',8000)}
    fields=','.join("json_extract(checked,'$."+k+"') AS "+k for k in spec)
    rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM (SELECT '+fields+
        " FROM (SELECT CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=16384 AND json_valid(payload) THEN payload END AS checked FROM events WHERE business=? AND id=? AND kind='reply'"+(" AND state='done'" if done else " AND state IN ('pending','done')")+"))",(business,event))
    if len(rows)!=1:raise BriefUnavailable()
    return rows[0]


def _task_load(reader,business,identifier):
    from .monthly_task_history import _bounded_columns,_text
    rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',16384)})+' FROM runtime WHERE key=?',(_task_key(business,identifier),))
    if len(rows)!=1:raise BriefUnavailable()
    def pairs(values):
        result={}
        for k,v in values:
            if k in result:raise BriefUnavailable()
            result[k]=v
        return result
    doc=json.loads(rows[0]['value'],object_pairs_hook=pairs)
    if type(doc) is not dict or set(doc)!={'version','event','request','number','input','source','task_id','refs','policy','seal','command'}:raise BriefUnavailable()
    if type(doc['version']) is not int or doc['version']!=1 or type(doc['number']) is not int or not 1<=doc['number']<=999999999:raise BriefUnavailable()
    if type(doc['event']) is not str or not 0<len(doc['event'].encode())<=1024:raise BriefUnavailable()
    if type(doc['request']) is not dict or set(doc['request'])!={'sender','channel','source_id'} or any(type(v) is not str for v in doc['request'].values()):raise BriefUnavailable()
    if type(doc['task_id']) is not str or not 0<len(doc['task_id'])<=160:raise BriefUnavailable()
    for k in ('input','source','policy','seal','command'):
        if type(doc[k]) is not str or re.fullmatch('[0-9a-f]{64}',doc[k]) is None:raise BriefUnavailable()
    if type(doc['refs']) is not list or len(doc['refs'])>256 or any(type(r) is not str or not 0<len(r)<=160 for r in doc['refs']) or doc['refs']!=sorted(set(doc['refs'])):raise BriefUnavailable()
    return doc


def cancel_task_brief(store,business,identifier):
    store.execute("UPDATE outbox SET state='cancelled',error=? WHERE business=? AND id=? AND dedupe LIKE 'task-design-brief:%' AND state IN ('pending','retry','blocked')",(TASK_CANCEL,business,identifier))


def _handle_task_brief(engine,event,payload):
    from .task_design_brief import TaskDesignBrief,TaskDesignBriefUnavailable
    from .task_card_command import build_task_brief_source,CardUnavailable
    from .task_card_reference import _barrier,_protected
    from .answer_citations import _hash,_scope
    from .monthly_task_history import _Reader
    from .weekly_review_command import _outbox,_seal
    store=engine.s;db=store.db
    if not db.in_transaction:raise BriefUnavailable()
    validator=TaskDesignBrief(engine=engine,enabled=True)
    policy=None;request={k:payload.get(k) for k in ('sender','channel','source_id')}
    db.execute('SAVEPOINT task_design_brief')
    try:
        policy=validator._policy(request)
        if type(event) is not str or not 0<len(event.encode())<=1024:raise BriefUnavailable()
        reader=_Reader(store);scope,parts=_scope(engine.c,request);_barrier(reader,engine.b,scope,event)
        accepted=_task_accepted(reader,engine.b,event)
        if accepted!=dict(request,text=payload['text']):raise BriefUnavailable()
        number,fields=_task_fields(payload['text'])
        if fields is None:
            body=payload['text']+'\nЦель: [уточнить]\nАудитория: [уточнить]\nФормат: [уточнить]\nМатериалы: [уточнить]'
            if validator._policy(request)!=policy:raise BriefUnavailable()
            store.queue(engine.b,request['sender'],body,'design-brief-task-template:'+_hash([event,scope]),engine.now(),preferred_transport=request['channel'])
            if validator._policy(request)!=policy:raise BriefUnavailable()
        else:
            draft=validator.preview(request,number,**fields)
            if validator._policy(request)!=policy:raise BriefUnavailable()
            dedupe=TASK_PREFIX+_hash([event,scope]);at=engine.now()
            if not store.queue(engine.b,request['sender'],draft.text,dedupe,at,draft.task_source.task_id,preferred_transport=request['channel']):raise BriefUnavailable()
            if validator._policy(request)!=policy:raise BriefUnavailable()
            rowid=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,dedupe)).fetchone()[0]
            engine.intelligence.protect_outbox(rowid,draft.evidence_ids)
            if validator._policy(request)!=policy:raise BriefUnavailable()
            row=_outbox(reader,engine.b,rowid)
            if (row['text']!=draft.text or row['state']!='pending' or row['task_id']!=draft.task_source.task_id
                    or row['recipient']!=request['sender'] or row['preferred_transport']!=request['channel'] or row['parse_mode'] is not None):raise BriefUnavailable()
            _protected(reader,engine.b,rowid,draft.evidence_ids)
            doc=dict(version=1,event=event,request=request,number=number,input=draft.input_fingerprint,source=draft.task_source.snapshot_digest,
                task_id=draft.task_source.task_id,refs=list(draft.evidence_ids),policy=policy,seal=_seal(row),command=_hash(payload['text']))
            value=json.dumps(doc,ensure_ascii=False,separators=(',',':'))
            if len(value.encode())>16384:raise BriefUnavailable()
            store.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(_task_key(engine.b,rowid),value))
            current=build_task_brief_source(store,engine.c,request['sender'],engine.intelligence.index,clock=engine.now,number=number)
            if current!=draft.task_source or validator._policy(request)!=policy:raise BriefUnavailable()
            if _outbox(reader,engine.b,rowid)!=row or _task_load(reader,engine.b,rowid)!=doc or _task_accepted(reader,engine.b,event)!=accepted:raise BriefUnavailable()
            _protected(reader,engine.b,rowid,draft.evidence_ids)
            _barrier(reader,engine.b,scope,event)
            if validator._policy(request)!=policy:raise BriefUnavailable()
    except BaseException as error:
        db.execute('ROLLBACK TO task_design_brief');db.execute('RELEASE task_design_brief')
        if not isinstance(error,(ValueError,TypeError,KeyError)):raise
        try:allowed=policy is not None and validator._policy(request)==policy
        except Exception:allowed=False
        if allowed:
            store.queue(engine.b,request['sender'],_NOTICE,'design-brief-task-notice:'+_hash(event),engine.now(),preferred_transport=request['channel'])
    else:db.execute('RELEASE task_design_brief')
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event))


def _task_chat(reader,business,request,parts,now):
    from .monthly_task_history import _bounded_columns,_text,_numeric
    spec={'bot_chat':_text('bot_chat',128,True),'business_chat':_text('business_chat',128,True),
          'connection':_text('connection',512,True),'last_inbound':_numeric('last_inbound')}
    rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM chats WHERE business=? AND recipient=?',(business,request['sender']))
    if len(rows)!=1:raise BriefUnavailable()
    chat=rows[0]
    if request['channel']=='bot':
        if str(chat['bot_chat'])!=parts[3]:raise BriefUnavailable()
    elif (str(chat['business_chat'])!=parts[3] or chat['connection']!=parts[4]
            or not 0<=now-chat['last_inbound']<86400):raise BriefUnavailable()
    return chat


def make_task_brief_guard(engine):
    from .task_design_brief import TaskDesignBrief
    from . import task_card_command as cards
    from .task_card_reference import _barrier,_protected
    from .answer_citations import _hash,_scope
    from .monthly_task_history import _Reader
    from .weekly_review_command import _outbox,_seal
    validator=TaskDesignBrief(engine=engine,enabled=True)
    store=engine.s;business=engine.b
    source_method=cards.build_task_brief_source
    def guard(row,route=None,transport=None):
        if not row['dedupe'].startswith(TASK_PREFIX):return False
        try:
            reader=_Reader(store);doc=_task_load(reader,business,row['id'])
            request=doc['request'];policy=validator._policy(request);scope,parts=_scope(engine.c,request)
            if policy!=doc['policy'] or cards.build_task_brief_source is not source_method:raise BriefUnavailable()
            _barrier(reader,business,scope,doc['event'])
            accepted=_task_accepted(reader,business,doc['event'],done=True)
            if any(accepted[k]!=request[k] for k in request) or _hash(accepted['text'])!=doc['command']:raise BriefUnavailable()
            number,fields=_task_fields(accepted['text'])
            if fields is None or number!=doc['number'] or _hash([request,number,[fields[k] for k in ('goal','audience','format','materials')]])!=doc['input']:raise BriefUnavailable()
            _protected(reader,business,row['id'],doc['refs'])
            stamps=[]
            def clock():
                value=engine.now()
                if type(value) not in (int,float) or not 0<=value<2**53 or (stamps and value<stamps[-1]):raise BriefUnavailable()
                stamps.append(value)
                if validator._policy(request)!=policy:raise BriefUnavailable()
                return value
            source=source_method(store,engine.c,request['sender'],engine.intelligence.index,clock=clock,number=number)
            if (source.task_id!=doc['task_id'] or source.status not in ('confirmed','in_progress','blocked')
                    or source.snapshot_digest!=doc['source'] or source.evidence_ids!=tuple(doc['refs'])):raise BriefUnavailable()
            current=_outbox(reader,business,row['id'])
            if current['state'] not in ('pending','retry','blocked') or any(current[k]!=row[k] for k in current):raise BriefUnavailable()
            if (current['task_id']!=doc['task_id'] or current['recipient']!=request['sender'] or current['preferred_transport']!=request['channel']
                    or current['parse_mode'] is not None or _seal(current)!=doc['seal']
                    or current['dedupe']!=TASK_PREFIX+_hash([doc['event'],scope])
                    or len(current['text'].encode('utf-16-le'))//2>3500 or len(current['text'].encode())>8000):raise BriefUnavailable()
            if validator._policy(request)!=policy or cards.build_task_brief_source is not source_method:raise BriefUnavailable()
            if _task_load(reader,business,row['id'])!=doc or _task_accepted(reader,business,doc['event'],done=True)!=accepted:raise BriefUnavailable()
            _protected(reader,business,row['id'],doc['refs'])
            if validator._policy(request)!=policy:raise BriefUnavailable()
            _task_chat(reader,business,request,parts,stamps[-1])
            if validator._policy(request)!=policy:raise BriefUnavailable()
            if route is not None:
                if (transport!=request['channel'] or set(route)!=( {'chat_id','business_connection_id'} if request['channel']=='business' else {'chat_id'})
                        or str(route.get('chat_id'))!=parts[3] or (route.get('business_connection_id') or None)!=parts[4]
                        or route.get('message_thread_id') is not None):raise BriefUnavailable()
            return True
        except Exception:
            cancel_task_brief(store,business,row['id']);return False
    return guard
