"""Explicit private evidence preview; no model, source discovery or new grants.

I98 lookup budgets are separate from bounded point metadata reads. Legacy generic
Dispatcher ACL/route/protect work is outside these helper budgets. Checks are
optimistic observations, not arbitrary-callback isolation or a security sandbox.
"""
from html import escape
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import hashlib,json,math,re

from .engine import Engine
from .store import Store
from .intelligence import Intelligence,intelligence_enabled,reader_key
from .knowledge import KnowledgeIndex,SelectedDocument,SelectedKnowledgeSnapshot
from .knowledge_sources import ReadOnlySources
from .trusted_source_bridge import TrustedSourceBridge
from .trusted_provider import TrustedProviders,SourceSpec,SourceMetadata
from .answer_citations import _hash,_scope
from .task_card_reference import _barrier,_protected
from .monthly_task_history import _Reader,_bounded_columns,_text,_integer,_numeric
from .weekly_review_command import _seal

HEADER='Покажи основания по выбранным документам'
PREFIX='selected-knowledge-preview:'
NOTICE='Не удалось показать основания. Проверьте коды регистрации и документа, запрос и текущий доступ.'
TEMPLATE=HEADER+'\nИсточник: регистрация <код регистрации>\nДокумент: <код документа>\nЗапрос: <вопрос до 256 символов>'
CANCEL='Selected knowledge changed; request a fresh private preview'


class PreviewUnavailable(ValueError):
    def __init__(self):super().__init__('selected_knowledge_preview_unavailable')


def _deny():raise PreviewUnavailable()


def _callable(value):return getattr(value,'__self__',None),getattr(value,'__func__',value)


def is_command(text):return type(text) is str and text.split('\n',1)[0]==HEADER


def _parse(text):
    if (type(text) is not str or not 0<len(text)<=4000 or len(text.encode())>8000
            or any(ord(c)<32 and c!='\n' or ord(c)==127 or c in '\u0085\u2028\u2029' for c in text)):_deny()
    if text==HEADER:return None
    lines=text.splitlines()
    if len(lines) not in (4,6,8) or lines[0]!=HEADER or not lines[-1].startswith('Запрос: '):_deny()
    query=lines[-1][len('Запрос: '):].strip()
    if not query or len(query)>256 or len(query.encode())>1024:_deny()
    selectors=[]
    for pos in range(1,len(lines)-1,2):
        if not lines[pos].startswith('Источник: ') or not lines[pos+1].startswith('Документ: '):_deny()
        source=lines[pos][len('Источник: '):];document=lines[pos+1][len('Документ: '):]
        if not re.fullmatch(r'(?:регистрация [A-Za-z0-9._-]{1,64}|провайдер [A-Za-z0-9._-]{1,64}/[A-Za-z0-9._-]{1,64})',source):_deny()
        if not document or document!=document.strip() or len(document)>256 or len(document.encode())>1024:_deny()
        selectors.append((source,document))
    if len(set(selectors))!=len(selectors):_deny()
    return tuple(selectors),query


class _Host:
    def __init__(self,engine):
        self.e=engine;s=getattr(engine,'s',None);i=getattr(engine,'intelligence',None);idx=getattr(i,'index',None)
        sources=getattr(i,'sources',None);trusted=getattr(sources,'trusted_bridge',None)
        self.saved=(s,getattr(s,'db',None),getattr(engine,'c',None),getattr(engine,'b',None),i,idx,sources,trusted,
            getattr(trusted,'registry',None),_callable(getattr(engine,'now',None)),_callable(getattr(idx,'search_selected',None)),
            _callable(getattr(trusted,'source_id',None)),getattr(engine,'tz',None),
            _callable(getattr(getattr(trusted,'registry',None),'source_metadata',None)))

    def bound(self):
        s,db,c,b,i,idx,sources,trusted,registry,clock,search,source_method,tz,metadata_method=self.saved;e=self.e
        if (type(e) is not Engine or type(s) is not Store or e.s is not s or s.db is not db or e.c is not c or type(c) is not dict
                or e.b!=b or type(i) is not Intelligence or e.intelligence is not i or i.s is not s or i.c is not c or i.b!=b
                or i.index is not idx or type(idx) is not KnowledgeIndex or idx.store is not s or i.sources is not sources
                or type(sources) is not ReadOnlySources or sources.index is not idx or sources.trusted_bridge is not trusted
                or e.tz is not tz or type(tz) is not ZoneInfo or tz.key!=c.get('timezone')):_deny()
        for saved,current in ((clock,_callable(e.now)),(search,_callable(idx.search_selected))):
            if any(a is not b for a,b in zip(saved,current)):_deny()

    def policy(self,request):
        self.bound()
        s,db,c,b,i,idx,sources,trusted,registry,clock,search,source_method,tz,metadata_method=self.saved
        if type(request) is not dict or set(request)!={'sender','channel','source_id'} or any(type(v) is not str for v in request.values()):_deny()
        if c.get('business')!=b or c.get('admins')!=[request['sender']] or not intelligence_enabled(c):_deny()
        scope,parts=_scope(c,request)
        if parts[3]!=str(c['employees'][request['sender']]['telegram_user_id']):_deny()
        reader=reader_key(b,request['sender'])
        if (type(sources.members) is not set or len(sources.members)>50 or reader not in sources.members
                or any(type(x) is not str or len(x)>128 for x in sources.members)
                or type(sources.specs) is not list or len(sources.specs)>16 or any(type(x) is not dict for x in sources.specs)):_deny()
        trusted_state=None
        if trusted is not None:
            if (type(trusted) is not TrustedSourceBridge or trusted.index is not idx or trusted.business!=b
                    or trusted.registry is not registry or type(registry) is not TrustedProviders or registry.business!=b
                    or trusted.members!=sources.members or type(trusted.bindings) is not tuple or len(trusted.bindings)>16
                    or any(a is not b for a,b in zip(source_method,_callable(trusted.source_id)))
                    or source_method[1] is not TrustedSourceBridge.source_id
                    or any(a is not b for a,b in zip(metadata_method,_callable(registry.source_metadata)))
                    or metadata_method[0] is not registry or metadata_method[1] is not TrustedProviders.source_metadata):_deny()
            specs=[]
            for spec in trusted.bindings:
                if type(spec) is not SourceSpec or type(spec.audience) is not frozenset or not spec.audience<=sources.members:_deny()
                specs.append([spec.provider_id,spec.source_id,sorted(spec.audience),spec.minimum_version,spec.ttl_seconds,spec.confirmed])
            trusted_state=[trusted.business,registry.business,sorted(trusted.members),specs]
        keys=('business','admins','employees','timezone','team_intake','team_roster_verified','business_account_user_id',
            'allowed_live_recipients','live_send','recipients_verified','review_mode','sheets_write','daily_summary',
            'allow_onboarding_replies','allow_registered_replies','allowed_group_ids','group_destinations','meeting_recipient',
            'bot_user_id_verified','bot_user_id','intelligence','business_bot_fallback')
        state=[scope,{key:c.get(key) for key in keys},sources.specs,sorted(sources.members),trusted_state]
        encoded=json.dumps(state,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
        if len(encoded)>65536:_deny()
        return scope,parts,hashlib.sha256(encoded).hexdigest()

    def documents(self,request,selectors):
        s,db,c,b,i,idx,sources,trusted,*_=self.saved;result=[];reader=reader_key(b,request['sender'])
        for label,document in selectors:
            if label.startswith('регистрация '):
                name=label[len('регистрация '):]
                originals=[spec for spec in c['intelligence'].get('sources',[]) if type(spec) is dict and spec.get('id')==name and request['sender'] in spec.get('readers',[])]
                if len(originals)!=1 or i.prefix!='source:'+hashlib.sha256(b.encode()).hexdigest()+':':_deny()
                source=i.prefix+name
                matches=[spec for spec in sources.specs if spec.get('id')==source and type(spec.get('readers')) is list and reader in spec['readers'] and set(spec['readers'])<=sources.members]
                if len(matches)!=1:_deny()
            else:
                provider,name=label[len('провайдер '):].split('/')
                if trusted is None:_deny()
                matches=[spec for spec in trusted.bindings if spec.provider_id==provider and spec.source_id==name and reader in spec.audience]
                if len(matches)!=1:_deny()
                source=trusted.source_id(matches[0])
            # Exact published index identity formula, not a permission grant.
            key=hashlib.sha256(json.dumps([source,document],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            result.append(SelectedDocument(source,document,key))
        if len({x.document_key for x in result})!=len(result):_deny()
        return tuple(result)

    def source_proofs(self,request,selectors,documents,reader,now):
        """Current public registry metadata must match the indexed observation."""
        store,db,c,b,i,index,sources,trusted,registry,*_=self.saved;proof=[]
        for (label,_),document in zip(selectors,documents):
            if not label.startswith('провайдер '):continue
            provider,name=label[len('провайдер '):].split('/')
            binding=next(spec for spec in trusted.bindings if spec.provider_id==provider and spec.source_id==name)
            metadata=registry.source_metadata(name,reader=reader_key(b,request['sender']),now=now)
            self.policy(request)
            if type(metadata) is not SourceMetadata or metadata.business!=b or metadata.spec!=binding:_deny()
            spec={'business':_text('business',128),'source_id':_text('source_id',256),
                'version':"typeof(version)='integer' AND version BETWEEN 1 AND 9223372036854775807",
                'observed_at':_numeric('observed_at'),'fingerprint':_text('fingerprint',64)}
            rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM trusted_source_versions WHERE business=? AND source_id=?',(b,document.source_id))
            if (len(rows)!=1 or rows[0]!=dict(business=b,source_id=document.source_id,version=metadata.version,
                    observed_at=metadata.observed_at,fingerprint=metadata.fingerprint)):_deny()
            spec={'source_id':_text('source_id',256),'document_id':_text('document_id',1024),
                'observed_at':_numeric('observed_at'),'permission_expires_at':_numeric('permission_expires_at'),
                'confirmed':"typeof(confirmed)='integer' AND confirmed IN (0,1)"}
            rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM knowledge_documents WHERE id=?',(document.document_key,))
            if (len(rows)!=1 or rows[0]!=dict(source_id=document.source_id,document_id=document.document_id,
                    observed_at=metadata.observed_at,permission_expires_at=metadata.expires_at,confirmed=int(binding.confirmed))):_deny()
            proof.append([document.source_id,metadata.version,metadata.observed_at,metadata.expires_at,metadata.fingerprint])
        return _hash(proof)

    def sample(self,request,policy,stamps):
        if self.policy(request)[2]!=policy:_deny()
        now=self.e.now()
        if type(now) not in (int,float) or not math.isfinite(now) or not 0<=now<2**53 or stamps and now<stamps[-1]:_deny()
        stamps.append(now)
        if self.policy(request)[2]!=policy:_deny()
        return now


def _literal(value):
    if type(value) is not str:_deny()
    value.encode();return escape(value,quote=True)


def _render(packet):
    if type(packet) is not SelectedKnowledgeSnapshot or packet.scope!='selected_documents_only':_deny()
    result=json.loads(packet.result_json)
    if type(result) is not dict or set(result)!={'evidence','facts','conflicts','status'}:_deny()
    lines=['<b>Основания в выбранных документах</b>','Выдержки из индекса; факты независимо не проверены. Область — только выбранные документы.']
    if result['status']=='no_evidence':
        if any(result[k] for k in ('evidence','facts','conflicts')):_deny()
        lines.append('По этому запросу в выбранных документах нет подходящих выдержек. Это не означает, что сведений нет в компании или самих источниках.')
    elif result['status']=='ok':
        ordinals={(doc.source_id,doc.document_id):n for n,doc in enumerate(packet.documents,1)};shown=set()
        def source(item):
            key=(item['source_id'],item['document_id'])
            if key not in ordinals:_deny()
            label='Источник '+str(ordinals[key])
            if key not in shown:
                observed=item['observed_at']
                if type(observed) not in (int,float) or not math.isfinite(observed):_deny()
                try:stamp=datetime.fromtimestamp(observed,timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
                except (ValueError,OverflowError,OSError):_deny()
                lines.append('<b>'+label+': '+_literal(item['title'])+'</b>\nСсылка как записана: '+_literal(item['url'])+
                    '\nНаблюдалось: '+stamp+'; обновление: '+(_literal(item['updated_at']) if item['updated_at'] is not None else 'не указано'))
                shown.add(key)
            return label
        for item in result['evidence']:
            label=source(item)
            if type(item['confirmed']) is not bool or item['status']!=('confirmed' if item['confirmed'] else 'assumption'):_deny()
            lines.append(label+' — '+('подтверждено в индексе' if item['confirmed'] else 'предположение, не подтверждено')+
                '\nВыдержка'+(' (сокращена индексом)' if item.get('truncated') is True else '')+': '+_literal(item['text']))
        for item in result['facts']:
            label=source(item)
            lines.append(label+'; заявленный факт: '+_literal(item['subject'])+' / '+_literal(item['predicate'])+': '+_literal(item['value'])+'\nЦитата: '+_literal(item['quote']))
        for conflict in result['conflicts']:
            resolution={'unresolved':'противоречие не разрешено','priority':'выбор по записанному приоритету','updated_at':'выбор по дате обновления'}.get(conflict['resolution'])
            if resolution is None:_deny()
            values=[]
            for item in conflict['values']:values.append(source(item)+': '+_literal(item['value']))
            lines.append('Противоречие: '+_literal(conflict['subject'])+' / '+_literal(conflict['predicate'])+'\n'+'; '.join(values)+'\nПравило индекса: '+resolution+
                ('; выбрано '+_literal(conflict['selected_value']) if conflict['selected_value'] is not None else ''))
    else:_deny()
    body='\n\n'.join(lines)
    if len(body.encode('utf-16-le'))//2>3500 or len(body.encode())>8000:_deny()
    return body


def _accepted(reader,business,event,done=False):
    spec={'sender':_text('sender',128),'channel':_text('channel',16),'source_id':_text('source_id',1024),'text':_text('text',8000)}
    fields=','.join("json_extract(checked,'$."+key+"') AS "+key for key in spec)
    rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM (SELECT '+fields+" FROM (SELECT CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=16384 AND json_valid(payload) THEN payload END checked FROM events WHERE business=? AND id=? AND kind='reply' AND state "+("='done'" if done else "IN ('pending','done')")+'))',(business,event))
    if len(rows)!=1:_deny()
    return rows[0]


def _row(reader,business,identifier,expected_body):
    """Project metadata and one exact-match bit, never saved outbox text.

    expected_body is the already-authorized build result or Dispatcher's existing
    fetched body. The guard compares it to the current I98 render only after its
    final source/permission phase; this query does not transfer another copy.
    """
    if type(expected_body) is not str or len(expected_body.encode())>8000:_deny()
    spec={'id':_integer('id'),'business':_text('business',128),'recipient':_text('recipient',128),
        'dedupe':_text('dedupe',512),'state':_text('state',16),'task_id':_text('task_id',160,True),
        'parse_mode':_text('parse_mode',16,True),'preferred_transport':_text('preferred_transport',16,True)}
    rows=reader.query('SELECT '+_bounded_columns(spec)+',CASE WHEN '+_text('text',8000)+
        ' AND text=? THEN 1 ELSE 0 END AS body_matches FROM outbox WHERE business=? AND id=?',(expected_body,business,identifier))
    if len(rows)!=1 or rows[0].pop('body_matches',None)!=1:_deny()
    return rows[0]


def _key(business,identifier):return PREFIX+'v1:'+_hash([business,identifier])


def _load(reader,business,identifier):
    rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',16384)})+' FROM runtime WHERE key=?',(_key(business,identifier),))
    if len(rows)!=1:_deny()
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:_deny()
            result[key]=value
        return result
    doc=json.loads(rows[0]['value'],object_pairs_hook=pairs)
    if type(doc) is not dict or set(doc)!={'version','event','request','command','bindings','query','snapshot','result','refs','policy','seal','registry'}:_deny()
    if type(doc['version']) is not int or doc['version']!=1 or type(doc['event']) is not str or not 0<len(doc['event'].encode())<=1024:_deny()
    if type(doc['request']) is not dict or set(doc['request'])!={'sender','channel','source_id'} or any(type(x) is not str for x in doc['request'].values()):_deny()
    if type(doc['bindings']) is not list or not 1<=len(doc['bindings'])<=3 or any(type(x) is not list or len(x)!=3 or any(type(v) is not str or len(v)>1024 for v in x) for x in doc['bindings']):_deny()
    if type(doc['refs']) is not list or not 1<=len(doc['refs'])<=16 or doc['refs']!=sorted(set(doc['refs'])) or any(type(x) is not str or not 0<len(x)<=160 for x in doc['refs']):_deny()
    for key in ('command','query','snapshot','result','policy','seal','registry'):
        if type(doc[key]) is not str or re.fullmatch('[0-9a-f]{64}',doc[key]) is None:_deny()
    return doc


def cancel(store,business,identifier):
    store.execute("UPDATE outbox SET state='cancelled',error=? WHERE business=? AND id=? AND dedupe LIKE 'selected-knowledge-preview:%' AND state IN ('pending','retry','blocked')",(CANCEL,business,identifier))


def _bindings(documents):return [[doc.source_id,doc.document_id,doc.document_key] for doc in documents]


def handle(engine,event,payload):
    host=_Host(engine);store,db,config,business,intel,index=host.saved[:6]
    if not db.in_transaction:_deny()
    request={key:payload.get(key) for key in ('sender','channel','source_id')};policy=None
    db.execute('SAVEPOINT selected_knowledge')
    try:
        scope,parts,policy=host.policy(request);reader=_Reader(store)
        if type(event) is not str or not 0<len(event.encode())<=1024:_deny()
        _barrier(reader,business,scope,event);accepted=_accepted(reader,business,event)
        if accepted!=dict(request,text=payload['text']):_deny()
        parsed=_parse(payload['text'])
        if parsed is None:
            store.queue(business,request['sender'],TEMPLATE,'selected-knowledge-template:'+_hash([event,scope]),host.sample(request,policy,[]),preferred_transport=request['channel'])
            if host.policy(request)[2]!=policy:_deny()
        else:
            selectors,query=parsed;documents=host.documents(request,selectors);stamps=[]
            now=host.sample(request,policy,stamps)
            source_proof=host.source_proofs(request,selectors,documents,reader,now)
            if host.policy(request)[2]!=policy:_deny()
            first=index.search_selected(query,reader_key(business,request['sender']),now=now,documents=documents)
            body=_render(first)
            if host.policy(request)[2]!=policy:_deny()
            dedupe=PREFIX+_hash([event,scope])
            if not store.queue(business,request['sender'],body,dedupe,now,parse_mode='HTML',preferred_transport=request['channel']):_deny()
            if host.policy(request)[2]!=policy:_deny()
            identifier=store.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(business,dedupe)).fetchone()[0]
            intel.protect_outbox(identifier,first.consulted_ids)
            if host.policy(request)[2]!=policy:_deny()
            row=_row(reader,business,identifier,body)
            if row['state']!='pending' or row['task_id'] is not None or row['recipient']!=request['sender'] or row['preferred_transport']!=request['channel'] or row['parse_mode']!='HTML':_deny()
            doc=dict(version=1,event=event,request=request,command=_hash(payload['text']),bindings=_bindings(documents),query=_hash(query),
                snapshot=first.snapshot_digest,result=_hash(first.result_json),refs=list(first.consulted_ids),policy=policy,seal=_seal(dict(row,text=body)),registry=source_proof)
            encoded=json.dumps(doc,ensure_ascii=False,separators=(',',':'))
            if len(encoded.encode())>16384:_deny()
            store.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(_key(business,identifier),encoded))
            if host.documents(request,selectors)!=documents:_deny()
            final_now=host.sample(request,policy,stamps)
            if host.source_proofs(request,selectors,documents,reader,final_now)!=source_proof:_deny()
            if host.policy(request)[2]!=policy:_deny()
            if _load(reader,business,identifier)!=doc or _row(reader,business,identifier,body)!=row or _accepted(reader,business,event)!=accepted:_deny()
            _protected(reader,business,identifier,doc['refs']);_barrier(reader,business,scope,event)
            final=index.search_selected(query,reader_key(business,request['sender']),now=final_now,documents=documents)
            if final!=first or _render(final)!=body or host.policy(request)[2]!=policy:_deny()
    except BaseException as error:
        db.execute('ROLLBACK TO selected_knowledge');db.execute('RELEASE selected_knowledge')
        if not isinstance(error,(ValueError,TypeError,KeyError)):raise
        try:allowed=policy is not None and host.policy(request)[2]==policy
        except Exception:allowed=False
        if allowed:store.queue(business,request['sender'],NOTICE,'selected-knowledge-notice:'+_hash(event),engine.now(),preferred_transport=request['channel'])
    else:db.execute('RELEASE selected_knowledge')
    db.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(business,event))


def _chat(reader,business,request,parts,now):
    spec={'bot_chat':_text('bot_chat',128,True),'business_chat':_text('business_chat',128,True),'connection':_text('connection',512,True),'last_inbound':'(last_inbound IS NULL OR '+_numeric('last_inbound')+')'}
    rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM chats WHERE business=? AND recipient=?',(business,request['sender']))
    if len(rows)!=1:_deny()
    chat=rows[0]
    if request['channel']=='bot':
        if chat['bot_chat']!=parts[3]:_deny()
    elif chat['business_chat']!=parts[3] or chat['connection']!=parts[4] or not 0<=now-chat['last_inbound']<86400:_deny()


def make_delivery_guard(engine):
    host=_Host(engine);store,db,config,business,intel,index=host.saved[:6]
    def guard(row,route=None,transport=None):
        if not row['dedupe'].startswith(PREFIX):return False
        try:
            host.bound()
            reader=_Reader(store);doc=_load(reader,business,row['id']);request=doc['request']
            scope,parts,policy=host.policy(request)
            if policy!=doc['policy']:_deny()
            accepted=_accepted(reader,business,doc['event'],True)
            if any(accepted[key]!=request[key] for key in request) or _hash(accepted['text'])!=doc['command']:_deny()
            parsed=_parse(accepted['text'])
            if parsed is None:_deny()
            selectors,query=parsed;documents=host.documents(request,selectors)
            if _bindings(documents)!=doc['bindings'] or _hash(query)!=doc['query']:_deny()
            now=host.sample(request,policy,[]);_chat(reader,business,request,parts,now)
            if route is not None and (transport!=request['channel'] or set(route)!=({'chat_id','business_connection_id'} if request['channel']=='business' else {'chat_id'})
                    or str(route['chat_id'])!=parts[3] or (route.get('business_connection_id') or None)!=parts[4]):_deny()
            if host.source_proofs(request,selectors,documents,reader,now)!=doc['registry']:_deny()
            if host.policy(request)[2]!=policy:_deny()
            _barrier(reader,business,scope,doc['event']);_protected(reader,business,row['id'],doc['refs'])
            current=_row(reader,business,row['id'],row['text'])
            if (current['state'] not in ('pending','retry','blocked') or any(current[key]!=row[key] for key in current)
                    or current['task_id'] is not None or current['recipient']!=request['sender'] or current['preferred_transport']!=request['channel']
                    or current['parse_mode']!='HTML' or current['dedupe']!=PREFIX+_hash([doc['event'],scope])):_deny()
            if _load(reader,business,row['id'])!=doc or _accepted(reader,business,doc['event'],True)!=accepted:_deny()
            # No external clock/source/route callback or metadata read follows
            # this final bounded data phase; rendering and host checks are pure.
            packet=index.search_selected(query,reader_key(business,request['sender']),now=now,documents=documents)
            body=_render(packet)
            if (packet.snapshot_digest!=doc['snapshot'] or _hash(packet.result_json)!=doc['result'] or list(packet.consulted_ids)!=doc['refs']
                    or body!=row['text'] or _seal(dict(current,text=body))!=doc['seal']):_deny()
            if host.policy(request)[2]!=policy:_deny()
            return True
        except Exception:
            if store.db is db:cancel(store,business,row['id'])
            return False
    return guard
