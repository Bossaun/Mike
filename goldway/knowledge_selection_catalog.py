"""Current private metadata selections; no body/search/refresh/provider/model I/O.

Logical helper caps exclude unchanged generic Dispatcher ACL/route/protection.
Observations are optimistic; metadata callbacks precede the final SQL phase.
"""
from dataclasses import dataclass
from html import escape
import hashlib,json,re

from . import selected_knowledge_command as selected
from .answer_citations import _hash
from .intelligence import reader_key
from .trusted_provider import SourceMetadata,ProviderError
from .monthly_task_history import _Reader,_bounded_columns,_text,_numeric
from .task_card_reference import _barrier,_protected
from .weekly_review_command import _seal

SOURCES='Покажи доступные источники'
DOCUMENTS='Покажи документы источника'
PREFIX='selected-knowledge-preview:catalog:'
NOTICE='Не удалось показать текущие варианты выбора. Проверьте регистрацию и доступ, затем запросите список заново.'
CANCEL='Knowledge catalog changed; request a fresh private list'
TEMPLATE=DOCUMENTS+'\nИсточник: регистрация <код из списка источников>'
MAX_QUERIES=128
MAX_ROWS=512
MAX_BYTES=4*1024*1024


class CatalogUnavailable(ValueError):
    def __init__(self):super().__init__('knowledge_selection_catalog_unavailable')


def _deny():raise CatalogUnavailable()


def is_command(text):return type(text) is str and text.split('\n',1)[0] in (SOURCES,DOCUMENTS)


def _parse(text):
    if (type(text) is not str or not 0<len(text)<=512 or len(text.encode())>2048
        or any(ord(c)<32 and c!='\n' or ord(c)==127 or c in '\u0085\u2028\u2029' for c in text)):_deny()
    if text==SOURCES:return 'sources',None
    if text==DOCUMENTS:return 'template',None
    lines=text.split('\n')
    if len(lines)!=2 or lines[0]!=DOCUMENTS or not lines[1].startswith('Источник: '):_deny()
    label=lines[1][len('Источник: '):]
    if re.fullmatch(r'(?:регистрация [A-Za-z0-9._-]{1,64}|провайдер [A-Za-z0-9._-]{1,64}/[A-Za-z0-9._-]{1,64})',label) is None:_deny()
    return 'documents',label


class Reader(_Reader):
    def __init__(self,store):super().__init__(store);self.db=store.db
    def query(self,sql,params=(),*,maximum=1):
        if self.store.db is not self.db or self.queries>=MAX_QUERIES or type(maximum) is not int or not 1<=maximum<=13:_deny()
        values=super().query(sql,params,maximum=maximum)
        if self.store.db is not self.db or self.rows>MAX_ROWS or self.bytes>MAX_BYTES:_deny()
        return values


@dataclass(frozen=True)
class Catalog:
    body:str
    refs:tuple[str,...]
    digest:str


def _mapping(host,request):
    store,db,c,b,intel,index,sources,trusted,registry,*_=host.saved
    reader=reader_key(b,request['sender']);entries=[]
    if intel.prefix!='source:'+hashlib.sha256(b.encode()).hexdigest()+':':_deny()
    originals=c['intelligence'].get('sources',[])
    if type(originals) is not list or len(originals)>16:_deny()
    for original in originals:
        if type(original) is not dict:_deny()
        name=original.get('id');audience=original.get('readers')
        if type(name) is not str or re.fullmatch('[A-Za-z0-9._-]{1,64}',name) is None or type(audience) is not list:_deny()
        if request['sender'] not in audience:continue
        canonical=intel.prefix+name
        matches=[s for s in sources.specs if s.get('id')==canonical and type(s.get('readers')) is list
                 and reader in s['readers'] and set(s['readers'])<=sources.members]
        if len(matches)!=1:_deny()
        entries.append(('регистрация '+name,canonical,None))
    if trusted is not None:
        for spec in trusted.bindings:
            if reader in spec.audience:entries.append(('провайдер '+spec.provider_id+'/'+spec.source_id,trusted.source_id(spec),spec))
    if len(entries)>16 or len({x[0] for x in entries})!=len(entries) or len({x[1] for x in entries})!=len(entries):_deny()
    return tuple(sorted(entries,key=lambda x:x[0]))


def _prepare(host,request,mode,label,policy,stamps):
    """All trusted external metadata/clock calls precede _snapshot SQL."""
    entries=_mapping(host,request)
    if mode=='documents':
        entries=tuple(e for e in entries if e[0]==label)
        if len(entries)!=1:_deny()
    now=host.sample(request,policy,stamps);result=[]
    registry=host.saved[8];business=host.saved[3]
    for name,source,spec in entries:
        metadata=None
        if spec is not None:
            try:metadata=registry.source_metadata(spec.source_id,reader=reader_key(business,request['sender']),now=now)
            except ProviderError:
                if mode=='documents':_deny()
                result.append((name,source,spec,None,False));continue
            if type(metadata) is not SourceMetadata or metadata.business!=business or metadata.spec!=spec:_deny()
        result.append((name,source,spec,metadata,True))
        if host.policy(request)[2]!=policy:_deny()
    if host.policy(request)[2]!=policy:_deny()
    return now,tuple(result)


def _current_version(reader,business,source,metadata):
    if metadata is None:return
    fields={'business':_text('business',128),'source_id':_text('source_id',256),
        'version':"typeof(version)='integer' AND version BETWEEN 1 AND 9223372036854775807",
        'observed_at':_numeric('observed_at'),'fingerprint':_text('fingerprint',64)}
    rows=reader.query('SELECT '+_bounded_columns(fields)+' FROM trusted_source_versions WHERE business=? AND source_id=?',(business,source))
    expected=dict(business=business,source_id=source,version=metadata.version,observed_at=metadata.observed_at,fingerprint=metadata.fingerprint)
    if rows!=[expected]:_deny()


def _slice(reader,source,actor,now,metadata,maximum,labels):
    """Only explicit source+reader; complete SQL gate before any labels transfer."""
    def text(column,chars,bytecap):return "typeof("+column+")='text' AND length("+column+") BETWEEN 1 AND "+str(chars)+" AND length(CAST("+column+" AS BLOB))<="+str(bytecap)
    numeric=lambda c:_numeric(c)
    anchor="(SELECT c.id FROM knowledge_chunks c WHERE c.document_key=d.id ORDER BY c.position,c.id LIMIT 1)"
    timegood=numeric('d.observed_at')+' AND '+numeric('d.permission_expires_at')
    guards=[text('d.id',64,64),text('d.source_id',256,1024),text('d.document_id',256,1024),text('d.title',300,1200),text('d.url',2048,8192),
        timegood,'d.observed_at<=? AND d.permission_expires_at>? AND d.permission_expires_at>d.observed_at',
        "typeof(d.confirmed)='integer' AND d.confirmed IN (0,1)",
        'o.document_key=d.id AND o.source_id=d.source_id AND '+numeric('o.observed_at')+' AND o.observed_at=d.observed_at',
        '(rv.source_id IS NULL OR ('+numeric('rv.observed_at')+" AND typeof(rv.inclusive)='integer' AND rv.inclusive IN (0,1) AND (d.observed_at>rv.observed_at OR (d.observed_at=rv.observed_at AND rv.inclusive=0))))",
        text(anchor,128,512)]
    parameters=[now,now]
    if metadata is not None:
        guards += ['d.observed_at=? AND d.permission_expires_at=? AND d.confirmed=?']
        parameters += [metadata.observed_at,metadata.expires_at,int(metadata.spec.confirmed)]
    # Invalid times are kept as bad candidates; ordinary expired/future records
    # are not current selections. No full-source count/body or fact query.
    cte='''WITH chosen AS (SELECT d.id FROM knowledge_documents d WHERE d.source_id=?
        AND EXISTS(SELECT 1 FROM knowledge_readers r WHERE r.document_key=d.id AND r.reader=?)
        AND (NOT ('''+timegood+''') OR (d.observed_at<=? AND d.permission_expires_at>?))
        ORDER BY d.document_id,d.id LIMIT '''+str(maximum)+'''), ds AS (
        SELECT d.id,d.source_id,d.document_id,d.title,d.url,d.observed_at,d.permission_expires_at,d.confirmed,
        o.observed_at AS observation_at,rv.observed_at AS revoked_at,rv.inclusive,'''+anchor+''' AS anchor,
        CASE WHEN '''+' AND '.join('('+g+')' for g in guards)+''' THEN 1 ELSE 0 END AS good
        FROM chosen JOIN knowledge_documents d ON d.id=chosen.id
        LEFT JOIN knowledge_document_observations o ON o.document_key=d.id
        LEFT JOIN knowledge_source_revocations rv ON rv.source_id=d.source_id),
        gate AS (SELECT COUNT(*) AS n,CASE WHEN COUNT(*)=0 OR MIN(good)=1 THEN 1 ELSE 0 END AS ok FROM ds) '''
    params=(source,actor,now,now,*parameters)
    gate=reader.query(cte+'SELECT n,ok FROM gate',params)
    if len(gate)!=1 or gate[0]['ok']!=1:_deny()
    names=['id','source_id','observed_at','permission_expires_at','confirmed','observation_at','revoked_at','inclusive','anchor']
    if labels:names+=['document_id','title','url']
    projection=','.join('CASE WHEN gate.ok=1 THEN ds.'+name+' END AS '+name for name in names)
    rows=reader.query(cte+'SELECT '+projection+',CASE WHEN gate.ok=1 THEN 0 ELSE 1 END AS _bad FROM ds CROSS JOIN gate ORDER BY ds.document_id,ds.id',params,maximum=maximum)
    if len(rows)!=gate[0]['n']:_deny()
    for row in rows:
        if type(row['id']) is not str or re.fullmatch('[0-9a-f]{64}',row['id']) is None or type(row['anchor']) is not str or len(row['anchor'])>160:_deny()
        if labels:
            if (row['document_id']!=row['document_id'].strip() or any(ord(c)<32 or ord(c)==127 or c in '\u0085\u2028\u2029' for c in row['document_id'])):_deny()
            canonical=hashlib.sha256(json.dumps([source,row['document_id']],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            if canonical!=row['id']:_deny()
    return rows


def _fits(body):return len(body.encode('utf-16-le'))//2<=3500 and len(body.encode())<=8000


def _render(mode,records):
    if mode=='sources':
        lines=['<b>Доступные варианты источников</b>','Только текущий индекс и ваш доступ; это не проверка полноты или фактов компании.']
        for label,rows in records:
            if rows:lines.append('Источник: '+escape(label))
        if not any(rows for _,rows in records):lines.append('Сейчас нет доступных индексированных вариантов выбора. Это не означает, что источников не существует.')
        lines.append('Для названий документов отправьте:\n'+DOCUMENTS+'\nИсточник: &lt;точная строка из списка&gt;')
        body='\n\n'.join(lines)
    else:
        label,rows=records[0]
        prefix='<b>Документы выбранного источника</b>\nТолько текущая индексированная выборка и ваш доступ; факты независимо не проверены.'
        blocks=[]
        for row in rows[:12]:
            title=' '.join(row['title'].split());shown=title[:80]+('… (название сокращено)' if len(title)>80 else '')
            block=escape(shown)+'\nИсточник: '+escape(label)+'\nДокумент: '+escape(row['document_id'])
            candidate='\n\n'.join([prefix]+blocks+[block,'Показаны первые 12; список неполный, есть ещё варианты.',
                'Скопируйте точную пару «Источник»/«Документ» в запрос «'+selected.HEADER+'».'])
            if not _fits(candidate):break
            blocks.append(block)
        if rows and not blocks:_deny()
        footer=('Показаны первые '+str(len(blocks))+'; список неполный, есть ещё варианты.' if len(blocks)<len(rows) else
                'Показаны все текущие доступные документы этой индексированной выборки.')
        if not rows:footer='Сейчас нет доступных документов в этом источнике. Это не проверка полноты самого источника.'
        body='\n\n'.join([prefix]+blocks+[footer,'Скопируйте точную пару «Источник»/«Документ» в запрос «'+selected.HEADER+'».'])
    if not _fits(body):_deny()
    return body


def _snapshot(host,request,mode,reader,prepared):
    now,entries=prepared;records=[];proof=[];refs=[];business=host.saved[3]
    for label,source,spec,metadata,available in entries:
        if not available:
            records.append((label,[]));proof.append([label,source,'unavailable']);continue
        _current_version(reader,business,source,metadata)
        rows=_slice(reader,source,reader_key(business,request['sender']),now,metadata,13 if mode=='documents' else 1,mode=='documents')
        records.append((label,rows));refs.extend(row['anchor'] for row in rows)
        proof.append([label,source,None if metadata is None else [metadata.business,metadata.version,metadata.observed_at,metadata.expires_at,metadata.fingerprint],rows])
    return Catalog(_render(mode,records),tuple(sorted(set(refs))),_hash(proof))


def _key(business,identifier):return PREFIX+'v1:'+_hash([business,identifier])


def _load(reader,business,identifier):
    rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',16384)})+' FROM runtime WHERE key=?',(_key(business,identifier),))
    if len(rows)!=1:_deny()
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:_deny()
            result[k]=v
        return result
    doc=json.loads(rows[0]['value'],object_pairs_hook=pairs)
    if type(doc) is not dict or set(doc)!={'version','event','request','command','digest','refs','policy','seal'}:_deny()
    if type(doc['version']) is not int or doc['version']!=1 or type(doc['event']) is not str or not 0<len(doc['event'].encode())<=1024:_deny()
    if type(doc['request']) is not dict or set(doc['request'])!={'sender','channel','source_id'} or any(type(v) is not str for v in doc['request'].values()):_deny()
    if type(doc['refs']) is not list or len(doc['refs'])>16 or doc['refs']!=sorted(set(doc['refs'])) or any(type(x) is not str or not 0<len(x)<=160 for x in doc['refs']):_deny()
    for k in ('command','digest','policy','seal'):
        if type(doc[k]) is not str or re.fullmatch('[0-9a-f]{64}',doc[k]) is None:_deny()
    return doc


def cancel(store,business,identifier):
    store.execute("UPDATE outbox SET state='cancelled',error=? WHERE business=? AND id=? AND dedupe LIKE 'selected-knowledge-preview:catalog:%' AND state IN ('pending','retry','blocked')",(CANCEL,business,identifier))


def handle(engine,event,payload):
    host=selected._Host(engine);store,db,c,b,intel,index=host.saved[:6]
    if not db.in_transaction:_deny()
    request={k:payload.get(k) for k in ('sender','channel','source_id')};policy=None
    db.execute('SAVEPOINT knowledge_catalog')
    try:
        scope,parts,policy=host.policy(request);reader=Reader(store);stamps=[]
        if type(event) is not str or not 0<len(event.encode())<=1024:_deny()
        _barrier(reader,b,scope,event);accepted=selected._accepted(reader,b,event)
        if accepted!=dict(request,text=payload['text']):_deny()
        mode,label=_parse(payload['text'])
        if mode=='template':
            store.queue(b,request['sender'],TEMPLATE,'knowledge-catalog-template:'+_hash([event,scope]),host.sample(request,policy,stamps),preferred_transport=request['channel'])
            if host.policy(request)[2]!=policy:_deny()
        else:
            prepared=_prepare(host,request,mode,label,policy,stamps)
            first=_snapshot(host,request,mode,reader,prepared)
            if host.policy(request)[2]!=policy:_deny()
            dedupe=PREFIX+_hash([event,scope])
            if not store.queue(b,request['sender'],first.body,dedupe,prepared[0],parse_mode='HTML',preferred_transport=request['channel']):_deny()
            host.bound()
            identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(b,dedupe)).fetchone()[0]
            intel.protect_outbox(identifier,first.refs)
            if host.policy(request)[2]!=policy:_deny()
            row=selected._row(reader,b,identifier,first.body)
            if row['state']!='pending' or row['task_id'] is not None or row['recipient']!=request['sender'] or row['preferred_transport']!=request['channel'] or row['parse_mode']!='HTML':_deny()
            doc=dict(version=1,event=event,request=request,command=_hash(payload['text']),digest=first.digest,refs=list(first.refs),policy=policy,seal=_seal(dict(row,text=first.body)))
            encoded=json.dumps(doc,ensure_ascii=False,separators=(',',':'))
            if len(encoded.encode())>16384:_deny()
            store.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(_key(b,identifier),encoded))
            if _load(reader,b,identifier)!=doc or selected._row(reader,b,identifier,first.body)!=row or selected._accepted(reader,b,event)!=accepted:_deny()
            _protected(reader,b,identifier,first.refs);_barrier(reader,b,scope,event)
            final_prepared=_prepare(host,request,mode,label,policy,stamps)
            final=_snapshot(host,request,mode,reader,final_prepared)
            if final!=first or host.policy(request)[2]!=policy:_deny()
    except BaseException as error:
        db.execute('ROLLBACK TO knowledge_catalog');db.execute('RELEASE knowledge_catalog')
        if not isinstance(error,(ValueError,TypeError,KeyError)):raise
        try:allowed=policy is not None and host.policy(request)[2]==policy
        except Exception:allowed=False
        if allowed:store.queue(b,request['sender'],NOTICE,'knowledge-catalog-notice:'+_hash(event),host.sample(request,policy,[]),preferred_transport=request['channel'])
    else:db.execute('RELEASE knowledge_catalog')
    db.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(b,event))


def make_delivery_guard(engine):
    host=selected._Host(engine);store,db,c,b,*_=host.saved
    def guard(row,route=None,transport=None):
        if not row['dedupe'].startswith(PREFIX):return False
        try:
            host.bound();reader=Reader(store);doc=_load(reader,b,row['id']);request=doc['request']
            scope,parts,policy=host.policy(request)
            if policy!=doc['policy']:_deny()
            accepted=selected._accepted(reader,b,doc['event'],True)
            if any(accepted[k]!=request[k] for k in request) or _hash(accepted['text'])!=doc['command']:_deny()
            mode,label=_parse(accepted['text'])
            if mode=='template':_deny()
            stamps=[];prepared=_prepare(host,request,mode,label,policy,stamps)
            first=_snapshot(host,request,mode,reader,prepared)
            if first.digest!=doc['digest'] or list(first.refs)!=doc['refs']:_deny()
            selected._chat(reader,b,request,parts,prepared[0])
            if route is not None and (transport!=request['channel'] or set(route)!=({'chat_id','business_connection_id'} if request['channel']=='business' else {'chat_id'})
                or str(route['chat_id'])!=parts[3] or (route.get('business_connection_id') or None)!=parts[4]):_deny()
            _barrier(reader,b,scope,doc['event']);_protected(reader,b,row['id'],doc['refs'])
            current=selected._row(reader,b,row['id'],first.body)
            if (current['state'] not in ('pending','retry','blocked') or any(current[k]!=row[k] for k in current)
                or current['task_id'] is not None or current['recipient']!=request['sender'] or current['preferred_transport']!=request['channel']
                or current['parse_mode']!='HTML' or current['dedupe']!=PREFIX+_hash([doc['event'],scope])):_deny()
            if _seal(dict(current,text=first.body))!=doc['seal'] or _load(reader,b,row['id'])!=doc or selected._accepted(reader,b,doc['event'],True)!=accepted:_deny()
            final_prepared=_prepare(host,request,mode,label,policy,stamps)
            final=_snapshot(host,request,mode,reader,final_prepared)
            if final!=first or final.body!=row['text'] or host.policy(request)[2]!=policy:_deny()
            return True
        except Exception:
            if store.db is db:cancel(store,b,row['id'])
            return False
    return guard
