"""Prospective exact citations for delivered private answers; no new retrieval.

Citation explanations do not replace the substantive-answer anchor. Every other
fresh private TEAM turn establishes a barrier, including unsupported commands.
No transcript/source/answer text is copied into the receipt store.
"""
from datetime import datetime, timezone
import hashlib
import json
import math

from .dialogue import scope_from_payload, _scope_parts
from .dialogue_focus import build_followup
from .intake import validate_team
from .monthly_task_history import _Reader, _bounded_columns, _text, _numeric, _integer, _current, MAX_BYTES, MAX_QUERIES, MAX_ROWS
from .work_context import WORK_PREFIX, _readable, work_id

_UNKNOWN='Сохранённые основания последнего содержательного ответа сейчас недоступны или не подтверждены. Новый поиск не использован для подмены его источников.'
_NONE='У последнего содержательного ответа нет сохранённых корпоративных ссылок. Это не подтверждение фактов компании.'


def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def _fail():raise ValueError('answer_citation_unavailable')

def _scope(config,payload):
    validate_team(config)
    if payload.get('channel') not in ('bot','business') or payload.get('sender') not in config['employees']:_fail()
    scope=scope_from_payload(payload,config['business'])
    if scope is None:_fail()
    return scope,_scope_parts(scope)


def is_source_followup(text):
    hint=build_followup(text,{'turns':[{'role':'assistant','content':'Источник ответа'}]})
    return bool(hint and hint['kind']=='cite_last_answer')


def begin_answer(store,config,event_id,payload,now):
    try:scope,_=_scope(config,payload)
    except (ValueError,TypeError,KeyError):return
    store.execute('INSERT OR IGNORE INTO answer_citation_turns(business,scope_key,event_key,at) VALUES(?,?,?,?)',
                  (config['business'],_hash(scope),_hash(event_id),now))


def observe_inbound(engine,event_id,sender,channel,source,text):
    """Trusted update path, after its existing update dedupe/identity gate."""
    if (engine.c.get('business')==engine.b and engine._closed_team()
            and not is_source_followup(text)):
        begin_answer(engine.s,engine.c,event_id,dict(sender=sender,channel=channel,source_id=source),engine.now())


def _doc(reader,ref,business,sender,now,*,confirmed=False):
    fields={'c.id':_text('c.id',160),'c.document_key':_text('c.document_key',160),
            'c.text':_text('c.text',8000),'d.source_id':_text('d.source_id',256),
            'd.title':_text('d.title',1200),'d.url':_text('d.url',2048),
            'd.updated_at':_text('d.updated_at',64,True),
            'd.confirmed':"typeof(d.confirmed)='integer' AND d.confirmed IN (0,1)",
            'd.observed_at':_numeric('d.observed_at'),'d.permission_expires_at':_numeric('d.permission_expires_at')}
    columns=_bounded_columns(fields).replace(' AS c.',' AS ').replace(' AS d.',' AS ')
    rows=reader.query('SELECT '+columns+'''
        FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_key
        JOIN knowledge_readers r ON r.document_key=d.id
        WHERE c.id=? AND r.reader=? AND d.observed_at<=? AND d.permission_expires_at>?''',
        (ref,'reader:'+hashlib.sha256(json.dumps([business,sender]).encode()).hexdigest(),now,now))
    if not rows:return None
    row=rows[0]
    if confirmed and row['confirmed']!=1:return None
    fingerprint=_hash({k:v for k,v in row.items() if k not in ('observed_at','permission_expires_at')})
    return fingerprint,row


def _permissions(reader,store,config,sender,refs,now):
    if type(refs) is not list or len(refs)>16 or any(type(v) is not str or not 0<len(v)<=160 for v in refs):_fail()
    business=config['business'];workrefs={v for v in refs if v.startswith(WORK_PREFIX)}
    records,acl={},{}
    if workrefs:
        ids=reader.query('SELECT '+_bounded_columns({'id':_text('id',160)})+' FROM tasks WHERE business=?',
                         (business,),maximum=128)
        records,acl=_current(reader,business,tuple(row['id'] for row in ids))
    docrefs={v for v in refs if not v.startswith(WORK_PREFIX)}
    # Dependencies are current grants only, never permissions from the receipt.
    pending=list(workrefs);seen=set()
    while pending:
        ref=pending.pop()
        if ref in seen:continue
        seen.add(ref)
        if len(seen)>128:_fail()
        for dependency in acl.get(ref) or ():
            if dependency.startswith(WORK_PREFIX):pending.append(dependency)
            else:docrefs.add(dependency)
    if len(docrefs)>256:_fail()
    docs={ref:_doc(reader,ref,business,sender,now) for ref in sorted(docrefs)}
    grants={ref for ref,value in docs.items() if value is not None}
    role='admin' if sender in config['admins'] else 'employee'
    allowed=grants|_readable(records,acl,role,sender,grants)
    if not set(refs)<=allowed:_fail()
    return records,acl,docs


def _cited(reader,store,config,sender,ref,now,records,acl,docs):
    if not ref.startswith(WORK_PREFIX):
        value=docs.get(ref)
        if value is None or value[1]['confirmed']!=1:_fail()
        return 'document',value[0],value[1]
    current=records.get(ref)
    if current is None:_fail()
    fields={name:_text(name,32000 if name=='evidence' else 2048,True)
            for name in ('title','owner','deadline','next_step','criterion','status','source_id','evidence')}
    fields.update(public_number=_integer('public_number'),revision=_integer('revision'))
    rows=reader.query('SELECT '+_bounded_columns(fields)+' FROM tasks WHERE business=? AND id=?',
                      (config['business'],current['id']))
    if not rows:_fail()
    row=rows[0]
    # Exact task state, dependency metadata and ACL are part of the receipt hash.
    pending=[ref];states={}
    while pending:
        node=pending.pop()
        if node in states:continue
        if node not in records or len(states)>=128:_fail()
        states[node]=[records[node],sorted(acl.get(node) or ())]
        pending.extend(dep for dep in acl.get(node) or () if dep.startswith(WORK_PREFIX))
    result_fingerprint=None
    if row['status']=='completed':
        from .task_result_context import registered_result, MAX_LINKED_SOURCES, MAX_SOURCE_BYTES, MAX_RESULT_BYTES
        sizes=reader.query('''SELECT CASE WHEN typeof(s.body)='text'
            AND length(CAST(s.body AS BLOB))<=?
            AND typeof(ts.source_id)='text' AND length(ts.source_id) BETWEEN 1 AND 256
            AND length(CAST(ts.source_id AS BLOB))<=1024
            AND typeof(s.deleted)='integer' AND s.deleted IN (0,1)
            THEN length(CAST(s.body AS BLOB))+length(CAST(ts.source_id AS BLOB))+16 END AS n
            FROM task_sources ts LEFT JOIN sources s ON s.business=ts.business AND s.id=ts.source_id
            WHERE ts.business=? AND ts.task_id=?''',
            (MAX_SOURCE_BYTES,config['business'],current['id']),maximum=MAX_LINKED_SOURCES)
        if not sizes or any(type(value['n']) is not int for value in sizes):_fail()
        # Charge the actual bounded source/evidence transfer to this same reader.
        # Conservative allowance also covers helper-side result/JSON metadata.
        reader.bytes += sum(value['n'] for value in sizes)+MAX_RESULT_BYTES+MAX_LINKED_SOURCES*32
        reader.queries += 2
        reader.rows += len(sizes)+1
        if reader.bytes>MAX_BYTES or reader.queries>MAX_QUERIES or reader.rows>MAX_ROWS:_fail()
        registered=registered_result(store,config['business'],current['id'],row['revision'])
        if registered is None:_fail()
        result_fingerprint=registered[1]
    return 'task',_hash([row,states,result_fingerprint]),row


def seal_answer_context(store,config,payload,context,now):
    """Seal supplied versions before the model; never send hashes in its prompt.

    Only an already staged future event can receive seals. Failed/legacy source
    projection gets no receipt, without affecting normal response processing.
    """
    try:
        scope,_=_scope(config,payload)
        key=(config['business'],_hash(scope),_hash(payload['link']))
        staged=store.execute("SELECT 1 FROM answer_citation_turns WHERE business=? AND scope_key=? AND event_key=? AND phase='pending'",key).fetchone()
        if not staged:return
        from .intelligence import Intelligence
        evidence=Intelligence.evidence(context['knowledge'],context.get('work_context'))
        refs=list(evidence)
        reader=_Reader(store);records,acl,docs=_permissions(reader,store,config,payload['sender'],refs,now)
        seals={}
        for ref in refs:
            kind,digest,current=_cited(reader,store,config,payload['sender'],ref,now,records,acl,docs)
            expected=evidence[ref]
            if kind=='document':
                if any(expected.get(k)!=current[k] for k in ('title','url','updated_at','observed_at','source_id')):_fail()
                if 'text' in expected and not current['text'].startswith(expected['text']):_fail()
            seals[ref]=dict(kind=kind,digest=digest,observed_at=expected.get('observed_at'))
        encoded=json.dumps(seals)
        if len(encoded)>8192:_fail()
        store.execute('UPDATE answer_citation_turns SET seals=? WHERE business=? AND scope_key=? AND event_key=?',(encoded,*key))
    except Exception:
        # Pending barrier remains, but absence of a seal cannot become provenance.
        return


def capture_answer(store,config,event_id,payload,result,protection,own,now):
    """Called only after existing validation/queue, inside its write transaction.

    An unavailable receipt cannot cancel an otherwise valid business operation.
    Protection semantics remain in Intelligence; this stores the distinct used set.
    """
    try:scope,_=_scope(config,payload)
    except (ValueError,TypeError,KeyError):return
    key=(config['business'],_hash(scope),_hash(event_id))
    try:
        refs=result.get('evidence_ids')
        if (result.get('actions') or len(own)!=1 or own[0]['text']!=result.get('reply_text')
                or type(refs) is not list or len(refs)>12 or len(set(refs))!=len(refs)
                or not set(refs)<=set(protection)):_fail()
        sealed=store.execute("SELECT CASE WHEN length(CAST(seals AS BLOB))<=8192 THEN seals END FROM answer_citation_turns WHERE business=? AND scope_key=? AND event_key=?",key).fetchone()
        seals=json.loads(sealed[0]) if sealed and sealed[0] is not None else None
        if type(seals) is not dict:_fail()
        reader=_Reader(store);records,acl,docs=_permissions(reader,store,config,payload['sender'],list(protection),now)
        citations=[]
        for ref in refs:
            kind,digest,_=_cited(reader,store,config,payload['sender'],ref,now,records,acl,docs)
            if ref not in seals or kind!=seals[ref]['kind'] or digest!=seals[ref]['digest']:_fail()
            citations.append(dict(ref=ref,kind=kind,digest=digest,observed_at=seals[ref]['observed_at']))
        outbox=[dict(id=own[0]['id'],digest=_hash(own[0]['text']))]
        store.execute('''UPDATE answer_citation_turns SET phase='recorded',outbox_ids=?,protection_refs=?,citations=?
            WHERE business=? AND scope_key=? AND event_key=?''',
            (json.dumps(outbox),json.dumps(list(protection)),json.dumps(citations),*key))
    except Exception:
        store.execute("UPDATE answer_citation_turns SET phase='unavailable',outbox_ids=NULL,protection_refs=NULL,citations=NULL WHERE business=? AND scope_key=? AND event_key=?",key)


def _resolve(store,config,payload,now,*,reader=None):
    scope,parts=_scope(config,payload)
    reader=reader if reader is not None else _Reader(store)
    row=reader.query('SELECT '+_bounded_columns({name:_text(name,8192,True) for name in ('outbox_ids','protection_refs','citations','phase')})+
        ',id FROM answer_citation_turns WHERE business=? AND scope_key=? AND id=(SELECT id FROM answer_citation_turns WHERE business=? AND scope_key=? ORDER BY id DESC LIMIT 1)',
        (config['business'],_hash(scope),config['business'],_hash(scope)))
    if not row or row[0]['phase']!='recorded':_fail()
    row=row[0];outbox=json.loads(row['outbox_ids']);refs=json.loads(row['protection_refs']);citations=json.loads(row['citations'])
    if type(outbox) is not list or len(outbox)!=1 or type(citations) is not list or len(citations)>12:_fail()
    receipt=outbox[0]
    if set(receipt)!={'id','digest'} or type(receipt['id']) is not int:_fail()
    rows=reader.query('SELECT '+_bounded_columns({'text':_text('text',16000),'state':_text('state',16),
        'transport':_text('transport',16,True),'destination_chat':_text('destination_chat',128,True),
        'destination_connection':_text('destination_connection',512,True),'recipient':_text('recipient',128),
        'business':_text('business',128),'destination_thread':_integer('destination_thread',True)})+' FROM outbox WHERE id=?',(receipt['id'],))
    if not rows:_fail()
    answer=rows[0]
    if (answer['business']!=parts[0] or answer['recipient']!=parts[1] or answer['state']!='sent'
            or answer['transport']!=parts[2] or answer['destination_chat']!=parts[3]
            or (answer['destination_connection'] or None)!=parts[4] or (answer['destination_thread'] or None)!=parts[5]
            or _hash(answer['text'])!=receipt['digest']):_fail()
    records,acl,docs=_permissions(reader,store,config,payload['sender'],refs,now)
    labels=[];used=[];metadata=[]
    for item in citations:
        if (type(item) is not dict or set(item)!={'ref','kind','digest','observed_at'}
                or item['ref'] in used or item['ref'] not in refs):_fail()
        kind,digest,current=_cited(reader,store,config,payload['sender'],item['ref'],now,records,acl,docs)
        if kind!=item['kind'] or digest!=item['digest']:_fail()
        if (type(item['observed_at']) not in (int,float) or not math.isfinite(item['observed_at'])
                or not 0<=item['observed_at']<=now):_fail()
        observed=datetime.fromtimestamp(item['observed_at'],timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        if kind=='task':label=f"Реестр задач Mike: №{current['public_number']} (состояние на {observed})"
        else:label=f"{current['title']} — {current['url']} (обновление: {current['updated_at'] or 'дата не указана'}; наблюдалось: {observed})"
        labels.append(label);used.append(item['ref']);metadata.append([kind,digest])
    text=('Основания последнего содержательного ответа в этой переписке:\n'+'\n'.join(labels)+
          '\nЭто сохранённые ссылки того ответа, а не новый поиск или независимая проверка фактов.') if labels else _NONE
    if len(text.encode('utf-16-le'))//2>3500:_fail()
    return text,tuple(refs),_hash([row,answer,metadata,config['admins'],config['employees']])


def handle_source_followup(engine,event_id,payload):
    if not is_source_followup(payload.get('text')):_fail()
    try:
        if engine.c.get('business')!=engine.b:_fail()
        _scope(engine.c,payload)
    except (ValueError,TypeError,KeyError):
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id));return
    text,refs=_UNKNOWN,()
    try:
        if (engine.intelligence is None or engine.intelligence.b!=engine.b
                or engine.intelligence.c is not engine.c or engine.intelligence.s is not engine.s):_fail()
        reader=_Reader(engine.s)
        initial=_resolve(engine.s,engine.c,payload,engine.now(),reader=reader)
        final=_resolve(engine.s,engine.c,payload,engine.now(),reader=reader)
        if initial!=final:_fail()
        text,refs,_=final
    except Exception:pass
    key='answer-citations:'+event_id
    engine.s.queue(engine.b,payload['sender'],text,key,engine.now(),preferred_transport=payload['channel'])
    if refs and engine.intelligence:
        row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,key)).fetchone()
        engine.intelligence.protect_outbox(row['id'],refs)
    elif refs:_fail()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event_id))
