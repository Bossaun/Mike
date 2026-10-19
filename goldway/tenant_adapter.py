"""Default-disabled bounded point reads of explicitly protected tenant tasks.

Trusted callbacks are host assertions, not authentication. Results are observations,
never credentials, send permission or a claim that raw Store is tenant-isolated.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import sqlite3

from . import business_scope as core
from .intelligence import reader_key
from .monthly_task_history import _Reader, _bounded_columns, _text, _integer, _numeric
from .telegram import Dispatcher


class TenantAdapterUnavailable(ValueError):
    def __init__(self):
        super().__init__('tenant_adapter_unavailable')


def _deny():
    raise TenantAdapterUnavailable()


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _id(value,maximum=128):
    if type(value) is not str or not 0<len(value.encode())<=maximum or value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):
        _deny()
    return value


def _time(value):
    if type(value) is not int or not 0<=value<2**53:
        _deny()


def _one(reader,table,spec,where,args):
    rows=reader.query('SELECT '+_bounded_columns(spec)+' FROM '+table+' WHERE '+where,args)
    if len(rows)!=1:
        _deny()
    return rows[0]


_TASK_META={'id':_text('id',128),'owner':_text('owner',128),'status':_text('status',32),
            'source_id':_text('source_id',256),'revision':_integer('revision'),'public_number':_integer('public_number')}
_TASK_BODY={'title':_text('title',2048),'owner':_text('owner',128),'deadline':_text('deadline',64,True),
            'next_step':_text('next_step',2048),'criterion':_text('criterion',2048),
            'status':_text('status',32),'revision':_integer('revision'),'public_number':_integer('public_number')}
_STATUSES=frozenset({'proposed','confirmed','in_progress','blocked','completed'})


@dataclass(frozen=True)
class TenantTaskSnapshot:
    task:core.ScopedRef
    owner:core.ScopedRef
    sources:tuple[core.ScopedRef,...]
    title:str
    deadline:str|None
    next_step:str
    criterion:str
    status:str
    revision:int
    public_number:int
    as_of:int
    proof_digest:str
    live_authorized:bool=False


@dataclass(frozen=True)
class TenantRoutePreview:
    snapshot:TenantTaskSnapshot
    recipient:core.ScopedRef
    connection:core.ScopedRef
    chat_id:str
    transport:str
    proof_digest:str
    review_required:bool=True
    live_authorized:bool=False


class TenantAdapter:
    def __init__(self,*,engine,dispatcher,current_context,source_bindings,enabled=False):
        self._engine=engine;self._dispatcher=dispatcher
        self._context=current_context;self._bindings=source_bindings
        self._enabled=enabled is True
        self._captured=self._identities()
        self._policy=_digest(getattr(engine,'c',None))
        sources=getattr(getattr(engine,'intelligence',None),'sources',None)
        self._source_policy=_digest([getattr(sources,'specs',None),sorted(getattr(sources,'members',()))])

    def _identities(self):
        e=self._engine;d=self._dispatcher;i=getattr(e,'intelligence',None)
        index=getattr(i,'index',None);s=getattr(e,'s',None)
        return (e,d,s,getattr(s,'db',None),getattr(e,'c',None),getattr(e,'b',None),i,index,
                getattr(i,'sources',None),getattr(d,'s',None),getattr(d,'c',None),getattr(d,'b',None),
                getattr(i,'s',None),getattr(i,'c',None),getattr(i,'b',None),getattr(index,'store',None),
                getattr(getattr(d,'route',None),'__func__',None))

    def _host(self):
        current=self._identities()
        # Strings compare by value; every authority-bearing object by identity.
        for n,(before,after) in enumerate(zip(self._captured,current)):
            if (before!=after if n in (5,11,14) else before is not after):
                _deny()
        e,d,s,db,c,b,i,index,sources,ds,dc,dbusiness,ins,inc,ib,idxs,route=current
        if s is None or db is None or type(c) is not dict or i is None or index is None or sources is None:
            _deny()
        if s is not ds or c is not dc or s is not ins or c is not inc or s is not idxs or b!=dbusiness or b!=ib or c.get('business')!=b:
            _deny()
        if type(d) is not Dispatcher or route is not Dispatcher.route or 'route' in d.__dict__:
            _deny()
        if getattr(sources,'index',None) is not index or _digest([sources.specs,sorted(sources.members)])!=self._source_policy or _digest(c)!=self._policy:
            _deny()
        return s,c,b,i

    def _current(self,task,now):
        s,c,b,i=self._host()
        context=self._context()
        if type(context) is not tuple or len(context)!=2:
            _deny()
        scope,actor=context
        if type(scope) is not core.TenantScope or type(actor) is not core.ScopedRef or type(task) is not core.ScopedRef:
            _deny()
        core.require_operation(scope,operation='read',actor=actor,references=(task,),now=now)
        if task.kind!='task' or scope.tenant!=b:
            _deny()
        employees=c.get('employees');admins=c.get('admins')
        if type(employees) is not dict or type(admins) not in (list,tuple) or not set(scope.readers)<=set(employees) or set(scope.admins)!=set(admins):
            _deny()
        if actor.id not in employees or not scope.admins<=scope.readers:
            _deny()
        mappings=self._bindings()
        if type(mappings) is not tuple or not 1<=len(mappings)<=20:
            _deny()
        seen=set()
        for item in mappings:
            if type(item) is not tuple or len(item)!=5:
                _deny()
            tenant,chunk,document,source,registration=item
            for value,limit in ((tenant,64),(chunk,128),(document,256),(source,256),(registration,128)):
                _id(value,limit)
            if tenant!=b or chunk in seen:
                _deny()
            seen.add(chunk)
        self._host()
        return scope,actor,mappings

    def _pass(self,reader,task,now):
        scope,actor,mappings=self._current(task,now)
        s,c,b,intel=self._host()
        meta=_one(reader,'tasks',_TASK_META,'business=? AND id=?',(b,task.id))
        if meta['id']!=task.id or meta['owner'] not in c['employees'] or meta['owner'] not in scope.readers or meta['status'] not in _STATUSES:
            _deny()
        links=reader.query('SELECT '+_bounded_columns({'source_id':_text('source_id',256)})+' FROM task_sources WHERE business=? AND task_id=? ORDER BY source_id',(b,task.id),maximum=20)
        linked=tuple(row['source_id'] for row in links)
        if not linked or len(set(linked))!=len(linked) or meta['source_id'] not in linked:
            _deny()
        parents=[]
        for source in linked:
            parent=_one(reader,'sources',{'id':_text('id',256),'deleted':"typeof(deleted)='integer' AND deleted=0"},'business=? AND id=?',(b,source))
            parents.append(parent)
        acl=_one(reader,'intelligence_task_acl',{'evidence_ids':_text('evidence_ids',8192)},'business=? AND task_id=?',(b,task.id))
        refs=json.loads(acl['evidence_ids'])
        if type(refs) is not list or not 1<=len(refs)<=20 or len(set(refs))!=len(refs):
            _deny()
        for ref in refs:
            _id(ref)
            if ref.startswith('work-task:'):
                _deny()
        refs=tuple(sorted(refs));mapping={item[1]:item for item in mappings}
        docs=[];acls=[]
        specs=getattr(intel.sources,'specs',None)
        policy=c.get('intelligence',{}).get('sources')
        if type(specs) not in (list,tuple) or type(policy) is not list:
            _deny()
        expected_prefix='source:'+hashlib.sha256(b.encode()).hexdigest()+':'
        if intel.prefix!=expected_prefix:
            _deny()
        for ref in refs:
            if ref not in mapping:
                _deny()
            tenant,chunk,document,source,registration=mapping[ref]
            configured=[spec for spec in policy if type(spec) is dict and spec.get('id')==registration]
            initialized=[spec for spec in specs if type(spec) is dict and spec.get('id')==source]
            if len(configured)!=1 or len(initialized)!=1 or source!=expected_prefix+registration:
                _deny()
            if actor.id not in configured[0].get('readers',()) or reader_key(b,actor.id) not in initialized[0].get('readers',()):
                _deny()
            ch=_one(reader,'knowledge_chunks',{'id':_text('id',128),'document_key':_text('document_key',256)},'id=?',(ref,))
            doc=_one(reader,'knowledge_documents',{'id':_text('id',256),'source_id':_text('source_id',256),
                'confirmed':"typeof(confirmed)='integer' AND confirmed IN (0,1)",'observed_at':_numeric('observed_at'),
                'permission_expires_at':_numeric('permission_expires_at')},'id=?',(document,))
            if ch['document_key']!=document or doc['source_id']!=source or doc['confirmed']!=1:
                _deny()
            grant=_one(reader,'knowledge_readers',{'document_key':_text('document_key',256),'reader':_text('reader',128)},'document_key=? AND reader=?',(document,reader_key(b,actor.id)))
            observations=reader.query('SELECT '+_bounded_columns({'source_id':_text('source_id',256),'observed_at':_numeric('observed_at')})+' FROM knowledge_document_observations WHERE document_key=?',(document,))
            if len(observations)!=1 or observations[0]['source_id']!=source or observations[0]['observed_at']!=doc['observed_at']:
                _deny()
            revoked=reader.query('SELECT '+_bounded_columns({'observed_at':_numeric('observed_at'),'inclusive':"typeof(inclusive)='integer' AND inclusive IN (0,1)"})+' FROM knowledge_source_revocations WHERE source_id=?',(source,))
            if revoked and (doc['observed_at']<revoked[0]['observed_at'] or doc['observed_at']==revoked[0]['observed_at'] and revoked[0]['inclusive']==1):
                _deny()
            issued=max(scope.issued_at,math.ceil(doc['observed_at']));expires=min(scope.expires_at,math.floor(doc['permission_expires_at']))
            source_ref=core.ScopedRef(b,'source',ref)
            acls.append(core.SourceACL(source_ref,(actor,),issued,expires))
            docs.append((ch,doc,grant,observations,revoked,configured[0],initialized[0]))
        binding=core.TaskBinding(task,core.ScopedRef(b,'reader',meta['owner']),tuple(acl.source for acl in acls))
        core.validate_task_access(scope,binding=binding,actor=actor,acls=tuple(acls),now=now)
        self._host()
        body=_one(reader,'tasks',_TASK_BODY,'business=? AND id=?',(b,task.id))
        if any(body[key]!=meta[key] for key in ('owner','status','revision','public_number')):
            _deny()
        self._host()
        proof=(scope,actor,mappings,meta,linked,parents,refs,docs,body)
        return proof,binding,tuple(acls),body

    def _chat(self,reader,actor):
        s,c,b,i=self._host()
        person=c['employees'].get(actor.id)
        uid=person.get('telegram_user_id') if type(person) is dict else None
        if type(uid) is not int or uid<=0:
            _deny()
        row=_one(reader,'chats',{'business':_text('business',64),'recipient':_text('recipient',128),
            'business_chat':_text('business_chat',32,True),'connection':_text('connection',128,True),
            'last_inbound':'(last_inbound IS NULL OR '+_numeric('last_inbound')+')',
            'bot_chat':_text('bot_chat',32),'group_chat':_text('group_chat',32,True),
            'group_thread_id':_integer('group_thread_id',True)},'business=? AND recipient=?',(b,actor.id))
        if row['bot_chat']!=str(uid):
            _deny()
        return row

    def _run(self,task,now,route):
        if not self._enabled:
            _deny()
        try:
            _time(now);store,config,b,intel=self._host();reader=_Reader(store)
            first,binding,acls,body=self._pass(reader,task,now)
            scope,actor=first[:2];first_digest=_digest(self._serial(first));chat=None;connection=None
            if route:
                connection=core.ScopedRef(b,'connection','bot:'+actor.id)
                chat=self._chat(reader,actor)
                outbox=core.ScopedRef(b,'outbox','preview:'+hashlib.sha256(task.id.encode()).hexdigest())
                prepared=core.prepare_delivery(scope,outbox=outbox,binding=binding,recipient=actor,connection=connection,acls=acls,now=now)
                if prepared.live_authorized is not False or prepared.review_required is not True:
                    _deny()
                actual=self._dispatcher.route(actor.id,'bot')
                if actual!=({'chat_id':chat['bot_chat']},'bot'):
                    _deny()
                self._host()
            final,_,_,_=self._pass(reader,task,now)
            if first_digest!=_digest(self._serial(final)) or route and self._chat(reader,actor)!=chat:
                _deny()
            self._host()
            proof=first_digest
            snapshot=TenantTaskSnapshot(task,binding.owner,binding.sources,body['title'],body['deadline'],body['next_step'],body['criterion'],body['status'],body['revision'],body['public_number'],now,proof)
            if not route:
                return snapshot
            return TenantRoutePreview(snapshot,actor,connection,chat['bot_chat'],'bot',_digest([proof,chat,connection.id]))
        except TenantAdapterUnavailable:
            raise
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError,UnicodeError,sqlite3.Error):
            raise TenantAdapterUnavailable() from None

    @staticmethod
    def _serial(value):
        from dataclasses import asdict,is_dataclass
        if is_dataclass(value):return TenantAdapter._serial(asdict(value))
        if isinstance(value,dict):return {k:TenantAdapter._serial(v) for k,v in value.items()}
        if isinstance(value,(tuple,list)):return [TenantAdapter._serial(v) for v in value]
        if isinstance(value,(set,frozenset)):return sorted(TenantAdapter._serial(v) for v in value)
        return value

    def read_task(self,task,*,now):
        return self._run(task,now,False)

    def preview_bot_route(self,task,*,now):
        return self._run(task,now,True)
