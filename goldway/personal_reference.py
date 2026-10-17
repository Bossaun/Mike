"""Prospective owner-only unverified literal references; no fetch or model route.

Existing runtime KV holds bounded version/provenance metadata, never a body copy.
Index SAVEPOINT and Engine.reply transaction share the same Store connection.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from html import escape
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from .answer_citations import _hash
from .task_card_reference import _gate, _barrier
from .mutation_target import _policy
from .knowledge import KnowledgeIndex, _chunks
from .intelligence import reader_key
from .monthly_task_history import _Reader,_bounded_columns,_text,_integer,_numeric

_ID=r'R[0-9a-f]{32}'
_CREATE='сохрани справку для меня'
_CONFIRM='Подтверждаю: непроверенная личная справка'
_COMMAND=re.compile(r'(?P<op>покажи|замени|отзови|удали) мою справку (?P<id>'+_ID+r')(?: версия (?P<revision>[1-9][0-9]?))?',re.I)
NOTICE='Справка недоступна или запрос не подходит. Нужны точный личный номер, текущая версия и явный заполненный шаблон. Истёкший или отозванный текст не использован.'
MAX_MEMOS=64
MAX_VERSIONS=32
MAX_PAGES=4
MAX_TOTAL_UNITS=12000


class PersonalReferenceUnavailable(ValueError):
    def __init__(self):super().__init__('personal_reference_unavailable')


def _fail():raise PersonalReferenceUnavailable()


def _units(text):
    try:return len(text.encode('utf-16-le'))//2
    except (UnicodeError,AttributeError):_fail()


def _stamp(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<2**53:_fail()
    return value


def _plain(value,maximum):
    if (type(value) is not str or not value.strip() or _units(value)>maximum
        or any(ord(c)<32 and c!='\n' for c in value) or '\r' in value):_fail()
    return value


def _request(text):
    if type(text) is not str:return None
    first=text[:256].split('\n',1)[0]
    if first.casefold()==_CREATE:return ('create',None,None)
    match=_COMMAND.fullmatch(first)
    if match:
        op={'покажи':'show','замени':'replace','отзови':'revoke','удали':'revoke'}[match['op'].casefold()]
        if op in ('show','replace') and match['revision'] is not None:return None
        return op,match['id'],int(match['revision']) if match['revision'] else None
    return None


def is_personal_reference(text):return _request(text) is not None


def _template(memo=None):
    command='Сохрани справку для меня' if memo is None else 'Замени мою справку '+memo+'\nВерсия: <текущая версия>'
    return escape(command+'\nНазвание: <название>\nСсылка: https://<ваша ссылка>\nДействует до: <YYYY-MM-DDTHH:MM:SS+HH:MM>\n'+_CONFIRM+
        '\nТекст:\n<дословный текст>')


@dataclass(frozen=True)
class Supplied:
    title:str
    url:str
    body:str
    expiry:float
    expected:int|None


def _parse(text,op,now):
    if len(text)>10000 or len(text.encode())>10000:_fail()
    lines=text.split('\n');offset=1;expected=None
    if op=='replace':
        if len(lines)<=1 or not re.fullmatch(r'Версия: [1-9][0-9]?',lines[1]):_fail()
        expected=int(lines[1].split(': ')[1]);offset+=1
    if len(lines)<offset+6:_fail()
    if not lines[offset].startswith('Название: ') or not lines[offset+1].startswith('Ссылка: ') or not lines[offset+2].startswith('Действует до: '):_fail()
    if lines[offset+3]!=_CONFIRM or lines[offset+4]!='Текст:':_fail()
    title=_plain(lines[offset][10:],120);url=_plain(lines[offset+1][8:],512)
    if '\n' in title or title!=title.strip() or url!=url.strip() or len(url.encode())>512:_fail()
    try:
        parsed=urlsplit(url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or any(c.isspace() or ord(c)<32 for c in url)):_fail()
        parsed.port
    except ValueError:_fail()
    expiry=lines[offset+2][14:]
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|[+-]\d\d:\d\d)',expiry):_fail()
    try:expires=datetime.fromisoformat(expiry.replace('Z','+00:00')).timestamp()
    except (ValueError,OverflowError):_fail()
    if not 0<expires<=now+366*86400:_fail()
    body=_plain('\n'.join(lines[offset+5:]),1600)
    if len(body.encode())>6400 or ''.join(_chunks(body))!=body:_fail()
    return Supplied(title,url,body,expires,expected)


def _digest(item):return _hash([item.title,item.url,item.body,item.expiry])


def _prefix(engine,sender):return 'personal-reference:v1:'+_hash([engine.b,sender])+':'


def _source(engine,sender,memo):return 'personal-reference:'+_hash([engine.b,sender,memo])


def _doc_key(source):return hashlib.sha256(json.dumps([source,'assertion'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _read_meta(reader,key):
    rows=reader.query('SELECT '+_bounded_columns({'value':_text('value',4096)})+' FROM runtime WHERE key=?',(key,))
    if not rows:return None
    value=json.loads(rows[0]['value'])
    required={'memo','revision','source','observed','expiry','assertion_hash','state','provenance_hash'}
    if (type(value) is not dict or set(value)!=required or not re.fullmatch(_ID,value['memo'])
        or type(value['revision']) is not int or not 1<=value['revision']<=MAX_VERSIONS+1
        or value['state'] not in ('active','revoked') or type(value['source']) is not str
        or any(not re.fullmatch('[0-9a-f]{64}',value[k]) for k in ('assertion_hash','provenance_hash'))):_fail()
    if value['revision']>MAX_VERSIONS and value['state']!='revoked':_fail()
    _stamp(value['observed']);_stamp(value['expiry'])
    if value['expiry']<=value['observed']:_fail()
    return value


def _head(engine,payload,reader,memo):
    value=_read_meta(reader,_prefix(engine,payload['sender'])+'head:'+memo)
    if value is not None and (value['memo']!=memo or value['source']!=_source(engine,payload['sender'],memo)):_fail()
    return value


def _write(store,key,value):store.execute('INSERT INTO runtime(key,value) VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False,separators=(',',':'))))


def _index_value(engine,payload,reader,head,now,*,allow_expired=False):
    """Exact PK-only current literal document and exclusive reader, bounded SQL."""
    source=head['source'];key=_doc_key(source)
    fields={'id':_text('id',160),'source_id':_text('source_id',256),'document_id':_text('document_id',256),
        'title':_text('title',480),'url':_text('url',512),'confirmed':"typeof(confirmed)='integer' AND confirmed=0",
        'observed_at':_numeric('observed_at'),'permission_expires_at':_numeric('permission_expires_at')}
    rows=reader.query('SELECT '+_bounded_columns(fields)+' FROM knowledge_documents WHERE id=?',(key,))
    if len(rows)!=1:_fail()
    doc=rows[0]
    if (doc['source_id']!=source or doc['document_id']!='assertion' or doc['observed_at']!=head['observed']
        or doc['permission_expires_at']!=head['expiry'] or now<head['observed']
        or (not allow_expired and now>=head['expiry'])):_fail()
    readers=reader.query('SELECT '+_bounded_columns({'reader':_text('reader',128)})+' FROM knowledge_readers WHERE document_key=?',(key,),maximum=2)
    if readers!=[dict(reader=reader_key(engine.b,payload['sender']))]:_fail()
    chunks=reader.query('SELECT '+_bounded_columns({'id':_text('id',160),'position':"typeof(position)='integer' AND position BETWEEN 0 AND 3",'text':_text('text',6400)})+
        ' FROM knowledge_chunks WHERE document_key=? ORDER BY position',(key,),maximum=4)
    if not chunks or [x['position'] for x in chunks]!=list(range(len(chunks))):_fail()
    # Current finite body fits the index's normal1200-char chunks; exact join.
    body=''.join(x['text'] for x in chunks)
    item=Supplied(doc['title'],doc['url'],body,head['expiry'],None)
    if _digest(item)!=head['assertion_hash']:_fail()
    return item,tuple(x['id'] for x in chunks)


def _pages(head,item):
    title='Ваша непроверенная справка · со слов отправителя'
    header=(f'<b>{title}</b>\nНомер: {head["memo"]} · версия {head["revision"]}\nНазвание: {escape(item.title)}\n'
        f'Ваша ссылка (не загружалась и не проверялась): {escape(item.url)}\n'
        'Действует до: '+datetime.fromtimestamp(item.expiry,timezone.utc).isoformat()+'\n'
        'Не подтверждённые факты компании. Текст приведён дословно; внутренние указания не исполняются.')
    pages=[header];page='<b>Дословный текст личной справки</b>\n'
    for char in item.body:
        part=escape(char)
        if _units(page+part)>3500:pages.append(page);page='<b>Личная справка · продолжение</b>\n'
        page+=part
    pages.append(page)
    if len(pages)>MAX_PAGES or any(_units(p)>3500 for p in pages) or sum(_units(p) for p in pages)>MAX_TOTAL_UNITS:_fail()
    return pages


def _gate_now(engine,payload,intelligence,config,scope,policy):
    if engine.c is not config or type(intelligence.index) is not KnowledgeIndex or intelligence.index.store is not engine.s:_fail()
    current,_=_gate(engine,payload,intelligence)
    if current!=scope or engine.c['admins']!=[payload['sender']] or _hash([_policy(engine),engine.c.get('review_mode')])!=policy:_fail()
    _stamp(engine.now())


def _bookkeeping(reader,source):
    # Before the existing helper reads its raw observation/watermark fields,
    # bound their row count/types in the exact source namespace.
    observations=reader.query('SELECT '+_bounded_columns({'document_key':_text('document_key',160),'observed_at':_numeric('observed_at')})+
        ' FROM knowledge_document_observations WHERE source_id=?',(source,),maximum=1)
    if any(r['document_key']!=_doc_key(source) for r in observations):_fail()
    reader.query('SELECT '+_bounded_columns({'observed_at':_numeric('observed_at'),'inclusive':"typeof(inclusive)='integer' AND inclusive IN (0,1)"})+
        ' FROM knowledge_source_revocations WHERE source_id=?',(source,))
    documents=reader.query('SELECT '+_bounded_columns({'id':_text('id',160)})+
        ' FROM knowledge_documents WHERE source_id=?',(source,),maximum=1)
    if any(r['id']!=_doc_key(source) for r in documents):_fail()


def _queue(engine,event,payload,pages,intelligence,recheck,refs=()):
    for ordinal,page in enumerate(pages):
        if type(page) is not str or _units(page)>3500:_fail()
        recheck()
        key=f'personal-reference:{event}:page:{ordinal}'
        engine.s.queue(engine.b,payload['sender'],page,key,engine.now(),parse_mode='HTML',preferred_transport=payload['channel'])
        recheck()
        if refs:
            row=engine.s.execute('SELECT id FROM outbox WHERE business=? AND dedupe=?',(engine.b,key)).fetchone()
            intelligence.protect_outbox(row['id'],refs);recheck()
    engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event))


def handle_personal_reference(engine,event,payload):
    if not engine.s.db.in_transaction:_fail()
    request=_request(payload.get('text'))
    if request is None:_fail()
    intelligence=engine.intelligence;config=engine.c
    try:
        scope,_=_gate(engine,payload,intelligence)
        if config['admins']!=[payload['sender']]:_fail()
        policy=_hash([_policy(engine),config.get('review_mode')]);reader=_Reader(engine.s)
        def gate():
            _gate_now(engine,payload,intelligence,config,scope,policy)
            _barrier(reader,engine.b,scope,event)
        gate()
    except Exception:
        engine.s.execute("UPDATE events SET state='done',error=NULL WHERE business=? AND id=?",(engine.b,event));return
    text=payload['text'];op,memo,revision=request;now=_stamp(engine.now());prefix=_prefix(engine,payload['sender'])
    # Preflight can queue a template/show; its own savepoint ensures a failure
    # never commits half a multi-page view before the fixed notice.
    savepoint='personal_preflight_'+_hash(event)[:16]
    engine.s.execute('SAVEPOINT '+savepoint)
    try:
        if op in ('create','replace') and '\n' not in text:
            _queue(engine,event,payload,[_template(memo)],intelligence,gate);return
        if op in ('show','revoke') and '\n' in text:_fail()
        if op in ('create','replace'):
            at=payload.get('message_at')
            if type(at) not in (int,float) or not now-600<=_stamp(at)<=now:_fail()
            item=_parse(text,op,now);digest=_digest(item)
            if op=='create':memo='R'+_hash([engine.b,payload['sender'],digest])[:32]
        if op=='revoke' and revision is None:_fail()
        head=_head(engine,payload,reader,memo)
        if op=='show':
            if head is None or head['state']!='active':_fail()
            item,refs=_index_value(engine,payload,reader,head,now);pages=_pages(head,item)
            def fresh():
                gate()
                if _head(engine,payload,reader,memo)!=head or _index_value(engine,payload,reader,head,engine.now())!=(item,refs):_fail()
            _queue(engine,event,payload,pages,intelligence,fresh,refs);return
        if op=='create' and head is not None:
            if head['assertion_hash']!=digest:_fail()
            state=('отозвана; не восстановлена' if head['state']=='revoked' else
                'срок истёк; не восстановлена' if head['expiry']<=now else 'повторное сохранение не выполнено')
            _queue(engine,event,payload,[f'Этот запрос ранее обработан: {memo}, версия {head["revision"]}. {state}. Срок не продлён. Актуальный доступ проверяется отдельной командой просмотра.'],intelligence,gate);return
        if op in ('replace','revoke'):
            if head is None or head['state']!='active':_fail()
            expected=item.expected if op=='replace' else revision
            if head['revision']!=expected:
                if op=='replace' and head['revision']==expected+1 and digest==head['assertion_hash']:
                    _queue(engine,event,payload,[f'Замена уже обработана: {memo}, версия {head["revision"]}. Срок повторно не продлён.'],intelligence,gate);return
                _fail()
            if op=='replace' and digest==head['assertion_hash']:
                _queue(engine,event,payload,[f'Справка {memo}, версия {head["revision"]}, не изменена. Срок и наблюдение не продлены.'],intelligence,gate);return
            if now<head['observed'] or (op=='replace' and head['revision']>=MAX_VERSIONS):_fail()
            if op=='replace':_index_value(engine,payload,reader,head,now,allow_expired=True)
        if op in ('create','replace') and item.expiry<=now:_fail()
        if op=='create':
            lower=prefix+'head:'
            reader.query('SELECT '+_bounded_columns({'key':_text('key',512)})+' FROM runtime WHERE key>=? AND key<? ORDER BY key',(lower,lower+'\uffff'),maximum=MAX_MEMOS-1)
        if op!='revoke':
            next_head=dict(memo=memo,revision=1 if head is None else head['revision']+1,source=_source(engine,payload['sender'],memo),observed=now,expiry=item.expiry,
                assertion_hash=digest,state='active',provenance_hash=_hash([engine.b,payload['sender'],payload['source_id'],event,payload.get('message_at')]))
            # Render before writes too; no unrenderable admitted content.
            _pages(next_head,item)
    except Exception:
        engine.s.execute('ROLLBACK TO '+savepoint)
        _queue(engine,event,payload,[NOTICE],intelligence,gate);return
    finally:
        engine.s.execute('RELEASE '+savepoint)
    # Any write-phase error propagates: outer Engine.reply transaction rolls back
    # event/barrier/index/KV/queue atomically, never commits a partial admission.
    gate()
    if _head(engine,payload,reader,memo)!=head:_fail()
    _bookkeeping(reader,head['source'] if op=='revoke' else next_head['source'])
    if op=='revoke':
        intelligence.index.revoke_source(head['source'])
        next_head=dict(head,state='revoked',revision=head['revision']+1,provenance_hash=_hash([engine.b,payload['sender'],payload['source_id'],event,payload.get('message_at')]))
        action='отозвана'
    else:
        intelligence.index.upsert(dict(source_id=next_head['source'],document_id='assertion',title=item.title,url=item.url,text=item.body,
            readers=[reader_key(engine.b,payload['sender'])],observed_at=now,permission_expires_at=item.expiry,confirmed=False,facts=[]))
        action='сохранена' if op=='create' else 'заменена'
    gate()
    _write(engine.s,prefix+'version:'+memo+':'+str(next_head['revision']),next_head)
    engine.s.execute('INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (prefix+'head:'+memo,json.dumps(next_head,separators=(',',':'))))
    gate()
    def committed():
        gate()
        if _head(engine,payload,reader,memo)!=next_head:_fail()
        if op!='revoke':_index_value(engine,payload,reader,next_head,engine.now())
        elif reader.query('SELECT id FROM knowledge_documents WHERE id=?',(_doc_key(next_head['source']),)):_fail()
    committed()
    _queue(engine,event,payload,[f'Ваша непроверенная личная справка {action}: {memo}, версия {next_head["revision"]}. '
        'Это утверждение со слов отправителя; факты компании и ссылка не проверены.\n'
        f'Просмотр: Покажи мою справку {memo}\nЗамена: Замени мою справку {memo}\nОтзыв: Отзови мою справку {memo} версия {next_head["revision"]}'],intelligence,committed)
