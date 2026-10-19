"""Bounded own-reference metadata catalogue; no body/URL/search/model reads.

One cumulative Reader per build (two fixed catalogue passes + output receipt),
one Reader per guard; each <=2048queries/8192rows/4MiB. At most64 owner heads,
8 entries on one explicit page (1–8), one3500UTF16 output. Real chunk IDs only.
"""
from datetime import datetime, timezone
from html import escape
import json
import re

from . import personal_reference as personal
from .answer_citations import _hash, _scope
from .mutation_target import _policy
from .monthly_task_history import _Reader, _bounded_columns, _text, _numeric
from .intelligence import reader_key
from .task_card_reference import _barrier, _protected
from .weekly_review_command import _host, _same_host, _outbox, _seal, _accepted

PREFIX='personal-reference-list:'
NOTICE='Не удалось показать личные справки. Проверьте текущий доступ и укажите страницу от 1 до 8.'
CANCEL='Personal reference catalogue authorization changed; request a fresh list'
_REQUEST=re.compile(r'Покажи мои справки(?: страница ([1-8]))?')


def _deny():raise ValueError('personal_reference_catalogue_unavailable')


def is_command(text):return type(text) is str and text.startswith('Покажи мои справки')


def _page(text):
    if type(text) is not str or len(text)>80:_deny()
    match=_REQUEST.fullmatch(text)
    if match is None:_deny()
    return int(match[1] or '1')


def _gate(engine,payload,host):
    _same_host(engine,host)
    scope,parts=personal._gate(engine,payload,host[3])
    if host[1]['admins']!=[payload['sender']]:_deny()
    personal._stamp(engine.now())
    return scope,parts,_hash([_policy(engine),engine.c.get('review_mode')])


def _metadata(engine,payload,head,reader,now):
    """Exact document identifiers/ACL before title; never URL or chunk text."""
    source=head['source'];key=personal._doc_key(source)
    spec={'id':_text('id',160),'source_id':_text('source_id',256),'document_id':_text('document_id',256),
          'confirmed':"typeof(confirmed)='integer' AND confirmed=0",'observed_at':_numeric('observed_at'),
          'permission_expires_at':_numeric('permission_expires_at')}
    docs=reader.query('SELECT '+_bounded_columns(spec)+' FROM knowledge_documents WHERE id=?',(key,))
    if not docs:return None, ['missing-document']
    doc=docs[0]
    if (doc['source_id']!=source or doc['document_id']!='assertion' or doc['observed_at']!=head['observed'] or
        doc['permission_expires_at']!=head['expiry']):_deny()
    grants=reader.query('SELECT '+_bounded_columns({'reader':_text('reader',128)})+
        ' FROM knowledge_readers WHERE document_key=? ORDER BY reader',(key,),maximum=2)
    observations=reader.query('SELECT '+_bounded_columns({'document_key':_text('document_key',160),'observed_at':_numeric('observed_at')})+
        ' FROM knowledge_document_observations WHERE source_id=?',(source,))
    revoked=reader.query('SELECT '+_bounded_columns({'observed_at':_numeric('observed_at'),
        'inclusive':"typeof(inclusive)='integer' AND inclusive IN (0,1)"})+
        ' FROM knowledge_source_revocations WHERE source_id=?',(source,))
    if observations!=[dict(document_key=key,observed_at=head['observed'])]:_deny()
    state=[doc,grants,observations,revoked]
    if (grants!=[dict(reader=reader_key(engine.b,payload['sender']))] or now<head['observed'] or now>=head['expiry'] or
        any(head['observed']<r['observed_at'] or (head['observed']==r['observed_at'] and r['inclusive']) for r in revoked)):
        return None,state
    chunks=reader.query('SELECT '+_bounded_columns({'id':_text('id',160),
        'position':"typeof(position)='integer' AND position BETWEEN 0 AND 3"})+
        ' FROM knowledge_chunks WHERE document_key=? ORDER BY position',(key,),maximum=4)
    if (not chunks or [r['position'] for r in chunks]!=list(range(len(chunks))) or
        any(not re.fullmatch(r'knowledge:[0-9a-f]{32}',r['id']) for r in chunks)):_deny()
    title=reader.query('SELECT '+_bounded_columns({'title':_text('title',480)})+' FROM knowledge_documents WHERE id=?',(key,))
    if len(title)!=1:_deny()
    label=personal._plain(title[0]['title'],120)
    if label!=label.strip() or '\n' in label:_deny()
    entry=dict(memo=head['memo'],revision=head['revision'],title=label,expiry=head['expiry'],refs=[r['id'] for r in chunks])
    return entry,state+[chunks,title]


def _collect(engine,payload,page,reader,host):
    scope,parts,policy=_gate(engine,payload,host)
    lower=personal._prefix(engine,payload['sender'])+'head:'
    keys=reader.query('SELECT '+_bounded_columns({'key':_text('key',512)})+
        ' FROM runtime WHERE key>=? AND key<? ORDER BY key',(lower,lower+'\uffff'),maximum=64)
    entries=[];states=[];now=engine.now()
    for row in keys:
        memo=row['key'][len(lower):]
        if not re.fullmatch(personal._ID,memo) or row['key']!=lower+memo:_deny()
        head=personal._head(engine,payload,reader,memo)
        if head is None:_deny()
        if head['state']=='revoked' or now>=head['expiry'] or now<head['observed']:
            states.append([head,'unavailable']);continue
        entry,state=_metadata(engine,payload,head,reader,now)
        states.append([head,state])
        if entry is not None:entries.append(entry)
    entries.sort(key=lambda r:(r['title'].casefold(),r['memo']))
    pages=max(1,(len(entries)+7)//8)
    if not 1<=page<=pages<=8:_deny()
    selected=entries[(page-1)*8:page*8]
    refs=tuple(sorted({ref for row in selected for ref in row['refs']}))
    if len(refs)>32:_deny()
    if _gate(engine,payload,host)!=(scope,parts,policy):_deny()
    return selected,pages,refs,_hash([scope,policy,states,entries,page,pages])


def _render(selected,page,pages):
    lines=['<b>Ваши непроверенные личные справки</b>','Названия со слов отправителя; факты компании не подтверждены.',f'Страница {page} из {pages}.']
    if not selected:lines.append('Сейчас нет доступных личных справок в этом списке.')
    for row in selected:
        expiry=datetime.fromtimestamp(row['expiry'],timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
        lines.append(escape(row['title'])+'\n'+row['memo']+' · версия '+str(row['revision'])+' · до '+expiry)
    if selected:lines.append('Просмотр: Покажи мою справку '+selected[0]['memo'])
    if page<pages:lines.append('Далее: Покажи мои справки страница '+str(page+1))
    if page>1:lines.append('Назад: Покажи мои справки страница '+str(page-1))
    body='\n\n'.join(lines)
    if personal._units(body)>3500:_deny()
    return body


def _key(business,identifier):return PREFIX+'v1:'+_hash([business,identifier])


def _load(reader,business,identifier):
    rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',16384)})+' FROM runtime WHERE key=?',(_key(business,identifier),))
    if len(rows)!=1:_deny()
    doc=json.loads(rows[0]['value'])
    if type(doc) is not dict or set(doc)!={'version','event','request','scope','page','pages','proof','refs','seal'}:_deny()
    if type(doc['version']) is not int or doc['version']!=1 or type(doc['event']) is not str or not 0<len(doc['event'].encode())<=1024:_deny()
    if type(doc['page']) is not int or type(doc['pages']) is not int or not 1<=doc['page']<=doc['pages']<=8:_deny()
    if type(doc['request']) is not dict or set(doc['request'])!={'sender','channel','source_id'}:_deny()
    if type(doc['refs']) is not list or len(doc['refs'])>32 or any(type(r) is not str or not re.fullmatch(r'knowledge:[0-9a-f]{32}',r) for r in doc['refs']):_deny()
    if doc['refs']!=sorted(set(doc['refs'])):_deny()
    if any(type(doc[k]) is not str or not re.fullmatch('[0-9a-f]{64}',doc[k]) for k in ('scope','proof','seal')):_deny()
    return doc


def cancel(store,business,identifier):
    store.execute("UPDATE outbox SET state='cancelled',error=? WHERE business=? AND id=? AND state IN ('pending','retry','blocked')",(CANCEL,business,identifier))


def handle(engine,event,payload):
    host=_host(engine);store=host[0]
    if not store.db.in_transaction:_deny()
    store.execute('SAVEPOINT personal_reference_catalogue')
    try:
        scope,parts,policy=_gate(engine,payload,host)
        page=_page(payload['text']);reader=_Reader(store)
        if type(event) is not str or not 0<len(event.encode())<=1024:_deny()
        _barrier(reader,engine.b,scope,event)
        initial=_collect(engine,payload,page,reader,host)
        selected,pages,refs,proof=initial;body=_render(selected,page,pages)
        request={k:payload[k] for k in ('sender','channel','source_id')}
        if len(json.dumps(request).encode())>2048:_deny()
        dedupe=PREFIX+_hash([event,scope])
        if not store.queue(engine.b,payload['sender'],body,dedupe,engine.now(),parse_mode='HTML',preferred_transport=payload['channel']):_deny()
        _same_host(engine,host)
        identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,dedupe)).fetchone()[0]
        host[3].protect_outbox(identifier,refs);_same_host(engine,host)
        row=_outbox(reader,engine.b,identifier)
        if row['text']!=body or row['state']!='pending' or row['recipient']!=payload['sender'] or row['preferred_transport']!=payload['channel'] or row['parse_mode']!='HTML' or row['task_id'] is not None:_deny()
        _protected(reader,engine.b,identifier,refs)
        doc=dict(version=1,event=event,request=request,scope=_hash(scope),page=page,pages=pages,proof=proof,refs=list(refs),seal=_seal(row))
        value=json.dumps(doc,ensure_ascii=False,separators=(',',':'))
        if len(value.encode())>16384:_deny()
        store.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(_key(engine.b,identifier),value))
        if _collect(engine,payload,page,reader,host)!=initial:_deny()
        fresh=_outbox(reader,engine.b,identifier)
        if fresh!=row or _load(reader,engine.b,identifier)!=doc:_deny()
        _protected(reader,engine.b,identifier,refs);_same_host(engine,host)
    except Exception:
        store.execute('ROLLBACK TO personal_reference_catalogue');store.execute('RELEASE personal_reference_catalogue')
        try:_gate(engine,payload,host)
        except Exception:pass
        else:store.queue(host[2],payload['sender'],NOTICE,'personal-reference-list-notice:'+_hash(event),engine.now(),preferred_transport=payload['channel'])
    else:store.execute('RELEASE personal_reference_catalogue')
    store.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(host[2],event))


def make_delivery_guard(engine):
    host=_host(engine)
    def guard(row,route=None,transport=None):
        if not row['dedupe'].startswith(PREFIX):return False
        try:
            _same_host(engine,host);reader=_Reader(host[0])
            current=_outbox(reader,host[2],row['id']);doc=_load(reader,host[2],row['id'])
            if current['state'] not in ('pending','retry','blocked') or any(current[k]!=row[k] for k in current):_deny()
            if current['parse_mode']!='HTML' or current['task_id'] is not None or doc['seal']!=_seal(current) or personal._units(current['text'])>3500:_deny()
            payload=doc['request'];scope,parts,policy=_gate(engine,payload,host)
            if (doc['scope']!=_hash(scope) or current['recipient']!=payload['sender'] or current['preferred_transport']!=payload['channel'] or
                current['dedupe']!=PREFIX+_hash([doc['event'],scope])):_deny()
            accepted=_accepted(reader,host[2],doc['event'])
            if any(accepted[k]!=payload[k] for k in payload) or _page(accepted['text'])!=doc['page']:_deny()
            selected,pages,refs,proof=_collect(engine,payload,doc['page'],reader,host)
            if pages!=doc['pages'] or refs!=tuple(doc['refs']) or proof!=doc['proof'] or _render(selected,doc['page'],pages)!=current['text']:_deny()
            _protected(reader,host[2],row['id'],refs);_same_host(engine,host)
            if route is not None and (transport!=parts[2] or str(route.get('chat_id'))!=parts[3] or
                (route.get('business_connection_id') or None)!=parts[4] or (route.get('message_thread_id') or None)!=parts[5]):_deny()
            return True
        except Exception:
            cancel(host[0],host[2],row['id']);return False
    return guard
